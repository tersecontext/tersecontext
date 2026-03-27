# CLAUDE.md — Serializer

## TerseContext — system overview

TerseContext produces minimum sufficient LLM context. The serializer is the final
service in the query pipeline. It takes a RankedSubgraph and renders the six-layer
plain-text context document that gets passed to the LLM.

All services expose: GET /health, GET /ready, GET /metrics

---

## Your role — Serializer

You receive a RankedSubgraph from the API gateway (forwarded from the subgraph expander)
and produce a single plain-text document. This is the artifact the LLM sees.
Your output quality directly determines LLM response quality.

You can be built with a mock RankedSubgraph before the expander is done.
The output format is the most testable thing in the system.

Port: 8089 (gRPC)
Language: Go
Input:  gRPC SerializeRequest (RankedSubgraph + raw query)
Output: gRPC ContextDocResponse (plain text)
Reads:  Postgres (BehaviorSpec per node)
Writes: nothing

---

## The six-layer output format — exact spec

### Layer 1 — Query header (always present)

```
QUERY:       {raw_query}
SEEDS:       {short_id_1} *  {short_id_2}  {short_id_3}
CONFIDENCE:  {high|medium|low}  ({N} observed runs · {P}% coverage · {W} warnings)
REPO:        {repo} @sha:{commit_sha}
```

Confidence:
- high:   seed node has BehaviorSpec with observed_calls >= 10 and branch_coverage >= 0.8
- medium: seed node has BehaviorSpec but lower coverage, OR no spec but no warnings
- low:    warnings present, or no dynamic data at all

### Layer 2 — Warnings (omit layer entirely if no warnings)

```
CONFLICT  {short_id} -> {short_id_2}
          static: always called · observed: {N}/{total} runs
          {detail}

DEAD      {short_id} -> {short_id_2}
          in AST · never observed · {detail}

STALE     {short_id}
          spec last run {N} days ago · code changed since
```

### Layer 3 — Node registry

One line per node. Columns: short_id, signature, location, provenance_pill.

```
{short_id}  {signature:<50}  [{file_path}:{line_start}]  {provenance_pill}
```

Provenance pills:
- "* seed"        — this is a seed node (put * after short_id)
- "spec.{N}%"     — has BehaviorSpec, N=branch_coverage as percent
- "static"        — static analysis only, no spec
- "runtime-only"  — discovered at runtime, not in AST

### Layer 4 — Edge topology

Group by edge type. Unannotated = confirmed. Tags on exceptions only.

```
CALLS:
  {short_id} -> {short_id_2}                    (optional frequency annotation)
  {short_id} -> {short_id_3}  [runtime-only]
  {short_id} -> {short_id_4}  [CONFLICT]        see warnings
  {short_id} -> {short_id_5}  [DEAD]            see warnings

IMPORTS:
  ...

TESTED_BY:
  ...
```

### Layer 5 — Behaviour (conditional per node)

For nodes with a BehaviorSpec: render the execution path.
For nodes without: render the code body.
Only include seed nodes and their direct hop-1 callees in this layer.

```
PATH {short_id}  (* seed · {N} runs observed)
  1.  {function_call}    {frequency}    {avg_ms}ms
  2a. {branch_result}    {N}/{total}    exits here
  2b. {other_call}       {N}/{total}    success path
  3.  {another_call}     always         [runtime-only]

BODY {short_id}  (* static · no spec)
  {code_body_text}
```

### Layer 6 — Side effects (omit if no side effects)

```
SIDE_EFFECTS {short_id}:
  DB READ   {table} WHERE {condition}
  DB WRITE  {table} {operation}               (via {short_id_n}, {N} hops, runtime-only)
  CACHE SET {key_pattern} TTL {ttl}
  HTTP OUT  {method} {url}                    (conditional · {condition} · {N}/{total} runs)
  ENV READ  {VAR1}, {VAR2}

CHANGE_IMPACT {short_id}:
  direct callers     {N} modules
  tables affected    {table1} ({r/w}), {table2} ({r/w})
  external services  {service_name} (conditional)
  test safety net    {N} tests · {uncovered_branch} uncovered
```

---

## Short ID assignment

Assign short IDs at the start of serialization, before rendering any layer.
IDs are deterministic based on the node list:

```go
func assignIDs(nodes []Node) map[string]string {
    byType := map[string][]Node{}
    for _, n := range nodes { byType[n.Type] = append(byType[n.Type], n) }
    ids := map[string]string{}
    counters := map[string]int{"function": 1, "class": 1, "file": 1, "method": 1}
    // sort each type by hop then score to get deterministic order
    for nodeType, group := range byType {
        prefix := map[string]string{"function":"fn","class":"cls","file":"file","method":"fn"}[nodeType]
        for _, n := range group {
            ids[n.StableID] = fmt.Sprintf("%s:%d", prefix, counters[nodeType])
            counters[nodeType]++
        }
    }
    return ids
}
```

---

## BehaviorSpec fetch from Postgres

```sql
SELECT spec_text, branch_coverage, observed_calls, generated_at, commit_sha
FROM behavior_specs
WHERE node_stable_id = $1
ORDER BY generated_at DESC
LIMIT 1
```

If no row: node has no spec, use code body in Layer 5.
If generated_at < (now - 30 days) OR spec commit_sha != current commit_sha: mark as STALE.

---

## Service structure

```
services/serializer/
  cmd/main.go
  internal/
    serializer/
      serializer.go     — orchestrates all six layers
      layers/
        header.go
        warnings.go
        registry.go
        edges.go
        behaviour.go
        sideeffects.go
      ids.go
    postgres/
      client.go
    server/server.go
  gen/
  go.mod
  Dockerfile
```

---

## Verification

```bash
# Test with a mock RankedSubgraph — do not need real stores for basic format tests

cat > /tmp/test_subgraph.json << 'EOF'
{
  "nodes": [
    {
      "stable_id": "sha256:fn3",
      "name": "authenticate",
      "type": "function",
      "signature": "authenticate(user: User, pw: str) -> Token",
      "docstring": "Validates credentials",
      "body": "def authenticate(self, user, pw):\n    ...",
      "score": 1.0,
      "hop": 0,
      "provenance": "confirmed",
      "observed_calls": 23,
      "avg_latency_ms": 28,
      "branch_coverage": 0.75
    },
    {
      "stable_id": "sha256:fn5",
      "name": "_hash_password",
      "type": "function",
      "signature": "_hash_password(pw: str, salt: bytes) -> str",
      "score": 0.7,
      "hop": 1,
      "provenance": "confirmed"
    }
  ],
  "edges": [
    {"source": "sha256:fn3", "target": "sha256:fn5", "type": "CALLS", "provenance": "confirmed"}
  ],
  "warnings": [],
  "budget_used": 140
}
EOF

grpcurl -plaintext \
  -d "{\"subgraph\": $(cat /tmp/test_subgraph.json), \"raw_query\": \"how does auth work\", \"repo\": \"test\"}" \
  localhost:8089 QueryService/Serialize

# Verify output:
# 1. Layer 1 present: QUERY:, SEEDS:, CONFIDENCE:, REPO:
# 2. Layer 3 present: fn:1 authenticate ... fn:2 _hash_password
# 3. Layer 4 present: CALLS: fn:1 -> fn:2
# 4. Layer 5 present: either PATH or BODY for fn:1
# 5. No layer 2 (no warnings in mock)
# 6. Total character count reasonable (~2000 chars for this mock)

# Format correctness test
# Run with a subgraph that has conflict edges — verify Layer 2 appears
# Run with stale spec — verify STALE warning in Layer 2

# Unit tests
cd services/serializer && go test ./...
```

---

## Definition of done

- [ ] All six layers render correctly for a mock subgraph
- [ ] Short IDs are deterministic and consistent within a document
- [ ] Layer 2 omitted when no warnings present
- [ ] CONFLICT and DEAD edges trigger Layer 2 entries
- [ ] Layer 5 uses execution path when BehaviorSpec exists, code body otherwise
- [ ] Stale spec (>30 days or different commit_sha) triggers STALE warning
- [ ] Token estimate for output is within expected range
- [ ] All Go tests pass
- [ ] Dockerfile builds cleanly
