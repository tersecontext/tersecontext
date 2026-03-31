# Perf-Tracker Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a FastAPI service that consumes existing pipeline streams to detect bottlenecks in both the pipeline and traced user code, exposing results via API and CLI.

**Architecture:** Single FastAPI service (port 8098) with a background async collector consuming `stream:raw-traces` and `stream:execution-paths`. Stores real-time snapshots in Redis, historical metrics in Postgres. Analyzer module detects bottlenecks. API router exposes query endpoints. Standalone CLI script hits the API.

**Tech Stack:** FastAPI, uvicorn, redis.asyncio, asyncpg, pydantic, httpx, pytest, pytest-asyncio

**Spec:** `docs/superpowers/specs/2026-03-30-perf-tracker-design.md`

---

## File Structure

```
services/perf-tracker/
├── app/
│   ├── __init__.py          # empty
│   ├── main.py              # FastAPI app, lifespan, /health /ready /metrics
│   ├── models.py            # All Pydantic models (input events, metrics, bottlenecks, API responses)
│   ├── collector.py         # Background stream consumer — extracts metrics from both streams
│   ├── analyzer.py          # Bottleneck detection, regression comparison, trend analysis
│   ├── store.py             # PerfStore: Postgres pool + Redis — read/write metrics
│   └── api.py               # APIRouter with /api/v1/* query endpoints
├── cli.py                   # Standalone CLI (httpx only, no app/ imports)
├── tests/
│   ├── __init__.py          # empty
│   ├── conftest.py          # Shared fixtures: mock store, mock redis, sample data
│   ├── test_models.py       # Model validation tests
│   ├── test_store.py        # Store read/write with mocked Postgres/Redis
│   ├── test_collector.py    # Metric extraction from stream messages
│   ├── test_analyzer.py     # Bottleneck detection logic
│   ├── test_api.py          # FastAPI TestClient endpoint tests
│   └── test_cli.py          # CLI output formatting with mocked httpx
├── Dockerfile               # Python 3.12-slim + uv + uvicorn on port 8080
├── requirements.txt         # Pinned dependencies
└── pytest.ini               # asyncio_mode = "auto"
```

---

### Task 1: Project Scaffolding

**Files:**
- Create: `services/perf-tracker/app/__init__.py`
- Create: `services/perf-tracker/tests/__init__.py`
- Create: `services/perf-tracker/requirements.txt`
- Create: `services/perf-tracker/Dockerfile`
- Create: `services/perf-tracker/pytest.ini`

- [ ] **Step 1: Create requirements.txt**

```
fastapi>=0.115.0
uvicorn[standard]>=0.30.0
redis>=5.0.0
pydantic>=2.0.0
httpx>=0.27.0
asyncpg>=0.29.0
pytest>=8.0.0
pytest-asyncio>=0.23.0
```

- [ ] **Step 2: Create Dockerfile**

```dockerfile
FROM python:3.12-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    curl \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir uv

COPY requirements.txt ./
RUN uv pip install --system -r requirements.txt

COPY app/ app/

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080"]
```

- [ ] **Step 3: Create pytest.ini**

```ini
[pytest]
asyncio_mode = auto
```

- [ ] **Step 4: Create empty __init__.py files**

Create empty files at:
- `services/perf-tracker/app/__init__.py`
- `services/perf-tracker/tests/__init__.py`

- [ ] **Step 5: Commit**

```bash
git add services/perf-tracker/
git commit -m "feat(perf-tracker): scaffold project structure"
```

---

### Task 2: Pydantic Models

**Files:**
- Create: `services/perf-tracker/app/models.py`
- Create: `services/perf-tracker/tests/test_models.py`

- [ ] **Step 1: Write the failing test**

```python
# services/perf-tracker/tests/test_models.py
import pytest
from datetime import datetime, timezone


def test_trace_event_from_dict():
    from app.models import TraceEvent
    e = TraceEvent(type="call", fn="authenticate", file="auth.py", line=34, timestamp_ms=0.0)
    assert e.fn == "authenticate"
    assert e.type == "call"


def test_raw_trace_from_dict():
    from app.models import RawTrace
    rt = RawTrace(
        entrypoint_stable_id="sha256:abc",
        commit_sha="a4f91c",
        repo="test",
        duration_ms=284.0,
        events=[
            {"type": "call", "fn": "auth", "file": "a.py", "line": 1, "timestamp_ms": 0.0},
            {"type": "return", "fn": "auth", "file": "a.py", "line": 2, "timestamp_ms": 28.0},
        ],
    )
    assert rt.repo == "test"
    assert len(rt.events) == 2


def test_call_sequence_item():
    from app.models import CallSequenceItem
    item = CallSequenceItem(
        stable_id="sha256:fn", name="auth", qualified_name="mod.auth",
        hop=1, frequency_ratio=0.95, avg_ms=12.5,
    )
    assert item.hop == 1


def test_execution_path_from_dict():
    from app.models import ExecutionPath
    ep = ExecutionPath(
        entrypoint_stable_id="sha256:abc",
        commit_sha="a4f91c",
        repo="test",
        call_sequence=[],
        side_effects=[],
        dynamic_only_edges=[],
        never_observed_static_edges=[],
        timing_p50_ms=10.0,
        timing_p99_ms=50.0,
    )
    assert ep.repo == "test"


def test_perf_metric_model():
    from app.models import PerfMetric
    m = PerfMetric(
        metric_type="pipeline", metric_name="stream_throughput",
        entity_id="stream:raw-traces", repo="test",
        value=42.5, unit="msg/s",
    )
    assert m.metric_type == "pipeline"


def test_bottleneck_model():
    from app.models import Bottleneck
    b = Bottleneck(
        entity_id="stream:raw-traces", entity_name="raw-traces stream",
        category="pipeline", severity="warning",
        value=15.2, unit="s", detail="Processing lag exceeds threshold",
        repo="test", detected_at=datetime.now(timezone.utc),
    )
    assert b.severity == "warning"


def test_execution_path_ignores_extra_fields():
    from app.models import ExecutionPath
    ep = ExecutionPath(
        entrypoint_stable_id="sha256:abc",
        commit_sha="a4f91c",
        repo="test",
        call_sequence=[], side_effects=[],
        dynamic_only_edges=[], never_observed_static_edges=[],
        timing_p50_ms=10.0, timing_p99_ms=50.0,
        some_future_field="ignored",
    )
    assert not hasattr(ep, "some_future_field")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd services/perf-tracker && python -m pytest tests/test_models.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.models'`

- [ ] **Step 3: Write the models**

```python
# services/perf-tracker/app/models.py
from __future__ import annotations
from datetime import datetime
from typing import Literal, Optional
from pydantic import BaseModel, ConfigDict


# --- Input events (consumed from streams) ---

class TraceEvent(BaseModel):
    type: Literal["call", "return", "exception"]
    fn: str
    file: str
    line: int
    timestamp_ms: float
    exc_type: Optional[str] = None


class RawTrace(BaseModel):
    model_config = ConfigDict(extra="ignore")

    entrypoint_stable_id: str
    commit_sha: str
    repo: str
    duration_ms: float
    events: list[TraceEvent]


class CallSequenceItem(BaseModel):
    stable_id: str
    name: str
    qualified_name: str
    hop: int
    frequency_ratio: float
    avg_ms: float


class SideEffect(BaseModel):
    type: Literal["db_read", "db_write", "cache_read", "cache_set", "http_out", "fs_write"]
    detail: str
    hop_depth: int


class EdgeRef(BaseModel):
    source: str
    target: str


class ExecutionPath(BaseModel):
    model_config = ConfigDict(extra="ignore")

    entrypoint_stable_id: str
    commit_sha: str
    repo: str
    call_sequence: list[CallSequenceItem]
    side_effects: list[SideEffect]
    dynamic_only_edges: list[EdgeRef]
    never_observed_static_edges: list[EdgeRef]
    timing_p50_ms: float
    timing_p99_ms: float


# --- Internal metrics ---

class PerfMetric(BaseModel):
    metric_type: Literal["pipeline", "user_code"]
    metric_name: str
    entity_id: str
    repo: str
    value: float
    unit: str
    commit_sha: Optional[str] = None


class Bottleneck(BaseModel):
    entity_id: str
    entity_name: str
    category: Literal["pipeline", "slow_fn", "regression", "hot_path", "deep_chain"]
    severity: Literal["critical", "warning", "info"]
    value: float
    unit: str
    detail: str
    repo: str
    detected_at: datetime
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd services/perf-tracker && python -m pytest tests/test_models.py -v`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add services/perf-tracker/app/models.py services/perf-tracker/tests/test_models.py
git commit -m "feat(perf-tracker): add Pydantic models for metrics and bottlenecks"
```

---

### Task 3: Store — Postgres + Redis

**Files:**
- Create: `services/perf-tracker/app/store.py`
- Create: `services/perf-tracker/tests/conftest.py`
- Create: `services/perf-tracker/tests/test_store.py`

- [ ] **Step 1: Write the failing tests**

```python
# services/perf-tracker/tests/conftest.py
import os
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import datetime, timezone

os.environ.setdefault("POSTGRES_DSN", "postgres://test:test@localhost/test")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379")


@pytest.fixture
def sample_perf_metrics():
    from app.models import PerfMetric
    return [
        PerfMetric(
            metric_type="pipeline", metric_name="stream_throughput",
            entity_id="stream:raw-traces", repo="test",
            value=42.5, unit="msg/s",
        ),
        PerfMetric(
            metric_type="user_code", metric_name="fn_duration",
            entity_id="sha256:fn_auth", repo="test",
            value=120.0, unit="ms", commit_sha="abc123",
        ),
    ]


@pytest.fixture
def sample_raw_trace_data():
    """Raw trace as it appears in a Redis stream message (bytes dict)."""
    import json
    return {
        b"event": json.dumps({
            "entrypoint_stable_id": "sha256:fn_test_login",
            "commit_sha": "a4f91c",
            "repo": "test",
            "duration_ms": 284.0,
            "events": [
                {"type": "call", "fn": "authenticate", "file": "auth.py", "line": 34, "timestamp_ms": 0.0},
                {"type": "return", "fn": "authenticate", "file": "auth.py", "line": 52, "timestamp_ms": 28.0},
                {"type": "call", "fn": "get_user", "file": "auth.py", "line": 60, "timestamp_ms": 30.0},
                {"type": "return", "fn": "get_user", "file": "auth.py", "line": 75, "timestamp_ms": 55.0},
            ],
        }).encode()
    }


@pytest.fixture
def sample_execution_path_data():
    """Execution path as it appears in a Redis stream message (bytes dict)."""
    import json
    return {
        b"event": json.dumps({
            "entrypoint_stable_id": "sha256:fn_test_login",
            "commit_sha": "a4f91c",
            "repo": "test",
            "call_sequence": [
                {"stable_id": "sha256:auth", "name": "authenticate", "qualified_name": "auth.authenticate", "hop": 0, "frequency_ratio": 1.0, "avg_ms": 28.0},
                {"stable_id": "sha256:get_user", "name": "get_user", "qualified_name": "auth.get_user", "hop": 1, "frequency_ratio": 0.9, "avg_ms": 25.0},
            ],
            "side_effects": [
                {"type": "db_read", "detail": "SELECT * FROM users", "hop_depth": 1},
            ],
            "dynamic_only_edges": [],
            "never_observed_static_edges": [],
            "timing_p50_ms": 50.0,
            "timing_p99_ms": 200.0,
        }).encode()
    }


@pytest.fixture
def mock_redis():
    m = AsyncMock()
    m.ping.return_value = True
    m.hset = AsyncMock()
    m.hgetall = AsyncMock(return_value={})
    m.zadd = AsyncMock()
    m.zrevrangebyscore = AsyncMock(return_value=[])
    m.zrevrange = AsyncMock(return_value=[])
    m.delete = AsyncMock()
    m.rpush = AsyncMock()
    m.lrange = AsyncMock(return_value=[])
    m.expire = AsyncMock()
    m.xlen = AsyncMock(return_value=0)
    m.llen = AsyncMock(return_value=0)
    return m


@pytest.fixture
def mock_pg_pool():
    pool = AsyncMock()
    conn = AsyncMock()
    conn.execute = AsyncMock()
    conn.fetch = AsyncMock(return_value=[])
    conn.fetchval = AsyncMock(return_value=1)
    # Make pool.acquire() work as async context manager
    pool.acquire = MagicMock(return_value=pool)
    pool.__aenter__ = AsyncMock(return_value=conn)
    pool.__aexit__ = AsyncMock(return_value=False)
    pool._conn = conn  # expose for assertions
    return pool


@pytest.fixture
def mock_store(mock_redis, mock_pg_pool):
    from app.store import PerfStore
    store = PerfStore.__new__(PerfStore)
    store._redis = mock_redis
    store._pool = mock_pg_pool
    store._pool_lock = __import__("asyncio").Lock()
    store._postgres_dsn = "postgres://test:test@localhost/test"
    return store
```

```python
# services/perf-tracker/tests/test_store.py
import pytest
from unittest.mock import AsyncMock, MagicMock
from datetime import datetime, timezone


async def test_ensure_schema(mock_store, mock_pg_pool):
    # Mock transaction context manager
    mock_pg_pool._conn.transaction = MagicMock(return_value=mock_pg_pool._conn)
    mock_pg_pool._conn.__aenter__ = AsyncMock(return_value=mock_pg_pool._conn)
    mock_pg_pool._conn.__aexit__ = AsyncMock(return_value=False)
    await mock_store.ensure_schema()
    conn = mock_pg_pool._conn
    conn.execute.assert_called_once()
    sql = conn.execute.call_args[0][0]
    assert "perf_metrics" in sql
    assert "CREATE TABLE" in sql


async def test_write_metrics(mock_store, mock_pg_pool, sample_perf_metrics):
    await mock_store.write_metrics(sample_perf_metrics)
    conn = mock_pg_pool._conn
    assert conn.executemany.called
    args = conn.executemany.call_args
    assert "INSERT INTO perf_metrics" in args[0][0]
    assert len(args[0][1]) == 2


async def test_update_realtime(mock_store, mock_redis):
    snapshot = {"throughput_raw_traces": "42.5", "queue_depth_raw_traces": "10"}
    await mock_store.update_realtime("test", snapshot)
    mock_redis.delete.assert_called_once_with("perf:realtime:test")
    mock_redis.hset.assert_called_once_with("perf:realtime:test", mapping=snapshot)


async def test_update_hot_functions(mock_store, mock_redis):
    fns = {"sha256:fn_auth": 120.0, "sha256:fn_login": 85.0}
    await mock_store.update_hot_functions("test", fns)
    mock_redis.delete.assert_called()
    mock_redis.zadd.assert_called_once_with("perf:hotfns:test", fns)


async def test_get_realtime(mock_store, mock_redis):
    mock_redis.hgetall.return_value = {b"throughput": b"42.5"}
    result = await mock_store.get_realtime("test")
    assert result == {"throughput": "42.5"}


async def test_get_slow_functions(mock_store, mock_pg_pool):
    mock_row = {"entity_id": "sha256:fn", "value": 120.0, "unit": "ms", "commit_sha": "abc"}
    mock_pg_pool._conn.fetch.return_value = [mock_row]
    rows = await mock_store.get_slow_functions("test", limit=10)
    assert len(rows) == 1
    assert rows[0]["value"] == 120.0


async def test_get_trends(mock_store, mock_pg_pool):
    mock_pg_pool._conn.fetch.return_value = [
        {"value": 100.0, "recorded_at": datetime(2026, 1, 1, tzinfo=timezone.utc)},
        {"value": 110.0, "recorded_at": datetime(2026, 1, 2, tzinfo=timezone.utc)},
    ]
    rows = await mock_store.get_trends("test", "sha256:fn", "fn_duration", window_hours=24)
    assert len(rows) == 2
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd services/perf-tracker && python -m pytest tests/test_store.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.store'`

- [ ] **Step 3: Write the store**

```python
# services/perf-tracker/app/store.py
from __future__ import annotations
import asyncio
import logging

import asyncpg
import redis.asyncio as aioredis

from app.models import PerfMetric

logger = logging.getLogger(__name__)

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS perf_metrics (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    metric_type     TEXT NOT NULL,
    metric_name     TEXT NOT NULL,
    entity_id       TEXT NOT NULL,
    repo            TEXT NOT NULL,
    value           FLOAT NOT NULL,
    unit            TEXT NOT NULL,
    commit_sha      TEXT,
    recorded_at     TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS perf_metrics_entity_repo_idx
    ON perf_metrics (entity_id, repo, recorded_at);
CREATE INDEX IF NOT EXISTS perf_metrics_type_name_idx
    ON perf_metrics (metric_type, metric_name, recorded_at);
"""

INSERT_SQL = """
INSERT INTO perf_metrics (metric_type, metric_name, entity_id, repo, value, unit, commit_sha)
VALUES ($1, $2, $3, $4, $5, $6, $7)
"""


class PerfStore:
    def __init__(self, postgres_dsn: str, redis_client: aioredis.Redis) -> None:
        self._postgres_dsn = postgres_dsn
        self._redis = redis_client
        self._pool: asyncpg.Pool | None = None
        self._pool_lock = asyncio.Lock()

    async def _get_pool(self) -> asyncpg.Pool:
        if self._pool is None:
            async with self._pool_lock:
                if self._pool is None:
                    self._pool = await asyncpg.create_pool(self._postgres_dsn)
        return self._pool

    async def ensure_schema(self) -> None:
        pool = await self._get_pool()
        async with pool.acquire() as conn:
            async with conn.transaction():
                await conn.execute(SCHEMA_SQL)

    async def write_metrics(self, metrics: list[PerfMetric]) -> None:
        if not metrics:
            return
        pool = await self._get_pool()
        rows = [
            (m.metric_type, m.metric_name, m.entity_id, m.repo, m.value, m.unit, m.commit_sha)
            for m in metrics
        ]
        async with pool.acquire() as conn:
            await conn.executemany(INSERT_SQL, rows)

    async def update_realtime(self, repo: str, snapshot: dict[str, str]) -> None:
        key = f"perf:realtime:{repo}"
        await self._redis.delete(key)
        await self._redis.hset(key, mapping=snapshot)

    async def update_hot_functions(self, repo: str, fn_scores: dict[str, float]) -> None:
        key = f"perf:hotfns:{repo}"
        await self._redis.delete(key)
        if fn_scores:
            await self._redis.zadd(key, fn_scores)

    async def update_bottlenecks(self, repo: str, bottlenecks_json: list[str]) -> None:
        key = f"perf:bottlenecks:{repo}"
        await self._redis.delete(key)
        if bottlenecks_json:
            await self._redis.rpush(key, *bottlenecks_json)
            await self._redis.expire(key, 60)

    async def get_realtime(self, repo: str) -> dict[str, str]:
        raw = await self._redis.hgetall(f"perf:realtime:{repo}")
        return {
            (k.decode() if isinstance(k, bytes) else k): (v.decode() if isinstance(v, bytes) else v)
            for k, v in raw.items()
        }

    async def get_hot_functions(self, repo: str, limit: int = 20) -> list[tuple[str, float]]:
        raw = await self._redis.zrevrange(f"perf:hotfns:{repo}", 0, limit - 1, withscores=True)
        return [
            (name.decode() if isinstance(name, bytes) else name, score)
            for name, score in raw
        ]

    async def get_bottlenecks_json(self, repo: str) -> list[str]:
        raw = await self._redis.lrange(f"perf:bottlenecks:{repo}", 0, -1)
        return [v.decode() if isinstance(v, bytes) else v for v in raw]

    async def get_slow_functions(
        self, repo: str, limit: int = 20, commit_sha: str | None = None,
    ) -> list[dict]:
        pool = await self._get_pool()
        sql = """
            SELECT entity_id, value, unit, commit_sha
            FROM perf_metrics
            WHERE repo = $1 AND metric_type = 'user_code' AND metric_name = 'fn_duration'
        """
        params: list = [repo]
        if commit_sha:
            sql += " AND commit_sha = $2"
            params.append(commit_sha)
        sql += " ORDER BY value DESC LIMIT $" + str(len(params) + 1)
        params.append(limit)
        async with pool.acquire() as conn:
            rows = await conn.fetch(sql, *params)
        return [dict(r) for r in rows]

    async def get_trends(
        self, repo: str, entity_id: str, metric_name: str, window_hours: int = 24,
    ) -> list[dict]:
        pool = await self._get_pool()
        sql = """
            SELECT value, recorded_at
            FROM perf_metrics
            WHERE repo = $1 AND entity_id = $2 AND metric_name = $3
              AND recorded_at > NOW() - make_interval(hours => $4)
            ORDER BY recorded_at
        """
        async with pool.acquire() as conn:
            rows = await conn.fetch(sql, repo, entity_id, metric_name, window_hours)
        return [dict(r) for r in rows]

    async def get_regressions(
        self, repo: str, base_sha: str, head_sha: str, threshold_pct: float = 20.0,
    ) -> list[dict]:
        pool = await self._get_pool()
        sql = """
            WITH base AS (
                SELECT entity_id, AVG(value) as avg_val
                FROM perf_metrics
                WHERE repo = $1 AND commit_sha = $2
                  AND metric_type = 'user_code' AND metric_name = 'fn_duration'
                GROUP BY entity_id
            ), head AS (
                SELECT entity_id, AVG(value) as avg_val
                FROM perf_metrics
                WHERE repo = $1 AND commit_sha = $3
                  AND metric_type = 'user_code' AND metric_name = 'fn_duration'
                GROUP BY entity_id
            )
            SELECT h.entity_id, b.avg_val as base_val, h.avg_val as head_val,
                   ((h.avg_val - b.avg_val) / NULLIF(b.avg_val, 0)) * 100 as pct_change
            FROM head h
            JOIN base b ON h.entity_id = b.entity_id
            WHERE ((h.avg_val - b.avg_val) / NULLIF(b.avg_val, 0)) * 100 > $4
            ORDER BY pct_change DESC
        """
        async with pool.acquire() as conn:
            rows = await conn.fetch(sql, repo, base_sha, head_sha, threshold_pct)
        return [dict(r) for r in rows]

    async def get_summary_counts(self, repo: str) -> dict:
        pool = await self._get_pool()
        sql = """
            SELECT metric_type, COUNT(*) as cnt
            FROM perf_metrics WHERE repo = $1
            GROUP BY metric_type
        """
        async with pool.acquire() as conn:
            rows = await conn.fetch(sql, repo)
        return {r["metric_type"]: r["cnt"] for r in rows}

    async def close(self) -> None:
        if self._pool:
            await self._pool.close()
            self._pool = None
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd services/perf-tracker && python -m pytest tests/test_store.py -v`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add services/perf-tracker/app/store.py services/perf-tracker/tests/conftest.py services/perf-tracker/tests/test_store.py
git commit -m "feat(perf-tracker): add PerfStore with Postgres + Redis read/write"
```

---

### Task 4: Collector — Stream Consumer

**Files:**
- Create: `services/perf-tracker/app/collector.py`
- Create: `services/perf-tracker/tests/test_collector.py`

- [ ] **Step 1: Write the failing tests**

```python
# services/perf-tracker/tests/test_collector.py
import pytest
import json


def test_extract_function_durations_from_raw_trace():
    from app.collector import extract_metrics_from_raw_trace
    raw = {
        "entrypoint_stable_id": "sha256:fn_test_login",
        "commit_sha": "a4f91c",
        "repo": "test",
        "duration_ms": 284.0,
        "events": [
            {"type": "call", "fn": "authenticate", "file": "auth.py", "line": 34, "timestamp_ms": 0.0},
            {"type": "return", "fn": "authenticate", "file": "auth.py", "line": 52, "timestamp_ms": 28.0},
            {"type": "call", "fn": "get_user", "file": "auth.py", "line": 60, "timestamp_ms": 30.0},
            {"type": "return", "fn": "get_user", "file": "auth.py", "line": 75, "timestamp_ms": 55.0},
        ],
    }
    metrics = extract_metrics_from_raw_trace(raw)
    fn_durations = [m for m in metrics if m.metric_name == "fn_duration"]
    assert len(fn_durations) == 2
    auth_m = next(m for m in fn_durations if "authenticate" in m.entity_id)
    assert auth_m.value == 28.0
    assert auth_m.unit == "ms"
    assert auth_m.commit_sha == "a4f91c"

    ep_duration = [m for m in metrics if m.metric_name == "entrypoint_duration"]
    assert len(ep_duration) == 1
    assert ep_duration[0].value == 284.0


def test_extract_function_durations_unmatched_return():
    """A return without a preceding call should be skipped gracefully."""
    from app.collector import extract_metrics_from_raw_trace
    raw = {
        "entrypoint_stable_id": "sha256:x",
        "commit_sha": "abc",
        "repo": "test",
        "duration_ms": 10.0,
        "events": [
            {"type": "return", "fn": "orphan", "file": "x.py", "line": 1, "timestamp_ms": 5.0},
        ],
    }
    metrics = extract_metrics_from_raw_trace(raw)
    fn_durations = [m for m in metrics if m.metric_name == "fn_duration"]
    assert len(fn_durations) == 0


def test_extract_metrics_from_execution_path():
    from app.collector import extract_metrics_from_execution_path
    path = {
        "entrypoint_stable_id": "sha256:fn_test_login",
        "commit_sha": "a4f91c",
        "repo": "test",
        "call_sequence": [
            {"stable_id": "sha256:auth", "name": "authenticate", "qualified_name": "auth.authenticate", "hop": 0, "frequency_ratio": 1.0, "avg_ms": 28.0},
            {"stable_id": "sha256:get_user", "name": "get_user", "qualified_name": "auth.get_user", "hop": 1, "frequency_ratio": 0.9, "avg_ms": 25.0},
        ],
        "side_effects": [
            {"type": "db_read", "detail": "SELECT * FROM users", "hop_depth": 1},
        ],
        "dynamic_only_edges": [],
        "never_observed_static_edges": [],
        "timing_p50_ms": 50.0,
        "timing_p99_ms": 200.0,
    }
    metrics = extract_metrics_from_execution_path(path)
    call_depth = [m for m in metrics if m.metric_name == "call_depth"]
    assert len(call_depth) == 1
    assert call_depth[0].value == 2  # max hop + 1

    side_effect_count = [m for m in metrics if m.metric_name == "side_effect_count"]
    assert len(side_effect_count) == 1
    assert side_effect_count[0].value == 1

    p50 = [m for m in metrics if m.metric_name == "timing_p50"]
    assert len(p50) == 1
    assert p50[0].value == 50.0


def test_extract_call_frequency():
    from app.collector import extract_metrics_from_raw_trace
    raw = {
        "entrypoint_stable_id": "sha256:ep",
        "commit_sha": "abc",
        "repo": "test",
        "duration_ms": 100.0,
        "events": [
            {"type": "call", "fn": "foo", "file": "a.py", "line": 1, "timestamp_ms": 0.0},
            {"type": "return", "fn": "foo", "file": "a.py", "line": 2, "timestamp_ms": 10.0},
            {"type": "call", "fn": "foo", "file": "a.py", "line": 1, "timestamp_ms": 20.0},
            {"type": "return", "fn": "foo", "file": "a.py", "line": 2, "timestamp_ms": 30.0},
        ],
    }
    metrics = extract_metrics_from_raw_trace(raw)
    freq = [m for m in metrics if m.metric_name == "call_frequency"]
    foo_freq = next(m for m in freq if "foo" in m.entity_id)
    assert foo_freq.value == 2
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd services/perf-tracker && python -m pytest tests/test_collector.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.collector'`

- [ ] **Step 3: Write the collector**

```python
# services/perf-tracker/app/collector.py
from __future__ import annotations
import asyncio
import json
import logging
import os
import socket
import time

import redis.asyncio as aioredis
import redis.exceptions
from pydantic import ValidationError

from app.models import PerfMetric, RawTrace, ExecutionPath
from app.store import PerfStore

logger = logging.getLogger(__name__)

STREAM_RAW_TRACES = "stream:raw-traces"
STREAM_EXECUTION_PATHS = "stream:execution-paths"
GROUP_TRACES = "perf-tracker-traces"
GROUP_PATHS = "perf-tracker-paths"

messages_processed_total: int = 0
messages_failed_total: int = 0
metrics_written_total: int = 0


def extract_metrics_from_raw_trace(data: dict) -> list[PerfMetric]:
    metrics: list[PerfMetric] = []
    repo = data.get("repo", "")
    commit_sha = data.get("commit_sha")
    entrypoint_id = data.get("entrypoint_stable_id", "")
    duration_ms = data.get("duration_ms", 0.0)
    events = data.get("events", [])

    # Entrypoint total duration
    metrics.append(PerfMetric(
        metric_type="user_code", metric_name="entrypoint_duration",
        entity_id=entrypoint_id, repo=repo,
        value=duration_ms, unit="ms", commit_sha=commit_sha,
    ))

    # Per-function duration from call/return pairs
    call_stack: list[dict] = []
    fn_durations: dict[str, list[float]] = {}
    fn_counts: dict[str, int] = {}

    for event in events:
        fn_key = f"{event['file']}:{event['fn']}"
        if event["type"] == "call":
            call_stack.append(event)
            fn_counts[fn_key] = fn_counts.get(fn_key, 0) + 1
        elif event["type"] == "return" and call_stack:
            # Match with most recent call of same function
            for i in range(len(call_stack) - 1, -1, -1):
                if call_stack[i]["fn"] == event["fn"]:
                    call_event = call_stack.pop(i)
                    duration = event["timestamp_ms"] - call_event["timestamp_ms"]
                    fn_durations.setdefault(fn_key, []).append(duration)
                    break

    for fn_key, durations in fn_durations.items():
        avg = sum(durations) / len(durations)
        metrics.append(PerfMetric(
            metric_type="user_code", metric_name="fn_duration",
            entity_id=fn_key, repo=repo,
            value=avg, unit="ms", commit_sha=commit_sha,
        ))

    for fn_key, count in fn_counts.items():
        metrics.append(PerfMetric(
            metric_type="user_code", metric_name="call_frequency",
            entity_id=fn_key, repo=repo,
            value=count, unit="count", commit_sha=commit_sha,
        ))

    return metrics


def extract_metrics_from_execution_path(data: dict) -> list[PerfMetric]:
    metrics: list[PerfMetric] = []
    repo = data.get("repo", "")
    commit_sha = data.get("commit_sha")
    entrypoint_id = data.get("entrypoint_stable_id", "")
    call_sequence = data.get("call_sequence", [])
    side_effects = data.get("side_effects", [])

    # Call depth
    max_hop = max((item["hop"] for item in call_sequence), default=-1)
    metrics.append(PerfMetric(
        metric_type="user_code", metric_name="call_depth",
        entity_id=entrypoint_id, repo=repo,
        value=max_hop + 1, unit="count", commit_sha=commit_sha,
    ))

    # Side effect count
    metrics.append(PerfMetric(
        metric_type="user_code", metric_name="side_effect_count",
        entity_id=entrypoint_id, repo=repo,
        value=len(side_effects), unit="count", commit_sha=commit_sha,
    ))

    # Timing percentiles
    timing_p50 = data.get("timing_p50_ms", 0.0)
    timing_p99 = data.get("timing_p99_ms", 0.0)
    metrics.append(PerfMetric(
        metric_type="user_code", metric_name="timing_p50",
        entity_id=entrypoint_id, repo=repo,
        value=timing_p50, unit="ms", commit_sha=commit_sha,
    ))
    metrics.append(PerfMetric(
        metric_type="user_code", metric_name="timing_p99",
        entity_id=entrypoint_id, repo=repo,
        value=timing_p99, unit="ms", commit_sha=commit_sha,
    ))

    return metrics


async def _collect_pipeline_metrics(redis_client: aioredis.Redis) -> list[PerfMetric]:
    metrics: list[PerfMetric] = []
    for stream in [STREAM_RAW_TRACES, STREAM_EXECUTION_PATHS]:
        depth = await redis_client.xlen(stream)
        metrics.append(PerfMetric(
            metric_type="pipeline", metric_name="queue_depth",
            entity_id=stream, repo="_pipeline",
            value=depth, unit="count",
        ))
    return metrics


async def run_collector(store: PerfStore, redis_client: aioredis.Redis) -> None:
    global messages_processed_total, messages_failed_total, metrics_written_total

    consumer_name = f"perf-tracker-{socket.gethostname()}"
    realtime_interval = int(os.environ.get("REALTIME_INTERVAL_S", "5"))
    history_interval = int(os.environ.get("HISTORY_INTERVAL_S", "60"))

    # Create consumer groups
    for stream, group in [
        (STREAM_RAW_TRACES, GROUP_TRACES),
        (STREAM_EXECUTION_PATHS, GROUP_PATHS),
    ]:
        try:
            await redis_client.xgroup_create(stream, group, id="0", mkstream=True)
        except redis.exceptions.ResponseError as exc:
            if "BUSYGROUP" not in str(exc):
                raise

    logger.info("Collector started: consumer=%s", consumer_name)

    pending_metrics: list[PerfMetric] = []
    last_realtime = 0.0
    last_history = 0.0
    trace_timestamps: dict[str, float] = {}  # (repo, stable_id) -> stream msg timestamp
    _MAX_TRACE_TIMESTAMPS = 10000  # Bound to prevent memory leak
    throughput_counts: dict[str, list[float]] = {}  # stream -> list of arrival timestamps

    while True:
        try:
            now = time.monotonic()

            # Read from both streams simultaneously
            messages = await redis_client.xreadgroup(
                groupname=GROUP_TRACES,
                consumername=consumer_name,
                streams={STREAM_RAW_TRACES: ">"},
                count=10,
                block=500,
            )
            for _stream, events in (messages or []):
                for msg_id, data in events:
                    try:
                        raw = data.get(b"event") or data.get("event")
                        if raw is None:
                            raise KeyError("missing 'event' key")
                        if isinstance(raw, bytes):
                            raw = raw.decode()
                        parsed = json.loads(raw)
                        new_metrics = extract_metrics_from_raw_trace(parsed)
                        pending_metrics.extend(new_metrics)
                        # Track timestamp for lag computation
                        ts_ms = int(msg_id.decode().split("-")[0]) if isinstance(msg_id, bytes) else int(msg_id.split("-")[0])
                        repo = parsed.get("repo", "")
                        sid = parsed.get("entrypoint_stable_id", "")
                        trace_timestamps[f"{repo}:{sid}"] = ts_ms
                        # Evict oldest entries if bounded dict is full
                        if len(trace_timestamps) > _MAX_TRACE_TIMESTAMPS:
                            oldest_key = next(iter(trace_timestamps))
                            del trace_timestamps[oldest_key]
                        # Track arrival for throughput
                        throughput_counts.setdefault(STREAM_RAW_TRACES, []).append(time.monotonic())
                        await redis_client.xack(STREAM_RAW_TRACES, GROUP_TRACES, msg_id)
                        messages_processed_total += 1
                    except (json.JSONDecodeError, ValidationError, KeyError) as exc:
                        logger.warning("Bad raw-trace %s, skipping: %s", msg_id, exc)
                        messages_failed_total += 1
                        await redis_client.xack(STREAM_RAW_TRACES, GROUP_TRACES, msg_id)

            messages = await redis_client.xreadgroup(
                groupname=GROUP_PATHS,
                consumername=consumer_name,
                streams={STREAM_EXECUTION_PATHS: ">"},
                count=10,
                block=500,
            )
            for _stream, events in (messages or []):
                for msg_id, data in events:
                    try:
                        raw = data.get(b"event") or data.get("event")
                        if raw is None:
                            raise KeyError("missing 'event' key")
                        if isinstance(raw, bytes):
                            raw = raw.decode()
                        parsed = json.loads(raw)
                        new_metrics = extract_metrics_from_execution_path(parsed)
                        pending_metrics.extend(new_metrics)
                        # Compute processing lag
                        ts_ms = int(msg_id.decode().split("-")[0]) if isinstance(msg_id, bytes) else int(msg_id.split("-")[0])
                        repo = parsed.get("repo", "")
                        sid = parsed.get("entrypoint_stable_id", "")
                        key = f"{repo}:{sid}"
                        if key in trace_timestamps:
                            lag_ms = ts_ms - trace_timestamps.pop(key)
                            pending_metrics.append(PerfMetric(
                                metric_type="pipeline", metric_name="processing_lag",
                                entity_id="trace-normalizer", repo=repo,
                                value=lag_ms / 1000.0, unit="s",
                            ))
                        # Track arrival for throughput
                        throughput_counts.setdefault(STREAM_EXECUTION_PATHS, []).append(time.monotonic())
                        await redis_client.xack(STREAM_EXECUTION_PATHS, GROUP_PATHS, msg_id)
                        messages_processed_total += 1
                    except (json.JSONDecodeError, ValidationError, KeyError) as exc:
                        logger.warning("Bad execution-path %s, skipping: %s", msg_id, exc)
                        messages_failed_total += 1
                        await redis_client.xack(STREAM_EXECUTION_PATHS, GROUP_PATHS, msg_id)

            # Compute throughput (msg/s) from arrival timestamps in last 60s
            cutoff = time.monotonic() - 60.0
            for stream in [STREAM_RAW_TRACES, STREAM_EXECUTION_PATHS]:
                arrivals = throughput_counts.get(stream, [])
                arrivals[:] = [t for t in arrivals if t > cutoff]
                rate = len(arrivals) / 60.0 if arrivals else 0.0
                pending_metrics.append(PerfMetric(
                    metric_type="pipeline", metric_name="stream_throughput",
                    entity_id=stream, repo="_pipeline",
                    value=rate, unit="msg/s",
                ))

            # Pipeline metrics
            pipeline_metrics = await _collect_pipeline_metrics(redis_client)
            pending_metrics.extend(pipeline_metrics)

            # Real-time snapshot
            if now - last_realtime >= realtime_interval:
                repos = {m.repo for m in pending_metrics if m.repo != "_pipeline"}
                for repo in repos:
                    repo_metrics = [m for m in pending_metrics if m.repo == repo]
                    snapshot: dict[str, str] = {}
                    fn_scores: dict[str, float] = {}  # cumulative time: frequency x duration
                    fn_dur: dict[str, float] = {}
                    fn_freq: dict[str, float] = {}
                    for m in repo_metrics:
                        if m.metric_name == "fn_duration":
                            fn_dur[m.entity_id] = m.value
                        elif m.metric_name == "call_frequency":
                            fn_freq[m.entity_id] = m.value
                    for eid, dur in fn_dur.items():
                        freq = fn_freq.get(eid, 1.0)
                        fn_scores[eid] = dur * freq
                        snapshot[f"{m.metric_name}:{m.entity_id}"] = str(m.value)
                    # Add pipeline metrics
                    for m in pipeline_metrics:
                        snapshot[f"{m.metric_name}:{m.entity_id}"] = str(m.value)
                    await store.update_realtime(repo, snapshot)
                    if fn_scores:
                        await store.update_hot_functions(repo, fn_scores)
                last_realtime = now

            # History batch
            if now - last_history >= history_interval and pending_metrics:
                await store.write_metrics(pending_metrics)
                metrics_written_total += len(pending_metrics)
                pending_metrics = []
                last_history = now

        except asyncio.CancelledError:
            # Flush remaining metrics before shutdown
            if pending_metrics:
                try:
                    await store.write_metrics(pending_metrics)
                except Exception:
                    logger.warning("Failed to flush metrics on shutdown")
            raise
        except Exception as exc:
            logger.error("Collector loop error: %s", exc)
            await asyncio.sleep(1)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd services/perf-tracker && python -m pytest tests/test_collector.py -v`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add services/perf-tracker/app/collector.py services/perf-tracker/tests/test_collector.py
git commit -m "feat(perf-tracker): add stream collector with metric extraction"
```

---

### Task 5: Analyzer — Bottleneck Detection

**Files:**
- Create: `services/perf-tracker/app/analyzer.py`
- Create: `services/perf-tracker/tests/test_analyzer.py`

- [ ] **Step 1: Write the failing tests**

```python
# services/perf-tracker/tests/test_analyzer.py
import pytest
from datetime import datetime, timezone


def test_detect_queue_buildup():
    from app.analyzer import detect_queue_buildup
    # Two consecutive snapshots where depth is growing
    prev = {"stream:raw-traces": 10, "stream:execution-paths": 5}
    curr = {"stream:raw-traces": 25, "stream:execution-paths": 5}
    bottlenecks = detect_queue_buildup(prev, curr, repo="test")
    assert len(bottlenecks) == 1
    assert bottlenecks[0].entity_id == "stream:raw-traces"
    assert bottlenecks[0].category == "pipeline"


def test_detect_queue_buildup_no_issue():
    from app.analyzer import detect_queue_buildup
    prev = {"stream:raw-traces": 10}
    curr = {"stream:raw-traces": 8}
    bottlenecks = detect_queue_buildup(prev, curr, repo="test")
    assert len(bottlenecks) == 0


def test_detect_processing_lag():
    from app.analyzer import detect_processing_lag
    # Lag of 15s exceeds default threshold of 10s
    lag_values = [15.0, 12.0, 11.0]
    bottlenecks = detect_processing_lag(lag_values, threshold_s=10.0, repo="test")
    assert len(bottlenecks) == 1
    assert bottlenecks[0].severity == "warning"


def test_detect_processing_lag_ok():
    from app.analyzer import detect_processing_lag
    lag_values = [3.0, 2.0, 5.0]
    bottlenecks = detect_processing_lag(lag_values, threshold_s=10.0, repo="test")
    assert len(bottlenecks) == 0


def test_detect_slow_functions():
    from app.analyzer import detect_slow_functions
    rows = [
        {"entity_id": "sha256:fn_slow", "value": 5000.0, "unit": "ms", "commit_sha": "abc"},
        {"entity_id": "sha256:fn_fast", "value": 10.0, "unit": "ms", "commit_sha": "abc"},
    ]
    bottlenecks = detect_slow_functions(rows, repo="test")
    assert len(bottlenecks) == 2
    assert bottlenecks[0].entity_id == "sha256:fn_slow"
    assert bottlenecks[0].severity == "critical"  # 5000ms is critical


def test_detect_deep_chains():
    from app.analyzer import detect_deep_chains
    rows = [
        {"entity_id": "sha256:ep1", "value": 20.0},
        {"entity_id": "sha256:ep2", "value": 5.0},
    ]
    bottlenecks = detect_deep_chains(rows, max_depth=15, repo="test")
    assert len(bottlenecks) == 1
    assert bottlenecks[0].entity_id == "sha256:ep1"


def test_detect_throughput_drop():
    from app.analyzer import detect_throughput_drop
    bottlenecks = detect_throughput_drop(
        current_rate=2.0, rolling_avg=10.0, threshold_pct=50.0,
        stream="stream:raw-traces", repo="test",
    )
    assert len(bottlenecks) == 1
    assert bottlenecks[0].category == "pipeline"
    assert bottlenecks[0].value == 80.0  # 80% drop


def test_detect_throughput_drop_ok():
    from app.analyzer import detect_throughput_drop
    bottlenecks = detect_throughput_drop(
        current_rate=8.0, rolling_avg=10.0, threshold_pct=50.0,
        stream="stream:raw-traces", repo="test",
    )
    assert len(bottlenecks) == 0


def test_compute_trend_slope():
    from app.analyzer import compute_trend_slope
    # Values increasing over time
    points = [
        {"value": 100.0, "recorded_at": datetime(2026, 1, 1, tzinfo=timezone.utc)},
        {"value": 110.0, "recorded_at": datetime(2026, 1, 2, tzinfo=timezone.utc)},
        {"value": 120.0, "recorded_at": datetime(2026, 1, 3, tzinfo=timezone.utc)},
    ]
    slope, direction = compute_trend_slope(points)
    assert slope > 0
    assert direction == "increasing"


def test_compute_trend_slope_decreasing():
    from app.analyzer import compute_trend_slope
    points = [
        {"value": 120.0, "recorded_at": datetime(2026, 1, 1, tzinfo=timezone.utc)},
        {"value": 110.0, "recorded_at": datetime(2026, 1, 2, tzinfo=timezone.utc)},
        {"value": 100.0, "recorded_at": datetime(2026, 1, 3, tzinfo=timezone.utc)},
    ]
    slope, direction = compute_trend_slope(points)
    assert slope < 0
    assert direction == "decreasing"


def test_compute_trend_slope_insufficient_data():
    from app.analyzer import compute_trend_slope
    points = [{"value": 100.0, "recorded_at": datetime(2026, 1, 1, tzinfo=timezone.utc)}]
    slope, direction = compute_trend_slope(points)
    assert slope == 0.0
    assert direction == "stable"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd services/perf-tracker && python -m pytest tests/test_analyzer.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.analyzer'`

- [ ] **Step 3: Write the analyzer**

```python
# services/perf-tracker/app/analyzer.py
from __future__ import annotations
from datetime import datetime, timezone

from app.models import Bottleneck


def detect_queue_buildup(
    prev_depths: dict[str, int],
    curr_depths: dict[str, int],
    repo: str,
) -> list[Bottleneck]:
    bottlenecks = []
    now = datetime.now(timezone.utc)
    for stream, curr in curr_depths.items():
        prev = prev_depths.get(stream, 0)
        if curr > prev and curr - prev > 5:
            bottlenecks.append(Bottleneck(
                entity_id=stream,
                entity_name=stream.split(":")[-1] + " stream",
                category="pipeline",
                severity="warning" if curr - prev < 50 else "critical",
                value=float(curr - prev),
                unit="messages",
                detail=f"Queue depth grew from {prev} to {curr}",
                repo=repo,
                detected_at=now,
            ))
    return bottlenecks


def detect_processing_lag(
    lag_values: list[float],
    threshold_s: float,
    repo: str,
) -> list[Bottleneck]:
    if not lag_values:
        return []
    avg_lag = sum(lag_values) / len(lag_values)
    if avg_lag <= threshold_s:
        return []
    now = datetime.now(timezone.utc)
    return [Bottleneck(
        entity_id="trace-normalizer",
        entity_name="trace-normalizer",
        category="pipeline",
        severity="critical" if avg_lag > threshold_s * 2 else "warning",
        value=avg_lag,
        unit="s",
        detail=f"Avg processing lag {avg_lag:.1f}s exceeds {threshold_s}s threshold",
        repo=repo,
        detected_at=now,
    )]


def detect_slow_functions(
    rows: list[dict],
    repo: str,
    critical_ms: float = 1000.0,
    warning_ms: float = 200.0,
) -> list[Bottleneck]:
    bottlenecks = []
    now = datetime.now(timezone.utc)
    for row in rows:
        value = row["value"]
        if value >= critical_ms:
            severity = "critical"
        elif value >= warning_ms:
            severity = "warning"
        else:
            severity = "info"
        bottlenecks.append(Bottleneck(
            entity_id=row["entity_id"],
            entity_name=row["entity_id"].split(":")[-1],
            category="slow_fn",
            severity=severity,
            value=value,
            unit=row.get("unit", "ms"),
            detail=f"Avg duration {value:.1f}ms",
            repo=repo,
            detected_at=now,
        ))
    return bottlenecks


def detect_deep_chains(
    rows: list[dict],
    max_depth: int,
    repo: str,
) -> list[Bottleneck]:
    bottlenecks = []
    now = datetime.now(timezone.utc)
    for row in rows:
        depth = row["value"]
        if depth > max_depth:
            bottlenecks.append(Bottleneck(
                entity_id=row["entity_id"],
                entity_name=row["entity_id"].split(":")[-1],
                category="deep_chain",
                severity="warning",
                value=depth,
                unit="levels",
                detail=f"Call depth {int(depth)} exceeds max {max_depth}",
                repo=repo,
                detected_at=now,
            ))
    return bottlenecks


def detect_throughput_drop(
    current_rate: float,
    rolling_avg: float,
    threshold_pct: float,
    stream: str,
    repo: str,
) -> list[Bottleneck]:
    if rolling_avg <= 0 or current_rate >= rolling_avg:
        return []
    drop_pct = ((rolling_avg - current_rate) / rolling_avg) * 100
    if drop_pct < threshold_pct:
        return []
    now = datetime.now(timezone.utc)
    return [Bottleneck(
        entity_id=stream,
        entity_name=stream.split(":")[-1] + " stream",
        category="pipeline",
        severity="critical" if drop_pct > threshold_pct * 1.5 else "warning",
        value=drop_pct,
        unit="%",
        detail=f"Throughput dropped {drop_pct:.0f}% (current {current_rate:.1f} msg/s vs avg {rolling_avg:.1f} msg/s)",
        repo=repo,
        detected_at=now,
    )]


def compute_trend_slope(points: list[dict]) -> tuple[float, str]:
    if len(points) < 2:
        return 0.0, "stable"

    n = len(points)
    # Simple linear regression: y = value, x = index
    x_mean = (n - 1) / 2.0
    y_mean = sum(p["value"] for p in points) / n

    numerator = sum((i - x_mean) * (p["value"] - y_mean) for i, p in enumerate(points))
    denominator = sum((i - x_mean) ** 2 for i in range(n))

    if denominator == 0:
        return 0.0, "stable"

    slope = numerator / denominator

    if slope > 0.01:
        direction = "increasing"
    elif slope < -0.01:
        direction = "decreasing"
    else:
        direction = "stable"

    return slope, direction
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd services/perf-tracker && python -m pytest tests/test_analyzer.py -v`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add services/perf-tracker/app/analyzer.py services/perf-tracker/tests/test_analyzer.py
git commit -m "feat(perf-tracker): add analyzer with bottleneck detection and trend analysis"
```

---

### Task 6: API Router

**Files:**
- Create: `services/perf-tracker/app/api.py`
- Create: `services/perf-tracker/tests/test_api.py`

- [ ] **Step 1: Write the failing tests**

```python
# services/perf-tracker/tests/test_api.py
import pytest
import json
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import datetime, timezone
from contextlib import asynccontextmanager


@pytest.fixture
def api_client(mock_store):
    @asynccontextmanager
    async def _noop_lifespan(app):
        yield

    import app.main as main_mod
    original_store = main_mod._store
    original_lifespan = main_mod.app.router.lifespan_context
    main_mod._store = mock_store
    main_mod.app.router.lifespan_context = _noop_lifespan

    with patch("app.main._get_redis", return_value=AsyncMock(ping=AsyncMock())):
        from fastapi.testclient import TestClient
        with TestClient(main_mod.app) as c:
            yield c

    main_mod._store = original_store
    main_mod.app.router.lifespan_context = original_lifespan


def test_health(api_client):
    resp = api_client.get("/health")
    assert resp.status_code == 200
    assert resp.json()["service"] == "perf-tracker"


def test_get_realtime(api_client, mock_store):
    mock_store.get_realtime = AsyncMock(return_value={"throughput": "42.5"})
    resp = api_client.get("/api/v1/realtime/test-repo")
    assert resp.status_code == 200
    assert resp.json()["throughput"] == "42.5"


def test_get_bottlenecks(api_client, mock_store):
    mock_store.get_bottlenecks_json = AsyncMock(return_value=[
        json.dumps({"entity_id": "x", "category": "pipeline", "severity": "warning",
                     "entity_name": "x", "value": 1.0, "unit": "s",
                     "detail": "test", "repo": "test", "detected_at": "2026-01-01T00:00:00Z"}),
    ])
    resp = api_client.get("/api/v1/bottlenecks/test-repo")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1


def test_get_bottlenecks_filter_category(api_client, mock_store):
    mock_store.get_bottlenecks_json = AsyncMock(return_value=[
        json.dumps({"entity_id": "a", "category": "pipeline", "severity": "warning",
                     "entity_name": "a", "value": 1.0, "unit": "s",
                     "detail": "t", "repo": "test", "detected_at": "2026-01-01T00:00:00Z"}),
        json.dumps({"entity_id": "b", "category": "slow_fn", "severity": "warning",
                     "entity_name": "b", "value": 2.0, "unit": "ms",
                     "detail": "t", "repo": "test", "detected_at": "2026-01-01T00:00:00Z"}),
    ])
    resp = api_client.get("/api/v1/bottlenecks/test-repo?category=pipeline")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    assert data[0]["category"] == "pipeline"


def test_get_slow_functions(api_client, mock_store):
    mock_store.get_slow_functions = AsyncMock(return_value=[
        {"entity_id": "sha256:fn", "value": 120.0, "unit": "ms", "commit_sha": "abc"},
    ])
    resp = api_client.get("/api/v1/slow-functions/test-repo?limit=5")
    assert resp.status_code == 200
    assert len(resp.json()) == 1


def test_get_trends(api_client, mock_store):
    mock_store.get_trends = AsyncMock(return_value=[
        {"value": 100.0, "recorded_at": datetime(2026, 1, 1, tzinfo=timezone.utc)},
        {"value": 110.0, "recorded_at": datetime(2026, 1, 2, tzinfo=timezone.utc)},
    ])
    resp = api_client.get("/api/v1/trends/test-repo/sha256:fn?metric_name=fn_duration&window=24h")
    assert resp.status_code == 200
    data = resp.json()
    assert "points" in data
    assert "trend" in data


def test_get_hot_paths(api_client, mock_store):
    mock_store.get_hot_functions = AsyncMock(return_value=[
        ("sha256:fn_auth", 3600.0),
        ("sha256:fn_login", 1200.0),
    ])
    resp = api_client.get("/api/v1/hot-paths/test-repo?limit=5")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 2
    assert data[0]["cumulative_ms"] == 3600.0


def test_get_regressions(api_client, mock_store):
    mock_store.get_regressions = AsyncMock(return_value=[
        {"entity_id": "sha256:fn", "base_val": 100.0, "head_val": 150.0, "pct_change": 50.0},
    ])
    resp = api_client.get("/api/v1/regressions/test-repo?base_sha=abc&head_sha=def")
    assert resp.status_code == 200
    assert len(resp.json()) == 1


def test_get_regressions_missing_params(api_client, mock_store):
    resp = api_client.get("/api/v1/regressions/test-repo")
    assert resp.status_code == 422


def test_get_summary(api_client, mock_store):
    mock_store.get_realtime = AsyncMock(return_value={"throughput": "42.5"})
    mock_store.get_slow_functions = AsyncMock(return_value=[])
    mock_store.get_bottlenecks_json = AsyncMock(return_value=[])
    mock_store.get_summary_counts = AsyncMock(return_value={"pipeline": 10, "user_code": 50})
    resp = api_client.get("/api/v1/summary/test-repo")
    assert resp.status_code == 200
    data = resp.json()
    assert "metric_counts" in data


def test_post_analyze(api_client, mock_store):
    mock_store.get_slow_functions = AsyncMock(return_value=[])
    mock_store.get_realtime = AsyncMock(return_value={})
    mock_store.get_bottlenecks_json = AsyncMock(return_value=[])
    mock_store.get_summary_counts = AsyncMock(return_value={})
    resp = api_client.post("/api/v1/analyze/test-repo")
    assert resp.status_code == 200
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd services/perf-tracker && python -m pytest tests/test_api.py -v`
Expected: FAIL — modules not found

- [ ] **Step 3: Write the API router**

```python
# services/perf-tracker/app/api.py
from __future__ import annotations
import json
import os
from typing import Optional

from fastapi import APIRouter, Query

from app.analyzer import compute_trend_slope, detect_slow_functions
from app.store import PerfStore

router = APIRouter(prefix="/api/v1")


def _parse_window(window: str) -> int:
    """Parse window string like '24h', '7d', '1h' into hours."""
    window = window.strip().lower()
    if window.endswith("d"):
        return int(window[:-1]) * 24
    if window.endswith("h"):
        return int(window[:-1])
    return 24


def _get_store() -> PerfStore:
    import app.main as main_mod
    return main_mod._store


@router.get("/realtime/{repo}")
async def get_realtime(repo: str):
    store = _get_store()
    return await store.get_realtime(repo)


@router.get("/bottlenecks/{repo}")
async def get_bottlenecks(
    repo: str,
    category: Optional[str] = Query(None),
    severity: Optional[str] = Query(None),
):
    store = _get_store()
    raw = await store.get_bottlenecks_json(repo)
    items = [json.loads(b) for b in raw]
    if category:
        items = [b for b in items if b.get("category") == category]
    if severity:
        items = [b for b in items if b.get("severity") == severity]
    return items


@router.get("/slow-functions/{repo}")
async def get_slow_functions(
    repo: str,
    limit: int = Query(20, ge=1, le=100),
    commit_sha: Optional[str] = Query(None),
):
    store = _get_store()
    return await store.get_slow_functions(repo, limit=limit, commit_sha=commit_sha)


@router.get("/hot-paths/{repo}")
async def get_hot_paths(repo: str, limit: int = Query(20, ge=1, le=100)):
    store = _get_store()
    results = await store.get_hot_functions(repo, limit=limit)
    return [{"entity_id": name, "cumulative_ms": score} for name, score in results]


@router.get("/regressions/{repo}")
async def get_regressions(
    repo: str,
    base_sha: str = Query(...),
    head_sha: str = Query(...),
):
    store = _get_store()
    threshold = float(os.environ.get("REGRESSION_THRESHOLD_PCT", "20"))
    return await store.get_regressions(repo, base_sha, head_sha, threshold)


@router.get("/trends/{repo}/{entity_id:path}")
async def get_trends(
    repo: str,
    entity_id: str,
    metric_name: str = Query("fn_duration"),
    window: str = Query("24h"),
):
    store = _get_store()
    hours = _parse_window(window)
    points = await store.get_trends(repo, entity_id, metric_name, window_hours=hours)
    slope, direction = compute_trend_slope(points)
    # Serialize datetimes for JSON response
    serialized = [
        {"value": p["value"], "recorded_at": p["recorded_at"].isoformat() if hasattr(p["recorded_at"], "isoformat") else str(p["recorded_at"])}
        for p in points
    ]
    return {
        "entity_id": entity_id,
        "metric_name": metric_name,
        "window": window,
        "points": serialized,
        "trend": {"slope": slope, "direction": direction},
    }


@router.get("/summary/{repo}")
async def get_summary(repo: str):
    store = _get_store()
    realtime = await store.get_realtime(repo)
    slow = await store.get_slow_functions(repo, limit=5)
    bottlenecks_raw = await store.get_bottlenecks_json(repo)
    bottlenecks = [json.loads(b) for b in bottlenecks_raw]
    counts = await store.get_summary_counts(repo)
    return {
        "repo": repo,
        "pipeline_health": realtime,
        "top_slow_functions": slow,
        "active_bottlenecks": bottlenecks,
        "metric_counts": counts,
    }


@router.post("/analyze/{repo}")
async def analyze(repo: str):
    store = _get_store()
    slow = await store.get_slow_functions(repo, limit=20)
    realtime = await store.get_realtime(repo)
    bottlenecks_raw = await store.get_bottlenecks_json(repo)
    bottlenecks = [json.loads(b) for b in bottlenecks_raw]
    counts = await store.get_summary_counts(repo)
    slow_bottlenecks = detect_slow_functions(slow, repo=repo)
    return {
        "repo": repo,
        "pipeline_health": realtime,
        "bottlenecks": bottlenecks + [b.model_dump(mode="json") for b in slow_bottlenecks],
        "slow_functions": slow,
        "metric_counts": counts,
    }
```

- [ ] **Step 4: Write main.py** (needed by test fixture)

```python
# services/perf-tracker/app/main.py
import asyncio
import logging
import os
from contextlib import asynccontextmanager

import redis.asyncio as aioredis
from fastapi import FastAPI
from fastapi.responses import JSONResponse, PlainTextResponse

from app.api import router
from app import collector as _collector

logger = logging.getLogger(__name__)

VERSION = "0.1.0"
SERVICE = "perf-tracker"

_redis_client: aioredis.Redis | None = None
_store = None


def _get_redis() -> aioredis.Redis:
    global _redis_client
    if _redis_client is None:
        url = os.environ.get("REDIS_URL", "redis://localhost:6379")
        _redis_client = aioredis.from_url(url)
    return _redis_client


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _store

    from app.store import PerfStore
    from app.collector import run_collector

    postgres_dsn = os.environ["POSTGRES_DSN"]
    redis_client = _get_redis()

    _store = PerfStore(postgres_dsn, redis_client)
    await _store.ensure_schema()

    task = asyncio.create_task(run_collector(_store, redis_client))
    try:
        yield
    finally:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        await _store.close()


app = FastAPI(lifespan=lifespan)
app.include_router(router)


@app.get("/health")
def health():
    return {"status": "ok", "service": SERVICE, "version": VERSION}


@app.get("/ready")
async def ready():
    errors = []
    try:
        await _get_redis().ping()
    except Exception as exc:
        errors.append(f"redis: {exc}")
    try:
        if _store:
            pool = await _store._get_pool()
            async with pool.acquire() as conn:
                await conn.fetchval("SELECT 1")
    except Exception as exc:
        errors.append(f"postgres: {exc}")
    if errors:
        return JSONResponse(status_code=503, content={"status": "unavailable", "errors": errors})
    return {"status": "ok"}


@app.get("/metrics")
def metrics():
    lines = [
        "# HELP perf_tracker_messages_processed_total Total stream messages processed",
        "# TYPE perf_tracker_messages_processed_total counter",
        f"perf_tracker_messages_processed_total {_collector.messages_processed_total}",
        "# HELP perf_tracker_messages_failed_total Total stream messages failed",
        "# TYPE perf_tracker_messages_failed_total counter",
        f"perf_tracker_messages_failed_total {_collector.messages_failed_total}",
        "# HELP perf_tracker_metrics_written_total Total metrics written to Postgres",
        "# TYPE perf_tracker_metrics_written_total counter",
        f"perf_tracker_metrics_written_total {_collector.metrics_written_total}",
    ]
    return PlainTextResponse("\n".join(lines) + "\n")
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd services/perf-tracker && python -m pytest tests/test_api.py -v`
Expected: all PASS

- [ ] **Step 6: Commit**

```bash
git add services/perf-tracker/app/api.py services/perf-tracker/app/main.py services/perf-tracker/tests/test_api.py
git commit -m "feat(perf-tracker): add API router and FastAPI main with lifespan"
```

---

### Task 7: CLI

**Files:**
- Create: `services/perf-tracker/cli.py`
- Create: `services/perf-tracker/tests/test_cli.py`

- [ ] **Step 1: Write the failing tests**

```python
# services/perf-tracker/tests/test_cli.py
import pytest
import json
from unittest.mock import patch, MagicMock


@pytest.fixture
def mock_httpx_get():
    with patch("httpx.get") as m:
        yield m


@pytest.fixture
def mock_httpx_post():
    with patch("httpx.post") as m:
        yield m


def test_status_command(mock_httpx_get, capsys):
    mock_httpx_get.return_value = MagicMock(
        status_code=200,
        json=lambda: {"throughput": "42.5", "queue_depth:stream:raw-traces": "10"},
    )
    from cli import run_command
    run_command(["status", "test-repo"], host="localhost", port=8098)
    out = capsys.readouterr().out
    assert "throughput" in out
    assert "42.5" in out


def test_bottlenecks_command(mock_httpx_get, capsys):
    mock_httpx_get.return_value = MagicMock(
        status_code=200,
        json=lambda: [
            {"entity_id": "x", "category": "pipeline", "severity": "warning",
             "value": 15.2, "unit": "s", "detail": "Lag exceeds threshold"},
        ],
    )
    from cli import run_command
    run_command(["bottlenecks", "test-repo"], host="localhost", port=8098)
    out = capsys.readouterr().out
    assert "pipeline" in out
    assert "warning" in out


def test_slow_command(mock_httpx_get, capsys):
    mock_httpx_get.return_value = MagicMock(
        status_code=200,
        json=lambda: [
            {"entity_id": "sha256:fn", "value": 120.0, "unit": "ms", "commit_sha": "abc"},
        ],
    )
    from cli import run_command
    run_command(["slow", "test-repo", "--limit", "5"], host="localhost", port=8098)
    out = capsys.readouterr().out
    assert "120.0" in out


def test_summary_command(mock_httpx_get, capsys):
    mock_httpx_get.return_value = MagicMock(
        status_code=200,
        json=lambda: {
            "repo": "test-repo",
            "pipeline_health": {},
            "top_slow_functions": [],
            "active_bottlenecks": [],
            "metric_counts": {"pipeline": 10},
        },
    )
    from cli import run_command
    run_command(["summary", "test-repo"], host="localhost", port=8098)
    out = capsys.readouterr().out
    assert "test-repo" in out


def test_trends_command(mock_httpx_get, capsys):
    mock_httpx_get.return_value = MagicMock(
        status_code=200,
        json=lambda: {
            "entity_id": "sha256:fn", "metric_name": "fn_duration", "window": "24h",
            "points": [{"value": 100.0, "recorded_at": "2026-01-01T00:00:00Z"}],
            "trend": {"slope": 0.5, "direction": "increasing"},
        },
    )
    from cli import run_command
    run_command(["trends", "test-repo", "sha256:fn", "--window", "24h"], host="localhost", port=8098)
    out = capsys.readouterr().out
    assert "increasing" in out


def test_regressions_command(mock_httpx_get, capsys):
    mock_httpx_get.return_value = MagicMock(
        status_code=200,
        json=lambda: [
            {"entity_id": "sha256:fn", "base_val": 100.0, "head_val": 150.0, "pct_change": 50.0},
        ],
    )
    from cli import run_command
    run_command(["regressions", "test-repo", "--base", "abc1234", "--head", "def5678"], host="localhost", port=8098)
    out = capsys.readouterr().out
    assert "50.0" in out


def test_json_output(mock_httpx_get, capsys):
    data = {"throughput": "42.5"}
    mock_httpx_get.return_value = MagicMock(status_code=200, json=lambda: data)
    from cli import run_command
    run_command(["status", "test-repo", "--json"], host="localhost", port=8098)
    out = capsys.readouterr().out
    parsed = json.loads(out)
    assert parsed["throughput"] == "42.5"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd services/perf-tracker && python -m pytest tests/test_cli.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'cli'`

- [ ] **Step 3: Write the CLI**

```python
#!/usr/bin/env python3
# services/perf-tracker/cli.py
"""Perf-tracker CLI — standalone client for the perf-tracker API."""
from __future__ import annotations
import argparse
import json
import os
import sys

import httpx


def _base_url(host: str, port: int) -> str:
    env = os.environ.get("PERF_TRACKER_URL")
    if env:
        return env.rstrip("/")
    return f"http://{host}:{port}"


def _print_table(rows: list[dict], columns: list[str]) -> None:
    if not rows:
        print("(no data)")
        return
    widths = {c: len(c) for c in columns}
    for row in rows:
        for c in columns:
            widths[c] = max(widths[c], len(str(row.get(c, ""))))
    header = "  ".join(c.ljust(widths[c]) for c in columns)
    print(header)
    print("  ".join("-" * widths[c] for c in columns))
    for row in rows:
        line = "  ".join(str(row.get(c, "")).ljust(widths[c]) for c in columns)
        print(line)


def _print_hash(data: dict) -> None:
    if not data:
        print("(no data)")
        return
    max_key = max(len(str(k)) for k in data)
    for k, v in data.items():
        print(f"  {str(k).ljust(max_key)}  {v}")


def run_command(args: list[str], host: str, port: int) -> None:
    base = _base_url(host, port)
    use_json = "--json" in args
    args = [a for a in args if a != "--json"]

    cmd = args[0]
    repo = args[1] if len(args) > 1 else None

    if cmd == "status":
        resp = httpx.get(f"{base}/api/v1/realtime/{repo}")
        data = resp.json()
        if use_json:
            print(json.dumps(data, indent=2))
        else:
            print(f"Pipeline Status: {repo}")
            print()
            _print_hash(data)

    elif cmd == "bottlenecks":
        params = {}
        i = 2
        while i < len(args):
            if args[i] == "--category" and i + 1 < len(args):
                params["category"] = args[i + 1]
                i += 2
            elif args[i] == "--severity" and i + 1 < len(args):
                params["severity"] = args[i + 1]
                i += 2
            else:
                i += 1
        resp = httpx.get(f"{base}/api/v1/bottlenecks/{repo}", params=params)
        data = resp.json()
        if use_json:
            print(json.dumps(data, indent=2))
        else:
            print(f"Bottlenecks: {repo}")
            print()
            _print_table(data, ["entity_id", "category", "severity", "value", "unit", "detail"])

    elif cmd == "slow":
        params: dict = {}
        i = 2
        while i < len(args):
            if args[i] == "--limit" and i + 1 < len(args):
                params["limit"] = args[i + 1]
                i += 2
            elif args[i] == "--commit" and i + 1 < len(args):
                params["commit_sha"] = args[i + 1]
                i += 2
            else:
                i += 1
        resp = httpx.get(f"{base}/api/v1/slow-functions/{repo}", params=params)
        data = resp.json()
        if use_json:
            print(json.dumps(data, indent=2))
        else:
            print(f"Slow Functions: {repo}")
            print()
            _print_table(data, ["entity_id", "value", "unit", "commit_sha"])

    elif cmd == "regressions":
        base_sha = head_sha = None
        i = 2
        while i < len(args):
            if args[i] == "--base" and i + 1 < len(args):
                base_sha = args[i + 1]
                i += 2
            elif args[i] == "--head" and i + 1 < len(args):
                head_sha = args[i + 1]
                i += 2
            else:
                i += 1
        if not base_sha or not head_sha:
            print("Error: --base and --head are required", file=sys.stderr)
            return
        resp = httpx.get(
            f"{base}/api/v1/regressions/{repo}",
            params={"base_sha": base_sha, "head_sha": head_sha},
        )
        data = resp.json()
        if use_json:
            print(json.dumps(data, indent=2))
        else:
            print(f"Regressions: {repo} ({base_sha[:7]} -> {head_sha[:7]})")
            print()
            _print_table(data, ["entity_id", "base_val", "head_val", "pct_change"])

    elif cmd == "trends":
        entity_id = args[2] if len(args) > 2 else ""
        window = "24h"
        i = 3
        while i < len(args):
            if args[i] == "--window" and i + 1 < len(args):
                window = args[i + 1]
                i += 2
            else:
                i += 1
        resp = httpx.get(
            f"{base}/api/v1/trends/{repo}/{entity_id}",
            params={"window": window},
        )
        data = resp.json()
        if use_json:
            print(json.dumps(data, indent=2))
        else:
            trend = data.get("trend", {})
            print(f"Trend: {entity_id} ({window})")
            print(f"  Direction: {trend.get('direction', 'unknown')}")
            print(f"  Slope:     {trend.get('slope', 0):.4f}")
            print(f"  Points:    {len(data.get('points', []))}")

    elif cmd == "summary":
        resp = httpx.get(f"{base}/api/v1/summary/{repo}")
        data = resp.json()
        if use_json:
            print(json.dumps(data, indent=2))
        else:
            print(f"Summary: {data.get('repo', repo)}")
            print()
            counts = data.get("metric_counts", {})
            print("Metric Counts:")
            for k, v in counts.items():
                print(f"  {k}: {v}")
            bottlenecks = data.get("active_bottlenecks", [])
            print(f"\nActive Bottlenecks: {len(bottlenecks)}")
            slow = data.get("top_slow_functions", [])
            if slow:
                print("\nTop Slow Functions:")
                _print_table(slow, ["entity_id", "value", "unit"])

    else:
        print(f"Unknown command: {cmd}", file=sys.stderr)


def main() -> None:
    parser = argparse.ArgumentParser(description="Perf-tracker CLI")
    parser.add_argument("--host", default="localhost")
    parser.add_argument("--port", type=int, default=8098)
    parser.add_argument("--json", action="store_true", dest="use_json")
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        return
    cmd_args = args.command
    if args.use_json:
        cmd_args.append("--json")
    run_command(cmd_args, host=args.host, port=args.port)


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd services/perf-tracker && python -m pytest tests/test_cli.py -v`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add services/perf-tracker/cli.py services/perf-tracker/tests/test_cli.py
git commit -m "feat(perf-tracker): add CLI client with tabular and JSON output"
```

---

### Task 8: Docker Compose Integration

**Files:**
- Modify: `docker-compose.yml` (add perf-tracker service)

- [ ] **Step 1: Add perf-tracker entry to docker-compose.yml**

Add after the spec-generator entry:

```yaml
  perf-tracker:
    build: ./services/perf-tracker
    ports:
      - "8098:8080"
    environment:
      REDIS_URL: redis://redis:6379
      POSTGRES_DSN: postgres://tersecontext:${POSTGRES_PASSWORD:-localpassword}@postgres:5432/tersecontext
    depends_on:
      redis:
        condition: service_healthy
      postgres:
        condition: service_healthy
    networks:
      - tersecontext
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:8080/health"]
      interval: 10s
      timeout: 5s
      retries: 5
```

- [ ] **Step 2: Verify Dockerfile builds**

Run: `docker build -t perf-tracker-test ./services/perf-tracker`
Expected: builds successfully

- [ ] **Step 3: Commit**

```bash
git add docker-compose.yml
git commit -m "feat(perf-tracker): add to docker-compose"
```

---

### Task 9: Run Full Test Suite

- [ ] **Step 1: Install dependencies and run all tests**

Run: `cd services/perf-tracker && pip install -r requirements.txt && python -m pytest tests/ -v`
Expected: all tests pass

- [ ] **Step 2: Check test count**

Verify: at least 25 tests across all test files

- [ ] **Step 3: Run with coverage (if available)**

Run: `cd services/perf-tracker && python -m pytest tests/ -v --tb=short`
Expected: all PASS, no warnings about missing modules
