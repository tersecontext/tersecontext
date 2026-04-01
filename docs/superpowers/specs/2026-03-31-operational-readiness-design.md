# Operational Readiness Fixes — Design Spec

**Date:** 2026-03-31
**Scope:** Six dynamic analysis services (`entrypoint-discoverer`, `instrumenter`, `trace-runner`, `trace-normalizer`, `graph-enricher`, `spec-generator`) and `services/shared/`.

---

## Problem Summary

Four categories of operational readiness weakness were identified:

1. **No startup config validation** — required env vars (`NEO4J_PASSWORD`, `POSTGRES_DSN`) are accessed lazily at first request, producing a `KeyError` with no context instead of a clear startup failure.
2. **Metrics are hardcoded zeros** — `graph-enricher`, `trace-runner`, and `entrypoint-discoverer` return `0` for all counters regardless of actual activity.
3. **Error messages drop context** — `instrumenter` reports "Max concurrent sessions reached" without stating the limit.
4. **`/ready` is binary** — the response lists error strings but doesn't distinguish required from optional dependencies, making it impossible to tell whether a service is fully broken or merely degraded.

---

## Approach

All fixes flow through the shared `services/shared/service.py` module, which all six services already import. No new dependencies are introduced.

---

## Section 1 — Config Validation

### Implementation

Add a module-level function to `services/shared/service.py`:

```python
def validate_env(required: list[str], service: str) -> None:
    missing = [k for k in required if not os.environ.get(k)]
    if missing:
        lines = [f"[{service}] Missing required environment variables:"]
        for k in missing:
            lines.append(f"  - {k}")
        lines.append("See usage.md for configuration details.")
        print("\n".join(lines), file=sys.stderr)
        sys.exit(1)
```

### Call sites

Each service calls `validate_env(...)` at the **top of `lifespan()`**, before any driver or connection is constructed:

| Service | Required vars | Conditional |
|---|---|---|
| entrypoint-discoverer | `NEO4J_PASSWORD`, `POSTGRES_DSN` | — |
| graph-enricher | `NEO4J_PASSWORD` | — |
| spec-generator | `POSTGRES_DSN` | — |
| trace-normalizer | `NEO4J_PASSWORD` | Only when `NEO4J_URL` is set |
| trace-runner | — | No required vars |
| instrumenter | — | No required vars |

For `trace-normalizer`, the conditional check is:
```python
if os.environ.get("NEO4J_URL"):
    validate_env(["NEO4J_PASSWORD"], "trace-normalizer")
```

### Behaviour

- Collects **all** missing vars before exiting (not one at a time).
- Exits with code `1` so container orchestrators mark the service as failed.
- Message goes to stderr so it appears in `docker compose logs`.

---

## Section 2 — Live Metrics Counters

### graph-enricher

Add counter fields to `GraphEnricherConsumer` in `consumer.py`:

```python
self.messages_processed: int = 0
self.messages_failed: int = 0
self.nodes_enriched: int = 0
self.dynamic_edges_written: int = 0
self.confirmed_edges_written: int = 0
```

- Increment `messages_processed` in `handle()` after successful parse.
- Increment `messages_failed` in the `except` branch of `post_batch()`.
- Increment `nodes_enriched`, `dynamic_edges_written`, `confirmed_edges_written` in `post_batch()` using the batch list lengths before clearing them.

In `main.py`, expose the consumer instance at module level so `/metrics` can read it:

```python
_consumer: GraphEnricherConsumer | None = None
```

### trace-runner

Add a module-level `_stats` dataclass (or named tuple) in `runner.py`:

```python
@dataclass
class RunnerStats:
    jobs_processed: int = 0
    jobs_cached: int = 0
    jobs_failed: int = 0
```

Expose as `runner.stats` (module-level singleton). Increment inside `run_worker()` at the appropriate branches. `main.py` reads `runner.stats` in `/metrics`.

### entrypoint-discoverer

`run_discover()` already returns `{"discovered": N, "queued": M}`. Add a module-level accumulator in `main.py`:

```python
_jobs_queued_total: int = 0
```

Increment by `result["queued"]` after each successful `run_discover()` call. Read in `/metrics`.

---

## Section 3 — Error Message Context

In `services/instrumenter/app/main.py`, line ~114:

```python
# Before
{"error": "Max concurrent sessions reached"}

# After
{"error": f"Max concurrent sessions reached (limit: {MAX_SESSIONS})"}
```

---

## Section 4 — Richer `/ready` Response

### `ServiceBase` changes

Update `add_dep_checker` signature:

```python
def add_dep_checker(
    self,
    checker: Callable[[], Awaitable[str | None]],
    name: str | None = None,
    required: bool = True,
) -> None:
```

Store tuples of `(checker, name, required)`. If `name` is not provided, use `checker.__name__`.

Update the `/ready` endpoint to return a structured response:

```python
{
    "status": "ok" | "degraded" | "unavailable",
    "deps": {
        "<name>": {
            "status": "ok" | "error",
            "error": "<message>",   # only present on error
            "required": true | false
        },
        ...
    }
}
```

Status rules:
- `"ok"` — all deps pass.
- `"degraded"` — at least one optional dep failed, all required deps pass. HTTP 200.
- `"unavailable"` — at least one required dep failed. HTTP 503.

### Call site updates

All existing `add_dep_checker(fn)` calls continue to work unchanged (name defaults to `fn.__name__`, required defaults to `True`). Optional deps (e.g. Neo4j in `trace-normalizer`) are registered with `required=False`:

```python
_svc.add_dep_checker(check_neo4j, name="neo4j", required=False)
```

---

## Section 5 — `usage.md` Updates

Add a new section **"Dynamic Analysis Pipeline — Environment Variables"** after the existing `## Configuration` section. It contains:

1. A table of all env vars consumed by the dynamic pipeline services, with columns: var name, required by, default value, description.
2. A note that services with required vars will print a clear error and exit immediately on startup if the var is absent.

The section covers these vars:

| Variable | Required by | Default | Description |
|---|---|---|---|
| `NEO4J_PASSWORD` | entrypoint-discoverer, graph-enricher, trace-normalizer (when NEO4J_URL set) | — | Neo4j authentication password |
| `NEO4J_URL` | entrypoint-discoverer, graph-enricher, trace-normalizer | `bolt://localhost:7687` | Neo4j bolt URL |
| `NEO4J_USER` | entrypoint-discoverer, graph-enricher, trace-normalizer | `neo4j` | Neo4j username |
| `POSTGRES_DSN` | entrypoint-discoverer, spec-generator | — | PostgreSQL connection string (`postgresql://user:pass@host/db`) |
| `REDIS_URL` | all | `redis://localhost:6379` | Redis connection URL |
| `QDRANT_URL` | spec-generator | `http://localhost:6333` | Qdrant URL |
| `INSTRUMENTER_URL` | trace-runner | `http://localhost:8093` | URL of the instrumenter service |
| `COMMIT_SHA` | trace-runner | `unknown` | Git commit SHA for trace cache keying |
| `REPOS` | trace-runner | `""` | Comma-separated list of repo names to watch |
| `EMBEDDING_PROVIDER` | spec-generator | `ollama` | `ollama` or `voyage` |
| `EMBEDDING_DIM` | spec-generator | `768` (ollama) / `1024` (voyage) | Embedding vector dimension |
| `TIMEOUT_MS` | instrumenter | `30000` | Max execution time per trace run (ms) |

---

## Files Changed

| File | Change |
|---|---|
| `services/shared/service.py` | Add `validate_env()`, update `add_dep_checker()` and `/ready` |
| `services/shared/tests/test_service.py` | Tests for new `validate_env` and structured `/ready` |
| `services/entrypoint-discoverer/app/main.py` | Call `validate_env`, wire `_jobs_queued_total` counter |
| `services/graph-enricher/app/main.py` | Call `validate_env`, expose `_consumer` for metrics |
| `services/graph-enricher/app/consumer.py` | Add counter fields, increment in handle/post_batch |
| `services/spec-generator/app/main.py` | Call `validate_env` |
| `services/trace-normalizer/app/main.py` | Conditional `validate_env`, register neo4j dep as optional |
| `services/trace-runner/app/main.py` | Wire `runner.stats` into metrics |
| `services/trace-runner/app/runner.py` | Add `RunnerStats` dataclass and `stats` singleton |
| `services/instrumenter/app/main.py` | Fix error message string |
| `usage.md` | Add env var table section |

---

## Definition of Done

- [ ] `validate_env` in shared; all applicable services call it at lifespan start
- [ ] `graph-enricher`, `trace-runner`, `entrypoint-discoverer` metrics return live values
- [ ] Instrumenter "sessions reached" message includes the limit
- [ ] `/ready` returns structured `deps` dict with `required` flag; existing tests pass
- [ ] `trace-normalizer` neo4j dep registered as `required=False`
- [ ] `usage.md` has env var table covering all dynamic pipeline services
- [ ] All existing unit tests pass unchanged
