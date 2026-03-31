# TerseContext Usage Guide

TerseContext produces minimum-sufficient context for LLMs about a codebase. Instead of dumping entire files, it builds a knowledge graph of the code and retrieves only the relevant slice an LLM needs to answer a question or complete a task.

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
  → trace-runner (executes them with instrumentation)
  → trace-normalizer (flat events → structured execution paths)
  → graph-enricher (dynamic edges + properties → Neo4j)
  → spec-generator (behavior specs → Postgres + Qdrant)
```

Enriches the graph with runtime behavior: which functions actually call which, branch frequencies, side effects (DB writes, HTTP calls), latency. Catches things static analysis misses like decorator dispatch, dependency injection, and conditional branches.

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
| instrumenter          | 8093 | Sets up trace hooks and I/O interception                |
| trace-runner          | 8094 | Executes instrumented entrypoints                       |
| trace-normalizer      | 8095 | Converts raw traces into structured execution paths     |
| graph-enricher        | 8096 | Writes dynamic properties and edges to Neo4j            |
| spec-generator        | 8097 | Generates behavior specs → Postgres + Qdrant            |

All services expose `GET /health`, `GET /ready`, and `GET /metrics`.

## Configuration

Copy `.env.example` to `.env` and set:

```
NEO4J_PASSWORD=localpassword
POSTGRES_PASSWORD=localpassword
ALLOWED_REPO_ROOTS=/repos
VOYAGE_API_KEY=              # optional; omit to use local Ollama embeddings
```

## Makefile Targets

| Target        | Description                              |
|---------------|------------------------------------------|
| `make up`     | Start all services via Docker Compose    |
| `make down`   | Stop all services                        |
| `make proto`  | Regenerate protobuf bindings             |
| `make verify` | Run health checks on infrastructure      |
| `make logs`   | Stream container logs                    |
