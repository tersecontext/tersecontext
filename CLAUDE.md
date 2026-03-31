# CLAUDE.md — Dynamic analysis pipeline

## TerseContext — system overview

TerseContext produces minimum sufficient LLM context. The dynamic analysis pipeline
discovers runtime behaviour that static analysis cannot see — side effects, conditional
branches, calls through decorators or dependency injection, hidden database writes.

All six dynamic services can be built in parallel. They communicate through shared
stores and streams — never through direct service calls.

All services expose: GET /health, GET /ready, GET /metrics

---

## The six services and their ports

| Service               | Port | Input                    | Output                   |
|-----------------------|------|--------------------------|--------------------------|
| entrypoint-discoverer | 8092 | Neo4j + Postgres         | EntrypointJob queue      |
| instrumenter          | 8093 | EntrypointJob            | Instrumented env         |
| trace-runner          | 8094 | Instrumented env         | stream:raw-traces        |
| trace-normalizer      | 8095 | stream:raw-traces        | stream:execution-paths   |
| graph-enricher        | 8096 | stream:execution-paths   | Neo4j (dynamic edges)    |
| spec-generator        | 8097 | stream:execution-paths   | Postgres + Qdrant specs  |

---

## ENTRYPOINT DISCOVERER (8092)

### Role
Finds the high-value entrypoints to trace. Runs on a schedule (nightly) and on
PR open events. Prioritises entrypoints whose static graph changed since last trace.

### What to build

1. Neo4j query for entrypoint patterns:
```cypher
MATCH (n:Node {repo: $repo, active: true})
WHERE n.name STARTS WITH 'test_'
   OR n.decorators CONTAINS 'app.route'
   OR n.decorators CONTAINS 'router.'
   OR n.decorators CONTAINS 'click.command'
RETURN n.stable_id, n.name, n.file_path
```

2. Check Postgres for last trace timestamp per stable_id:
```sql
SELECT node_stable_id, MAX(generated_at) as last_traced
FROM behavior_specs WHERE repo = $1
GROUP BY node_stable_id
```

3. Priority scoring:
   - Score 3: dependency graph changed since last_traced (re-query Neo4j for changed nodes)
   - Score 2: no entry in behavior_specs (never traced)
   - Score 1: last_traced < 30 days ago

4. Push ranked EntrypointJob list to Redis:
   Key: "entrypoint_queue:{repo}"
   RPUSH each job as JSON: {stable_id, name, file_path, priority, repo}

### HTTP endpoint

POST /discover { repo: str, trigger: "schedule"|"pr_open" }
→ 202 Accepted { discovered: N, queued: M }

### Verification
```bash
curl -X POST http://localhost:8092/discover \
  -H 'Content-Type: application/json' \
  -d '{"repo":"test-repo","trigger":"schedule"}'
# expect: 202 {"discovered": N, "queued": M}

redis-cli LLEN entrypoint_queue:test-repo
# expect: > 0 (jobs queued)
```

---

## INSTRUMENTER (8093)

### Role
Sets up Python trace hooks and intercepts I/O so the trace runner can execute
code safely without side effects hitting real external systems.

### What to build

1. `sys.settrace` hook that captures every Call/Return/Exception event:
```python
def trace_func(frame, event, arg):
    if event in ('call', 'return', 'exception'):
        emit_event(event, frame.f_code.co_filename,
                   frame.f_code.co_name, frame.f_lineno)
    return trace_func
sys.settrace(trace_func)
sys.setprofile(trace_func)
```

2. I/O intercept patches (use unittest.mock.patch):
   - `sqlalchemy.engine.Engine.execute` / `psycopg2.connect` → log SQL, return mock cursor
   - `httpx.Client.request` / `requests.request` → log request, return mock 200 response
   - `open()` for write modes → redirect to tempdir

3. HTTP endpoint:
   POST /instrument { stable_id, file_path, repo }
   → Returns session_id for the trace runner to reference

4. The instrumented environment is an in-memory context manager, not a subprocess.
   The trace runner calls /instrument to get a session, then /run to execute in that context.

### Verification
```bash
curl -X POST http://localhost:8093/instrument \
  -H 'Content-Type: application/json' \
  -d '{"stable_id":"sha256:fn_test_login","file_path":"tests/test_auth.py","repo":"test"}'
# expect: {"session_id":"uuid","status":"ready"}
```

---

## TRACE RUNNER (8094)

### Role
Executes instrumented entrypoints and emits raw trace event streams.

### What to build

1. Consume from Redis list "entrypoint_queue:{repo}" (BLPOP, blocking pop)

2. For each EntrypointJob:
   a. Call instrumenter /instrument to get session_id
   b. Execute the entrypoint in the instrumented context with 30s timeout
   c. Collect events from the trace hook
   d. Emit RawTrace to stream:raw-traces

3. Cache result in Redis:
   Key: "trace_cache:{commit_sha}:{stable_id}"
   TTL: 24 hours
   Check cache before executing — skip if already traced at this commit

4. RawTrace event:
```json
{
  "entrypoint_stable_id": "sha256:...",
  "commit_sha": "a4f91c",
  "repo": "test",
  "duration_ms": 284,
  "events": [
    {"type":"call","fn":"authenticate","file":"auth/service.py","line":34,"timestamp_ms":0},
    {"type":"return","fn":"authenticate","file":"auth/service.py","line":52,"timestamp_ms":28}
  ]
}
```

### Verification
```bash
# Push a job manually
redis-cli RPUSH entrypoint_queue:test '{"stable_id":"sha256:fn_test_login","name":"test_login","file_path":"tests/test_auth.py","priority":2,"repo":"test"}'
sleep 5
python scripts/read_stream.py stream:raw-traces
# expect: RawTrace event with events[] containing call/return sequence
```

---

## TRACE NORMALIZER (8095)

### Role
Transforms flat raw trace events into structured ExecutionPath records with
branch frequencies, side effect classification, and reconciliation against static graph.

### What to build

1. Consume stream:raw-traces (consumer group: normalizer-group)

2. Reconstruct call tree from flat events:
   - Track call stack depth as events are processed
   - Match call/return pairs to compute per-function duration

3. Branch frequency: aggregate across multiple RawTrace events for same entrypoint.
   Accumulate in Redis: "trace_agg:{repo}:{entrypoint_stable_id}"
   After 5+ runs: compute frequencies as observed_count/total_runs

4. Side effect classification from intercepted I/O in events:
   - DB: events where fn matches sql interceptor patterns → parse SQL for READ/WRITE
   - Cache: events for redis SET/GET
   - HTTP: events for http interceptor

5. Reconcile against static graph:
   - Query Neo4j for static CALLS edges from this entrypoint
   - Events in trace but not in static graph → dynamic_only_edges
   - Static edges never in any trace → never_observed_static_edges

6. Emit ExecutionPath to stream:execution-paths

### Verification
```bash
python scripts/push_raw_trace.py  # push a sample RawTrace to stream:raw-traces
sleep 3
python scripts/read_stream.py stream:execution-paths
# expect: ExecutionPath with call_sequence, branch frequencies, side_effects
# dynamic_only_edges should contain AuditLogger.log if not in static graph
```

---

## GRAPH ENRICHER (8096)

### Role
Writes dynamic properties and runtime-discovered edges back to Neo4j nodes.
Runs the conflict detector after each batch.

### What to build

1. Consume stream:execution-paths (consumer group: graph-enricher-group)

2. For each node observed in ExecutionPath: write dynamic properties
```cypher
MATCH (n:Node {stable_id: $stable_id})
SET n += {
  observed_calls:  $observed_calls,
  avg_latency_ms:  $avg_latency_ms,
  branch_coverage: $branch_coverage,
  last_traced_at:  datetime()
}
```

3. Write dynamic-only edges with source='dynamic':
```cypher
MERGE (a)-[r:CALLS]->(b)
SET r.source = 'dynamic', r.observed_count = $count
```

4. Upgrade static edges to 'confirmed' when observed:
```cypher
MATCH (a)-[r:CALLS {source:'static'}]->(b)
WHERE a.stable_id = $src AND b.stable_id = $tgt
SET r.source = 'confirmed', r.observed_count = $count
```

5. Conflict detector (run after each batch):
```cypher
MATCH (a)-[r:CALLS]->(b)
WHERE r.source = 'static' AND r.observed_count IS NULL
  AND a.last_traced_at IS NOT NULL
SET r.source = 'conflict', r.detail = 'in AST but never observed in traces'
```

6. Staleness rule: confirmed edge where source node updated_at > r.confirmed_at → downgrade to static

### Verification
```bash
# After trace-normalizer has emitted ExecutionPaths:
curl -u neo4j:localpassword http://localhost:7474/db/neo4j/tx/commit \
  -H 'Content-Type: application/json' \
  -d '{"statements":[{"statement":"MATCH (n:Node {name:\"authenticate\"}) RETURN n.observed_calls, n.avg_latency_ms, n.branch_coverage"}]}'
# expect: dynamic properties populated

curl -u neo4j:localpassword http://localhost:7474/db/neo4j/tx/commit \
  -H 'Content-Type: application/json' \
  -d '{"statements":[{"statement":"MATCH ()-[r:CALLS]->() RETURN r.source, count(*) as n ORDER BY r.source"}]}'
# expect: mix of static, confirmed, dynamic, conflict edges
```

---

## SPEC GENERATOR (8097)

### Role
Renders BehaviorSpec documents from ExecutionPath data and stores them in Postgres.
Also embeds specs into Qdrant for semantic search retrieval.

### Postgres schema

```sql
CREATE TABLE IF NOT EXISTS behavior_specs (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    node_stable_id  TEXT NOT NULL,
    repo            TEXT NOT NULL,
    commit_sha      TEXT NOT NULL,
    version         INTEGER NOT NULL DEFAULT 1,
    spec_text       TEXT NOT NULL,
    branch_coverage FLOAT,
    observed_calls  INTEGER,
    generated_at    TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE (node_stable_id, repo, commit_sha)
);
CREATE INDEX ON behavior_specs (node_stable_id, repo);
```

### Spec text format

The spec_text is the content that goes into Layer 5 and Layer 6 of the context document.
Format it as structured plain text matching the serializer's expected input:

```
PATH {entrypoint_name}  ({N} runs observed)
  1.  {fn_name}    {frequency_ratio}    ~{avg_ms}ms
  2a. {branch}     {N}/{total} runs     exits here
  2b. {fn_name}    {N}/{total} runs     success path
  ...

SIDE_EFFECTS:
  DB READ   {table} WHERE ...
  DB WRITE  {table} INSERT/UPDATE
  CACHE SET {key_pattern} TTL {ttl}
  HTTP OUT  {method} {url}   (conditional · {condition})
  ENV READ  {VARS}

CHANGE_IMPACT:
  tables affected    {table1} ({r/w})
  external services  {service} (conditional)
```

### Qdrant specs collection

Collection name: "specs"
Vector size: same as EMBEDDING_DIM
Create on startup if not exists.

Embed the spec_text using the same embedding provider as the embedder service.
Upsert to Qdrant with payload: {node_stable_id, entrypoint_name, repo, commit_sha}

### Verification
```bash
# 1. Check Postgres after a trace cycle
psql -h localhost -U tersecontext -d tersecontext \
  -c "SELECT node_stable_id, version, observed_calls, branch_coverage FROM behavior_specs LIMIT 5;"
# expect: rows with populated spec data

# 2. Check Qdrant specs collection
curl http://localhost:6333/collections/specs
# expect: collection exists with vectors

# 3. Semantic search on specs
curl -X POST http://localhost:6333/collections/specs/points/search \
  -H 'Content-Type: application/json' \
  -d "{\"vector\": $(python scripts/embed_text.py 'authentication flow'), \"limit\": 3, \"with_payload\": true}"
# expect: authenticate-related specs returned
```

---

## Shared definition of done for all six dynamic services

- [ ] All six services: /health returns 200
- [ ] entrypoint-discoverer: queues jobs after POST /discover
- [ ] trace-runner: emits RawTrace events to stream:raw-traces
- [ ] trace-normalizer: emits ExecutionPath events with branch frequencies and side effects
- [ ] graph-enricher: Neo4j nodes have dynamic properties, edges upgraded to confirmed/conflict
- [ ] spec-generator: behavior_specs table populated, specs searchable in Qdrant
- [ ] All services: Dockerfile builds cleanly
- [ ] All services: unit tests pass
