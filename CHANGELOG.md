# Changelog

## [Unreleased] — dev branch

### Added

- **lsp-indexer** (port 8101) — new Python service that consumes `stream:repo-indexed` from Redis, waits for a `graph-writer:repo-ready:{repo}:{commit_sha}` readiness key, then runs language servers (pyright, gopls, typescript-language-server) against the repo and writes `source='lsp'` CALLS edges to Neo4j. Covers cross-file references that AST parsing cannot see (imports resolved through type inference, method dispatch, decorator-injected calls). Supports Python, Go, and TypeScript.

- **zoekt-indexer** (port 8102) — new Go service that consumes `stream:repo-indexed` (full re-index) and `stream:file-changed` (debounced 100 ms incremental update) from Redis, then invokes the `zoekt-index` binary to build a trigram search corpus. Serves as the backing index for zoekt-webserver.

- **zoekt-webserver** — added to docker-compose as the trigram search engine (port 6070). Used by dual-retriever path 2 for fast keyword and symbol search over source files.

- **`stream:repo-indexed`** — new Redis stream emitted by repo-watcher after all `stream:file-changed` events for a commit have been published. Payload: `{repo, path, commit_sha}`. Consumed by lsp-indexer and zoekt-indexer.

- **graph-writer readiness key** — `run_repo_indexed_consumer()` added to graph-writer. After a configurable settle delay (default 5 s), graph-writer writes `graph-writer:repo-ready:{repo}:{commit_sha}` to Redis (TTL 3600 s) so lsp-indexer knows the graph is ready before starting LSP sessions.

- **`scripts/migrate_drop_node_search_index.py`** — one-time migration that drops the `node_search` fulltext index from Neo4j (replaced by zoekt).

- TypeScript support throughout: lsp-indexer adds `typescript-language-server` and Dockerfile installs Node 18 + npm.

### Changed

- **dual-retriever path 2** — replaced Neo4j fulltext search (`node_search` index + `buildFullTextQuery`) with zoekt trigram search. `GraphSearcher.Search` signature changed from `(ctx, query string, ...)` to `(ctx, keywords []string, symbols []string, ...)`. Symbols get a `sym:` prefix in the zoekt query; keywords are plain terms. Results are resolved to Neo4j `stable_id`s via a file-path + line-range lookup with an `ORDER BY span ASC` narrowest-enclosing-node tiebreaker.

- **dual-retriever** — `ZOEKT_URL` env var added (default `http://zoekt-webserver:6070`). `neo4j.go` / `neo4j_test.go` deleted; replaced by `zoekt.go`.

- **repo-watcher** — `process_commit` now emits `stream:repo-indexed` after publishing all `stream:file-changed` events for a commit.

- **graph-writer** — wired `run_repo_indexed_consumer()` as an asyncio task alongside existing consumers. Consumer uses `id="0"` (not `id="$"`) to avoid missing pre-startup messages.

- **docker-compose** — removed `symbol-resolver` service; added `zoekt-webserver`, `zoekt-indexer`, `lsp-indexer`; added `zoekt-index` volume; wired `ZOEKT_URL`, `REDIS_URL`, `REPOS_DIR` explicitly for all new services.

- **docker/neo4j-init.cypher** — removed `CREATE FULLTEXT INDEX node_search` (index no longer used).

- **docker/prometheus.yml** — removed `symbol-resolver:8080`; added `zoekt-indexer:8102` and `lsp-indexer:8101`.

- Language support expanded from **Python and Go** to **Python, Go, and TypeScript**.

### Removed

- **symbol-resolver** service — cross-file symbol resolution now handled by lsp-indexer using real language server protocol. The Neo4j `node_search` fulltext index and `buildFullTextQuery` function in dual-retriever are both gone.

### Fixed

- graph-writer settle sleep was inside the per-message loop instead of per-batch; moved outside.
- lsp-indexer `index_repo` called `asyncio.get_event_loop()` (deprecated in 3.10+); changed to `asyncio.get_running_loop()`.
- lsp-indexer resolved call targets using absolute paths; stripped repo path prefix before passing to `resolve_stable_id`.
- lsp-indexer `write_lsp_edges` called `session.run()` without consuming the result (Neo4j lazy execution); fixed with `result.consume()`.
- zoekt-indexer and lsp-indexer Dockerfiles used passthrough env vars (`- REDIS_URL`) instead of explicit values; fixed to `REDIS_URL: redis://redis:6379` and `REPOS_DIR: /repos`.
- gopls installed to `/root/go/bin` was inaccessible after switching to a non-root `app` user; copied to `/usr/local/bin/gopls`.
- dual-retriever `ZOEKT_URL` default pointed to `http://zoekt:6070`; corrected to `http://zoekt-webserver:6070` to match the service name in docker-compose.
- zoekt-indexer `stream:file-changed` consumer expected flat fields but repo-watcher wraps the payload in an `event` JSON field; added fallback JSON parse.
- Port assignments for lsp-indexer (8101) and zoekt-indexer (8102) corrected after conflict with go-instrumenter (8098) and go-trace-runner (8099).
