# Perf-Tracker Service Design

## Overview

A modular FastAPI service (port 8098) for bottleneck analysis across both the dynamic analysis pipeline and the user code it traces. Combines a background stream collector with query endpoints and a CLI tool.

**Architecture:** Single service with async worker isolation — the collector runs as a background `asyncio.create_task()` in the FastAPI lifespan (same pattern as `spec-generator` and `trace-runner`). Modules are cleanly separated so the collector can be extracted into its own service later if needed.

**Storage:** Postgres for historical metrics, Redis for real-time snapshots.

---

## Service Structure

```
services/perf-tracker/
├── app/
│   ├── __init__.py
│   ├── main.py              # FastAPI app, lifespan, /health /ready /metrics
│   ├── models.py            # Pydantic models for all data types
│   ├── collector.py         # Stream consumer (background async task)
│   ├── analyzer.py          # Bottleneck detection + trend analysis
│   ├── store.py             # Postgres (history) + Redis (real-time)
│   └── api.py               # Query endpoints (router)
├── cli.py                   # CLI client
├── tests/
│   └── test_*.py
├── Dockerfile
├── requirements.txt
└── pytest.ini
```

Port: 8098 (external docker-compose mapping; internal container port is 8080, matching all other services)
Standard endpoints: `GET /health`, `GET /ready`, `GET /metrics`

---

## Configuration

Environment variables (with defaults):

| Variable | Default | Description |
|----------|---------|-------------|
| `REDIS_URL` | `redis://localhost:6379` | Redis connection URL |
| `POSTGRES_DSN` | *(required)* | Postgres connection string |
| `REALTIME_INTERVAL_S` | `5` | Real-time snapshot frequency (seconds) |
| `HISTORY_INTERVAL_S` | `60` | Postgres batch insert frequency (seconds) |
| `LAG_THRESHOLD_S` | `10` | Processing lag threshold before flagging bottleneck |
| `REGRESSION_THRESHOLD_PCT` | `20` | Duration increase % to flag a regression |
| `THROUGHPUT_DROP_PCT` | `50` | Throughput drop % vs rolling average to flag |
| `MAX_CALL_DEPTH` | `15` | Call depth threshold for deep-chain detection |

---

## Docker Compose

```yaml
perf-tracker:
  build:
    context: .
    dockerfile: docker/python.Dockerfile
    args:
      SERVICE: perf-tracker
  ports:
    - "8098:8080"
  environment:
    REDIS_URL: redis://redis:6379
    POSTGRES_DSN: postgresql://tersecontext:${POSTGRES_PASSWORD:-localpassword}@postgres:5432/tersecontext
  depends_on:
    redis:
      condition: service_healthy
    postgres:
      condition: service_healthy
  healthcheck:
    test: ["CMD", "curl", "-f", "http://localhost:8080/health"]
    interval: 10s
    timeout: 5s
    retries: 3
```

---

## Collector

The collector consumes both existing streams using new consumer groups, running as a background async task.

### Stream Consumption

Uses `redis.asyncio` (matching the `spec-generator` pattern). A single `XREADGROUP` call reads both streams simultaneously, blocking until data arrives on either.

- `stream:raw-traces` via consumer group `perf-tracker-traces` — extracts per-function timing, entrypoint duration, event counts
- `stream:execution-paths` via consumer group `perf-tracker-paths` — extracts branch coverage, side effect counts, call depth

**Field availability:** Both RawTrace and ExecutionPath payloads include `repo`. If `repo` is missing from a message, extract it from the `entrypoint_stable_id` (which encodes `repo:file_path:...`) or skip the message with a warning log. Correlation keys for processing lag always include `(repo, entrypoint_stable_id)` to avoid cross-repo confusion.

**Malformed messages:** ACK and log a warning (matching `spec-generator` pattern). Do not block the consumer on bad data.

### Pipeline Metrics (extracted from stream data)

- **Throughput:** messages per second on each stream (measured by arrival rate)
- **Processing lag:** time between a RawTrace being emitted and its corresponding ExecutionPath appearing. Computed using Redis stream message IDs (which encode millisecond timestamps), correlated by `(repo, entrypoint_stable_id)`
- **Queue depth:** periodic `XLEN` on both streams + `LLEN` on `entrypoint_queue:*`

### User-Code Metrics (extracted from trace data)

- **Function duration:** from RawTrace `events[]` call/return pairs
- **Hot paths:** functions appearing most frequently across traces
- **Slow functions:** highest `avg_duration_ms` per function
- **Call depth:** max nesting depth per entrypoint

### Data Output

- Real-time snapshots written to Redis (overwritten each cycle)
- Historical records appended to Postgres

### Collection Interval

- Every 5 seconds for real-time Redis snapshots
- Batch insert to Postgres every 60 seconds

---

## Storage

### Postgres — Historical Metrics

```sql
CREATE TABLE IF NOT EXISTS perf_metrics (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    metric_type     TEXT NOT NULL,        -- 'pipeline' | 'user_code'
    metric_name     TEXT NOT NULL,        -- e.g. 'fn_duration', 'stream_throughput', 'queue_depth'
    entity_id       TEXT NOT NULL,        -- stable_id for functions, stream name for pipeline
    repo            TEXT NOT NULL,
    value           FLOAT NOT NULL,
    unit            TEXT NOT NULL,        -- 'ms', 'msg/s', 'count'
    commit_sha      TEXT,
    recorded_at     TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX ON perf_metrics (entity_id, repo, recorded_at);
CREATE INDEX ON perf_metrics (metric_type, metric_name, recorded_at);
```

Single flexible table. `metric_type` + `metric_name` + `entity_id` identifies what's being measured.

### Redis — Real-Time Snapshots

- `perf:realtime:{repo}` — hash with current pipeline state (throughput, queue depths, lag)
- `perf:hotfns:{repo}` — sorted set of functions by avg duration (ZADD with score = avg_ms)
- `perf:bottlenecks:{repo}` — list of currently detected bottlenecks (JSON, TTL 60s)

### Retention

Postgres rows older than 90 days can be pruned by a scheduled cleanup (not in MVP — schema supports it via indexed `recorded_at`).

---

## Analyzer

Runs on-demand (called by API endpoints) and on a schedule (after each collector batch).

### Pipeline Bottleneck Detection

- **Queue buildup:** `LLEN` / `XLEN` growing over consecutive snapshots — flag the downstream consumer as a bottleneck
- **Processing lag:** if ExecutionPath arrival consistently lags RawTrace by more than a threshold (configurable, default 10s), flag trace-normalizer as slow
- **Throughput drop:** compare current msg/s to rolling 1-hour average — flag if below 50%

### User-Code Bottleneck Detection

- **Slow functions:** top N functions by `avg_duration_ms` from `perf_metrics`
- **Regression detection:** requires explicit `base_sha` and `head_sha` (supplied by caller or API query params). Compares avg function durations between the two commits — flags functions where duration increased by more than the configured threshold (default 20%)
- **Hot paths:** functions with highest call frequency x duration product (cumulative time)
- **Deep call chains:** entrypoints with call depth exceeding a threshold (default 15)

### Trend Analysis (Historical)

- Query Postgres for a given `entity_id` over a time window
- Compute simple linear regression slope — positive slope = getting slower
- Return trend direction + magnitude

### Output Model

```python
class Bottleneck:
    entity_id: str
    entity_name: str
    category: str        # 'pipeline' | 'slow_fn' | 'regression' | 'hot_path' | 'deep_chain'
    severity: str        # 'critical' | 'warning' | 'info'
    value: float
    unit: str
    detail: str          # human-readable explanation
    repo: str
    detected_at: datetime
```

---

## API Endpoints

All mounted under `/api/v1` prefix.

### Real-Time

- `GET /api/v1/realtime/{repo}` — current pipeline state (throughput, queue depths, lag, active bottlenecks)
- `GET /api/v1/bottlenecks/{repo}` — currently detected bottlenecks, filterable by `?category=pipeline&severity=critical`

### User-Code Analysis

- `GET /api/v1/slow-functions/{repo}` — top N slowest functions, `?limit=20&commit_sha=abc`
- `GET /api/v1/hot-paths/{repo}` — highest cumulative-time functions
- `GET /api/v1/regressions/{repo}` — functions that got slower between commits, `?base_sha=abc&head_sha=def`

### Historical

- `GET /api/v1/trends/{repo}/{entity_id}` — time-series data for a specific metric, `?window=24h&metric_name=fn_duration`
- `GET /api/v1/summary/{repo}` — high-level overview: total bottlenecks by severity, top 5 slowest functions, pipeline health status

### Trigger

- `POST /api/v1/analyze/{repo}` — run analysis synchronously, returns 200 with full analysis results (bottlenecks, slow functions, pipeline health). For repos with large history this may take a few seconds, but avoids the complexity of async polling for the MVP

---

## CLI

Thin Python script at `services/perf-tracker/cli.py` that hits the API and formats output.

### Commands

- `python cli.py status <repo>` — real-time pipeline health
- `python cli.py bottlenecks <repo> [--category pipeline|slow_fn|regression] [--severity critical|warning]` — current bottlenecks
- `python cli.py slow <repo> [--limit 20] [--commit SHA]` — slowest functions
- `python cli.py regressions <repo> --base SHA --head SHA` — regression report
- `python cli.py trends <repo> <entity_id> [--window 24h]` — trend for a metric
- `python cli.py summary <repo>` — dashboard-style overview

### Output

- Tabular by default (simple column alignment, no external dependency)
- `--json` flag for machine-readable output

### Configuration

- `--host` and `--port` flags, defaulting to `localhost:8098`
- Reads `PERF_TRACKER_URL` env var as override

---

## Testing

### Unit Tests

- `conftest.py` — shared fixtures for mock store, mock Redis, sample RawTrace/ExecutionPath data
- `test_collector.py` — mock Redis streams, verify metric extraction from RawTrace and ExecutionPath messages
- `test_analyzer.py` — feed known metrics, verify bottleneck detection logic (queue buildup, regression detection, trend slope)
- `test_store.py` — mock Postgres/Redis, verify writes and reads
- `test_api.py` — FastAPI TestClient against all endpoints, mock the store and analyzer
- `test_cli.py` — mock httpx responses, verify output formatting

### Pattern

Same as existing services: `unittest.mock.patch` for external dependencies, `TestClient` for endpoints, `pytest-asyncio` for async tests.

### CLI

`cli.py` is a standalone script with no imports from `app/`. Only dependency beyond stdlib is `httpx`.

---

## Definition of Done

- [ ] Service starts and /health returns 200
- [ ] Collector consumes from both streams via new consumer groups
- [ ] Real-time Redis snapshots updated every 5 seconds
- [ ] Historical metrics written to Postgres every 60 seconds
- [ ] All API endpoints return correct data
- [ ] Bottleneck detection identifies pipeline and user-code issues
- [ ] Trend analysis computes regression slopes
- [ ] CLI commands produce formatted output
- [ ] Dockerfile builds cleanly
- [ ] Unit tests pass
