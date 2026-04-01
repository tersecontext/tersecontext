# TerseContext Usage Guide

TerseContext produces minimum-sufficient context for LLMs about a codebase. Instead of dumping entire files, it builds a knowledge graph of the code and retrieves only the relevant slice an LLM needs to answer a question or complete a task.

## Adding a repo to the repo-watcher

The repo-watcher has two trigger modes. Set `WATCH_MODE` in your `.env`:

### Hook mode (default, recommended)

Install a post-commit git hook into the target repo. After every `git commit`, the hook calls `/hook` automatically.

```bash
curl -X POST http://localhost:8091/install-hook \
  -H 'Content-Type: application/json' \
  -d '{"repo_path": "/path/to/your/repo"}'
# returns: {"installed": true, "hook_path": "/path/to/your/repo/.git/hooks/post-commit"}
```

The hook script calls the watcher at `WATCHER_URL` (default `http://localhost:8091`). Change this if the watcher runs inside Docker and your repo lives on the host.

### Polling mode

Set `WATCH_MODE=poll` in `.env`. The watcher checks every `POLL_INTERVAL_SECONDS` (default 30) for HEAD changes in `REPO_ROOT`.

### Manual full rescan

Trigger a one-off full index of any repo at any time:

```bash
curl -X POST http://localhost:8091/index \
  -H 'Content-Type: application/json' \
  -d '{"repo_path": "/path/to/your/repo", "full_rescan": true}'
# returns: {"queued": true}
```

### Check indexing status

```bash
curl "http://localhost:8091/status?repo_path=/path/to/your/repo"
# returns: {"repo": "my-repo", "last_sha": "a4f91c", "last_indexed_at": "...", "pending": false}
```

### What gets indexed

Only files that changed since the last indexed commit are processed (incremental by default). On first run, or with `full_rescan: true`, all files are processed. The watcher emits `FileChanged` events to `stream:file-changed` with AST-level node diffs — only modified functions/classes flow downstream, not whole files.

Supported languages: Python (`.py`), TypeScript (`.ts`, `.tsx`).

---

## Inputs

There are two entry points:

### 1. Indexing (building the knowledge base)

A source code repository watched by `repo-watcher` (port 8091). Files are parsed, graphed, embedded, and stored across Neo4j, Qdrant, and Postgres.

### 2. Querying (getting context out)

A natural language question or task description via `POST /query` to the API gateway (port 8090).

```bash
curl -X POST http://localhost:8090/query \
  -H 'Content-Type: application/json' \
  -d '{"query": "how does authentication work?"}'
```

## Output

A structured context document containing the most relevant functions, their relationships, runtime behavior, and side effects, sized to fit within a token budget (default 8000 tokens).

## Pipelines

### Pipeline 1: Static Indexing

```
repo-watcher (detects file changes)
  → parser (AST via tree-sitter)
  → graph-writer (nodes + static edges → Neo4j)
  → symbol-resolver (cross-references, qualified names)
  → embedder (vector embeddings via Ollama/Voyage)
  → vector-writer (embeddings → Qdrant)
```

Produces a code knowledge graph in Neo4j and searchable vectors in Qdrant.

### Pipeline 2: Dynamic Analysis

```
entrypoint-discoverer (finds test fns, routes, CLI commands)
  → trace-runner (executes Python entrypoints with instrumentation)
  → go-trace-runner (executes Go entrypoints via tracert binary injection)
  → trace-normalizer (flat events → structured execution paths)
  → graph-enricher (dynamic edges + properties → Neo4j)
  → spec-generator (behavior specs → Postgres + Qdrant)
```

Enriches the graph with runtime behavior: which functions actually call which, branch frequencies, side effects (DB writes, HTTP calls), latency. Catches things static analysis misses like decorator dispatch, dependency injection, and conditional branches.

Python tracing uses `sys.settrace` hooks with I/O interception (SQLAlchemy, httpx, open). Go tracing uses the `tracert` binary injected via `go-instrumenter` to instrument Go binaries at runtime without source modification.

### Pipeline 3: Query

```
POST /query {"query": "how does auth work?"}
  → query-understander (parse intent, keywords, symbols)
  → dual-retriever (hybrid: vector similarity + graph keyword search, fused via RRF)
  → subgraph-expander (BFS from seed nodes, scored with decay, budget-capped)
  → serializer (render final context document)
  → response
```

Returns a compact, relevant context document ready to feed to an LLM.

## Infrastructure

All services communicate via Redis Streams. Start everything with:

```bash
make up
```

| Store        | Contents                                              |
|--------------|-------------------------------------------------------|
| Neo4j        | Code knowledge graph (nodes, edges, dynamic properties) |
| Qdrant       | Vector embeddings for code nodes and behavior specs   |
| Postgres     | Behavior spec documents and metadata                  |
| Redis        | Event streams, job queues, trace caches               |
| Ollama       | Local embeddings and LLM for query understanding      |

## Services

| Service               | Port | Role                                                    |
|-----------------------|------|---------------------------------------------------------|
| api-gateway           | 8090 | HTTP entry point for `/query` and `/index`              |
| query-understander    | 8086 | Parses queries into structured intent                   |
| dual-retriever        | 8087 | Hybrid vector + graph search with RRF fusion            |
| subgraph-expander     | 8088 | BFS expansion from seed nodes with token budgeting      |
| serializer            | 8089 | Renders ranked subgraph into context document           |
| repo-watcher          | 8091 | Monitors repo changes, emits file_changed events        |
| parser                | —    | Parses source code via tree-sitter                      |
| graph-writer          | 8084 | Writes AST nodes and static edges to Neo4j              |
| symbol-resolver       | 8082 | Resolves cross-references and qualifies names           |
| embedder              | 8083 | Generates vector embeddings via Ollama or Voyage        |
| vector-writer         | 8085 | Writes embeddings to Qdrant                             |
| entrypoint-discoverer | 8092 | Finds high-value entrypoints to trace                   |
| instrumenter          | 8093 | Sets up Python trace hooks and I/O interception         |
| trace-runner          | 8094 | Executes instrumented Python entrypoints                |
| trace-normalizer      | 8095 | Converts raw traces into structured execution paths     |
| graph-enricher        | 8096 | Writes dynamic properties and edges to Neo4j            |
| spec-generator        | 8097 | Generates behavior specs → Postgres + Qdrant            |
| go-instrumenter       | 8098 | Instruments Go binaries via tracert binary injection    |
| go-trace-runner       | 8099 | Executes instrumented Go entrypoints                    |
| perf-tracker          | 8100 | Collects pipeline performance metrics → Postgres        |

All services expose `GET /health`, `GET /ready`, and `GET /metrics`.

## Configuration

Copy `.env.example` to `.env` and set:

```
NEO4J_PASSWORD=localpassword
POSTGRES_PASSWORD=localpassword
ALLOWED_REPO_ROOTS=/repos
VOYAGE_API_KEY=              # optional; omit to use local Ollama embeddings
```

## Docker networking note

The git hook installed by `POST /install-hook` calls back to `WATCHER_URL` (default `http://localhost:8091`). This works when:
- Your repo lives on the **host machine** and services run in Docker — `localhost` routes to the host port.

It silently fails when:
- Your repo lives **inside a Docker container or devcontainer** — `localhost` is that container, not the host.

Fix:

~~~bash
# Mac / Windows (Docker Desktop)
WATCHER_URL=http://host.docker.internal:8091 make up

# Linux — find the bridge gateway
GATEWAY=$(docker network inspect bridge --format='{{(index .IPAM.Config 0).Gateway}}')
WATCHER_URL=http://${GATEWAY}:8091 make up
~~~

Or set `WATCHER_URL` permanently in your `.env` file.

## Makefile Targets

| Target        | Description                              |
|---------------|------------------------------------------|
| `make up`     | Start all services via Docker Compose    |
| `make down`   | Stop all services                        |
| `make proto`  | Regenerate protobuf bindings             |
| `make verify` | Run health checks on infrastructure      |
| `make logs`   | Stream container logs                    |

## Troubleshooting

### Services won't start

1. **Port conflict** — check that ports 8082–8100, 7474, 7687, 6333 are free:
   ~~~bash
   ss -tlnp | grep -E '808[0-9]|809[0-9]|810[0-9]|7474|7687|6333'
   ~~~
   Kill or reconfigure any process holding those ports, then retry `make up`.

2. **Missing .env** — copy the example and fill in required values:
   ~~~bash
   cp .env.example .env
   ~~~
   `NEO4J_PASSWORD` and `POSTGRES_PASSWORD` must be set.

3. **Service keeps restarting** — check logs for the failing service:
   ~~~bash
   docker compose logs --tail=50 <service-name>
   ~~~
   Common cause: Neo4j or Postgres not yet healthy when dependent services start.
   Wait 30 seconds and re-run `make up`.

### Hook fires but nothing indexes

The most common cause is a networking mismatch — see the [Docker networking note](#docker-networking-note) above. The hook installed by `/install-hook` calls `WATCHER_URL`. If `localhost` doesn't route to the repo-watcher container from where the hook runs, events are never emitted.

Verify the hook is firing and reaching the service:
~~~bash
# On the host, tail repo-watcher logs and make a test commit
docker compose logs -f repo-watcher &
cd your-repo && git commit --allow-empty -m "test hook"
# Expect: log line showing POST /hook received
~~~

If you see no log line, the hook URL is wrong. Fix with:
~~~bash
WATCHER_URL=http://host.docker.internal:8091 make up   # Mac/Windows
~~~

### Queries return empty results

1. Confirm nodes were indexed:
   ~~~bash
   curl -sf -u neo4j:localpassword http://localhost:7474/db/neo4j/tx/commit \
     -H 'Content-Type: application/json' \
     -d '{"statements":[{"statement":"MATCH (n:Node) RETURN count(n)"}]}'
   ~~~
   Expect: `count(n) > 0`. If zero, indexing hasn't completed — check parser and graph-writer logs.

2. Confirm vectors are in Qdrant:
   ~~~bash
   curl http://localhost:6333/collections/nodes
   ~~~
   Expect: collection exists with `vectors_count > 0`.

3. Check the `repo` field matches what you indexed. The `repo` in your query must match the repo name used during indexing (the directory name under `/repos`).
