# Operational Readiness Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix four categories of operational weakness across the six dynamic analysis services: crash-fast config validation, live metrics counters, richer error messages, and a structured `/ready` response that distinguishes required from optional deps.

**Architecture:** All changes flow through `services/shared/service.py` (new `validate_env()` function, updated `add_dep_checker()` and `/ready` endpoint), with per-service wiring changes. No new dependencies introduced.

**Tech Stack:** Python 3.11+, FastAPI, Pydantic, pytest, Redis asyncio client.

**Spec:** `docs/superpowers/specs/2026-03-31-operational-readiness-design.md`

---

## File Map

| File | Action | What changes |
|---|---|---|
| `services/shared/service.py` | Modify | Add `validate_env()`, update `add_dep_checker()` signature + internal storage, rewrite `/ready` |
| `services/shared/tests/test_service.py` | Modify | Add `validate_env` tests; update 2 tests that check old `"errors"` list format |
| `services/instrumenter/app/main.py` | Modify | Fix "Max concurrent sessions reached" error message (1 line) |
| `services/instrumenter/tests/test_main.py` | Modify | Add test asserting error message includes the limit |
| `services/trace-runner/app/runner.py` | Modify | Add `RunnerStats` dataclass + `stats` singleton; increment in `run_worker()` |
| `services/trace-runner/app/main.py` | Modify | Import `runner_stats`; wire into `/metrics` |
| `services/trace-runner/tests/test_runner.py` | Modify | Add tests for counter increments |
| `services/graph-enricher/app/consumer.py` | Modify | Add 5 counter fields; increment in `handle()` and `post_batch()` |
| `services/graph-enricher/app/main.py` | Modify | Add module-level `_consumer`; assign in lifespan; wire into `/metrics`; call `validate_env` |
| `services/graph-enricher/tests/test_consumer.py` | Modify | Add tests for counter increments |
| `services/graph-enricher/tests/test_main.py` | Modify | Update 2 tests that check old `"errors"` format; update metrics assertions |
| `services/entrypoint-discoverer/app/main.py` | Modify | Call `validate_env`; add `_jobs_queued_total`; wire into `/metrics` |
| `services/entrypoint-discoverer/tests/test_main.py` | Modify | Update 1 test checking old `"errors"` format; add metrics counter test |
| `services/spec-generator/app/main.py` | Modify | Call `validate_env` |
| `services/trace-normalizer/app/main.py` | Modify | Conditional `validate_env`; pass `required=False` to neo4j dep checker |
| `usage.md` | Modify | Add "Dynamic Analysis Pipeline — Environment Variables" section |

---

## Task 1: Add `validate_env()` to shared

**Files:**
- Modify: `services/shared/service.py`
- Modify: `services/shared/tests/test_service.py`

- [ ] **Step 1: Write failing tests**

Add to `services/shared/tests/test_service.py`:

```python
import sys
import os
from unittest.mock import patch


def test_validate_env_passes_when_all_vars_set(monkeypatch):
    monkeypatch.setenv("FOO", "bar")
    monkeypatch.setenv("BAZ", "qux")
    from shared.service import validate_env
    validate_env(["FOO", "BAZ"], "test-svc")  # must not raise or exit


def test_validate_env_exits_when_var_missing(monkeypatch, capsys):
    monkeypatch.delenv("MISSING_VAR", raising=False)
    from shared.service import validate_env
    with pytest.raises(SystemExit) as exc_info:
        validate_env(["MISSING_VAR"], "test-svc")
    assert exc_info.value.code == 1
    captured = capsys.readouterr()
    assert "MISSING_VAR" in captured.err
    assert "test-svc" in captured.err
    assert "usage.md" in captured.err


def test_validate_env_reports_all_missing_at_once(monkeypatch, capsys):
    monkeypatch.delenv("VAR_A", raising=False)
    monkeypatch.delenv("VAR_B", raising=False)
    from shared.service import validate_env
    with pytest.raises(SystemExit):
        validate_env(["VAR_A", "VAR_B"], "test-svc")
    captured = capsys.readouterr()
    assert "VAR_A" in captured.err
    assert "VAR_B" in captured.err


def test_validate_env_treats_empty_string_as_missing(monkeypatch, capsys):
    monkeypatch.setenv("EMPTY_VAR", "")
    from shared.service import validate_env
    with pytest.raises(SystemExit):
        validate_env(["EMPTY_VAR"], "test-svc")
```

- [ ] **Step 2: Run tests to confirm they fail**

```bash
cd services/shared && python -m pytest tests/test_service.py -k "validate_env" -v
```

Expected: 4 failures — `ImportError` or `AttributeError: module has no attribute 'validate_env'`

- [ ] **Step 3: Implement `validate_env` in `services/shared/service.py`**

Add at top of file after existing imports:
```python
import sys
```

Add after the module-level `logger = logging.getLogger(__name__)` line:

```python
def validate_env(required: list[str], service: str) -> None:
    """Exit immediately if any required env var is missing or empty.

    Treats empty string as missing — an empty NEO4J_PASSWORD is not valid.
    Collects all missing vars before exiting so the user sees everything at once.
    """
    missing = [k for k in required if not os.environ.get(k)]
    if missing:
        lines = [f"[{service}] Missing required environment variables:"]
        for k in missing:
            lines.append(f"  - {k}")
        lines.append("See usage.md for configuration details.")
        print("\n".join(lines), file=sys.stderr)
        sys.exit(1)
```

- [ ] **Step 4: Run tests to confirm they pass**

```bash
cd services/shared && python -m pytest tests/test_service.py -k "validate_env" -v
```

Expected: 4 PASSED

- [ ] **Step 5: Commit**

```bash
git add services/shared/service.py services/shared/tests/test_service.py
git commit -m "feat(shared): add validate_env for crash-fast startup config checking"
```

---

## Task 2: Structured `/ready` response in `ServiceBase`

**Files:**
- Modify: `services/shared/service.py`
- Modify: `services/shared/tests/test_service.py`

The current `/ready` returns `{"status": "unavailable", "errors": [...]}`.
The new format returns `{"status": "ok|degraded|unavailable", "deps": {name: {status, error?, required}}}`.
Two existing tests check `resp.json()["errors"]` and must be updated.

- [ ] **Step 1: Write new tests and update broken ones**

First, delete three tests from `services/shared/tests/test_service.py` that either use the old `"errors"` format or will conflict with the new response shape:
- `test_ready_no_checkers_returns_ok` (asserts `resp.json() == {"status": "ok"}` — new format returns `{"status": "ok", "deps": {}}`)
- `test_ready_failing_checker_returns_503` (asserts `resp.json()["errors"][0]` — key no longer exists)
- `test_ready_passing_checker_returns_ok` (replaced by a more specific test below)

Replace the existing `test_ready_failing_checker_returns_503` and add new tests. In `services/shared/tests/test_service.py`, replace:

```python
def test_ready_failing_checker_returns_503():
    svc = ServiceBase("my-svc", "1.0.0")

    async def bad_dep() -> str | None:
        return "redis: connection refused"

    svc.add_dep_checker(bad_dep)
    client = TestClient(_app(svc))
    resp = client.get("/ready")
    assert resp.status_code == 503
    assert "redis" in resp.json()["errors"][0]
```

with:

```python
def test_ready_required_dep_failing_returns_503_unavailable():
    svc = ServiceBase("my-svc", "1.0.0")

    async def check_redis() -> str | None:
        return "redis: connection refused"

    svc.add_dep_checker(check_redis)
    client = TestClient(_app(svc))
    resp = client.get("/ready")
    assert resp.status_code == 503
    body = resp.json()
    assert body["status"] == "unavailable"
    assert "check_redis" in body["deps"]
    assert body["deps"]["check_redis"]["status"] == "error"
    assert body["deps"]["check_redis"]["required"] is True
    assert "connection refused" in body["deps"]["check_redis"]["error"]


def test_ready_optional_dep_failing_returns_200_degraded():
    svc = ServiceBase("my-svc", "1.0.0")

    async def check_neo4j() -> str | None:
        return "neo4j: unreachable"

    svc.add_dep_checker(check_neo4j, name="neo4j", required=False)
    client = TestClient(_app(svc))
    resp = client.get("/ready")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "degraded"
    assert body["deps"]["neo4j"]["required"] is False
    assert body["deps"]["neo4j"]["status"] == "error"


def test_ready_custom_name_overrides_function_name():
    svc = ServiceBase("my-svc", "1.0.0")

    async def check_something() -> str | None:
        return None

    svc.add_dep_checker(check_something, name="mydb")
    client = TestClient(_app(svc))
    resp = client.get("/ready")
    body = resp.json()
    assert "mydb" in body["deps"]
    assert "check_something" not in body["deps"]


def test_ready_passing_dep_appears_in_deps_ok():
    svc = ServiceBase("my-svc", "1.0.0")

    async def check_redis() -> str | None:
        return None

    svc.add_dep_checker(check_redis)
    client = TestClient(_app(svc))
    resp = client.get("/ready")
    body = resp.json()
    assert body["status"] == "ok"
    assert body["deps"]["check_redis"]["status"] == "ok"
    assert body["deps"]["check_redis"]["required"] is True
    assert "error" not in body["deps"]["check_redis"]


def test_ready_no_checkers_returns_ok_empty_deps():
    svc = ServiceBase("my-svc", "1.0.0")
    client = TestClient(_app(svc))
    resp = client.get("/ready")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["deps"] == {}
```

Also remove `test_ready_no_checkers_returns_ok` (replaced by `test_ready_no_checkers_returns_ok_empty_deps`) and `test_ready_passing_checker_returns_ok` (replaced by `test_ready_passing_dep_appears_in_deps_ok`).

- [ ] **Step 2: Run tests to confirm they fail**

```bash
cd services/shared && python -m pytest tests/test_service.py -k "ready" -v
```

Expected: new tests fail, old tests that were replaced are gone.

- [ ] **Step 3: Update `ServiceBase` in `services/shared/service.py`**

Replace the `__init__` method and the `add_dep_checker` method with:

```python
def __init__(self, name: str, version: str = "0.1.0") -> None:
    self.name = name
    self.version = version
    self._redis: aioredis.Redis | None = None
    # Each entry: (checker_fn, dep_name, required)
    self._dep_checkers: list[tuple[Callable[[], Awaitable[str | None]], str, bool]] = []

    self.router = APIRouter()

    @self.router.get("/health", name=f"{name}-health")
    def health() -> dict[str, str]:
        return {"status": "ok", "service": name, "version": version}

    @self.router.get("/ready", name=f"{name}-ready")
    async def ready() -> Any:
        dep_results: dict[str, dict] = {}
        any_required_failed = False
        any_optional_failed = False

        for checker, dep_name, required in self._dep_checkers:
            err = await checker()
            if err:
                dep_results[dep_name] = {"status": "error", "error": err, "required": required}
                if required:
                    any_required_failed = True
                else:
                    any_optional_failed = True
            else:
                dep_results[dep_name] = {"status": "ok", "required": required}

        if any_required_failed:
            return JSONResponse(
                status_code=503,
                content={"status": "unavailable", "deps": dep_results},
            )
        if any_optional_failed:
            return {"status": "degraded", "deps": dep_results}
        return {"status": "ok", "deps": dep_results}


def add_dep_checker(
    self,
    checker: Callable[[], Awaitable[str | None]],
    name: str | None = None,
    required: bool = True,
) -> None:
    """Register a dep checker. Returns None if OK, an error string if not.

    name defaults to checker.__name__. required=False means failure returns
    HTTP 200 with status 'degraded' instead of 503 'unavailable'.
    """
    dep_name = name if name is not None else checker.__name__
    self._dep_checkers.append((checker, dep_name, required))
```

- [ ] **Step 4: Run shared tests**

```bash
cd services/shared && python -m pytest tests/test_service.py -v
```

Expected: all tests PASS

- [ ] **Step 5: Update the two service tests that check `"errors"` format**

`services/entrypoint-discoverer/tests/test_main.py` line ~50:

```python
# Old (delete this):
assert any("redis" in e for e in resp.json()["errors"])

# New (replace with):
body = resp.json()
assert body["status"] == "unavailable"
assert any(v["status"] == "error" for v in body["deps"].values())
```

`services/graph-enricher/tests/test_main.py` — two tests use `body["errors"]`. Replace both:

```python
# In test_ready_503_redis_down — replace:
assert any("redis" in e for e in body["errors"])
# with:
assert any("redis" in k or "redis" in v.get("error", "") for k, v in body["deps"].items())

# In test_ready_503_neo4j_down — replace:
assert any("neo4j" in e for e in body["errors"])
# with:
assert any("neo4j" in k or "neo4j" in v.get("error", "") for k, v in body["deps"].items())
```

- [ ] **Step 6: Run affected service tests to confirm they pass**

```bash
cd services/entrypoint-discoverer && python -m pytest tests/test_main.py -v
cd services/graph-enricher && python -m pytest tests/test_main.py -v
```

Expected: all PASS

- [ ] **Step 7: Commit**

```bash
git add services/shared/service.py services/shared/tests/test_service.py \
        services/entrypoint-discoverer/tests/test_main.py \
        services/graph-enricher/tests/test_main.py
git commit -m "feat(shared): structured /ready response with required/optional dep distinction"
```

---

## Task 3: Fix instrumenter error message

**Files:**
- Modify: `services/instrumenter/app/main.py`

- [ ] **Step 1: Find and confirm the existing test**

```bash
grep -n "Max concurrent sessions" services/instrumenter/tests/*.py services/instrumenter/app/main.py
```

Expected: `main.py` shows the current string without the limit.

- [ ] **Step 2: Write the failing test**

Find or create `services/instrumenter/tests/test_main.py`. Add:

```python
def test_instrument_returns_limit_in_503_when_sessions_full(monkeypatch):
    """Error message must include the session limit so operators know the cap."""
    from fastapi.testclient import TestClient
    from app.main import app, MAX_SESSIONS, _sessions, _session_created_at

    # Fill sessions to the limit with fake entries
    fake_sessions = {f"fake-{i}": object() for i in range(MAX_SESSIONS)}
    fake_times = {f"fake-{i}": 0.0 for i in range(MAX_SESSIONS)}

    monkeypatch.setattr("app.main._sessions", fake_sessions)
    monkeypatch.setattr("app.main._session_created_at", fake_times)

    with TestClient(app) as client:
        resp = client.post("/instrument", json={
            "stable_id": "sha256:fn_test", "file_path": "tests/test.py", "repo": "test"
        })

    assert resp.status_code == 503
    assert str(MAX_SESSIONS) in resp.json()["error"]
    assert "limit" in resp.json()["error"].lower()
```

- [ ] **Step 3: Run to confirm it fails**

```bash
cd services/instrumenter && python -m pytest tests/test_main.py -k "sessions_full" -v
```

Expected: FAIL — assertion on `str(MAX_SESSIONS)` in error message fails.

- [ ] **Step 4: Fix the message in `services/instrumenter/app/main.py`**

Find (around line 114):
```python
content={"error": "Max concurrent sessions reached"},
```

Replace with:
```python
content={"error": f"Max concurrent sessions reached (limit: {MAX_SESSIONS})"},
```

- [ ] **Step 5: Run test to confirm it passes**

```bash
cd services/instrumenter && python -m pytest tests/test_main.py -k "sessions_full" -v
```

Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add services/instrumenter/app/main.py services/instrumenter/tests/test_main.py
git commit -m "fix(instrumenter): include session limit in 503 error message"
```

---

## Task 4: Wire `validate_env` into services

**Files:**
- Modify: `services/entrypoint-discoverer/app/main.py`
- Modify: `services/graph-enricher/app/main.py`
- Modify: `services/spec-generator/app/main.py`
- Modify: `services/trace-normalizer/app/main.py`

No new tests needed — the behavior is verified by startup (crash = missing var). The existing tests already mock/patch the drivers so they won't hit `validate_env` (it checks env, not connection).

- [ ] **Step 1: Add `validate_env` import and call in `entrypoint-discoverer/app/main.py`**

Find the existing import:
```python
from shared.service import ServiceBase
```

Change to:
```python
from shared.service import ServiceBase, validate_env
```

At the top of `lifespan()`, before any `_svc._dep_checkers.clear()`, add:
```python
validate_env(["NEO4J_PASSWORD", "POSTGRES_DSN"], "entrypoint-discoverer")
```

- [ ] **Step 2: Add to `graph-enricher/app/main.py`**

Same import change. At top of `lifespan()`:
```python
validate_env(["NEO4J_PASSWORD"], "graph-enricher")
```

- [ ] **Step 3: Add to `spec-generator/app/main.py`**

Same import change. At top of `lifespan()`:
```python
validate_env(["POSTGRES_DSN"], "spec-generator")
```

- [ ] **Step 4: Add to `trace-normalizer/app/main.py` (conditional)**

Same import change. At top of `lifespan()`:
```python
if os.environ.get("NEO4J_URL"):
    validate_env(["NEO4J_PASSWORD"], "trace-normalizer")
```

- [ ] **Step 5: Update neo4j dep checker in `trace-normalizer/app/main.py` to use `required=False`**

Find the existing block (around line 77):
```python
if driver is not None:
    async def check_neo4j() -> str | None:
        try:
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(None, driver.verify_connectivity)
            return None
        except Exception as exc:
            return f"neo4j: {exc}"
    _svc.add_dep_checker(check_neo4j)
```

Change the last line to:
```python
    _svc.add_dep_checker(check_neo4j, name="neo4j", required=False)
```

- [ ] **Step 6: Run all four service test suites**

```bash
cd services/entrypoint-discoverer && python -m pytest tests/ -v
cd services/graph-enricher && python -m pytest tests/ -v
cd services/spec-generator && python -m pytest tests/ -v
cd services/trace-normalizer && python -m pytest tests/ -v
```

Expected: all existing tests PASS (validate_env won't fire in tests because the tests don't call lifespan with missing env vars — they patch the drivers instead)

- [ ] **Step 7: Commit**

```bash
git add services/entrypoint-discoverer/app/main.py \
        services/graph-enricher/app/main.py \
        services/spec-generator/app/main.py \
        services/trace-normalizer/app/main.py
git commit -m "feat: crash-fast on missing required env vars at startup"
```

---

## Task 5: Live metrics for trace-runner

**Files:**
- Modify: `services/trace-runner/app/runner.py`
- Modify: `services/trace-runner/app/main.py`
- Modify: `services/trace-runner/tests/test_runner.py`

- [ ] **Step 1: Write failing tests for counter increments**

Add to `services/trace-runner/tests/test_runner.py`:

```python
@pytest.mark.asyncio
async def test_run_worker_increments_jobs_cached_on_cache_hit():
    from unittest.mock import AsyncMock, MagicMock, patch
    from app.models import EntrypointJob
    import app.runner as runner_mod

    # Reset stats before test
    runner_mod.stats.jobs_processed = 0
    runner_mod.stats.jobs_cached = 0
    runner_mod.stats.jobs_failed = 0

    job = EntrypointJob(
        stable_id="sha256:fn_test", name="test_fn",
        file_path="tests/test.py", priority=2, repo="test",
    )
    raw_job = job.model_dump_json().encode()

    mock_r = AsyncMock()
    mock_r.blpop.side_effect = [
        (b"entrypoint_queue:test", raw_job),
        asyncio.CancelledError(),
    ]
    mock_r.exists.return_value = 1  # cache hit

    mock_instrumenter = AsyncMock()
    mock_instrumenter.aclose = AsyncMock()

    with patch("app.runner.aioredis.from_url", return_value=mock_r), \
         patch("app.runner.InstrumenterClient", return_value=mock_instrumenter):
        with pytest.raises(asyncio.CancelledError):
            await runner_mod.run_worker(
                redis_url="redis://localhost:6379",
                instrumenter_url="http://localhost:8093",
                commit_sha="abc123",
                repos=["test"],
            )

    assert runner_mod.stats.jobs_cached == 1
    assert runner_mod.stats.jobs_processed == 0
    assert runner_mod.stats.jobs_failed == 0


@pytest.mark.asyncio
async def test_run_worker_increments_jobs_processed_on_ok():
    from unittest.mock import AsyncMock, MagicMock, patch
    from app.models import EntrypointJob, TraceEvent
    import app.runner as runner_mod

    runner_mod.stats.jobs_processed = 0
    runner_mod.stats.jobs_cached = 0
    runner_mod.stats.jobs_failed = 0

    job = EntrypointJob(
        stable_id="sha256:fn_test", name="test_fn",
        file_path="tests/test.py", priority=2, repo="test",
    )
    raw_job = job.model_dump_json().encode()
    events = [TraceEvent(type="call", fn="f", file="f.py", line=1, timestamp_ms=0.0)]

    mock_r = AsyncMock()
    mock_r.blpop.side_effect = [
        (b"entrypoint_queue:test", raw_job),
        asyncio.CancelledError(),
    ]
    mock_r.exists.return_value = 0  # cache miss

    mock_instrumenter = AsyncMock()
    mock_instrumenter.instrument.return_value = "sess-uuid"
    mock_instrumenter.run.return_value = (events, 10.0)
    mock_instrumenter.aclose = AsyncMock()

    with patch("app.runner.aioredis.from_url", return_value=mock_r), \
         patch("app.runner.InstrumenterClient", return_value=mock_instrumenter):
        with pytest.raises(asyncio.CancelledError):
            await runner_mod.run_worker(
                redis_url="redis://localhost:6379",
                instrumenter_url="http://localhost:8093",
                commit_sha="abc123",
                repos=["test"],
            )

    assert runner_mod.stats.jobs_processed == 1
    assert runner_mod.stats.jobs_cached == 0
    assert runner_mod.stats.jobs_failed == 0


@pytest.mark.asyncio
async def test_run_worker_increments_jobs_failed_on_timeout():
    from unittest.mock import AsyncMock, patch
    from app.models import EntrypointJob
    import app.runner as runner_mod

    runner_mod.stats.jobs_processed = 0
    runner_mod.stats.jobs_cached = 0
    runner_mod.stats.jobs_failed = 0

    job = EntrypointJob(
        stable_id="sha256:fn_test", name="test_fn",
        file_path="tests/test.py", priority=2, repo="test",
    )
    raw_job = job.model_dump_json().encode()

    mock_r = AsyncMock()
    mock_r.blpop.side_effect = [
        (b"entrypoint_queue:test", raw_job),
        asyncio.CancelledError(),
    ]
    mock_r.exists.return_value = 0

    mock_instrumenter = AsyncMock()
    mock_instrumenter.aclose = AsyncMock()

    # Patch asyncio.wait_for so the outer wait_for in run_worker raises TimeoutError,
    # exercising the `except asyncio.TimeoutError` branch directly (not process_job's
    # inner error handling).
    with patch("app.runner.aioredis.from_url", return_value=mock_r), \
         patch("app.runner.InstrumenterClient", return_value=mock_instrumenter), \
         patch("app.runner.asyncio.wait_for", side_effect=asyncio.TimeoutError):
        with pytest.raises(asyncio.CancelledError):
            await runner_mod.run_worker(
                redis_url="redis://localhost:6379",
                instrumenter_url="http://localhost:8093",
                commit_sha="abc123",
                repos=["test"],
            )

    assert runner_mod.stats.jobs_failed == 1
    assert runner_mod.stats.jobs_processed == 0
```

- [ ] **Step 2: Run to confirm they fail**

```bash
cd services/trace-runner && python -m pytest tests/test_runner.py -k "increments" -v
```

Expected: 3 failures — `AttributeError: module 'app.runner' has no attribute 'stats'`

- [ ] **Step 3: Add `RunnerStats` to `services/trace-runner/app/runner.py`**

Add after the existing imports, before any function definitions:

```python
from dataclasses import dataclass, field


@dataclass
class RunnerStats:
    jobs_processed: int = 0
    jobs_cached: int = 0
    jobs_failed: int = 0


stats = RunnerStats()
```

In `run_worker()`, after the `logger.info("Job %s [%s]: %s", ...)` line (around line 153), add outcome-based increments:

```python
logger.info("Job %s [%s]: %s", job.stable_id, job.language, outcome)
if outcome == "cached":
    stats.jobs_cached += 1
elif outcome == "ok":
    stats.jobs_processed += 1
elif outcome == "error":
    stats.jobs_failed += 1
```

In the `except asyncio.TimeoutError:` branch (around line 154), add:

```python
except asyncio.TimeoutError:
    logger.error("Job timed out: %s", job.stable_id)
    stats.jobs_failed += 1
```

- [ ] **Step 4: Wire stats into `/metrics` in `services/trace-runner/app/main.py`**

Add at the top-level imports (NOT inside `_start_worker`):

```python
from .runner import stats as runner_stats
```

Replace the `/metrics` endpoint body:

```python
@app.get("/metrics")
def metrics():
    lines = [
        "# HELP trace_runner_jobs_processed_total Total jobs processed",
        "# TYPE trace_runner_jobs_processed_total counter",
        f"trace_runner_jobs_processed_total {runner_stats.jobs_processed}",
        "# HELP trace_runner_jobs_cached_total Total jobs skipped (cache hit)",
        "# TYPE trace_runner_jobs_cached_total counter",
        f"trace_runner_jobs_cached_total {runner_stats.jobs_cached}",
        "# HELP trace_runner_jobs_failed_total Total jobs failed",
        "# TYPE trace_runner_jobs_failed_total counter",
        f"trace_runner_jobs_failed_total {runner_stats.jobs_failed}",
    ]
    return PlainTextResponse("\n".join(lines) + "\n")
```

- [ ] **Step 5: Run tests**

```bash
cd services/trace-runner && python -m pytest tests/ -v
```

Expected: all PASS (including the 3 new tests and the existing `test_metrics_returns_prometheus_text`)

- [ ] **Step 6: Commit**

```bash
git add services/trace-runner/app/runner.py services/trace-runner/app/main.py \
        services/trace-runner/tests/test_runner.py
git commit -m "feat(trace-runner): live metrics counters for jobs processed/cached/failed"
```

---

## Task 6: Live metrics for graph-enricher

**Files:**
- Modify: `services/graph-enricher/app/consumer.py`
- Modify: `services/graph-enricher/app/main.py`
- Modify: `services/graph-enricher/tests/test_consumer.py`
- Modify: `services/graph-enricher/tests/test_main.py`

- [ ] **Step 1: Write failing tests for counter increments**

Add to `services/graph-enricher/tests/test_consumer.py`:

```python
@pytest.mark.asyncio
async def test_consumer_increments_messages_processed_on_handle():
    """messages_processed increments once per successfully parsed message."""
    from app.consumer import GraphEnricherConsumer

    driver = _make_driver()
    consumer = GraphEnricherConsumer(driver)
    assert consumer.messages_processed == 0

    path = _make_execution_path()
    data = {"event": path.model_dump_json().encode()}

    with patch("app.consumer.enricher"):
        await consumer.handle(data)

    assert consumer.messages_processed == 1


@pytest.mark.asyncio
async def test_consumer_increments_node_and_edge_counters_on_post_batch():
    """nodes_enriched, dynamic_edges_written, confirmed_edges_written count batch items."""
    from app.consumer import GraphEnricherConsumer

    driver = _make_driver()
    consumer = GraphEnricherConsumer(driver)

    path = _make_execution_path()
    data = {"event": path.model_dump_json().encode()}

    with patch("app.consumer.enricher") as mock_enricher:
        mock_enricher.update_node_props_batch = MagicMock()
        mock_enricher.upsert_dynamic_edges = MagicMock()
        mock_enricher.confirm_static_edges = MagicMock()
        mock_enricher.run_conflict_detector = MagicMock()
        mock_enricher.run_staleness_downgrade = MagicMock()

        await consumer.handle(data)
        nodes_before = len(consumer._batch_node_records)
        edges_before = len(consumer._batch_dynamic_edges)
        ids_before = len(consumer._batch_observed_ids)

        await consumer.post_batch()

    assert consumer.nodes_enriched == nodes_before
    assert consumer.dynamic_edges_written == edges_before
    assert consumer.confirmed_edges_written == ids_before


@pytest.mark.asyncio
async def test_consumer_increments_batches_failed_on_neo4j_error():
    """batches_failed increments when the Neo4j write cycle raises."""
    from app.consumer import GraphEnricherConsumer

    driver = _make_driver()
    consumer = GraphEnricherConsumer(driver)

    path = _make_execution_path()
    data = {"event": path.model_dump_json().encode()}

    with patch("app.consumer.enricher") as mock_enricher:
        mock_enricher.update_node_props_batch.side_effect = Exception("Neo4j down")
        await consumer.handle(data)
        await consumer.post_batch()

    assert consumer.batches_failed == 1
    assert consumer.messages_processed == 1  # handle() still succeeded
```

- [ ] **Step 2: Run to confirm they fail**

```bash
cd services/graph-enricher && python -m pytest tests/test_consumer.py -k "increments" -v
```

Expected: 3 failures — `AttributeError: 'GraphEnricherConsumer' object has no attribute 'messages_processed'`

- [ ] **Step 3: Add counter fields to `GraphEnricherConsumer.__init__` in `consumer.py`**

In `services/graph-enricher/app/consumer.py`, add after the existing `__init__` batch list fields:

```python
def __init__(self, driver) -> None:
    self._driver = driver
    self._batch_node_records: list[dict] = []
    self._batch_dynamic_edges: list[dict] = []
    self._batch_observed_ids: list[str] = []
    # Metrics counters
    self.messages_processed: int = 0
    self.batches_failed: int = 0
    self.nodes_enriched: int = 0
    self.dynamic_edges_written: int = 0
    self.confirmed_edges_written: int = 0
```

In `handle()`, add after the successful `model_validate_json` and before accumulating:

```python
async def handle(self, data: dict) -> None:
    raw = data.get(b"event") or data.get("event")
    if raw is None:
        raise KeyError("missing 'event' key")
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8")
    try:
        event = ExecutionPath.model_validate_json(raw)
    except ValidationError as exc:
        raise ValueError(f"invalid ExecutionPath JSON: {exc}") from exc
    self.messages_processed += 1          # <-- add this line
    node_records, dynamic_edges, observed_ids = _extract_records(event)
    self._batch_node_records.extend(node_records)
    self._batch_dynamic_edges.extend(dynamic_edges)
    self._batch_observed_ids.extend(observed_ids)
```

In `post_batch()`, capture lengths before clearing and increment counters:

```python
async def post_batch(self) -> None:
    if not self._batch_observed_ids:
        return
    loop = asyncio.get_running_loop()
    # Capture lengths before clearing — counts total ops dispatched this batch
    _nodes = len(self._batch_node_records)
    _edges = len(self._batch_dynamic_edges)
    _ids = len(self._batch_observed_ids)
    try:
        await loop.run_in_executor(
            None, enricher.update_node_props_batch, self._driver, self._batch_node_records
        )
        await loop.run_in_executor(
            None, enricher.upsert_dynamic_edges, self._driver, self._batch_dynamic_edges
        )
        await loop.run_in_executor(
            None, enricher.confirm_static_edges, self._driver, self._batch_observed_ids
        )
        await loop.run_in_executor(None, enricher.run_conflict_detector, self._driver)
        await loop.run_in_executor(None, enricher.run_staleness_downgrade, self._driver)
        self.nodes_enriched += _nodes
        self.dynamic_edges_written += _edges
        self.confirmed_edges_written += _ids
    except Exception as exc:
        logger.error("Batch Neo4j write failed: %s", exc)
        self.batches_failed += 1
    finally:
        self._batch_node_records.clear()
        self._batch_dynamic_edges.clear()
        self._batch_observed_ids.clear()
```

- [ ] **Step 4: Wire `_consumer` into main.py and update `/metrics`**

In `services/graph-enricher/app/main.py`:

Add module-level variable after `_driver = None`:
```python
_consumer: "GraphEnricherConsumer | None" = None
```

Inside `lifespan()`, after `consumer = GraphEnricherConsumer(_driver)`, add:
```python
global _consumer
_consumer = consumer
```

Replace the `/metrics` endpoint:

```python
@app.get("/metrics")
def metrics():
    c = _consumer
    lines = [
        "# HELP graph_enricher_messages_processed_total Total messages processed",
        "# TYPE graph_enricher_messages_processed_total counter",
        f"graph_enricher_messages_processed_total {c.messages_processed if c else 0}",
        "# HELP graph_enricher_batches_failed_total Total Neo4j write batches that failed",
        "# TYPE graph_enricher_batches_failed_total counter",
        f"graph_enricher_batches_failed_total {c.batches_failed if c else 0}",
        "# HELP graph_enricher_nodes_enriched_total Total node-update operations dispatched",
        "# TYPE graph_enricher_nodes_enriched_total counter",
        f"graph_enricher_nodes_enriched_total {c.nodes_enriched if c else 0}",
        "# HELP graph_enricher_dynamic_edges_total Total dynamic edges written",
        "# TYPE graph_enricher_dynamic_edges_total counter",
        f"graph_enricher_dynamic_edges_total {c.dynamic_edges_written if c else 0}",
        "# HELP graph_enricher_confirmed_edges_total Total static edges confirmed",
        "# TYPE graph_enricher_confirmed_edges_total counter",
        f"graph_enricher_confirmed_edges_total {c.confirmed_edges_written if c else 0}",
    ]
    return PlainTextResponse("\n".join(lines) + "\n")
```

Also update the metrics test assertion in `tests/test_main.py` — the old metric `graph_enricher_messages_failed_total` is now `graph_enricher_batches_failed_total`:

```python
def test_metrics_returns_prometheus_format(client):
    resp = client.get("/metrics")
    assert resp.status_code == 200
    text = resp.text
    assert "graph_enricher_messages_processed_total" in text
    assert "graph_enricher_batches_failed_total" in text
    assert "graph_enricher_nodes_enriched_total" in text
    assert "graph_enricher_dynamic_edges_total" in text
    assert "graph_enricher_confirmed_edges_total" in text
    assert "# TYPE" in text
    assert "# HELP" in text
```

- [ ] **Step 5: Run tests**

```bash
cd services/graph-enricher && python -m pytest tests/ -v
```

Expected: all PASS

- [ ] **Step 6: Commit**

```bash
git add services/graph-enricher/app/consumer.py services/graph-enricher/app/main.py \
        services/graph-enricher/tests/test_consumer.py services/graph-enricher/tests/test_main.py
git commit -m "feat(graph-enricher): live metrics counters; wire _consumer to /metrics"
```

---

## Task 7: Live metrics for entrypoint-discoverer

**Files:**
- Modify: `services/entrypoint-discoverer/app/main.py`
- Modify: `services/entrypoint-discoverer/tests/test_main.py`

- [ ] **Step 1: Write failing test**

Add to `services/entrypoint-discoverer/tests/test_main.py`:

```python
def test_metrics_counter_increments_after_discover():
    """jobs_queued_total must reflect cumulative jobs queued, not always 0."""
    import app.main as main_mod
    main_mod._jobs_queued_total = 0  # reset

    with patch("app.main._get_neo4j_driver", return_value=MagicMock()), \
         patch("app.main._get_pg_conn", return_value=MagicMock()), \
         patch("app.main._get_redis", return_value=MagicMock()), \
         patch("app.main.run_discover", return_value={"discovered": 5, "queued": 3}):
        from app.main import app
        client = TestClient(app)
        client.post("/discover", json={"repo": "myrepo", "trigger": "schedule"})
        resp = client.get("/metrics")

    assert "3" in resp.text  # cumulative total should be 3 after one call
    assert "entrypoint_discoverer_jobs_queued_total 3" in resp.text
```

- [ ] **Step 2: Run to confirm it fails**

```bash
cd services/entrypoint-discoverer && python -m pytest tests/test_main.py -k "counter_increments" -v
```

Expected: FAIL — metrics still shows `0`

- [ ] **Step 3: Add `_jobs_queued_total` to `services/entrypoint-discoverer/app/main.py`**

Add module-level variable after VERSION:
```python
_jobs_queued_total: int = 0
```

In the `discover()` endpoint, after `return DiscoverResponse(**result)` succeeds, add:
```python
@app.post("/discover", status_code=202, response_model=DiscoverResponse)
def discover(req: DiscoverRequest):
    global _jobs_queued_total
    try:
        result = run_discover(
            neo4j_driver=_get_neo4j_driver(),
            pg_conn=_get_pg_conn(),
            redis_client=_get_redis(),
            repo=req.repo,
            trigger=req.trigger,
        )
        _jobs_queued_total += result.get("queued", 0)
        return DiscoverResponse(**result)
    except Exception as exc:
        logger.error("Discover failed: %s", exc)
        raise HTTPException(status_code=500, detail="Discovery failed")
```

Replace the `/metrics` endpoint:
```python
@app.get("/metrics")
def metrics():
    lines = [
        "# HELP entrypoint_discoverer_jobs_queued_total Total EntrypointJobs pushed to Redis",
        "# TYPE entrypoint_discoverer_jobs_queued_total counter",
        f"entrypoint_discoverer_jobs_queued_total {_jobs_queued_total}",
    ]
    return PlainTextResponse("\n".join(lines) + "\n")
```

- [ ] **Step 4: Run tests**

```bash
cd services/entrypoint-discoverer && python -m pytest tests/ -v
```

Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add services/entrypoint-discoverer/app/main.py \
        services/entrypoint-discoverer/tests/test_main.py
git commit -m "feat(entrypoint-discoverer): live jobs_queued_total counter in /metrics"
```

---

## Task 8: Update `usage.md`

**Files:**
- Modify: `usage.md`

No tests for docs. One step.

- [ ] **Step 1: Add env var section after `## Configuration`**

Find the existing section:
```markdown
## Configuration

Copy `.env.example` to `.env` and set:
...
```

Insert after that block (before `## Docker networking note`):

```markdown
## Dynamic Analysis Pipeline — Environment Variables

Services with required variables will print a clear error message to stderr and exit immediately if the variable is absent or empty. Set these before running `make up`.

### Required variables

| Variable | Required by | Description |
|---|---|---|
| `NEO4J_PASSWORD` | entrypoint-discoverer, graph-enricher, trace-normalizer (when `NEO4J_URL` is set) | Neo4j authentication password |
| `POSTGRES_DSN` | entrypoint-discoverer, spec-generator | Full PostgreSQL connection string, e.g. `postgresql://tersecontext:pass@localhost/tersecontext` |

These are already covered by `.env.example` — if you copied it and filled in the passwords, you're set.

### Optional variables (with defaults)

| Variable | Used by | Default | Description |
|---|---|---|---|
| `NEO4J_URL` | entrypoint-discoverer, graph-enricher, trace-normalizer | `bolt://localhost:7687` | Neo4j bolt URL |
| `NEO4J_USER` | entrypoint-discoverer, graph-enricher, trace-normalizer | `neo4j` | Neo4j username |
| `REDIS_URL` | all services | `redis://localhost:6379` | Redis connection URL |
| `QDRANT_URL` | spec-generator | `http://localhost:6333` | Qdrant URL |
| `INSTRUMENTER_URL` | trace-runner | `http://localhost:8093` | URL of the instrumenter service |
| `COMMIT_SHA` | trace-runner | `unknown` | Git commit SHA used as trace cache key — set automatically in Docker Compose |
| `REPOS` | trace-runner | `""` | Comma-separated repo names to watch; auto-discovered from Redis if empty |
| `EMBEDDING_PROVIDER` | spec-generator | `ollama` | `ollama` or `voyage` |
| `EMBEDDING_DIM` | spec-generator | `768` (ollama) / `1024` (voyage) | Embedding vector dimension |
| `TIMEOUT_MS` | instrumenter | `30000` | Max execution time per trace run (ms) |

### What a missing required variable looks like

```
[graph-enricher] Missing required environment variables:
  - NEO4J_PASSWORD
See usage.md for configuration details.
```

The service exits with code 1. Check `docker compose logs <service-name>` to see this message.
```

- [ ] **Step 2: Commit**

```bash
git add usage.md
git commit -m "docs(usage): add dynamic pipeline environment variable reference"
```

---

## Final verification

- [ ] **Run all modified service test suites together**

```bash
cd services/shared && python -m pytest tests/ -v
cd services/instrumenter && python -m pytest tests/ -v
cd services/trace-runner && python -m pytest tests/ -v
cd services/graph-enricher && python -m pytest tests/ -v
cd services/entrypoint-discoverer && python -m pytest tests/ -v
cd services/spec-generator && python -m pytest tests/ -v
cd services/trace-normalizer && python -m pytest tests/ -v
```

Expected: all pass, no regressions.
