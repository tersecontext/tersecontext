# Instrumenter Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the instrumenter FastAPI service — a stateless config generator that returns a JSON patch-spec to the trace runner so it can instrument a Python subprocess with sys.settrace and I/O mocks.

**Architecture:** Single stateless FastAPI app with no external dependencies. `POST /instrument` generates a UUID session, reads env vars for timeout/tempdir prefix, and returns a fixed patch catalog as a JSON config. No state stored anywhere — every request is fully independent.

**Tech Stack:** Python 3.12, FastAPI, Pydantic v2, uvicorn, pytest + httpx TestClient

---

## File Map

| File | Responsibility |
|------|----------------|
| `services/instrumenter/requirements.txt` | Python dependencies |
| `services/instrumenter/Dockerfile` | Container build |
| `services/instrumenter/app/__init__.py` | Empty package marker |
| `services/instrumenter/app/models.py` | Pydantic models: `InstrumentRequest`, `PatchSpec`, `InstrumentResponse` |
| `services/instrumenter/app/config.py` | `PATCH_CATALOG` list + startup validation |
| `services/instrumenter/app/main.py` | FastAPI app: `/health`, `/ready`, `/metrics`, `POST /instrument` |
| `services/instrumenter/tests/__init__.py` | Empty package marker |
| `services/instrumenter/tests/test_config.py` | Catalog shape tests |
| `services/instrumenter/tests/test_main.py` | Endpoint tests |
| `docker-compose.yml` | Add `instrumenter` service entry |

---

## Task 1: Scaffold

**Files:**
- Create: `services/instrumenter/requirements.txt`
- Create: `services/instrumenter/Dockerfile`
- Create: `services/instrumenter/app/__init__.py`
- Create: `services/instrumenter/tests/__init__.py`

- [ ] **Step 1: Create requirements.txt**

```
services/instrumenter/requirements.txt
```
```
fastapi>=0.115.0
uvicorn[standard]>=0.30.0
pydantic>=2.0.0
pytest>=8.0.0
httpx>=0.27.0
pytest-asyncio>=0.23.0
```

- [ ] **Step 2: Create Dockerfile**

```
services/instrumenter/Dockerfile
```
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

- [ ] **Step 3: Create empty package markers**

```
services/instrumenter/app/__init__.py     (empty)
services/instrumenter/tests/__init__.py   (empty)
```

- [ ] **Step 4: Install dependencies**

Run from `services/instrumenter/`:
```bash
pip install -r requirements.txt
```
Expected: all packages install without error.

- [ ] **Step 5: Commit**

```bash
git add services/instrumenter/requirements.txt services/instrumenter/Dockerfile services/instrumenter/app/__init__.py services/instrumenter/tests/__init__.py
git commit -m "feat(instrumenter): scaffold service"
```

---

## Task 2: Models and Patch Catalog

**Files:**
- Create: `services/instrumenter/tests/test_config.py`
- Create: `services/instrumenter/app/models.py`
- Create: `services/instrumenter/app/config.py`

- [ ] **Step 1: Write the failing test**

`services/instrumenter/tests/test_config.py`:
```python
# services/instrumenter/tests/test_config.py
from app.config import PATCH_CATALOG
from app.models import PatchSpec

KNOWN_TARGETS = {
    "sqlalchemy.orm.Session.execute",
    "sqlalchemy.engine.Engine.connect",
    "psycopg2.connect",
    "httpx.Client.request",
    "httpx.AsyncClient.request",
    "requests.request",
    "builtins.open",
}


def test_known_targets_present():
    catalog_targets = {entry["target"] for entry in PATCH_CATALOG}
    assert KNOWN_TARGETS.issubset(catalog_targets)


def test_all_actions_valid():
    for entry in PATCH_CATALOG:
        PatchSpec(**entry)  # raises ValidationError if invalid action


def test_catalog_validates_on_import():
    from app.config import _validated
    assert len(_validated) == len(PATCH_CATALOG)
```

- [ ] **Step 2: Run test to verify it fails**

Run from `services/instrumenter/`:
```bash
python -m pytest tests/test_config.py -v
```
Expected: FAIL — `ModuleNotFoundError: No module named 'app.config'`

- [ ] **Step 3: Write models.py**

`services/instrumenter/app/models.py`:
```python
# services/instrumenter/app/models.py
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel


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

- [ ] **Step 4: Write config.py**

`services/instrumenter/app/config.py`:
```python
# services/instrumenter/app/config.py
from __future__ import annotations

from .models import PatchSpec

PATCH_CATALOG: list[dict] = [
    {"target": "sqlalchemy.orm.Session.execute",   "action": "mock_db"},
    {"target": "sqlalchemy.engine.Engine.connect", "action": "mock_db"},
    {"target": "psycopg2.connect",                 "action": "mock_db"},
    {"target": "httpx.Client.request",             "action": "mock_http"},
    {"target": "httpx.AsyncClient.request",        "action": "mock_http"},
    {"target": "requests.request",                 "action": "mock_http"},
    {"target": "builtins.open",                    "action": "redirect_writes"},
]

# Validate entire catalog against PatchSpec on import — fails fast if catalog has typos
_validated: list[PatchSpec] = [PatchSpec(**entry) for entry in PATCH_CATALOG]
```

- [ ] **Step 5: Run test to verify it passes**

```bash
python -m pytest tests/test_config.py -v
```
Expected: 3 PASSED

- [ ] **Step 6: Commit**

```bash
git add services/instrumenter/app/models.py services/instrumenter/app/config.py services/instrumenter/tests/test_config.py
git commit -m "feat(instrumenter): add models and patch catalog"
```

---

## Task 3: Standard Endpoints

**Files:**
- Create: `services/instrumenter/tests/test_main.py` (health/ready/metrics tests only)
- Create: `services/instrumenter/app/main.py` (app + standard endpoints)

- [ ] **Step 1: Write the failing tests**

`services/instrumenter/tests/test_main.py`:
```python
# services/instrumenter/tests/test_main.py
import uuid as uuid_lib

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_health():
    resp = client.get("/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"
    assert data["service"] == "instrumenter"
    assert data["version"] == "0.1.0"


def test_ready():
    resp = client.get("/ready")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


def test_metrics():
    resp = client.get("/metrics")
    assert resp.status_code == 200
    assert "instrumenter_sessions_created_total" in resp.text
```

- [ ] **Step 2: Run test to verify it fails**

```bash
python -m pytest tests/test_main.py -v
```
Expected: FAIL — `ModuleNotFoundError: No module named 'app.main'`

- [ ] **Step 3: Write main.py with standard endpoints**

`services/instrumenter/app/main.py`:
```python
# services/instrumenter/app/main.py
from __future__ import annotations

import logging
import os
import uuid

from fastapi import FastAPI
from fastapi.responses import JSONResponse, PlainTextResponse

from .config import PATCH_CATALOG
from .models import InstrumentRequest, InstrumentResponse, PatchSpec

logger = logging.getLogger(__name__)

VERSION = "0.1.0"
SERVICE = "instrumenter"

_sessions_created: int = 0

app = FastAPI()


@app.get("/health")
def health():
    return {"status": "ok", "service": SERVICE, "version": VERSION}


@app.get("/ready")
def ready():
    return {"status": "ok"}


@app.get("/metrics")
def metrics():
    lines = [
        "# HELP instrumenter_sessions_created_total Total instrumentation sessions created",
        "# TYPE instrumenter_sessions_created_total counter",
        f"instrumenter_sessions_created_total {_sessions_created}",
    ]
    return PlainTextResponse("\n".join(lines) + "\n")
```

- [ ] **Step 4: Run test to verify it passes**

```bash
python -m pytest tests/test_main.py -v
```
Expected: 3 PASSED

- [ ] **Step 5: Commit**

```bash
git add services/instrumenter/app/main.py services/instrumenter/tests/test_main.py
git commit -m "feat(instrumenter): add health, ready, metrics endpoints"
```

---

## Task 4: POST /instrument Endpoint

**Files:**
- Modify: `services/instrumenter/tests/test_main.py` (add instrument tests)
- Modify: `services/instrumenter/app/main.py` (add /instrument endpoint)

- [ ] **Step 1: Add failing tests to test_main.py**

Append to `services/instrumenter/tests/test_main.py`:
```python

def test_instrument_happy_path():
    resp = client.post("/instrument", json={
        "stable_id": "sha256:fn_test_login",
        "file_path": "tests/test_auth.py",
        "repo": "test",
    })
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ready"
    uuid_lib.UUID(data["session_id"])  # raises ValueError if not a valid UUID
    assert data["session_id"] in data["output_key"]
    assert data["output_key"] == f"trace_events:{data['session_id']}"
    assert isinstance(data["patches"], list)
    assert len(data["patches"]) > 0
    assert data["timeout_ms"] > 0
    assert data["tempdir"].endswith(data["session_id"])


def test_instrument_unique_session_ids():
    payload = {"stable_id": "sha256:fn", "file_path": "f.py", "repo": "r"}
    r1 = client.post("/instrument", json=payload).json()
    r2 = client.post("/instrument", json=payload).json()
    assert r1["session_id"] != r2["session_id"]


def test_instrument_missing_field_returns_422():
    resp = client.post("/instrument", json={"stable_id": "x", "file_path": "y"})
    assert resp.status_code == 422


def test_instrument_empty_stable_id_returns_400():
    resp = client.post("/instrument", json={
        "stable_id": "",
        "file_path": "tests/test_auth.py",
        "repo": "test",
    })
    assert resp.status_code == 400


def test_instrument_empty_file_path_returns_400():
    resp = client.post("/instrument", json={
        "stable_id": "sha256:fn",
        "file_path": "",
        "repo": "test",
    })
    assert resp.status_code == 400


def test_instrument_empty_repo_returns_400():
    resp = client.post("/instrument", json={
        "stable_id": "sha256:fn",
        "file_path": "tests/test_auth.py",
        "repo": "",
    })
    assert resp.status_code == 400


def test_instrument_all_known_patch_targets_present():
    resp = client.post("/instrument", json={
        "stable_id": "sha256:fn",
        "file_path": "f.py",
        "repo": "r",
    })
    targets = {p["target"] for p in resp.json()["patches"]}
    expected = {
        "sqlalchemy.orm.Session.execute",
        "sqlalchemy.engine.Engine.connect",
        "psycopg2.connect",
        "httpx.Client.request",
        "httpx.AsyncClient.request",
        "requests.request",
        "builtins.open",
    }
    assert expected.issubset(targets)
```

- [ ] **Step 2: Run tests to verify new ones fail**

```bash
python -m pytest tests/test_main.py -v
```
Expected: first 3 pass, new tests FAIL — `404 Not Found` for `/instrument`

- [ ] **Step 3: Add /instrument endpoint to main.py**

Append to `services/instrumenter/app/main.py` (after the `/metrics` endpoint):
```python

@app.post("/instrument")
def instrument(req: InstrumentRequest) -> InstrumentResponse:
    global _sessions_created

    if not req.stable_id or not req.file_path or not req.repo:
        return JSONResponse(
            status_code=400,
            content={"error": "stable_id, file_path, and repo are required"},
        )

    session_id = str(uuid.uuid4())
    tempdir_prefix = os.environ.get("TEMPDIR_PREFIX", "/tmp/tc")
    timeout_ms = int(os.environ.get("TIMEOUT_MS", "30000"))

    _sessions_created += 1

    return InstrumentResponse(
        session_id=session_id,
        status="ready",
        patches=[PatchSpec(**entry) for entry in PATCH_CATALOG],
        output_key=f"trace_events:{session_id}",
        tempdir=f"{tempdir_prefix}/{session_id}",
        timeout_ms=timeout_ms,
    )
```

- [ ] **Step 4: Run all tests to verify they all pass**

```bash
python -m pytest tests/ -v
```
Expected: all tests PASSED (test_config.py + test_main.py)

- [ ] **Step 5: Commit**

```bash
git add services/instrumenter/app/main.py services/instrumenter/tests/test_main.py
git commit -m "feat(instrumenter): add POST /instrument endpoint"
```

---

## Task 5: docker-compose Integration

**Files:**
- Modify: `docker-compose.yml` (root of repo)

- [ ] **Step 1: Add instrumenter service to docker-compose.yml**

Open `docker-compose.yml` and add the following block in the `services:` section (after the `repo-watcher` entry is a natural location):

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

Note: no `depends_on` — the instrumenter has no external dependencies.

- [ ] **Step 2: Verify docker-compose config parses cleanly**

Run from repo root:
```bash
docker compose config --quiet
```
Expected: exits 0 with no errors.

- [ ] **Step 3: Commit**

```bash
git add docker-compose.yml
git commit -m "feat(instrumenter): add to docker-compose"
```

---

## Verification

After all tasks complete, run the full test suite and confirm the CLAUDE.md definition of done:

```bash
# All unit tests pass
cd services/instrumenter && python -m pytest tests/ -v

# Manual endpoint check (requires running service)
curl -X POST http://localhost:8093/instrument \
  -H 'Content-Type: application/json' \
  -d '{"stable_id":"sha256:fn_test_login","file_path":"tests/test_auth.py","repo":"test"}'
# expect: {"session_id":"<uuid>","status":"ready","patches":[...],...}
```

Expected test output:
```
tests/test_config.py::test_known_targets_present PASSED
tests/test_config.py::test_all_actions_valid PASSED
tests/test_config.py::test_catalog_validates_on_import PASSED
tests/test_main.py::test_health PASSED
tests/test_main.py::test_ready PASSED
tests/test_main.py::test_metrics PASSED
tests/test_main.py::test_instrument_happy_path PASSED
tests/test_main.py::test_instrument_unique_session_ids PASSED
tests/test_main.py::test_instrument_missing_field_returns_422 PASSED
tests/test_main.py::test_instrument_empty_stable_id_returns_400 PASSED
tests/test_main.py::test_instrument_empty_file_path_returns_400 PASSED
tests/test_main.py::test_instrument_empty_repo_returns_400 PASSED
tests/test_main.py::test_instrument_all_known_patch_targets_present PASSED
```
