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

Note: `not os.environ.get(k)` treats an **empty string as missing**. This is intentional — an empty `NEO4J_PASSWORD` or `POSTGRES_DSN` is not a valid value and should fail fast just like an absent var. Do not change this to `k not in os.environ`.

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
self.batches_failed: int = 0       # batch-level, not per-message
self.nodes_enriched: int = 0       # total node-update operations dispatched
self.dynamic_edges_written: int = 0
self.confirmed_edges_written: int = 0
```

Increment rules:
- `messages_processed`: in `handle()` after successful `model_validate_json`.
- `batches_failed`: in the `except Exception` branch of `post_batch()`. This is a batch-level counter (one increment per failed Neo4j write cycle, not per message). The metric name reflects this.
- `nodes_enriched`: in `post_batch()`, set to `len(self._batch_node_records)` **before** clearing the list. This counts total node-update operations dispatched (not unique nodes — a node appearing in two messages in the same batch counts twice).
- `dynamic_edges_written`: `len(self._batch_dynamic_edges)` before clearing.
- `confirmed_edges_written`: `len(self._batch_observed_ids)` before clearing.

In `main.py`, expose the consumer instance at module level and assign it inside `lifespan()`:

```python
# module level
_consumer: GraphEnricherConsumer | None = None

# inside lifespan(), after constructing the consumer:
_consumer = consumer   # <-- must be set here; the /metrics endpoint reads it at request time
```

### trace-runner

Add a module-level `_stats` dataclass in `runner.py`:

```python
@dataclass
class RunnerStats:
    jobs_processed: int = 0
    jobs_cached: int = 0
    jobs_failed: int = 0

stats = RunnerStats()
```

Increment rules inside `run_worker()`:
- `jobs_cached`: after `outcome == "cached"` (returned by both `process_job` and `go_client.process_job`).
- `jobs_processed`: after `outcome == "ok"`.
- `jobs_failed`: after `outcome == "error"` **and** in the `except asyncio.TimeoutError` branch (line ~154 — the timeout branch currently only logs and does not set `outcome`; it must also increment `jobs_failed`).

`main.py` imports `stats` at module level and reads it in `/metrics`:

```python
from .runner import stats as runner_stats   # top-level import in main.py
```

This must be a top-level import, not deferred inside `_start_worker()`, to avoid creating a stale reference to a different module instance.

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

`MAX_SESSIONS` remains a hardcoded constant (`100`) — it does not become an env var. The fix is purely to surface its value in the error message.

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

All existing `add_dep_checker(fn)` calls continue to work unchanged (name defaults to `fn.__name__`, required defaults to `True`).

For `trace-normalizer`, the existing code only registers `check_neo4j` when the driver is not `None` (conditional registration). This pattern is preserved — the checker is still only registered when the driver is present, but the call now passes `required=False`:

```python
if driver is not None:
    async def check_neo4j() -> str | None:
        ...
    _svc.add_dep_checker(check_neo4j, name="neo4j", required=False)
```

A service with no neo4j checker registered (because `NEO4J_URL` was not set) and a service where neo4j is checked and passes are distinguishable: the former simply omits the `neo4j` key from the `deps` dict, which is acceptable — the absence of a key means the dependency was not configured, not that it failed.

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
| `services/graph-enricher/app/consumer.py` | Add counter fields (`messages_processed`, `batches_failed`, `nodes_enriched`, `dynamic_edges_written`, `confirmed_edges_written`), increment in handle/post_batch |
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
