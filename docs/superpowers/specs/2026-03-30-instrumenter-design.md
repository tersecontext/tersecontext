# Instrumenter — Design Spec
_Date: 2026-03-30_

## Overview

The instrumenter is a stateless FastAPI service (port 8093) that acts as a pure configuration generator for the dynamic analysis pipeline. It receives an entrypoint descriptor from the trace runner, generates a UUID session, and returns a patch-spec JSON payload describing exactly which I/O targets to mock and where to write trace output. It never executes code, never writes files, and has no external dependencies.

---

## Architecture Decision: Stateless Config Generator

The instrumenter does not execute instrumented code and has no `/run` endpoint. Execution responsibility belongs entirely to the trace runner, which spawns an isolated subprocess and applies the instrumentation config returned by this service. This separation ensures that crashes, segfaults, or hangs in instrumented code cannot affect the instrumenter service.

---

## File Layout

```
services/instrumenter/
  app/
    __init__.py
    main.py        # FastAPI app: /health /ready /metrics /instrument
    models.py      # Pydantic: InstrumentRequest, InstrumentResponse, PatchSpec
    config.py      # PATCH_CATALOG: fixed list of patch targets + actions
  Dockerfile
  requirements.txt
  tests/
    __init__.py
    test_main.py   # endpoint tests
    test_config.py # patch catalog shape tests
```

---

## API

### POST /instrument

**Request:**
```json
{
  "stable_id": "sha256:fn_test_login",
  "file_path": "tests/test_auth.py",
  "repo": "test"
}
```

**Response `200 OK`:**
```json
{
  "session_id": "550e8400-e29b-41d4-a716-446655440000",
  "status": "ready",
  "patches": [
    {"target": "sqlalchemy.orm.Session.execute",   "action": "mock_db"},
    {"target": "sqlalchemy.engine.Engine.connect", "action": "mock_db"},
    {"target": "psycopg2.connect",                 "action": "mock_db"},
    {"target": "httpx.Client.request",             "action": "mock_http"},
    {"target": "httpx.AsyncClient.request",        "action": "mock_http"},
    {"target": "requests.request",                 "action": "mock_http"},
    {"target": "builtins.open",                    "action": "redirect_writes"}
  ],
  "output_key": "trace_events:550e8400-e29b-41d4-a716-446655440000",
  "tempdir":    "/tmp/tc/550e8400-e29b-41d4-a716-446655440000",
  "timeout_ms": 30000
}
```

### Standard endpoints

- `GET /health` → `{"status":"ok","service":"instrumenter","version":"0.1.0"}`
- `GET /ready` → `{"status":"ok"}` 200 — always ready; the instrumenter has no external dependencies, so unlike repo-watcher which pings Redis, this endpoint never returns 503
- `GET /metrics` → stub Prometheus text with counter `instrumenter_sessions_created_total`

---

## Models (`models.py`)

```python
class InstrumentRequest(BaseModel):
    stable_id: str
    file_path: str
    repo: str

class PatchSpec(BaseModel):
    target: str
    action: Literal["mock_db", "mock_http", "redirect_writes"]

class InstrumentResponse(BaseModel):
    session_id: str
    status: Literal["ready"]
    patches: list[PatchSpec]
    output_key: str
    tempdir: str
    timeout_ms: int
```

---

## Patch Catalog (`config.py`)

A fixed list of patch targets validated against `PatchSpec` on startup:

| Target | Action |
|--------|--------|
| `sqlalchemy.orm.Session.execute` | `mock_db` |
| `sqlalchemy.engine.Engine.connect` | `mock_db` |
| `psycopg2.connect` | `mock_db` |
| `httpx.Client.request` | `mock_http` |
| `httpx.AsyncClient.request` | `mock_http` |
| `requests.request` | `mock_http` |
| `builtins.open` | `redirect_writes` |

SQLAlchemy 1.x's `Engine.execute` and `Connection.execute` were removed in 2.0. The catalog targets the current 2.x execution paths (`Session.execute` for ORM queries, `Engine.connect` to intercept connection acquisition).

The catalog is the single source of truth for what the trace runner's subprocess will patch. Adding a new patch target requires only updating this file.

**Cross-service contract:** The `output_key` value (`trace_events:{session_id}`) is defined here and must be honoured by the trace runner when writing trace events to Redis. The trace runner spec must reference this key pattern explicitly.

**Patch error handling:** The trace runner must apply patches with `create=False` (the default). If a target symbol is absent in the traced codebase (e.g., the repo uses a different DB driver), `unittest.mock.patch` will raise `AttributeError`. The trace runner should catch this per-patch, skip the failing patch with a warning log, and continue — a missing patch target should not abort the trace run.

---

## Environment Variables

| Var | Default | Description |
|-----|---------|-------------|
| `TIMEOUT_MS` | `30000` | Execution timeout passed to trace runner subprocess |
| `TEMPDIR_PREFIX` | `/tmp/tc` | Base path for per-session filesystem redirect dirs. The `tempdir` value in the response (`{TEMPDIR_PREFIX}/{session_id}`) is a convention — the instrumenter never creates this directory. The trace runner must create it on its own filesystem before applying the `redirect_writes` patch. |

---

## Error Handling

| Condition | Status | Body |
|-----------|--------|------|
| Missing required field | 422 | Pydantic default validation error |
| Empty `stable_id`, `file_path`, or `repo` | 400 | `{"error": "stable_id, file_path, and repo are required"}` |

No 500s expected in normal operation.

---

## Testing

**`tests/test_main.py`:**
- Happy path: valid request → 200, session_id is UUID, status is "ready", patches is non-empty, output_key contains session_id, timeout_ms is positive int
- Two calls with same input produce different `session_id` values
- Missing fields → 422
- Empty string fields → 400
- `/health` → 200 with correct service/version fields
- `/ready` → 200
- `/metrics` → 200, body contains `instrumenter_sessions_created_total`

**`tests/test_config.py`:**
- The 7 known patch targets are a subset of `PATCH_CATALOG` (set-containment check, not exact length — catalog may be extended over time)
- All entries in `PATCH_CATALOG` have actions that are valid `PatchSpec` literals
- Catalog validates cleanly against `PatchSpec` model on import

---

## Infrastructure

### `requirements.txt`
```
fastapi>=0.115.0
uvicorn[standard]>=0.30.0
pydantic>=2.0.0
pytest>=8.0.0
httpx>=0.27.0
pytest-asyncio>=0.23.0
```

### `Dockerfile`
```dockerfile
FROM python:3.12-slim
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends curl && rm -rf /var/lib/apt/lists/*
RUN pip install --no-cache-dir uv
COPY requirements.txt ./
RUN uv pip install --system -r requirements.txt
COPY app/ app/
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080"]
```

### `docker-compose.yml` addition
```yaml
instrumenter:
  build: ./services/instrumenter
  ports:
    - "8093:8080"
  environment:
    TIMEOUT_MS: "30000"
    TEMPDIR_PREFIX: "/tmp/tc"
  networks:
    - tersecontext
  healthcheck:
    test: ["CMD", "curl", "-f", "http://localhost:8080/health"]
    interval: 10s
    timeout: 5s
    retries: 5
```

No `depends_on` — fully standalone service.

---

## Definition of Done

- [ ] `GET /health` returns `{"status":"ok","service":"instrumenter","version":"0.1.0"}`
- [ ] `GET /ready` returns 200
- [ ] `GET /metrics` returns 200 with `instrumenter_sessions_created_total` counter
- [ ] `POST /instrument` returns 200 with all 7 patches, valid UUID session_id, output_key containing session_id
- [ ] Two calls with same input return different session_ids
- [ ] Empty field validation returns 400
- [ ] All unit tests pass
- [ ] Dockerfile builds cleanly
- [ ] Service added to `docker-compose.yml`
