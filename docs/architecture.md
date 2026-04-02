# TerseContext Architecture

TerseContext produces minimum-sufficient LLM context from codebases. It builds a knowledge graph from source code, optionally enriches it with runtime traces, and answers natural language queries by extracting and serializing the most relevant subgraph within a token budget.

This document covers the full system: how repos get indexed, how queries get answered, what the data model looks like, and what the output format means.

---

## System overview

Three pipelines run independently and share data through Neo4j, Qdrant, Postgres, and Redis:

```
                         ┌──────────────────────────────────┐
                         │         DATA STORES              │
                         │                                  │
                         │  Neo4j    — code knowledge graph │
                         │  Qdrant   — vector embeddings    │
                         │  Postgres — behavior specs       │
                         │  Redis    — streams + caches     │
                         └──────────┬───────────────────────┘
                                    │
              ┌─────────────────────┼─────────────────────┐
              │                     │                     │
     ┌────────▼────────┐  ┌────────▼────────┐  ┌────────▼────────┐
     │ STATIC PIPELINE │  │ DYNAMIC PIPELINE│  │ QUERY PIPELINE  │
     │ (indexing)      │  │ (tracing)       │  │ (retrieval)     │
     └─────────────────┘  └─────────────────┘  └─────────────────┘
```

Services never call each other directly. All communication flows through Redis streams (for events) and shared stores (for persistent data). This means every service can be built, deployed, and scaled independently.

---

## Pipeline 1: Static indexing

Turns source code into a searchable knowledge graph. Triggered by git commits.

### Flow

```
Git commit
  │
  ▼
repo-watcher (8091)
  │  compares prev_sha → current_sha via git diff
  │  emits FileChangedEvent per changed file
  │
  ▼
stream:file-changed
  │
  ├──▶ parser (consumer)
  │      parses AST via tree-sitter (Python, Go, TypeScript)
  │      extracts: functions, classes, methods with signatures, bodies, docstrings
  │      extracts: intra-file CALLS edges
  │      emits ParsedFileEvent
  │      │
  │      ▼
  │    stream:parsed-file
  │      │
  │      ├──▶ graph-writer (8084) [edge consumer]
  │      │      writes CALLS edges to Neo4j (source='static')
  │      │      tombstones deleted nodes (sets active=false)
  │      │
  │      ├──▶ embedder (8083)
  │      │      builds embed text: "{name} {signature} {docstring} {body[:512]}"
  │      │      embeds via Ollama (nomic-embed-text, 768-dim) or Voyage (1024-dim)
  │      │      skips unchanged nodes (checks node_hash in Neo4j)
  │      │      emits EmbeddedNodesEvent
  │      │      │
  │      │      ▼
  │      │    stream:embedded-nodes
  │      │      │
  │      │      ├──▶ graph-writer (8084) [node consumer]
  │      │      │      upserts full Node properties to Neo4j
  │      │      │      (stable_id, name, type, signature, docstring, body,
  │      │      │       file_path, language, repo, embed_text, node_hash)
  │      │      │
  │      │      └──▶ vector-writer (8085)
  │      │             upserts vectors to Qdrant collection "nodes"
  │      │             payload: {stable_id, name, type, file_path, language}
  │      │
  │      └──▶ lsp-indexer (8101)
  │             uses language servers (pyright, gopls, ts-server)
  │             enriches nodes with cross-file definitions and references
  │
  └──▶ zoekt-indexer (8102)
         builds full-text search index for keyword/symbol lookup
         used by dual-retriever during query time
```

### What gets stored

After indexing, each code symbol (function, class, method) becomes a **Node** in Neo4j with these properties:

| Property | Description |
|----------|-------------|
| `stable_id` | Content-addressable hash (survives renames) |
| `name` | Symbol name (`authenticate`, `UserModel`) |
| `type` | `function`, `class`, `method` |
| `signature` | Full signature (`def authenticate(user: str, pwd: str) -> bool`) |
| `docstring` | Extracted docstring |
| `body` | Full source code body |
| `file_path` | Relative path (`auth/service.py`) |
| `language` | `python`, `go`, `typescript` |
| `repo` | Repository name |
| `active` | `true` if node exists in current HEAD |
| `node_hash` | Hash of content for change detection |

Edges between nodes:

| Edge | Source | Meaning |
|------|--------|---------|
| `CALLS` | `static` | Function A calls function B (from AST) |
| `IMPORTS` | `static` | Module A imports module B |
| `INHERITS` | `static` | Class A extends class B |
| `DEFINES` | `static` | Module defines symbol |
| `TESTED_BY` | `static` | Function tested by test function |

### Incremental indexing

Only changed files are processed on each commit. The repo-watcher compares `prev_sha` to `current_sha` via `git diff --name-status` and emits events only for added, modified, or deleted files. The parser further narrows this to changed nodes within each file by comparing AST hashes.

---

## Pipeline 2: Dynamic analysis

Discovers runtime behavior that static analysis cannot see: which branches execute, what side effects occur, which static edges are actually traversed. This pipeline is optional — the query pipeline works with static-only data, but results improve when dynamic data is available.

### Flow

```
Schedule or PR open event
  │
  ▼
entrypoint-discoverer (8092)
  │  queries Neo4j for high-value entrypoints:
  │    - test functions (name starts with test_)
  │    - HTTP routes (@app.route, @router)
  │    - CLI commands (@click.command)
  │  checks Postgres for last trace timestamp
  │  scores by priority: changed deps (3), never traced (2), stale (1)
  │  pushes EntrypointJob list to Redis queue
  │
  ▼
entrypoint_queue:{repo}  (Redis list)
  │
  ▼
trace-runner (8094) / go-trace-runner (8099)
  │  BLPOP from queue
  │  calls instrumenter/go-instrumenter for trace session
  │  executes entrypoint with sys.settrace hooks (Python)
  │    or tracert binary injection (Go)
  │  captures call/return/exception events with timing
  │  I/O intercepted: SQL → logged, HTTP → mocked, file writes → tempdir
  │  emits RawTrace to stream
  │
  ▼
stream:raw-traces
  │
  ▼
trace-normalizer (8095)
  │  reconstructs call tree from flat events
  │  matches call/return pairs for per-function duration
  │  aggregates branch frequencies across runs (5+ runs → frequencies)
  │  classifies side effects from intercepted I/O:
  │    DB READ/WRITE, CACHE SET/GET, HTTP OUT
  │  reconciles against static graph:
  │    events in trace but not static → dynamic_only_edges
  │    static edges never in any trace → never_observed_static_edges
  │  emits ExecutionPath
  │
  ▼
stream:execution-paths
  │
  ├──▶ graph-enricher (8096)
  │      writes dynamic properties to Neo4j nodes:
  │        observed_calls, avg_latency_ms, branch_coverage, last_traced_at
  │      writes dynamic-only edges (source='dynamic')
  │      upgrades static edges to 'confirmed' when observed in traces
  │      flags conflicts: static edge where source was traced but edge never observed
  │      detects staleness: confirmed edge where source updated after last trace
  │
  └──▶ spec-generator (8097)
         renders BehaviorSpec document from ExecutionPath data
         stores in Postgres (behavior_specs table, versioned)
         embeds spec text into Qdrant collection "specs"
```

### Edge provenance lifecycle

After dynamic analysis, edges carry provenance tags showing their confidence level:

```
static → (traced, edge observed) → confirmed
static → (traced, edge NOT observed) → conflict
(not in AST, observed in trace) → dynamic (runtime-only)
confirmed → (source code updated after last trace) → stale → static
```

### What dynamic analysis adds to nodes

| Property | Description |
|----------|-------------|
| `observed_calls` | Number of times the function was called across all traces |
| `avg_latency_ms` | Average execution time |
| `branch_coverage` | Fraction of branches observed (0.0–1.0) |
| `last_traced_at` | Timestamp of last trace |
| `raises_observed` | Exception types raised during execution |
| `side_effects` | I/O operations: DB reads/writes, HTTP calls, cache ops |

---

## Pipeline 3: Query

Answers natural language questions about the codebase. Four sequential stages transform a question into a context document.

### Flow

```
POST /query {repo, question}
  │
  ▼
api-gateway (8090)
  │
  │  ┌─────────────────────────────────────────────────────────────┐
  │  │ Stage 1: UNDERSTAND (query-understander, 8086)             │
  │  │                                                             │
  │  │ Input:  "how does authentication work?"                     │
  │  │ Method: LLM prompt (Ollama) with fallback to keyword split  │
  │  │ Output: QueryIntent                                         │
  │  │   keywords:   [authentication, auth, login, password]       │
  │  │   symbols:    [authenticate, login, check_password]         │
  │  │   query_type: flow                                          │
  │  │   embed_query: "authentication login flow password verify"  │
  │  │   scope:      null                                          │
  │  │                                                             │
  │  │ Cached in Redis per (question, repo) — no TTL.              │
  │  └─────────────────────────────────────────────────────────────┘
  │
  │  ┌─────────────────────────────────────────────────────────────┐
  │  │ Stage 2: RETRIEVE (dual-retriever, 8087 gRPC)              │
  │  │                                                             │
  │  │ Two searches run in parallel (500ms timeout each):          │
  │  │                                                             │
  │  │ Vector search:                                              │
  │  │   embed_query → Ollama → 768-dim vector                    │
  │  │   → Qdrant nearest neighbors (collection "nodes")          │
  │  │   → ranked by cosine similarity                             │
  │  │                                                             │
  │  │ Graph search:                                               │
  │  │   keywords + symbols → Zoekt full-text index               │
  │  │   → matching nodes from Neo4j                               │
  │  │   → ranked by graph properties (in-degree, centrality)     │
  │  │                                                             │
  │  │ Merge via Reciprocal Rank Fusion (RRF):                    │
  │  │   score = 1/(rank_vector + 60) + 1/(rank_graph + 60)       │
  │  │                                                             │
  │  │ Output: top 8 SeedNodes                                     │
  │  │   {stable_id, name, type, score, retrieval_method}          │
  │  │   retrieval_method: "vector" | "graph" | "both"             │
  │  └─────────────────────────────────────────────────────────────┘
  │
  │  ┌─────────────────────────────────────────────────────────────┐
  │  │ Stage 3: EXPAND (subgraph-expander, 8088 gRPC)             │
  │  │                                                             │
  │  │ 1. Hydrate seeds: load full Node data from Neo4j            │
  │  │    (name, signature, body, file_path, dynamic properties)   │
  │  │                                                             │
  │  │ 2. BFS traversal from seeds:                                │
  │  │    - Hop depth: 2 (default, configurable)                   │
  │  │    - Direction depends on query_type:                        │
  │  │      flow:   seed → callees (CALLS, IMPORTS, INHERITS)     │
  │  │      impact: callers → seed (CALLS, TESTED_BY)             │
  │  │      lookup: seed → definitions (DEFINES, IMPORTS, INHERITS)│
  │  │    - Score decay: 0.7 per hop                               │
  │  │      seed=1.0, hop1=0.7, hop2=0.49                         │
  │  │                                                             │
  │  │ 3. Prune to token budget:                                   │
  │  │    - Sort all discovered nodes by score (descending)        │
  │  │    - Greedily include nodes until max_tokens exhausted      │
  │  │    - Behavior specs attached to seeds (from Postgres)       │
  │  │                                                             │
  │  │ 4. Collect edges between surviving nodes                    │
  │  │    - Standard edges (CALLS, IMPORTS, etc.)                  │
  │  │    - Conflict/dead edges always included (budget overshoot) │
  │  │                                                             │
  │  │ Output: RankedSubgraphResponse                              │
  │  │   nodes[], edges[], warnings[], budget_used, total_candidates│
  │  └─────────────────────────────────────────────────────────────┘
  │
  │  ┌─────────────────────────────────────────────────────────────┐
  │  │ Stage 4: SERIALIZE (serializer, 8089 gRPC)                 │
  │  │                                                             │
  │  │ Renders a 6-layer plain-text context document.              │
  │  │ See "Output format" section below.                          │
  │  │                                                             │
  │  │ Output: ContextDocResponse {document, token_count}          │
  │  └─────────────────────────────────────────────────────────────┘
  │
  ▼
HTTP response (Content-Type: text/plain, streamed)
```

### Query types

The query understander classifies every question into one of three types, which controls BFS traversal direction:

| Type | Question pattern | BFS direction | Edge types followed |
|------|-----------------|---------------|-------------------|
| `flow` | "How does X work?" | Forward from seed | CALLS, IMPORTS, INHERITS |
| `impact` | "What breaks if I change X?" | Backward into seed | CALLS, TESTED_BY |
| `lookup` | "Where is X defined?" | Forward from seed | DEFINES, IMPORTS, INHERITS |

---

## Output format

The serializer renders a 6-layer plain-text document. Each layer is optional and omitted when empty.

### Layer 1: Header

```
QUERY:       how does authentication work?
SEEDS:       A1 * B2 * C3 *
CONFIDENCE:  high  (25 observed runs · 92% coverage · 0 warnings)
REPO:        my-app
```

- **SEEDS** lists the seed nodes that anchored the search. `*` marks seeds.
- **CONFIDENCE** is derived from dynamic analysis coverage:
  - `high` — seeds have observed runs and good branch coverage, no warnings
  - `medium` — some dynamic data but incomplete
  - `low` — static only, or warnings present

### Layer 2: Warnings (omitted if none)

```
── WARNINGS ──
CONFLICT  A3 → C5   in AST but never observed in 25 trace runs
DEAD      B4 → D6   source function has zero observed calls
STALE     A1 → B2   source updated 2024-03-15, last traced 2024-01-10
```

These flag edges where static and dynamic analysis disagree — the most valuable signal for catching dead code, missed test coverage, or stale assumptions.

### Layer 3: Registry

A table of all nodes in the subgraph with their identity and provenance:

```
── REGISTRY ──
A1 *         def authenticate(user, pwd)        auth/service.py         spec.92%
B2           def check_password(user, pwd)      auth/helpers.py         static
C3           class AuthMiddleware                auth/middleware.py      spec.45%
D4           def hash_password(pwd)             auth/helpers.py         runtime-only
```

Columns: short ID (with `*` for seeds), signature, file path, provenance pill.

Provenance pills:
- `static` — only known from AST analysis
- `spec.N%` — has dynamic data with N% branch coverage
- `runtime-only` — discovered only through traces, not in AST

### Layer 4: Edges

```
── EDGES ──
CALLS
  A1 → B2  (confirmed, 0.94)
  A1 → C3  (static)
  C3 → D4  (dynamic)

IMPORTS
  A1 → E5  (static)
```

Grouped by edge type. Each edge shows source → target, provenance, and frequency ratio (if available from traces). Conflict/dead edges from Layer 2 are also listed here with their tags.

### Layer 5: Behaviour

For seeds and hop-1 nodes that have behavior specs or bodies:

```
── BEHAVIOUR ──
SPEC A1 (authenticate · 25 runs)
  PATH authenticate  (25 runs observed)
    1.  authenticate       100%    ~15ms
    2a. check_password     22/25   success path
    2b. raise AuthError    3/25    invalid credentials
    3.  cache.set          22/25   on success

  SIDE_EFFECTS:
    DB READ   users WHERE email = ?
    CACHE SET session:{user_id} TTL 3600

BODY B2 (* static · no spec)
  def check_password(user, pwd):
      stored = db.get_hash(user.id)
      return bcrypt.verify(pwd, stored)
```

- Nodes with behavior specs get their structured execution path and side effects.
- Nodes without specs get their raw source body.
- Only seeds and hop-1 nodes are shown (deeper nodes appear in the registry only).

### Layer 6: Side effects (omitted if none)

```
── SIDE EFFECTS ──
A1  DB READ   users WHERE email = ?
A1  CACHE SET session:{user_id} TTL 3600
C3  HTTP OUT  POST https://audit.internal/log  (conditional · on_login_success)
C3  ENV READ  AUTH_SECRET_KEY
```

Aggregated I/O operations across all nodes in the subgraph.

---

## Data stores

### Neo4j (code knowledge graph)

The primary data store. Contains every code symbol as a Node and every relationship as an edge. Both static (from AST) and dynamic (from traces) data live here.

- **Connection:** `bolt://neo4j:7687` (default)
- **Indexes:** on `Node.stable_id`, `Node.repo`, `Node.name`

### Qdrant (vector search)

Two collections:

| Collection | Contents | Vector dim |
|------------|----------|-----------|
| `nodes` | Code symbol embeddings (name + signature + docstring + body snippet) | 768 (Ollama) or 1024 (Voyage) |
| `specs` | Behavior spec text embeddings | Same as nodes |

- **Connection:** `http://qdrant:6333`

### Postgres (behavior specs)

Stores versioned behavior spec documents generated from traces.

```sql
behavior_specs (
  id              UUID PRIMARY KEY,
  node_stable_id  TEXT NOT NULL,
  repo            TEXT NOT NULL,
  commit_sha      TEXT NOT NULL,
  version         INTEGER NOT NULL DEFAULT 1,
  spec_text       TEXT NOT NULL,
  branch_coverage FLOAT,
  observed_calls  INTEGER,
  generated_at    TIMESTAMPTZ DEFAULT NOW(),
  UNIQUE (node_stable_id, repo, commit_sha)
)
```

- **Connection:** `postgresql://tersecontext:password@postgres:5432/tersecontext`

### Redis (streams + caches)

Streams (event bus between services):

| Stream | Producer | Consumers |
|--------|----------|-----------|
| `stream:file-changed` | repo-watcher | parser, zoekt-indexer, vector-writer (delete), lsp-indexer |
| `stream:parsed-file` | parser | graph-writer (edges), embedder, lsp-indexer |
| `stream:embedded-nodes` | embedder | graph-writer (nodes), vector-writer |
| `stream:repo-indexed` | repo-watcher | graph-writer (repo-ready), zoekt-indexer |
| `stream:raw-traces` | trace-runner, go-trace-runner | trace-normalizer |
| `stream:execution-paths` | trace-normalizer | graph-enricher, spec-generator |

Caches:

| Key pattern | Used by | Description |
|-------------|---------|-------------|
| `last_indexed_sha:{repo}` | repo-watcher | Last commit SHA that was indexed |
| `trace_cache:{commit}:{stable_id}` | trace-runner | Skip re-tracing at same commit (TTL 24h) |
| `trace_agg:{repo}:{stable_id}` | trace-normalizer | Accumulated branch frequency data |
| `graph-writer:repo-ready:{repo}:{sha}` | graph-writer | Signals indexing complete (TTL 1h) |
| `entrypoint_queue:{repo}` | entrypoint-discoverer → trace-runner | Job queue for tracing |

---

## Service reference

### Indexing services

| Service | Port | Language | Role |
|---------|------|----------|------|
| repo-watcher | 8091 | Python | Detects git changes, emits FileChanged events |
| parser | — | Python | Parses AST, extracts nodes and edges |
| graph-writer | 8084 | Python | Writes nodes and edges to Neo4j |
| symbol-resolver | 8082 | Python | Resolves cross-file references, qualifies names |
| embedder | 8083 | Python | Generates vector embeddings via Ollama/Voyage |
| vector-writer | 8085 | Python | Writes vectors to Qdrant |
| lsp-indexer | 8101 | Python | Enriches graph via language servers |
| zoekt-indexer | 8102 | Go | Builds full-text search index |

### Dynamic analysis services

| Service | Port | Language | Role |
|---------|------|----------|------|
| entrypoint-discoverer | 8092 | Python | Finds entrypoints to trace, priority-scores them |
| instrumenter | 8093 | Python | Sets up Python sys.settrace hooks + I/O interception |
| go-instrumenter | 8098 | Go | Instruments Go binaries via tracert injection |
| trace-runner | 8094 | Python | Executes instrumented Python entrypoints |
| go-trace-runner | 8099 | Go | Executes instrumented Go entrypoints |
| trace-normalizer | 8095 | Python | Transforms raw traces into structured execution paths |
| graph-enricher | 8096 | Python | Writes dynamic properties and edges to Neo4j |
| spec-generator | 8097 | Python | Generates behavior specs to Postgres + Qdrant |

### Query services

| Service | Port | Protocol | Language | Role |
|---------|------|----------|----------|------|
| api-gateway | 8090 | HTTP | Go | Entry point, orchestrates query pipeline |
| query-understander | 8086 | HTTP | Python | Extracts intent from natural language |
| dual-retriever | 8087 | gRPC | Go | Hybrid vector + graph search with RRF |
| subgraph-expander | 8088 | gRPC | Go | BFS expansion, scoring, budget pruning |
| serializer | 8089 | gRPC | Go | Renders context document |

### Infrastructure

| Service | Port | Role |
|---------|------|------|
| Neo4j | 7474 (HTTP), 7687 (Bolt) | Graph database |
| Qdrant | 6333 | Vector database |
| Postgres | 5432 | Relational database |
| Redis | 6379 | Streams + caches |
| Ollama | 11434 | Local LLM for embeddings + query understanding |
| Zoekt | 6070 | Full-text code search server |
| perf-tracker | 8100 | Pipeline performance metrics |

All application services expose: `GET /health`, `GET /ready`, `GET /metrics`.

---

## Protobuf contract

The query pipeline services communicate via protobuf (`proto/query.proto`). Key messages:

```protobuf
// Stage 1 output
QueryIntentResponse { raw_query, keywords[], symbols[], query_type, embed_query, scope }

// Stage 2 output
SeedNode { stable_id, name, type, score, retrieval_method }

// Stage 3 output
SubgraphNode { stable_id, name, type, signature, docstring, body, file_path,
               score, hop, provenance, observed_calls, avg_latency_ms,
               branch_coverage, raises_observed[], side_effects[], behavior_spec }

SubgraphEdge { source, target, type, provenance, frequency_ratio }

SubgraphWarning { type, source, target, detail }

RankedSubgraphResponse { nodes[], edges[], warnings[], budget_used, total_candidates }

// Stage 4 output
ContextDocResponse { document, token_count }
```

---

## Configuration

### Required environment variables

| Variable | Services | Description |
|----------|----------|-------------|
| `NEO4J_PASSWORD` | graph-writer, dual-retriever, subgraph-expander, graph-enricher, entrypoint-discoverer | Neo4j auth |
| `POSTGRES_PASSWORD` | spec-generator, entrypoint-discoverer | Postgres auth |

### Key tuning parameters

| Variable | Default | Description |
|----------|---------|-------------|
| `EXPANDER_HOP_DEPTH` | `2` | BFS depth — higher finds more context but uses more budget |
| `EXPANDER_DECAY` | `0.7` | Score decay per hop — lower values prune more aggressively |
| `EMBEDDING_MODEL` | `nomic-embed-text` | Ollama embedding model |
| `EMBEDDING_DIM` | `768` | Vector dimensionality (768 for Ollama, 1024 for Voyage) |
| `EMBEDDING_PROVIDER` | `ollama` | `ollama` or `voyage` |
| `TIMEOUT_MS` | `30000` | Max trace execution time per entrypoint |

See [usage.md](../usage.md) for the full variable reference and setup instructions.
