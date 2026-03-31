# Readability & Performance Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Extract shared Python service boilerplate into a `shared/` package, decompose oversized files, enrich `spec_text` with provenance and confidence signals, and improve pipeline throughput with batch consumers, batched Neo4j writes, and a tighter sys.settrace filter.

**Architecture:** Shared `ServiceBase` and `RedisConsumerBase` live in `services/shared/` and are installed as a local editable package into each Python service's venv. The renderer and store in spec-generator are extended in-place — no model changes. All performance improvements are call-site changes in existing files.

**Tech Stack:** Python 3.10+, FastAPI, asyncio, redis-py, pydantic v2, neo4j driver, sysconfig (stdlib)

---

## File Map

### New files
- `services/shared/__init__.py`
- `services/shared/service.py` — `ServiceBase`: FastAPI factory, /health, /ready, `get_redis()`
- `services/shared/consumer.py` — `RedisConsumerBase`: XREADGROUP loop, COUNT=50, DLQ, `handle()` + `post_batch()`
- `services/shared/setup.py` — local package declaration

### Modified files
- `services/graph-enricher/app/consumer.py` — replace loop with `GraphEnricherConsumer(RedisConsumerBase)`; batch Neo4j writes in `post_batch()`
- `services/graph-enricher/app/main.py` — use `ServiceBase`
- `services/spec-generator/app/consumer.py` — replace loop with `SpecGeneratorConsumer(RedisConsumerBase)`
- `services/spec-generator/app/main.py` — use `ServiceBase`
- `services/trace-normalizer/app/main.py` — replace loop with `NormalizerConsumer(RedisConsumerBase)`, use `ServiceBase`
- `services/trace-runner/app/main.py` — use `ServiceBase`
- `services/entrypoint-discoverer/app/main.py` — use `ServiceBase`
- `services/instrumenter/app/main.py` — use `ServiceBase`
- `services/spec-generator/app/renderer.py` — confidence band header, per-call provenance tags, static-only warnings
- `services/spec-generator/app/consumer.py` — pass `confidence_band` from renderer to store
- `services/spec-generator/app/store.py` — add `confidence_band` + `coverage_pct` to Qdrant payload
- `services/instrumenter/app/trace.py` — sysconfig-based EXCLUDE_PREFIXES, return None for stdlib/site-packages
- `services/parser/app/extractor_go.py` → split into `go_nodes.py`, `go_edges.py`, `go_stable_id.py`
- `services/parser/app/extractor.py` → split into `py_nodes.py`, `py_edges.py`, `py_stable_id.py`
- `services/perf-tracker/app/collector.py` → extract `aggregator.py`
- `services/go-instrumenter/internal/rewriter/rewriter.go` → extract `internal/rewriter/passes/entry.go`, `internal/rewriter/passes/goroutine.go`, `internal/rewriter/passes/imports.go`

### Dockerfiles to update (add shared package install)
- `services/graph-enricher/Dockerfile`
- `services/spec-generator/Dockerfile`
- `services/trace-normalizer/Dockerfile`
- `services/trace-runner/Dockerfile`
- `services/entrypoint-discoverer/Dockerfile`
- `services/instrumenter/Dockerfile`

---

## Task 1: Create shared/ package skeleton

**Files:**
- Create: `services/shared/__init__.py`
- Create: `services/shared/setup.py`
- Create: `services/shared/service.py`
- Create: `services/shared/tests/__init__.py`
- Create: `services/shared/tests/test_service.py`

- [ ] **Step 1: Write the failing test**

```python
# services/shared/tests/test_service.py
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from shared.service import ServiceBase


def _app(svc: ServiceBase) -> FastAPI:
    """Mount svc.router onto a throwaway FastAPI app for testing."""
    app = FastAPI()
    app.include_router(svc.router)
    return app


def test_health_returns_ok():
    svc = ServiceBase("my-svc", "1.0.0")
    client = TestClient(_app(svc))
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok", "service": "my-svc", "version": "1.0.0"}


def test_ready_no_checkers_returns_ok():
    svc = ServiceBase("my-svc", "1.0.0")
    client = TestClient(_app(svc))
    resp = client.get("/ready")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_ready_failing_checker_returns_503():
    svc = ServiceBase("my-svc", "1.0.0")

    async def bad_dep() -> str | None:
        return "redis: connection refused"

    svc.add_dep_checker(bad_dep)
    client = TestClient(_app(svc))
    resp = client.get("/ready")
    assert resp.status_code == 503
    assert "redis" in resp.json()["errors"][0]


def test_ready_passing_checker_returns_ok():
    svc = ServiceBase("my-svc", "1.0.0")

    async def good_dep() -> str | None:
        return None

    svc.add_dep_checker(good_dep)
    client = TestClient(_app(svc))
    resp = client.get("/ready")
    assert resp.status_code == 200
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd services/shared
pip install -e . -q
pytest tests/test_service.py -v
```

Expected: ImportError or ModuleNotFoundError — `shared` not yet implemented.

- [ ] **Step 3: Create the package files**

```python
# services/shared/__init__.py
# (empty)
```

```python
# services/shared/setup.py
from setuptools import setup, find_packages
setup(name="tersecontext-shared", version="0.1.0", packages=find_packages())
```

```python
# services/shared/service.py
from __future__ import annotations

import logging
import os
from collections.abc import Awaitable, Callable
from contextlib import asynccontextmanager
from typing import Any

import redis.asyncio as aioredis
from fastapi import APIRouter
from fastapi.responses import JSONResponse

logger = logging.getLogger(__name__)


class ServiceBase:
    """Shared FastAPI scaffolding for all Python microservices.

    Provides an APIRouter with /health and /ready (with pluggable dep
    checkers), and a singleton Redis client. Each service creates its own
    FastAPI app, includes svc.router, and keeps its own /metrics, lifespan,
    and business-logic routes.

    Usage::

        svc = ServiceBase("my-service", "0.1.0")
        svc.add_dep_checker(my_redis_checker)

        app = FastAPI(lifespan=lifespan)
        app.include_router(svc.router)   # registers /health and /ready

        @app.get("/metrics")
        def metrics(): ...
    """

    def __init__(self, name: str, version: str = "0.1.0") -> None:
        self.name = name
        self.version = version
        self._redis: aioredis.Redis | None = None
        self._dep_checkers: list[Callable[[], Awaitable[str | None]]] = []

        self.router = APIRouter()

        @self.router.get("/health")
        def health() -> dict[str, str]:
            return {"status": "ok", "service": name, "version": version}

        @self.router.get("/ready")
        async def ready() -> Any:
            errors: list[str] = []
            for checker in self._dep_checkers:
                err = await checker()
                if err:
                    errors.append(err)
            if errors:
                return JSONResponse(
                    status_code=503,
                    content={"status": "unavailable", "errors": errors},
                )
            return {"status": "ok"}

    def add_dep_checker(self, checker: Callable[[], Awaitable[str | None]]) -> None:
        """Register a dep checker. Return None if OK, an error string if not."""
        self._dep_checkers.append(checker)

    def get_redis(self, url: str | None = None) -> aioredis.Redis:
        """Return the singleton Redis client, creating it on first call."""
        if self._redis is None:
            self._redis = aioredis.from_url(
                url or os.environ.get("REDIS_URL", "redis://localhost:6379")
            )
        return self._redis

    async def close_redis(self) -> None:
        if self._redis is not None:
            await self._redis.aclose()
            self._redis = None
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd services/shared
pytest tests/test_service.py -v
```

Expected: 4 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add services/shared/
git commit -m "feat(shared): add ServiceBase with /health, /ready, and dep checker pattern"
```

---

## Task 2: Create RedisConsumerBase

**Files:**
- Create: `services/shared/consumer.py`
- Modify: `services/shared/tests/test_service.py` (add consumer tests)

- [ ] **Step 1: Write the failing tests**

```python
# append to services/shared/tests/test_service.py

import asyncio
import json
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from shared.consumer import RedisConsumerBase


class EchoConsumer(RedisConsumerBase):
    stream = "stream:test"
    group = "test-group"

    def __init__(self):
        self.handled: list[dict] = []

    async def handle(self, data: dict) -> None:
        self.handled.append(data)


@pytest.mark.asyncio
async def test_consumer_calls_handle_for_each_event():
    consumer = EchoConsumer()
    event_data = {"event": json.dumps({"foo": "bar"}).encode()}

    fake_messages = [("stream:test", [(b"1-0", event_data)])]

    mock_redis = AsyncMock()
    mock_redis.xreadgroup = AsyncMock(side_effect=[fake_messages, asyncio.CancelledError()])
    mock_redis.xack = AsyncMock()
    mock_redis.xgroup_create = AsyncMock(return_value=True)
    mock_redis.aclose = AsyncMock()

    with patch("shared.consumer.aioredis.from_url", return_value=mock_redis):
        with pytest.raises(asyncio.CancelledError):
            await consumer.run("redis://localhost:6379")

    assert len(consumer.handled) == 1
    assert consumer.handled[0] == event_data


@pytest.mark.asyncio
async def test_consumer_sends_to_dlq_on_bad_message():
    class BadConsumer(RedisConsumerBase):
        stream = "stream:test"
        group = "test-group"

        async def handle(self, data: dict) -> None:
            raise KeyError("missing 'event' key")

    consumer = BadConsumer()
    event_data = {b"bad": b"data"}
    fake_messages = [("stream:test", [(b"1-0", event_data)])]

    mock_redis = AsyncMock()
    mock_redis.xreadgroup = AsyncMock(side_effect=[fake_messages, asyncio.CancelledError()])
    mock_redis.xack = AsyncMock()
    mock_redis.xadd = AsyncMock()
    mock_redis.xgroup_create = AsyncMock(return_value=True)
    mock_redis.aclose = AsyncMock()

    with patch("shared.consumer.aioredis.from_url", return_value=mock_redis):
        with pytest.raises(asyncio.CancelledError):
            await consumer.run("redis://localhost:6379")

    mock_redis.xadd.assert_called_once()
    dlq_stream = mock_redis.xadd.call_args[0][0]
    assert "dlq" in dlq_stream
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd services/shared
pytest tests/test_service.py -v -k consumer
```

Expected: ImportError — `shared.consumer` not yet implemented.

- [ ] **Step 3: Implement RedisConsumerBase**

```python
# services/shared/consumer.py
from __future__ import annotations

import asyncio
import logging
import socket
from abc import ABC, abstractmethod

import redis.asyncio as aioredis
import redis.exceptions

logger = logging.getLogger(__name__)


class RedisConsumerBase(ABC):
    """XREADGROUP consumer with COUNT=50, DLQ on error, and post-batch hook.

    Subclasses set class-level ``stream`` and ``group`` and implement
    ``handle(data)``. Override ``post_batch()`` to run logic (e.g. batch
    DB writes) after each group of up to 50 events.

    Usage::

        class MyConsumer(RedisConsumerBase):
            stream = "stream:my-events"
            group  = "my-group"

            async def handle(self, data: dict) -> None:
                raw = data.get(b"event") or data.get("event")
                event = MyModel.model_validate_json(raw)
                ...

        consumer = MyConsumer(...)
        await consumer.run(redis_url)
    """

    stream: str  # set by subclass
    group: str   # set by subclass

    @abstractmethod
    async def handle(self, data: dict) -> None:
        """Process a single event. Raise KeyError/ValidationError for bad messages
        (→ DLQ + ACK). Raise any other Exception to log without ACK (retry)."""

    async def post_batch(self) -> None:
        """Called after each read batch (up to 50 events). Override for batch writes."""

    async def run(self, redis_url: str) -> None:
        """Run the consumer loop until cancelled."""
        r = aioredis.from_url(redis_url)
        consumer_name = f"{self.group}-{socket.gethostname()}"
        dlq_stream = f"{self.stream}-dlq"

        try:
            try:
                await r.xgroup_create(self.stream, self.group, id="0", mkstream=True)
            except redis.exceptions.ResponseError as exc:
                if "BUSYGROUP" not in str(exc):
                    raise

            logger.info("Consumer started: stream=%s group=%s", self.stream, self.group)

            while True:
                try:
                    messages = await r.xreadgroup(
                        groupname=self.group,
                        consumername=consumer_name,
                        streams={self.stream: ">"},
                        count=50,
                        block=100,
                    )
                    had_events = False
                    for _stream, events in (messages or []):
                        for msg_id, data in events:
                            had_events = True
                            try:
                                await self.handle(data)
                                await r.xack(self.stream, self.group, msg_id)
                            except (KeyError, ValueError) as exc:
                                logger.warning("Bad message %s → DLQ: %s", msg_id, exc)
                                await r.xadd(
                                    dlq_stream,
                                    {b"msg_id": str(msg_id).encode(), b"error": str(exc).encode()},
                                )
                                await r.xack(self.stream, self.group, msg_id)
                            except asyncio.CancelledError:
                                raise
                            except Exception as exc:
                                logger.error("Consumer error msg=%s: %s", msg_id, exc)
                                # No ACK — message will be redelivered

                    if had_events:
                        await self.post_batch()

                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    logger.error("Consumer loop error: %s", exc)

        finally:
            await r.aclose()
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd services/shared
pytest tests/test_service.py -v
```

Expected: all tests PASS.

- [ ] **Step 5: Commit**

```bash
git add services/shared/consumer.py services/shared/tests/test_service.py
git commit -m "feat(shared): add RedisConsumerBase with COUNT=50, DLQ, and post_batch hook"
```

---

## Task 3: Migrate graph-enricher to shared base + batch Neo4j writes

**Files:**
- Modify: `services/graph-enricher/app/consumer.py`
- Modify: `services/graph-enricher/app/main.py`
- Modify: `services/graph-enricher/tests/test_consumer.py`
- Modify: `services/graph-enricher/Dockerfile` (add shared install)

**Background:** graph-enricher's current `run_consumer(driver)` function does everything. We replace it with a `GraphEnricherConsumer` class that:
1. Accumulates `node_records`, `dynamic_edges`, and `observed_ids` in `handle()`
2. Calls the three enricher batch functions + conflict detector once in `post_batch()`

The existing `_process_event` function and its tests stay intact as a utility to extract the records from an ExecutionPath — we just stop calling it with immediate DB writes.

- [ ] **Step 1: Write failing tests for new consumer structure**

Open `services/graph-enricher/tests/test_consumer.py`. Add at the bottom:

```python
# New tests for GraphEnricherConsumer

import pytest
from unittest.mock import AsyncMock, MagicMock, patch, call as mock_call
from app.consumer import GraphEnricherConsumer


@pytest.mark.asyncio
async def test_consumer_batches_neo4j_writes():
    """post_batch flushes all accumulated records in one call each."""
    driver = _make_driver()
    consumer = GraphEnricherConsumer(driver)

    path1 = _make_execution_path()
    path2 = _make_execution_path(entrypoint_stable_id="sha256:fn_test_register")

    import json
    data1 = {"event": path1.model_dump_json().encode()}
    data2 = {"event": path2.model_dump_json().encode()}

    with patch("app.consumer.enricher") as mock_enricher:
        mock_enricher.update_node_props_batch = MagicMock()
        mock_enricher.upsert_dynamic_edges = MagicMock()
        mock_enricher.confirm_static_edges = MagicMock()
        mock_enricher.run_conflict_detector = MagicMock()
        mock_enricher.run_staleness_downgrade = MagicMock()

        # Accumulate two events
        await consumer.handle(data1)
        await consumer.handle(data2)
        assert len(consumer._batch_node_records) > 0, "should have accumulated node records"

        # post_batch must call each enricher function exactly once, with all accumulated data
        await consumer.post_batch()
        mock_enricher.update_node_props_batch.assert_called_once()
        mock_enricher.run_conflict_detector.assert_called_once()

    # Buffers cleared after post_batch
    assert consumer._batch_node_records == []
    assert consumer._batch_dynamic_edges == []
    assert consumer._batch_observed_ids == []
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd services/graph-enricher
pip install -e ../../shared -q
pytest tests/test_consumer.py::test_consumer_batches_neo4j_writes -v
```

Expected: ImportError — `GraphEnricherConsumer` not defined yet.

- [ ] **Step 3: Rewrite consumer.py**

Replace `services/graph-enricher/app/consumer.py` entirely:

```python
# services/graph-enricher/app/consumer.py
from __future__ import annotations

import asyncio
import logging

from pydantic import ValidationError
from shared.consumer import RedisConsumerBase

from . import enricher
from .models import ExecutionPath

logger = logging.getLogger(__name__)


def _extract_records(event: ExecutionPath) -> tuple[list[dict], list[dict], list[str]]:
    """Extract (node_records, dynamic_edges, observed_ids) from an ExecutionPath."""
    node_records = [
        {
            "stable_id": node.stable_id,
            "avg_latency_ms": node.avg_ms,
            "branch_coverage": node.frequency_ratio,
        }
        for node in event.call_sequence
    ]
    observed_in_sequence = {r["stable_id"] for r in node_records}
    if event.entrypoint_stable_id not in observed_in_sequence:
        node_records.append({
            "stable_id": event.entrypoint_stable_id,
            "avg_latency_ms": event.timing_p50_ms,
            "branch_coverage": 1.0,
        })

    dynamic_edges = [
        {"source": e.source, "target": e.target, "count": 1}
        for e in event.dynamic_only_edges
    ]

    observed_ids = [node.stable_id for node in event.call_sequence]
    if event.entrypoint_stable_id not in observed_ids:
        observed_ids.insert(0, event.entrypoint_stable_id)

    return node_records, dynamic_edges, observed_ids


class GraphEnricherConsumer(RedisConsumerBase):
    stream = "stream:execution-paths"
    group = "graph-enricher-group"

    def __init__(self, driver) -> None:
        self._driver = driver
        self._batch_node_records: list[dict] = []
        self._batch_dynamic_edges: list[dict] = []
        self._batch_observed_ids: list[str] = []

    async def handle(self, data: dict) -> None:
        raw = data.get(b"event") or data.get("event")
        if raw is None:
            raise KeyError("missing 'event' key")
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8")
        event = ExecutionPath.model_validate_json(raw)
        node_records, dynamic_edges, observed_ids = _extract_records(event)
        self._batch_node_records.extend(node_records)
        self._batch_dynamic_edges.extend(dynamic_edges)
        self._batch_observed_ids.extend(observed_ids)

    async def post_batch(self) -> None:
        if not self._batch_node_records:
            return
        loop = asyncio.get_running_loop()
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
        except Exception as exc:
            logger.error("Batch Neo4j write failed: %s", exc)
        finally:
            self._batch_node_records.clear()
            self._batch_dynamic_edges.clear()
            self._batch_observed_ids.clear()


# ── Backward-compat shims — existing tests import these names ──────────────
# The existing test file imports: _process_event, run_consumer, STREAM, GROUP

STREAM = GraphEnricherConsumer.stream
GROUP = GraphEnricherConsumer.group


def _process_event(driver, event: ExecutionPath) -> None:
    """Used by existing unit tests. New code uses GraphEnricherConsumer."""
    node_records, dynamic_edges, observed_ids = _extract_records(event)
    enricher.update_node_props_batch(driver, node_records)
    enricher.upsert_dynamic_edges(driver, dynamic_edges)
    enricher.confirm_static_edges(driver, observed_ids)


async def run_consumer(driver) -> None:
    """Used by existing unit tests. New code calls GraphEnricherConsumer.run() directly."""
    redis_url = __import__("os").environ.get("REDIS_URL", "redis://localhost:6379")
    await GraphEnricherConsumer(driver).run(redis_url)
```

- [ ] **Step 4: Update main.py to use ServiceBase**

Replace `services/graph-enricher/app/main.py`:

```python
# services/graph-enricher/app/main.py
from __future__ import annotations

import asyncio
import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import PlainTextResponse
from neo4j import GraphDatabase
from shared.service import ServiceBase

logger = logging.getLogger(__name__)

VERSION = "0.1.0"
_svc = ServiceBase("graph-enricher", VERSION)
_driver = None


def _make_driver():
    return GraphDatabase.driver(
        os.environ.get("NEO4J_URL", "bolt://localhost:7687"),
        auth=(os.environ.get("NEO4J_USER", "neo4j"), os.environ["NEO4J_PASSWORD"]),
    )


async def _check_neo4j() -> str | None:
    if _driver is None:
        return "neo4j: driver not initialized"
    try:
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, _driver.verify_connectivity)
        return None
    except Exception as exc:
        return f"neo4j: {exc}"


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _driver
    from .consumer import GraphEnricherConsumer

    _driver = _make_driver()
    consumer = GraphEnricherConsumer(_driver)
    redis_url = os.environ.get("REDIS_URL", "redis://localhost:6379")

    async def check_redis() -> str | None:
        try:
            await _svc.get_redis().ping()
            return None
        except Exception as exc:
            return f"redis: {exc}"

    _svc.add_dep_checker(check_redis)
    _svc.add_dep_checker(_check_neo4j)

    task = asyncio.create_task(consumer.run(redis_url))
    try:
        yield
    finally:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        if _driver:
            _driver.close()
        await _svc.close_redis()


app = FastAPI(lifespan=lifespan)
app.include_router(_svc.router)  # registers /health and /ready


@app.get("/metrics")
def metrics():
    lines = [
        "# HELP graph_enricher_messages_processed_total Total messages processed",
        "# TYPE graph_enricher_messages_processed_total counter",
        "graph_enricher_messages_processed_total 0",
    ]
    return PlainTextResponse("\n".join(lines) + "\n")
```

- [ ] **Step 5: Update Dockerfile**

Add to `services/graph-enricher/Dockerfile` before `COPY . .`:

```dockerfile
COPY ../shared /shared
RUN pip install -e /shared
```

- [ ] **Step 6: Run all graph-enricher tests**

```bash
cd services/graph-enricher
pip install -e ../../shared -q
pytest tests/ -v
```

Expected: all existing tests pass + new batch test passes.

- [ ] **Step 7: Commit**

```bash
git add services/graph-enricher/ services/shared/
git commit -m "feat(graph-enricher): migrate to ServiceBase + RedisConsumerBase, batch Neo4j writes"
```

---

## Task 4: Migrate spec-generator to shared base

**Files:**
- Modify: `services/spec-generator/app/consumer.py`
- Modify: `services/spec-generator/app/main.py`
- Modify: `services/spec-generator/Dockerfile`

- [ ] **Step 1: Write the failing test**

```python
# append to services/spec-generator/tests/test_consumer.py

@pytest.mark.asyncio
async def test_consumer_is_redis_consumer_base():
    """SpecGeneratorConsumer inherits RedisConsumerBase."""
    from shared.consumer import RedisConsumerBase
    from app.consumer import SpecGeneratorConsumer
    assert issubclass(SpecGeneratorConsumer, RedisConsumerBase)
    assert SpecGeneratorConsumer.stream == "stream:execution-paths"
    assert SpecGeneratorConsumer.group == "spec-generator-group"
```

- [ ] **Step 2: Run to verify it fails**

```bash
cd services/spec-generator
pip install -e ../../shared -q
pytest tests/test_consumer.py::test_consumer_is_redis_consumer_base -v
```

Expected: ImportError — `SpecGeneratorConsumer` not defined.

- [ ] **Step 3: Rewrite consumer.py**

Replace `services/spec-generator/app/consumer.py`:

```python
# services/spec-generator/app/consumer.py
from __future__ import annotations

import json
import logging

from pydantic import ValidationError
from shared.consumer import RedisConsumerBase

from app.models import ExecutionPath
from app.renderer import render_spec_text  # upgraded to render_spec in Task 9
from app.store import SpecStore

logger = logging.getLogger(__name__)

messages_processed_total: int = 0
messages_failed_total: int = 0
specs_written_total: int = 0
specs_embedded_total: int = 0


class SpecGeneratorConsumer(RedisConsumerBase):
    stream = "stream:execution-paths"
    group = "spec-generator-group"

    def __init__(self, store: SpecStore) -> None:
        self._store = store

    async def handle(self, data: dict) -> None:
        global messages_processed_total, specs_written_total, specs_embedded_total
        raw = data.get(b"event") or data.get("event")
        if raw is None:
            raise KeyError("message missing 'event' key")
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8")

        path = ExecutionPath.model_validate_json(raw)
        entrypoint_name = (
            path.call_sequence[0].name if path.call_sequence else path.entrypoint_stable_id
        )

        spec_text = render_spec_text(path, entrypoint_name)
        await self._store.upsert_spec(path, spec_text)
        specs_written_total += 1
        await self._store.upsert_qdrant(path, entrypoint_name, spec_text)
        specs_embedded_total += 1
        messages_processed_total += 1
```

> **Upgrade in Task 9:** After Task 8 adds `render_spec()` returning `(spec_text, confidence_band)`, update this file to call `render_spec` and pass `confidence_band` to `upsert_qdrant`.

- [ ] **Step 4: Update main.py**

Replace `services/spec-generator/app/main.py` with ServiceBase pattern (same structure as graph-enricher's main.py above, adapted for postgres/qdrant deps).

- [ ] **Step 5: Run existing tests**

```bash
cd services/spec-generator
pytest tests/ -v
```

Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add services/spec-generator/
git commit -m "feat(spec-generator): migrate to ServiceBase + RedisConsumerBase"
```

---

## Task 5: Migrate trace-normalizer to shared base

**Files:**
- Modify: `services/trace-normalizer/app/main.py`

**Note:** trace-normalizer uses sync `redis.Redis` + `run_in_executor` for its consumer loop (unlike the async consumers above). `RedisConsumerBase` is async-native. The migration requires switching to `redis.asyncio` for the consumer loop while keeping the sync processing functions as `run_in_executor` calls.

- [ ] **Step 1: Write the failing test**

```python
# services/trace-normalizer/tests/test_consumer.py (create if not exists)
import pytest
from shared.consumer import RedisConsumerBase
from app.consumer import NormalizerConsumer  # new class


def test_normalizer_consumer_inherits_base():
    assert issubclass(NormalizerConsumer, RedisConsumerBase)
    assert NormalizerConsumer.stream == "stream:raw-traces"
    assert NormalizerConsumer.group == "normalizer-group"
```

- [ ] **Step 2: Run to verify it fails**

```bash
cd services/trace-normalizer
pip install -e ../../shared -q
pytest tests/test_consumer.py -v
```

- [ ] **Step 3: Extract consumer class into app/consumer.py**

Create `services/trace-normalizer/app/consumer.py`:

```python
# services/trace-normalizer/app/consumer.py
from __future__ import annotations

import asyncio
import json
import logging

from shared.consumer import RedisConsumerBase

from .classifier import classify_side_effects
from .emitter import emit_execution_path
from .models import ExecutionPath, RawTrace
from .normalizer import aggregate_frequencies, compute_percentiles, reconstruct_call_tree
from .reconciler import reconcile

logger = logging.getLogger(__name__)


class NormalizerConsumer(RedisConsumerBase):
    stream = "stream:raw-traces"
    group = "normalizer-group"

    def __init__(self, redis_sync, neo4j_driver) -> None:
        # redis_sync is a sync redis.Redis client used by aggregation helpers
        # (reconciler, aggregate_frequencies). We run them in executor.
        self._redis_sync = redis_sync
        self._driver = neo4j_driver

    async def handle(self, data: dict) -> None:
        raw = data.get(b"event") or data.get("event")
        if raw is None:
            raise KeyError("missing 'event' key")
        if isinstance(raw, bytes):
            raw = raw.decode()

        loop = asyncio.get_running_loop()
        await loop.run_in_executor(
            None, self._process_sync, self._redis_sync, self._driver, raw
        )

    @staticmethod
    def _process_sync(r, driver, raw_json: str) -> None:
        trace = RawTrace.model_validate_json(raw_json)
        nodes = reconstruct_call_tree(trace.events)

        repo = trace.repo or "unknown"
        agg_key = f"trace_agg:{repo}:{trace.entrypoint_stable_id}"
        raw_agg = r.get(agg_key)
        existing_agg = json.loads(raw_agg) if raw_agg else {}
        max_runs = max((v.get("runs", 0) for v in existing_agg.values()), default=0) + 1

        nodes = aggregate_frequencies(r, repo, trace.entrypoint_stable_id, nodes, max_runs)
        side_effects = classify_side_effects(trace.events)
        observed_fns = {ev.fn for ev in trace.events if ev.type == "call"}
        dynamic_only, never_observed = reconcile(driver, repo, trace.entrypoint_stable_id, observed_fns)

        durations = [n.avg_ms for n in nodes if n.avg_ms > 0]
        p50, p99 = compute_percentiles(durations or [trace.duration_ms])

        # coverage_pct: use observed-call ratio as a proxy. A dedicated
        # coverage pre-pass (coverage.py) may populate this more accurately
        # in the trace-runner; for now default to None so the renderer
        # renders "MEDIUM" confidence rather than crashing.
        coverage_pct: float | None = None
        if nodes:
            observed = sum(1 for n in nodes if n.frequency_ratio > 0.0)
            coverage_pct = observed / len(nodes)

        ep = ExecutionPath(
            entrypoint_stable_id=trace.entrypoint_stable_id,
            commit_sha=trace.commit_sha,
            repo=repo,
            call_sequence=nodes,
            side_effects=side_effects,
            dynamic_only_edges=dynamic_only,
            never_observed_static_edges=never_observed,
            timing_p50_ms=p50,
            timing_p99_ms=p99,
            coverage_pct=coverage_pct,
        )
        emit_execution_path(r, ep)
```

- [ ] **Step 4: Simplify main.py**

Replace the consumer loop in `main.py` with:

```python
# In lifespan, replace _consumer_loop creation with:
from .consumer import NormalizerConsumer
consumer = NormalizerConsumer(_get_redis_sync(), _get_neo4j_driver())
redis_url = os.environ.get("REDIS_URL", "redis://localhost:6379")
_consumer_task = asyncio.create_task(consumer.run(redis_url))
```

Also add `ServiceBase` for `/health` and `/ready` endpoints.

- [ ] **Step 5: Run tests**

```bash
cd services/trace-normalizer
pytest tests/ -v
```

- [ ] **Step 6: Commit**

```bash
git add services/trace-normalizer/
git commit -m "feat(trace-normalizer): migrate to NormalizerConsumer + ServiceBase"
```

---

## Task 6: Migrate ServiceBase-only services (trace-runner, entrypoint-discoverer, instrumenter)

**Files:**
- Modify: `services/trace-runner/app/main.py`
- Modify: `services/entrypoint-discoverer/app/main.py`
- Modify: `services/instrumenter/app/main.py`

These three services have no XREADGROUP loop — they get `ServiceBase` for `/health` and `/ready` only.

- [ ] **Step 1: For each service, write a smoke test**

```python
# e.g. services/trace-runner/tests/test_health.py
from fastapi.testclient import TestClient
from app.main import app

def test_health():
    client = TestClient(app)
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json()["service"] == "trace-runner"
```

Repeat for entrypoint-discoverer and instrumenter.

- [ ] **Step 2: Run to verify (should already pass with current code)**

```bash
cd services/trace-runner && pytest tests/ -v
cd services/entrypoint-discoverer && pytest tests/ -v
cd services/instrumenter && pytest tests/ -v
```

These tests define the baseline. They must still pass after migration.

- [ ] **Step 3: Migrate each main.py**

For each service, replace the inline `/health`, `_get_redis()` factory, and `/ready` with `ServiceBase`:

```python
# Pattern for trace-runner/app/main.py
from shared.service import ServiceBase

_svc = ServiceBase("trace-runner", VERSION)

@asynccontextmanager
async def lifespan(app):
    async def check_redis():
        try:
            await _svc.get_redis().ping()
            return None
        except Exception as exc:
            return f"redis: {exc}"
    _svc.add_dep_checker(check_redis)
    _worker_task = await _start_worker()
    try:
        yield
    finally:
        _worker_task.cancel()
        ...
        await _svc.close_redis()

app = FastAPI(lifespan=lifespan)
app.include_router(_svc.router)

@app.get("/metrics")
def metrics(): ...
```

Apply the same pattern to entrypoint-discoverer and instrumenter (instrumenter has no lifespan task to manage).

- [ ] **Step 4: Run smoke tests again — must still pass**

```bash
cd services/trace-runner && pytest tests/ -v
cd services/entrypoint-discoverer && pytest tests/ -v
cd services/instrumenter && pytest tests/ -v
```

- [ ] **Step 5: Commit**

```bash
git add services/trace-runner/ services/entrypoint-discoverer/ services/instrumenter/
git commit -m "feat(services): migrate trace-runner, entrypoint-discoverer, instrumenter to ServiceBase"
```

---

## Task 7: File decomposition

### 7a: Parser Python extractor

**Files:**
- Modify: `services/parser/app/extractor.py` (becomes orchestrator)
- Create: `services/parser/app/py_nodes.py`
- Create: `services/parser/app/py_edges.py`
- Create: `services/parser/app/py_stable_id.py`

- [ ] **Step 1: Run existing parser tests as baseline**

```bash
cd services/parser
pytest tests/ -v
```

Expected: all pass. This is your safety net.

- [ ] **Step 2: Extract py_stable_id.py**

Move the `stable_id(...)` and `node_hash(...)` functions (currently in `extractor.py`) to `py_stable_id.py`. Update `extractor.py` to import from it.

- [ ] **Step 3: Extract py_nodes.py**

Move the class/function node extraction logic to `py_nodes.py`. Keep `extractor.py` as the orchestrator that calls into it.

- [ ] **Step 4: Extract py_edges.py**

Move CALLS edge inference to `py_edges.py`.

- [ ] **Step 5: Run parser tests**

```bash
cd services/parser
pytest tests/ -v
```

Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add services/parser/app/
git commit -m "refactor(parser): decompose extractor.py into py_nodes, py_edges, py_stable_id"
```

### 7b: Parser Go extractor (same pattern)

Repeat steps 1–6 for `extractor_go.py` → `go_nodes.py`, `go_edges.py`, `go_stable_id.py`.

```bash
git commit -m "refactor(parser): decompose extractor_go.py into go_nodes, go_edges, go_stable_id"
```

### 7c: perf-tracker aggregator extraction

**Files:**
- Modify: `services/perf-tracker/app/collector.py`
- Create: `services/perf-tracker/app/aggregator.py`

- [ ] **Step 1: Run existing tests as baseline**

```bash
cd services/perf-tracker
pytest tests/ -v
```

- [ ] **Step 2: Identify aggregation functions in collector.py**

Look for percentile computation, windowing, and rate calculations — functions that take event lists and return aggregated stats. These move to `aggregator.py`.

- [ ] **Step 3: Extract to aggregator.py and update collector.py imports**

- [ ] **Step 4: Run tests**

```bash
pytest tests/ -v
```

- [ ] **Step 5: Commit**

```bash
git add services/perf-tracker/app/
git commit -m "refactor(perf-tracker): extract aggregator.py from collector.py"
```

### 7d: go-instrumenter rewriter passes

**Files:**
- Modify: `services/go-instrumenter/internal/rewriter/rewriter.go` (becomes orchestrator)
- Create: `services/go-instrumenter/internal/rewriter/passes/entry.go`
- Create: `services/go-instrumenter/internal/rewriter/passes/goroutine.go`
- Create: `services/go-instrumenter/internal/rewriter/passes/imports.go`

The new `passes` package lives inside `internal/rewriter/` to stay within the Go `internal/` boundary and maintain the existing package visibility rules.

- [ ] **Step 1: Run existing Go tests as baseline**

```bash
cd services/go-instrumenter
go test ./... -v
```

- [ ] **Step 2: Create passes/ package and move entry injection**

Move the function-entry injection logic (tracert.Enter + defer) to `internal/rewriter/passes/entry.go` as `func InjectEntry(...)`.

- [ ] **Step 3: Move goroutine wrapping**

Move `go f()` → `tracert.Go(func() { f() })` logic to `internal/rewriter/passes/goroutine.go`.

- [ ] **Step 4: Move import deduplication**

Move the tracert import add/deduplicate logic to `internal/rewriter/passes/imports.go`.

- [ ] **Step 5: Update rewriter.go to call passes**

`rewriter.go` becomes: parse → `passes.InjectEntry` → `passes.WrapGoroutines` → `passes.EnsureImport` → format.

- [ ] **Step 6: Run Go tests**

```bash
go test ./... -v
```

- [ ] **Step 7: Commit**

```bash
git add services/go-instrumenter/
git commit -m "refactor(go-instrumenter): decompose rewriter.go into passes/ package"
```

---

## Task 8: Output enrichment — confidence band and provenance tags in renderer

**Files:**
- Modify: `services/spec-generator/app/renderer.py`

**What changes:**
1. PATH header gains `(N runs · BAND confidence · P% branch coverage)`
2. Each call sequence item gains `[confirmed]` or `[dynamic-only]` tag
3. `never_observed_static_edges` items are rendered as `⚠ fn_name  static-only — present in AST but never observed`
4. LOW confidence header gets a warning line
5. `render_spec_text(path, name)` → `render_spec(path, name)` returning `(str, str)` (spec_text, confidence_band)

- [ ] **Step 1: Write failing tests**

```python
# services/spec-generator/tests/test_renderer.py (create)
import pytest
from app.models import CallSequenceItem, EdgeRef, ExecutionPath, SideEffect
from app.renderer import render_spec, _confidence_band


def _make_path(coverage_pct=0.85, dynamic_only=None, never_observed=None, call_sequence=None):
    return ExecutionPath(
        entrypoint_stable_id="sha256:fn_login",
        commit_sha="abc123",
        repo="acme",
        call_sequence=call_sequence or [
            CallSequenceItem(
                stable_id="sha256:fn_validate",
                name="validate",
                qualified_name="auth.validate",
                hop=1,
                frequency_ratio=1.0,
                avg_ms=10.0,
            ),
            CallSequenceItem(
                stable_id="sha256:fn_audit",
                name="audit_log",
                qualified_name="audit.log",
                hop=2,
                frequency_ratio=0.5,
                avg_ms=3.0,
            ),
        ],
        side_effects=[],
        dynamic_only_edges=dynamic_only or [],
        never_observed_static_edges=never_observed or [],
        timing_p50_ms=10.0,
        timing_p99_ms=40.0,
        coverage_pct=coverage_pct,
    )


def test_confidence_band_high():
    assert _confidence_band(0.85) == "HIGH"
    assert _confidence_band(0.80) == "HIGH"


def test_confidence_band_medium():
    assert _confidence_band(0.79) == "MEDIUM"
    assert _confidence_band(0.40) == "MEDIUM"


def test_confidence_band_low():
    assert _confidence_band(0.39) == "LOW"
    assert _confidence_band(0.0) == "LOW"


def test_render_spec_returns_tuple():
    path = _make_path()
    result = render_spec(path, "login")
    assert isinstance(result, tuple) and len(result) == 2
    spec_text, band = result
    assert band == "HIGH"
    assert isinstance(spec_text, str)


def test_path_header_contains_band_and_coverage():
    path = _make_path(coverage_pct=0.84)
    spec_text, _ = render_spec(path, "login")
    first_line = spec_text.split("\n")[0]
    assert "HIGH" in first_line
    assert "84%" in first_line
    assert first_line.startswith("PATH login")


def test_low_coverage_warning():
    path = _make_path(coverage_pct=0.30)
    spec_text, band = render_spec(path, "login")
    assert band == "LOW"
    assert "LOW COVERAGE" in spec_text


def test_confirmed_tag_on_normal_call():
    path = _make_path(dynamic_only=[])
    spec_text, _ = render_spec(path, "login")
    assert "[confirmed]" in spec_text


def test_dynamic_only_tag():
    path = _make_path(dynamic_only=[EdgeRef(source="sha256:fn_login", target="sha256:fn_audit")])
    spec_text, _ = render_spec(path, "login")
    assert "[dynamic-only]" in spec_text


def test_static_only_warning():
    never_obs = [EdgeRef(source="sha256:fn_login", target="sha256:fn_notify")]
    path = _make_path(never_observed=never_obs)
    spec_text, _ = render_spec(path, "login")
    assert "static-only" in spec_text
    assert "fn_notify" in spec_text or "sha256:fn_notify" in spec_text
```

- [ ] **Step 2: Run to verify failures**

```bash
cd services/spec-generator
pytest tests/test_renderer.py -v
```

Expected: multiple failures — `render_spec` does not exist, `_confidence_band` does not exist.

- [ ] **Step 3: Implement renderer changes**

Update `services/spec-generator/app/renderer.py`:

```python
# Add at top of file:
from typing import Literal

ConfidenceBand = Literal["HIGH", "MEDIUM", "LOW"]


def _confidence_band(coverage_pct: float | None) -> ConfidenceBand:
    if coverage_pct is None:
        return "MEDIUM"
    if coverage_pct >= 0.80:
        return "HIGH"
    if coverage_pct >= 0.40:
        return "MEDIUM"
    return "LOW"
```

Update `render_spec_text` → rename to `render_spec`, change signature and return type:

```python
def render_spec(path: ExecutionPath, entrypoint_name: str) -> tuple[str, ConfidenceBand]:
    band = _confidence_band(path.coverage_pct)
    coverage_pct_display = f"{round((path.coverage_pct or 0.0) * 100)}%"

    lines: list[str] = []

    # PATH header with confidence band.
    # ExecutionPath has no run_count field — omit it to avoid displaying a
    # misleading number. A future model update can add run_count if needed.
    lines.append(
        f"PATH {entrypoint_name}  ({band} confidence · {coverage_pct_display} branch coverage)"
    )
    if band == "LOW":
        lines.append("⚠ LOW COVERAGE — spec reflects partial observation only")

    # Build provenance lookup sets
    dynamic_only_targets = {e.target for e in path.dynamic_only_edges}

    for i, item in enumerate(path.call_sequence, start=1):
        freq_str = f"{item.frequency_ratio:.2f}"
        ms_str = f"~{item.avg_ms:.1f}ms"
        tag = "[dynamic-only]" if item.stable_id in dynamic_only_targets else "[confirmed]"
        line = f"  {i}.  {item.name}    {freq_str}    {ms_str}    {tag}"
        if item.args:
            line += f"    args: {item.args}"
        lines.append(line)

    # Static-only warnings
    for edge in path.never_observed_static_edges:
        lines.append(
            f"  ⚠ {edge.target}   static-only — present in AST but never observed"
        )

    lines.append("")

    # SIDE_EFFECTS section (unchanged from existing code)
    if path.side_effects:
        lines.append("SIDE_EFFECTS:")
        for effect in path.side_effects:
            label = _SIDE_EFFECT_LABELS.get(effect.type, effect.type.upper().replace("_", " "))
            suffix = "   (conditional)" if effect.hop_depth > 1 else ""
            lines.append(f"  {label}   {effect.detail}{suffix}")
        lines.append("")

    # CHANGE_IMPACT section (unchanged from existing code)
    # ... (keep existing logic verbatim)

    return "\n".join(lines), band


# Keep old name as alias for backward compatibility during migration
def render_spec_text(path: ExecutionPath, entrypoint_name: str) -> str:
    spec_text, _ = render_spec(path, entrypoint_name)
    return spec_text
```

- [ ] **Step 4: Run renderer tests**

```bash
cd services/spec-generator
pytest tests/test_renderer.py -v
```

Expected: all pass.

- [ ] **Step 5: Run full spec-generator test suite**

```bash
pytest tests/ -v
```

Expected: all pass (backward compat alias preserves old callers).

- [ ] **Step 6: Commit**

```bash
git add services/spec-generator/app/renderer.py services/spec-generator/tests/test_renderer.py
git commit -m "feat(spec-generator): add confidence band, provenance tags, and static-only warnings to spec_text"
```

---

## Task 9: Update store Qdrant payload with confidence_band + coverage_pct

**Files:**
- Modify: `services/spec-generator/app/store.py`
- Modify: `services/spec-generator/app/consumer.py` (update call sites)

- [ ] **Step 1: Write failing test**

```python
# append to services/spec-generator/tests/test_consumer.py

@pytest.mark.asyncio
async def test_process_passes_confidence_band_to_qdrant():
    from app.consumer import SpecGeneratorConsumer

    mock_store = MagicMock()
    mock_store.upsert_spec = AsyncMock()
    mock_store.upsert_qdrant = AsyncMock()

    consumer = SpecGeneratorConsumer(mock_store)
    path_dict = _make_path_dict()
    path_dict["coverage_pct"] = 0.85
    data = {"event": json.dumps(path_dict).encode()}

    await consumer.handle(data)

    # upsert_qdrant should be called with confidence_band as 4th arg
    args = mock_store.upsert_qdrant.call_args[0]
    assert len(args) == 4, f"expected 4 args, got {len(args)}: {args}"
    confidence_band = args[3]
    assert confidence_band in ("HIGH", "MEDIUM", "LOW")
```

- [ ] **Step 2: Run to verify failure**

```bash
cd services/spec-generator
pytest tests/test_consumer.py::test_process_passes_confidence_band_to_qdrant -v
```

- [ ] **Step 3: Update store.py**

Change `upsert_qdrant` signature:

```python
async def upsert_qdrant(
    self, path: ExecutionPath, entrypoint_name: str, spec_text: str, confidence_band: str
) -> None:
    vectors = await self._provider.embed([spec_text])
    vector = vectors[0]
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(
        None, self._upsert_qdrant_sync, path, entrypoint_name, spec_text, vector, confidence_band
    )
```

Change `_upsert_qdrant_sync`:

```python
def _upsert_qdrant_sync(
    self, path: ExecutionPath, entrypoint_name: str, spec_text: str,
    vector: list[float], confidence_band: str
) -> None:
    point_id = str(uuid.uuid5(uuid.NAMESPACE_OID, f"{path.entrypoint_stable_id}:{path.repo}"))
    self._qdrant.upsert(
        collection_name=COLLECTION,
        points=[
            PointStruct(
                id=point_id,
                vector=vector,
                payload={
                    "node_stable_id": path.entrypoint_stable_id,
                    "entrypoint_name": entrypoint_name,
                    "repo": path.repo,
                    "commit_sha": path.commit_sha,
                    "confidence_band": confidence_band,
                    "coverage_pct": path.coverage_pct,
                },
            )
        ],
    )
```

- [ ] **Step 4: Update consumer.py to use render_spec and pass confidence_band**

In `SpecGeneratorConsumer.handle`:
```python
spec_text, confidence_band = render_spec(path, entrypoint_name)
await self._store.upsert_spec(path, spec_text)
await self._store.upsert_qdrant(path, entrypoint_name, spec_text, confidence_band)
```

Remove the `render_spec_text` import.

- [ ] **Step 5: Run tests**

```bash
cd services/spec-generator
pytest tests/ -v
```

- [ ] **Step 6: Commit**

```bash
git add services/spec-generator/app/store.py services/spec-generator/app/consumer.py
git commit -m "feat(spec-generator): add confidence_band and coverage_pct to Qdrant payload"
```

---

## Task 10: sys.settrace stdlib/site-packages filter

**Files:**
- Modify: `services/instrumenter/app/trace.py`

- [ ] **Step 1: Write failing test**

```python
# append to services/instrumenter/tests/test_trace.py (or create)
import sysconfig
from app.trace import create_trace_func, TraceSession
import tempfile, os

def _make_session():
    td = tempfile.mkdtemp()
    return TraceSession(
        session_id="test-001",
        file_path="test_module.py",
        repo="test",
        stable_id="sha256:fn_test",
        tempdir=td,
    )


def test_stdlib_frames_are_excluded():
    """Frames from stdlib should return None (subtree pruning), not trace_func."""
    session = _make_session()
    trace_func = create_trace_func(session)

    stdlib_prefix = sysconfig.get_path("stdlib")
    stdlib_file = os.path.join(stdlib_prefix, "importlib", "__init__.py")

    class FakeFrame:
        class f_code:
            co_filename = stdlib_file
            co_name = "something"
            co_firstlineno = 1
        f_lineno = 1
        f_locals = {}

    result = trace_func(FakeFrame(), "call", None)
    assert result is None, f"Expected None for stdlib frame, got {result!r}"
    assert len(session.events) == 0, "No events should be emitted for stdlib frames"


def test_user_code_frames_are_traced():
    """Frames from user code should return trace_func (continue tracing)."""
    session = _make_session()
    trace_func = create_trace_func(session)

    class FakeFrame:
        class f_code:
            co_filename = "/repos/myproject/app/auth.py"
            co_name = "authenticate"
            co_firstlineno = 10
        f_lineno = 10
        f_locals = {}

    result = trace_func(FakeFrame(), "call", None)
    assert result is trace_func
    assert len(session.events) == 1
```

- [ ] **Step 2: Run to verify failures**

```bash
cd services/instrumenter
pytest tests/test_trace.py::test_stdlib_frames_are_excluded \
       tests/test_trace.py::test_user_code_frames_are_traced -v
```

Expected: `test_stdlib_frames_are_excluded` FAIL (currently returns `trace_func`, not `None`). `test_user_code_frames_are_traced` may already pass.

- [ ] **Step 3: Implement the filter in trace.py**

At the top of `services/instrumenter/app/trace.py`, below the existing imports, add:

```python
import sysconfig as _sysconfig

# Full absolute path prefixes for stdlib and installed packages.
# Returning None from trace_func prunes CPython's trace for the entire
# subtree below that frame — no events emitted, significant overhead saved.
_EXCLUDE_PREFIXES: tuple[str, ...] = tuple(
    p for p in (
        _sysconfig.get_path("stdlib"),    # /usr/lib/python3.12
        _sysconfig.get_path("purelib"),   # /usr/local/lib/python3.12/dist-packages
        _sysconfig.get_path("platlib"),   # platform-specific site-packages
    )
    if p is not None
)
```

In `create_trace_func`, inside `trace_func`, add the exclude check as the **first** check before the `event` check:

```python
def trace_func(frame, event, arg):
    # Prune stdlib/site-packages subtrees immediately.
    # Returning None stops CPython from calling trace_func for any
    # descendant frame — this is the critical subtree-pruning behavior.
    filename = frame.f_code.co_filename
    if filename.startswith(_EXCLUDE_PREFIXES):
        return None

    if event not in ("call", "return", "exception"):
        return trace_func

    # ... rest of existing code unchanged
```

- [ ] **Step 4: Run tests**

```bash
cd services/instrumenter
pytest tests/test_trace.py -v
```

Expected: all pass including the two new tests.

- [ ] **Step 5: Run full instrumenter test suite**

```bash
pytest tests/ -v
```

- [ ] **Step 6: Commit**

```bash
git add services/instrumenter/app/trace.py services/instrumenter/tests/test_trace.py
git commit -m "perf(instrumenter): prune stdlib/site-packages subtrees from sys.settrace using sysconfig paths"
```

---

## Definition of Done Checklist

- [ ] `shared/` package installable as `pip install -e services/shared`
- [ ] graph-enricher, spec-generator, trace-normalizer use `RedisConsumerBase` (XREADGROUP COUNT=50)
- [ ] trace-runner, entrypoint-discoverer, instrumenter use `ServiceBase` only
- [ ] Each migrated service's unique logic lives in its own class/module; main.py is wiring only
- [ ] graph-enricher batch functions called once per 50-event loop iteration
- [ ] parser `extractor.py` and `extractor_go.py` decomposed into 3 focused modules each
- [ ] perf-tracker `collector.py` decomposed, `aggregator.py` extracted
- [ ] go-instrumenter `rewriter.go` decomposed into `passes/` package
- [ ] `spec_text` PATH header includes run count, confidence band, and coverage %
- [ ] LOW confidence specs include `⚠ LOW COVERAGE` warning line
- [ ] Each call sequence item has `[confirmed]` or `[dynamic-only]` tag
- [ ] `never_observed_static_edges` rendered as `⚠ static-only` warnings
- [ ] Qdrant payload includes `confidence_band` and `coverage_pct`
- [ ] `sys.settrace` uses `sysconfig`-derived prefixes; stdlib frames return `None`
- [ ] All existing unit and integration tests pass
