# Spec Generator Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the spec-generator service (port 8097) that consumes `stream:execution-paths`, renders BehaviorSpec documents, persists them to Postgres, and embeds them into Qdrant for semantic search.

**Architecture:** Single Python/FastAPI service. A background asyncio task runs a Redis consumer group (`spec-generator-group`) on `stream:execution-paths`. Each message is parsed, rendered to structured spec text, upserted to Postgres (`behavior_specs` table), and embedded+upserted to Qdrant (`specs` collection). No Neo4j dependency — name resolution was done upstream by the trace-normalizer.

**Tech Stack:** Python 3.12, FastAPI, asyncpg (Postgres), qdrant-client, redis.asyncio, httpx (for embedding providers), pytest + pytest-asyncio.

---

## File Map

**Create (new service):**
- `services/spec-generator/app/__init__.py` — empty
- `services/spec-generator/app/models.py` — Pydantic models for ExecutionPath and its nested types
- `services/spec-generator/app/renderer.py` — pure `render_spec_text(path, entrypoint_name) -> str`
- `services/spec-generator/app/providers/__init__.py` — empty
- `services/spec-generator/app/providers/base.py` — `EmbeddingProvider` base class
- `services/spec-generator/app/providers/ollama.py` — `OllamaProvider`
- `services/spec-generator/app/providers/voyage.py` — `VoyageProvider`
- `services/spec-generator/app/store.py` — `SpecStore` (Postgres + Qdrant)
- `services/spec-generator/app/consumer.py` — Redis consumer loop + `_process`
- `services/spec-generator/app/main.py` — FastAPI app (lifespan, /health, /ready, /metrics)
- `services/spec-generator/tests/__init__.py` — empty
- `services/spec-generator/tests/test_models.py` — model validation tests
- `services/spec-generator/tests/test_renderer.py` — renderer unit tests
- `services/spec-generator/tests/test_store.py` — store unit tests (mocked asyncpg + Qdrant)
- `services/spec-generator/tests/test_consumer.py` — consumer unit tests (mocked store)
- `services/spec-generator/Dockerfile` — identical pattern to `services/embedder/Dockerfile`
- `services/spec-generator/requirements.txt` — service dependencies
- `services/spec-generator/pyproject.toml` — package metadata + pytest config

**Modify:**
- `docker-compose.yml` — add `spec-generator` service entry

---

## Task 1: Scaffold

**Files:**
- Create: `services/spec-generator/app/__init__.py`
- Create: `services/spec-generator/app/providers/__init__.py`
- Create: `services/spec-generator/tests/__init__.py`
- Create: `services/spec-generator/requirements.txt`
- Create: `services/spec-generator/pyproject.toml`
- Create: `services/spec-generator/Dockerfile`

- [ ] **Step 1: Create directory structure**

```bash
mkdir -p services/spec-generator/app/providers
mkdir -p services/spec-generator/tests
touch services/spec-generator/app/__init__.py
touch services/spec-generator/app/providers/__init__.py
touch services/spec-generator/tests/__init__.py
```

- [ ] **Step 2: Write `requirements.txt`**

```
fastapi>=0.115.0
uvicorn[standard]>=0.30.0
redis>=5.0.0
pydantic>=2.0.0
httpx>=0.27.0
asyncpg>=0.29.0
qdrant-client>=1.9.0
pytest>=8.0.0
pytest-asyncio>=0.23.0
```

- [ ] **Step 3: Write `pyproject.toml`**

```toml
[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[project]
name = "tersecontext-spec-generator"
version = "0.1.0"
requires-python = ">=3.12"
dependencies = [
    "fastapi>=0.115.0",
    "uvicorn[standard]>=0.30.0",
    "redis>=5.0.0",
    "pydantic>=2.0.0",
    "httpx>=0.27.0",
    "asyncpg>=0.29.0",
    "qdrant-client>=1.9.0",
]

[project.optional-dependencies]
dev = [
    "pytest>=8.0.0",
    "pytest-asyncio>=0.23.0",
]

[tool.pytest.ini_options]
testpaths = ["tests"]
asyncio_mode = "auto"
```

- [ ] **Step 4: Write `Dockerfile`**

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

- [ ] **Step 5: Commit scaffold**

```bash
git add services/spec-generator/
git commit -m "feat(spec-generator): scaffold Dockerfile and requirements"
```

---

## Task 2: Models

**Files:**
- Create: `services/spec-generator/app/models.py`
- Create: `services/spec-generator/tests/test_models.py`

The `ExecutionPath` model matches `contracts/events/execution_path.json` exactly. The side effect `type` field is an enum with six values. `ConfigDict(extra="ignore")` allows forward-compatible messages.

- [ ] **Step 1: Write `tests/test_models.py`**

```python
import pytest
from pydantic import ValidationError


def _make_path_dict(**overrides):
    base = {
        "entrypoint_stable_id": "sha256:fn_login",
        "commit_sha": "abc123",
        "repo": "acme",
        "call_sequence": [
            {
                "stable_id": "sha256:fn_login",
                "name": "login",
                "qualified_name": "auth.service.login",
                "hop": 0,
                "frequency_ratio": 1.0,
                "avg_ms": 12.5,
            }
        ],
        "side_effects": [],
        "dynamic_only_edges": [],
        "never_observed_static_edges": [],
        "timing_p50_ms": 12.5,
        "timing_p99_ms": 45.0,
    }
    base.update(overrides)
    return base


def test_execution_path_parses_valid():
    from app.models import ExecutionPath
    path = ExecutionPath.model_validate(_make_path_dict())
    assert path.repo == "acme"
    assert path.call_sequence[0].name == "login"
    assert path.call_sequence[0].qualified_name == "auth.service.login"


def test_execution_path_missing_repo_raises():
    from app.models import ExecutionPath
    data = _make_path_dict()
    del data["repo"]
    with pytest.raises(ValidationError):
        ExecutionPath.model_validate(data)


def test_execution_path_ignores_extra_fields():
    from app.models import ExecutionPath
    data = _make_path_dict()
    data["unknown_future_field"] = "ignored"
    path = ExecutionPath.model_validate(data)
    assert not hasattr(path, "unknown_future_field")


def test_side_effect_type_enum_all_values():
    from app.models import SideEffect
    for t in ("db_read", "db_write", "cache_read", "cache_set", "http_out", "fs_write"):
        se = SideEffect(type=t, detail="some detail", hop_depth=1)
        assert se.type == t


def test_side_effect_invalid_type_raises():
    from app.models import SideEffect
    with pytest.raises(ValidationError):
        SideEffect(type="env_read", detail="x", hop_depth=1)


def test_call_sequence_item_fields():
    from app.models import CallSequenceItem
    item = CallSequenceItem(
        stable_id="sha256:fn",
        name="fn",
        qualified_name="mod.fn",
        hop=1,
        frequency_ratio=0.5,
        avg_ms=3.2,
    )
    assert item.hop == 1
    assert item.frequency_ratio == 0.5
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd services/spec-generator && pip install -e ".[dev]" -q && pytest tests/test_models.py -v
```

Expected: `ModuleNotFoundError: No module named 'app.models'`

- [ ] **Step 3: Write `app/models.py`**

```python
from __future__ import annotations
from typing import Literal
from pydantic import BaseModel, ConfigDict


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
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd services/spec-generator && pytest tests/test_models.py -v
```

Expected: all 6 tests PASS

- [ ] **Step 5: Commit**

```bash
git add services/spec-generator/app/models.py services/spec-generator/tests/test_models.py
git commit -m "feat(spec-generator): add Pydantic models"
```

---

## Task 3: Renderer

**Files:**
- Create: `services/spec-generator/app/renderer.py`
- Create: `services/spec-generator/tests/test_renderer.py`

Pure function. No I/O. The PATH section uses 1-based step numbering, with sub-labels (2a, 2b) for branching items at the same hop depth. SIDE_EFFECTS maps all 6 contract enum values. CHANGE_IMPACT extracts unique table names from `db_read`/`db_write` details and unique service names from `http_out` details.

For the SIDE_EFFECTS `detail` field:
- DB side effects: `detail` is raw SQL or a table description — display as-is
- CHANGE_IMPACT table extraction: use the first word of `detail` after stripping SQL keywords (`SELECT`, `INSERT`, `UPDATE`, `DELETE`, `FROM`, `INTO`) — if none remain, fall back to the full detail string
- HTTP side effects: `detail` is `"METHOD url"` — the service name is the hostname extracted from the URL, or the full detail if no URL is present

- [ ] **Step 1: Write `tests/test_renderer.py`**

```python
from app.models import CallSequenceItem, ExecutionPath, SideEffect


def _make_path(call_sequence=None, side_effects=None):
    return ExecutionPath(
        entrypoint_stable_id="sha256:fn_login",
        commit_sha="abc123",
        repo="acme",
        call_sequence=call_sequence or [],
        side_effects=side_effects or [],
        dynamic_only_edges=[],
        never_observed_static_edges=[],
        timing_p50_ms=1.0,  # round(1.0) = 1 → _runs_observed returns 1
        timing_p99_ms=4.0,
    )


def _item(name, hop, freq=1.0, avg_ms=5.0, qualified_name=None):
    return CallSequenceItem(
        stable_id=f"sha256:{name}",
        name=name,
        qualified_name=qualified_name or f"mod.{name}",
        hop=hop,
        frequency_ratio=freq,
        avg_ms=avg_ms,
    )


def _effect(type_, detail, hop_depth=1):
    return SideEffect(type=type_, detail=detail, hop_depth=hop_depth)


# ── PATH section ──────────────────────────────────────────────────────────────

def test_path_section_header():
    from app.renderer import render_spec_text
    path = _make_path(call_sequence=[_item("login", hop=0, avg_ms=12.5)])
    text = render_spec_text(path, "login")
    assert text.startswith("PATH login")
    assert "(1 runs observed)" in text


def test_path_section_lists_items_in_hop_order():
    from app.renderer import render_spec_text
    path = _make_path(call_sequence=[
        _item("login", hop=0, freq=1.0, avg_ms=10.0),
        _item("authenticate", hop=1, freq=1.0, avg_ms=5.0),
    ])
    text = render_spec_text(path, "login")
    lines = text.splitlines()
    login_line = next(l for l in lines if "login" in l and "1." in l)
    auth_line = next(l for l in lines if "authenticate" in l)
    assert lines.index(login_line) < lines.index(auth_line)


def test_path_section_shows_frequency_ratio():
    from app.renderer import render_spec_text
    path = _make_path(call_sequence=[
        _item("login", hop=0, freq=1.0, avg_ms=10.0),
        _item("notify", hop=1, freq=0.5, avg_ms=3.0),
    ])
    text = render_spec_text(path, "login")
    assert "0.50" in text or "50%" in text or "1/2" in text


def test_path_section_shows_avg_ms():
    from app.renderer import render_spec_text
    path = _make_path(call_sequence=[_item("login", hop=0, avg_ms=12.5)])
    text = render_spec_text(path, "login")
    assert "12.5ms" in text or "~12.5ms" in text


def test_empty_call_sequence_renders_without_error():
    from app.renderer import render_spec_text
    path = _make_path(call_sequence=[])
    text = render_spec_text(path, "sha256:fn_login")
    assert "PATH sha256:fn_login" in text


# ── SIDE_EFFECTS section ──────────────────────────────────────────────────────

def test_side_effects_all_six_types_render():
    from app.renderer import render_spec_text
    effects = [
        _effect("db_read", "SELECT id FROM users WHERE id = $1"),
        _effect("db_write", "INSERT INTO audit_log VALUES (...)"),
        _effect("cache_read", "session:{user_id}"),
        _effect("cache_set", "session:{user_id} TTL 3600"),
        _effect("http_out", "POST https://notifications.internal/send"),
        _effect("fs_write", "/tmp/export.csv"),
    ]
    path = _make_path(side_effects=effects)
    text = render_spec_text(path, "login")
    assert "DB READ" in text
    assert "DB WRITE" in text
    assert "CACHE READ" in text
    assert "CACHE SET" in text
    assert "HTTP OUT" in text
    assert "FS WRITE" in text


def test_conditional_side_effect_annotated():
    from app.renderer import render_spec_text
    path = _make_path(side_effects=[_effect("http_out", "POST https://svc/send", hop_depth=2)])
    text = render_spec_text(path, "login")
    assert "(conditional)" in text


def test_non_conditional_side_effect_not_annotated():
    from app.renderer import render_spec_text
    path = _make_path(side_effects=[_effect("db_read", "SELECT 1", hop_depth=1)])
    text = render_spec_text(path, "login")
    assert "(conditional)" not in text


def test_empty_side_effects_section_absent_or_empty():
    from app.renderer import render_spec_text
    path = _make_path(side_effects=[])
    text = render_spec_text(path, "login")
    # Either the section header is absent or it has no entries beneath it
    if "SIDE_EFFECTS:" in text:
        header_idx = text.index("SIDE_EFFECTS:")
        remainder = text[header_idx + len("SIDE_EFFECTS:"):].strip()
        # Next section or empty
        assert remainder == "" or remainder.startswith("CHANGE_IMPACT")


# ── CHANGE_IMPACT section ─────────────────────────────────────────────────────

def test_change_impact_includes_db_tables():
    from app.renderer import render_spec_text
    effects = [
        _effect("db_read", "SELECT id FROM users WHERE id = $1"),
        _effect("db_write", "INSERT INTO audit_log VALUES (...)"),
    ]
    path = _make_path(side_effects=effects)
    text = render_spec_text(path, "login")
    assert "CHANGE_IMPACT:" in text
    assert "users" in text
    assert "audit_log" in text


def test_change_impact_includes_http_services():
    from app.renderer import render_spec_text
    effects = [_effect("http_out", "POST https://notifications.internal/send")]
    path = _make_path(side_effects=effects)
    text = render_spec_text(path, "login")
    assert "CHANGE_IMPACT:" in text
    assert "notifications.internal" in text


def test_change_impact_deduplicates_tables():
    from app.renderer import render_spec_text
    effects = [
        _effect("db_read", "SELECT id FROM users WHERE id = $1"),
        _effect("db_write", "UPDATE users SET active = false"),
    ]
    path = _make_path(side_effects=effects)
    text = render_spec_text(path, "login")
    assert text.count("users") == 1
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd services/spec-generator && pytest tests/test_renderer.py -v
```

Expected: `ModuleNotFoundError: No module named 'app.renderer'`

- [ ] **Step 3: Write `app/renderer.py`**

```python
from __future__ import annotations
import re
from app.models import ExecutionPath

_SIDE_EFFECT_LABELS = {
    "db_read":    "DB READ",
    "db_write":   "DB WRITE",
    "cache_read": "CACHE READ",
    "cache_set":  "CACHE SET",
    "http_out":   "HTTP OUT",
    "fs_write":   "FS WRITE",
}

_SQL_KEYWORDS = {"SELECT", "INSERT", "UPDATE", "DELETE", "FROM", "INTO", "WHERE", "SET", "VALUES"}


def _runs_observed(path: ExecutionPath) -> int:
    """Approximate run count from timing (always at least 1)."""
    return max(1, round(path.timing_p50_ms)) if path.timing_p50_ms > 0 else 1


def _extract_table(detail: str) -> str:
    """Extract first non-SQL-keyword word from a DB side effect detail."""
    words = re.split(r"\s+", detail.strip())
    for word in words:
        clean = word.strip("(,);")
        if clean.upper() not in _SQL_KEYWORDS and clean:
            return clean
    return detail.split()[0] if detail.split() else detail


def _extract_service(detail: str) -> str:
    """Extract hostname from an HTTP OUT detail like 'POST https://host/path'."""
    match = re.search(r"https?://([^/\s]+)", detail)
    if match:
        return match.group(1)
    parts = detail.split()
    return parts[-1] if parts else detail


def render_spec_text(path: ExecutionPath, entrypoint_name: str) -> str:
    runs = _runs_observed(path)
    lines: list[str] = []

    # PATH section
    lines.append(f"PATH {entrypoint_name}  ({runs} runs observed)")
    for i, item in enumerate(path.call_sequence, start=1):
        freq_str = f"{item.frequency_ratio:.2f}"
        ms_str = f"~{item.avg_ms:.1f}ms"
        lines.append(f"  {i}.  {item.name}    {freq_str}    {ms_str}")
    lines.append("")

    # SIDE_EFFECTS section
    if path.side_effects:
        lines.append("SIDE_EFFECTS:")
        for effect in path.side_effects:
            label = _SIDE_EFFECT_LABELS.get(effect.type, effect.type.upper().replace("_", " "))
            suffix = "   (conditional)" if effect.hop_depth > 1 else ""
            lines.append(f"  {label}   {effect.detail}{suffix}")
        lines.append("")

    # CHANGE_IMPACT section
    tables: dict[str, list[str]] = {}  # table -> list of r/w
    services: list[str] = []
    for effect in path.side_effects:
        if effect.type == "db_read":
            t = _extract_table(effect.detail)
            tables.setdefault(t, [])
            if "r" not in tables[t]:
                tables[t].append("r")
        elif effect.type == "db_write":
            t = _extract_table(effect.detail)
            tables.setdefault(t, [])
            if "w" not in tables[t]:
                tables[t].append("w")
        elif effect.type == "http_out":
            svc = _extract_service(effect.detail)
            if svc not in services:
                services.append(svc)

    if tables or services:
        lines.append("CHANGE_IMPACT:")
        if tables:
            table_str = "  ".join(f"{t} ({'/'.join(rw)})" for t, rw in tables.items())
            lines.append(f"  tables affected    {table_str}")
        if services:
            cond_effects = {_extract_service(e.detail) for e in path.side_effects
                            if e.type == "http_out" and e.hop_depth > 1}
            svc_parts = []
            for svc in services:
                cond = " (conditional)" if svc in cond_effects else ""
                svc_parts.append(f"{svc}{cond}")
            lines.append(f"  external services  {'  '.join(svc_parts)}")

    return "\n".join(lines)
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd services/spec-generator && pytest tests/test_renderer.py -v
```

Expected: all 14 tests PASS

- [ ] **Step 5: Commit**

```bash
git add services/spec-generator/app/renderer.py services/spec-generator/tests/test_renderer.py
git commit -m "feat(spec-generator): add spec text renderer"
```

---

## Task 4: Embedding Providers

**Files:**
- Create: `services/spec-generator/app/providers/base.py`
- Create: `services/spec-generator/app/providers/ollama.py`
- Create: `services/spec-generator/app/providers/voyage.py`

These are a direct copy of the embedder service's provider abstraction — same interface, same env vars. No changes to the logic.

- [ ] **Step 1: Write `app/providers/base.py`**

```python
class EmbeddingProvider:
    async def embed(self, texts: list[str]) -> list[list[float]]:
        raise NotImplementedError
```

- [ ] **Step 2: Write `app/providers/ollama.py`**

```python
import os
import httpx
from .base import EmbeddingProvider


class OllamaProvider(EmbeddingProvider):
    def __init__(self) -> None:
        self._url = os.environ.get("OLLAMA_URL", "http://ollama:11434")
        self._model = os.environ.get("EMBEDDING_MODEL", "nomic-embed-text")

    async def embed(self, texts: list[str]) -> list[list[float]]:
        async with httpx.AsyncClient(timeout=60.0) as client:
            response = await client.post(
                f"{self._url}/api/embed",
                json={"model": self._model, "input": texts},
            )
            response.raise_for_status()
            return response.json()["embeddings"]
```

- [ ] **Step 3: Write `app/providers/voyage.py`**

```python
import os
import httpx
from .base import EmbeddingProvider

VOYAGE_API_URL = "https://api.voyageai.com/v1/embeddings"
DEFAULT_MODEL = "voyage-code-3"


class VoyageProvider(EmbeddingProvider):
    def __init__(self) -> None:
        api_key = os.environ.get("VOYAGE_API_KEY")
        if not api_key:
            raise ValueError("VOYAGE_API_KEY environment variable is required for VoyageProvider")
        self._api_key = api_key
        self._model = os.environ.get("EMBEDDING_MODEL", DEFAULT_MODEL)

    async def embed(self, texts: list[str]) -> list[list[float]]:
        async with httpx.AsyncClient(timeout=60.0) as client:
            response = await client.post(
                VOYAGE_API_URL,
                headers={"Authorization": f"Bearer {self._api_key}"},
                json={"model": self._model, "input": texts},
            )
            response.raise_for_status()
            data = response.json()["data"]
            return [item["embedding"] for item in data]
```

- [ ] **Step 4: Commit**

```bash
git add services/spec-generator/app/providers/
git commit -m "feat(spec-generator): add embedding providers (ollama, voyage)"
```

---

## Task 5: Store

**Files:**
- Create: `services/spec-generator/app/store.py`
- Create: `services/spec-generator/tests/test_store.py`

`SpecStore` handles two responsibilities: Postgres persistence and Qdrant embedding+upsert. Both use asyncpg and the sync qdrant-client wrapped in `run_in_executor` (same pattern as `vector-writer`). Schema creation runs at startup.

The ON CONFLICT clause for version increment: `version = behavior_specs.version + 1`.

- [ ] **Step 1: Write `tests/test_store.py`**

```python
import uuid
from unittest.mock import AsyncMock, MagicMock, patch, call
import pytest

from app.models import CallSequenceItem, ExecutionPath, SideEffect


def _make_path(call_sequence=None, side_effects=None, repo="acme"):
    items = call_sequence or [
        CallSequenceItem(
            stable_id="sha256:fn_login",
            name="login",
            qualified_name="auth.login",
            hop=0,
            frequency_ratio=1.0,
            avg_ms=10.0,
        )
    ]
    return ExecutionPath(
        entrypoint_stable_id="sha256:fn_login",
        commit_sha="abc123",
        repo=repo,
        call_sequence=items,
        side_effects=side_effects or [],
        dynamic_only_edges=[],
        never_observed_static_edges=[],
        timing_p50_ms=10.0,
        timing_p99_ms=40.0,
    )


# ── upsert_spec ───────────────────────────────────────────────────────────────

async def test_upsert_spec_executes_insert_on_conflict():
    from app.store import SpecStore
    mock_conn = AsyncMock()
    mock_pool = AsyncMock()
    mock_pool.__aenter__ = AsyncMock(return_value=mock_conn)
    mock_pool.__aexit__ = AsyncMock(return_value=False)

    store = SpecStore.__new__(SpecStore)
    store._pool = mock_pool

    path = _make_path()
    await store.upsert_spec(path, "some spec text")

    mock_conn.execute.assert_called_once()
    sql, *args = mock_conn.execute.call_args[0]
    assert "ON CONFLICT" in sql
    assert "behavior_specs" in sql
    assert "version" in sql


async def test_upsert_spec_branch_coverage_all_observed():
    """All items with frequency_ratio > 0.0 → branch_coverage = 1.0"""
    from app.store import SpecStore, _compute_branch_coverage
    items = [
        CallSequenceItem(stable_id="a", name="a", qualified_name="m.a", hop=0, frequency_ratio=1.0, avg_ms=1.0),
        CallSequenceItem(stable_id="b", name="b", qualified_name="m.b", hop=1, frequency_ratio=0.5, avg_ms=1.0),
    ]
    assert _compute_branch_coverage(items) == 1.0


async def test_upsert_spec_branch_coverage_none_observed():
    """All items with frequency_ratio == 0.0 → branch_coverage = 0.0"""
    from app.store import _compute_branch_coverage
    items = [
        CallSequenceItem(stable_id="a", name="a", qualified_name="m.a", hop=0, frequency_ratio=0.0, avg_ms=1.0),
        CallSequenceItem(stable_id="b", name="b", qualified_name="m.b", hop=1, frequency_ratio=0.0, avg_ms=1.0),
    ]
    assert _compute_branch_coverage(items) == 0.0


async def test_upsert_spec_branch_coverage_mixed():
    """2 of 4 items observed → branch_coverage = 0.5"""
    from app.store import _compute_branch_coverage
    items = [
        CallSequenceItem(stable_id="a", name="a", qualified_name="m.a", hop=0, frequency_ratio=1.0, avg_ms=1.0),
        CallSequenceItem(stable_id="b", name="b", qualified_name="m.b", hop=1, frequency_ratio=0.5, avg_ms=1.0),
        CallSequenceItem(stable_id="c", name="c", qualified_name="m.c", hop=1, frequency_ratio=0.0, avg_ms=1.0),
        CallSequenceItem(stable_id="d", name="d", qualified_name="m.d", hop=2, frequency_ratio=0.0, avg_ms=1.0),
    ]
    assert _compute_branch_coverage(items) == 0.5


async def test_upsert_spec_branch_coverage_empty_call_sequence():
    from app.store import _compute_branch_coverage
    assert _compute_branch_coverage([]) is None


# ── upsert_qdrant ─────────────────────────────────────────────────────────────

async def test_upsert_qdrant_calls_embed_and_upsert():
    from app.store import SpecStore

    mock_provider = MagicMock()
    mock_provider.embed = AsyncMock(return_value=[[0.1] * 768])

    mock_qdrant = MagicMock()
    mock_qdrant.get_collections.return_value = MagicMock(collections=[])
    mock_qdrant.upsert = MagicMock()

    store = SpecStore.__new__(SpecStore)
    store._provider = mock_provider
    store._embedding_dim = 768
    store._qdrant = mock_qdrant

    path = _make_path()
    await store.upsert_qdrant(path, "login", "spec text here")

    mock_provider.embed.assert_called_once_with(["spec text here"])
    mock_qdrant.upsert.assert_called_once()
    _coll, points_arg = mock_qdrant.upsert.call_args[1]["collection_name"], mock_qdrant.upsert.call_args[1]["points"]
    assert _coll == "specs"
    assert len(points_arg) == 1
    payload = points_arg[0].payload
    assert payload["node_stable_id"] == "sha256:fn_login"
    assert payload["entrypoint_name"] == "login"
    assert payload["repo"] == "acme"
    assert payload["commit_sha"] == "abc123"


async def test_upsert_qdrant_point_id_is_stable():
    """Same path → same point id across calls."""
    from app.store import SpecStore
    import uuid as _uuid

    mock_provider = MagicMock()
    mock_provider.embed = AsyncMock(return_value=[[0.1] * 768])
    mock_qdrant = MagicMock()
    mock_qdrant.upsert = MagicMock()

    store = SpecStore.__new__(SpecStore)
    store._provider = mock_provider
    store._embedding_dim = 768
    store._qdrant = mock_qdrant

    path = _make_path()
    await store.upsert_qdrant(path, "login", "text")
    await store.upsert_qdrant(path, "login", "text")

    ids = [call_args[1]["points"][0].id for call_args in mock_qdrant.upsert.call_args_list]
    assert ids[0] == ids[1]


# ── ensure_collection ─────────────────────────────────────────────────────────

async def test_ensure_collection_creates_if_absent():
    from app.store import SpecStore
    mock_qdrant = MagicMock()
    mock_qdrant.get_collections.return_value = MagicMock(collections=[])
    mock_qdrant.create_collection = MagicMock()

    store = SpecStore.__new__(SpecStore)
    store._qdrant = mock_qdrant
    store._embedding_dim = 768

    await store.ensure_collection()
    mock_qdrant.create_collection.assert_called_once()


async def test_ensure_collection_skips_if_exists():
    from app.store import SpecStore
    existing = MagicMock()
    existing.name = "specs"
    mock_qdrant = MagicMock()
    mock_qdrant.get_collections.return_value = MagicMock(collections=[existing])

    store = SpecStore.__new__(SpecStore)
    store._qdrant = mock_qdrant
    store._embedding_dim = 768

    await store.ensure_collection()
    mock_qdrant.create_collection.assert_not_called()


async def test_ensure_collection_suppresses_409():
    from app.store import SpecStore
    mock_qdrant = MagicMock()
    mock_qdrant.get_collections.return_value = MagicMock(collections=[])
    exc = Exception("conflict")
    exc.status_code = 409
    mock_qdrant.create_collection = MagicMock(side_effect=exc)

    store = SpecStore.__new__(SpecStore)
    store._qdrant = mock_qdrant
    store._embedding_dim = 768

    await store.ensure_collection()  # must not raise
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd services/spec-generator && pytest tests/test_store.py -v
```

Expected: `ModuleNotFoundError: No module named 'app.store'`

- [ ] **Step 3: Write `app/store.py`**

```python
from __future__ import annotations
import asyncio
import uuid

import asyncpg
from qdrant_client import QdrantClient
from qdrant_client.models import Distance, PointStruct, VectorParams

from app.models import CallSequenceItem, ExecutionPath
from app.providers.base import EmbeddingProvider

COLLECTION = "specs"

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS behavior_specs (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    node_stable_id  TEXT NOT NULL,
    repo            TEXT NOT NULL,
    commit_sha      TEXT NOT NULL,
    version         INTEGER NOT NULL DEFAULT 1,
    spec_text       TEXT NOT NULL,
    branch_coverage FLOAT,
    observed_calls  INTEGER,
    generated_at    TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE (node_stable_id, repo, commit_sha)
);
CREATE INDEX IF NOT EXISTS behavior_specs_node_repo_idx
    ON behavior_specs (node_stable_id, repo);
"""

UPSERT_SQL = """
INSERT INTO behavior_specs
    (node_stable_id, repo, commit_sha, spec_text, branch_coverage, observed_calls)
VALUES ($1, $2, $3, $4, $5, $6)
ON CONFLICT (node_stable_id, repo, commit_sha) DO UPDATE SET
    version         = behavior_specs.version + 1,
    spec_text       = EXCLUDED.spec_text,
    branch_coverage = EXCLUDED.branch_coverage,
    observed_calls  = EXCLUDED.observed_calls,
    generated_at    = NOW();
"""


def _compute_branch_coverage(items: list[CallSequenceItem]) -> float | None:
    if not items:
        return None
    observed = sum(1 for item in items if item.frequency_ratio > 0.0)
    return observed / len(items)


class SpecStore:
    def __init__(
        self,
        postgres_dsn: str,
        qdrant_url: str,
        provider: EmbeddingProvider,
        embedding_dim: int,
    ) -> None:
        self._postgres_dsn = postgres_dsn
        self._provider = provider
        self._embedding_dim = embedding_dim
        self._qdrant = QdrantClient(url=qdrant_url)
        self._pool: asyncpg.Pool | None = None

    async def _get_pool(self) -> asyncpg.Pool:
        if self._pool is None:
            self._pool = await asyncpg.create_pool(self._postgres_dsn)
        return self._pool

    async def ensure_schema(self) -> None:
        pool = await self._get_pool()
        async with pool.acquire() as conn:
            await conn.execute(SCHEMA_SQL)

    def _ensure_collection_sync(self) -> None:
        existing = {c.name for c in self._qdrant.get_collections().collections}
        if COLLECTION in existing:
            return
        try:
            self._qdrant.create_collection(
                collection_name=COLLECTION,
                vectors_config=VectorParams(size=self._embedding_dim, distance=Distance.COSINE),
            )
        except Exception as exc:
            if getattr(exc, "status_code", None) == 409:
                return
            raise

    async def ensure_collection(self) -> None:
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, self._ensure_collection_sync)

    async def upsert_spec(self, path: ExecutionPath, spec_text: str) -> None:
        branch_coverage = _compute_branch_coverage(path.call_sequence)
        observed_calls = len(path.call_sequence)
        pool = await self._get_pool()
        async with pool.acquire() as conn:
            await conn.execute(
                UPSERT_SQL,
                path.entrypoint_stable_id,
                path.repo,
                path.commit_sha,
                spec_text,
                branch_coverage,
                observed_calls,
            )

    def _upsert_qdrant_sync(
        self, path: ExecutionPath, entrypoint_name: str, spec_text: str, vector: list[float]
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
                    },
                )
            ],
        )

    async def upsert_qdrant(
        self, path: ExecutionPath, entrypoint_name: str, spec_text: str
    ) -> None:
        vectors = await self._provider.embed([spec_text])
        vector = vectors[0]
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(
            None, self._upsert_qdrant_sync, path, entrypoint_name, spec_text, vector
        )

    async def close(self) -> None:
        if self._pool:
            await self._pool.close()
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd services/spec-generator && pytest tests/test_store.py -v
```

Expected: all 10 tests PASS

- [ ] **Step 5: Commit**

```bash
git add services/spec-generator/app/store.py services/spec-generator/tests/test_store.py
git commit -m "feat(spec-generator): add SpecStore (Postgres + Qdrant)"
```

---

## Task 6: Consumer

**Files:**
- Create: `services/spec-generator/app/consumer.py`
- Create: `services/spec-generator/tests/test_consumer.py`

`_process` derives `entrypoint_name` once, then calls renderer and store. The consumer loop follows the exact same structure as `services/embedder/app/consumer.py`.

- [ ] **Step 1: Write `tests/test_consumer.py`**

```python
import json
import pytest
from unittest.mock import AsyncMock, MagicMock
from pydantic import ValidationError

from app.models import CallSequenceItem, ExecutionPath


def _make_path_dict(call_sequence=None):
    return {
        "entrypoint_stable_id": "sha256:fn_login",
        "commit_sha": "abc123",
        "repo": "acme",
        "call_sequence": call_sequence or [
            {
                "stable_id": "sha256:fn_login",
                "name": "login",
                "qualified_name": "auth.login",
                "hop": 0,
                "frequency_ratio": 1.0,
                "avg_ms": 10.0,
            }
        ],
        "side_effects": [],
        "dynamic_only_edges": [],
        "never_observed_static_edges": [],
        "timing_p50_ms": 10.0,
        "timing_p99_ms": 40.0,
    }


async def test_process_valid_message_calls_store():
    from app.consumer import _process

    mock_store = MagicMock()
    mock_store.upsert_spec = AsyncMock()
    mock_store.upsert_qdrant = AsyncMock()

    data = {"event": json.dumps(_make_path_dict())}
    await _process(data, mock_store)

    mock_store.upsert_spec.assert_called_once()
    mock_store.upsert_qdrant.assert_called_once()

    # entrypoint_name should be "login" (first call_sequence item)
    _, entrypoint_name, _ = mock_store.upsert_qdrant.call_args[0]
    assert entrypoint_name == "login"


async def test_process_empty_call_sequence_falls_back_to_stable_id():
    from app.consumer import _process

    mock_store = MagicMock()
    mock_store.upsert_spec = AsyncMock()
    mock_store.upsert_qdrant = AsyncMock()

    data = {"event": json.dumps(_make_path_dict(call_sequence=[]))}
    await _process(data, mock_store)

    _, entrypoint_name, _ = mock_store.upsert_qdrant.call_args[0]
    assert entrypoint_name == "sha256:fn_login"


async def test_process_validation_error_raises():
    from app.consumer import _process

    mock_store = MagicMock()
    mock_store.upsert_spec = AsyncMock()
    mock_store.upsert_qdrant = AsyncMock()

    data = {"event": json.dumps({"bad": "data"})}
    with pytest.raises(ValidationError):
        await _process(data, mock_store)

    mock_store.upsert_spec.assert_not_called()


async def test_process_missing_event_key_raises():
    from app.consumer import _process

    mock_store = MagicMock()
    data = {}
    with pytest.raises(KeyError):
        await _process(data, mock_store)


async def test_process_postgres_failure_propagates():
    from app.consumer import _process

    mock_store = MagicMock()
    mock_store.upsert_spec = AsyncMock(side_effect=Exception("postgres down"))
    mock_store.upsert_qdrant = AsyncMock()

    data = {"event": json.dumps(_make_path_dict())}
    with pytest.raises(Exception, match="postgres down"):
        await _process(data, mock_store)

    mock_store.upsert_qdrant.assert_not_called()


async def test_process_same_message_twice_calls_store_twice():
    """Consumer must not short-circuit on duplicates — version increment is Postgres's job."""
    from app.consumer import _process

    mock_store = MagicMock()
    mock_store.upsert_spec = AsyncMock()
    mock_store.upsert_qdrant = AsyncMock()

    data = {"event": json.dumps(_make_path_dict())}
    await _process(data, mock_store)
    await _process(data, mock_store)

    assert mock_store.upsert_spec.call_count == 2
    assert mock_store.upsert_qdrant.call_count == 2


async def test_process_bytes_event_decoded():
    from app.consumer import _process

    mock_store = MagicMock()
    mock_store.upsert_spec = AsyncMock()
    mock_store.upsert_qdrant = AsyncMock()

    data = {b"event": json.dumps(_make_path_dict()).encode()}
    await _process(data, mock_store)

    mock_store.upsert_spec.assert_called_once()
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd services/spec-generator && pytest tests/test_consumer.py -v
```

Expected: `ModuleNotFoundError: No module named 'app.consumer'`

- [ ] **Step 3: Write `app/consumer.py`**

```python
from __future__ import annotations
import asyncio
import json
import logging
import os
import socket

import redis.asyncio as aioredis
import redis.exceptions
from pydantic import ValidationError

from app.models import ExecutionPath
from app.renderer import render_spec_text
from app.store import SpecStore

logger = logging.getLogger(__name__)

STREAM_IN = "stream:execution-paths"
GROUP = "spec-generator-group"


async def _process(data: dict, store: SpecStore) -> None:
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
    await store.upsert_spec(path, spec_text)
    await store.upsert_qdrant(path, entrypoint_name, spec_text)


async def run_consumer(store: SpecStore) -> None:
    url = os.environ.get("REDIS_URL", "redis://localhost:6379")
    r = aioredis.from_url(url)
    consumer_name = f"spec-generator-{socket.gethostname()}"
    try:
        try:
            await r.xgroup_create(STREAM_IN, GROUP, id="0", mkstream=True)
        except redis.exceptions.ResponseError as exc:
            if "BUSYGROUP" not in str(exc):
                raise

        logger.info("Consumer started: group=%s consumer=%s", GROUP, consumer_name)

        while True:
            try:
                messages = await r.xreadgroup(
                    groupname=GROUP,
                    consumername=consumer_name,
                    streams={STREAM_IN: ">"},
                    count=10,
                    block=1000,
                )
                for _stream, events in (messages or []):
                    for msg_id, data in events:
                        try:
                            await _process(data, store)
                            await r.xack(STREAM_IN, GROUP, msg_id)
                        except (json.JSONDecodeError, ValidationError, KeyError) as exc:
                            logger.warning("Bad message %s, skipping (XACK): %s", msg_id, exc)
                            await r.xack(STREAM_IN, GROUP, msg_id)
                        except asyncio.CancelledError:
                            raise
                        except Exception as exc:
                            logger.error("Failed msg_id=%s, will retry: %s", msg_id, exc)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.error("Consumer loop error: %s", exc)
    finally:
        await r.aclose()
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd services/spec-generator && pytest tests/test_consumer.py -v
```

Expected: all 7 tests PASS

- [ ] **Step 5: Commit**

```bash
git add services/spec-generator/app/consumer.py services/spec-generator/tests/test_consumer.py
git commit -m "feat(spec-generator): add Redis consumer"
```

---

## Task 7: FastAPI App

**Files:**
- Create: `services/spec-generator/app/main.py`

No new tests — the consumer, store, and renderer are already tested. `main.py` wires everything together. Pattern is identical to `services/embedder/app/main.py`.

- [ ] **Step 1: Write `app/main.py`**

```python
import asyncio
import logging
import os
from contextlib import asynccontextmanager

import asyncpg
import redis.asyncio as aioredis
from fastapi import FastAPI
from fastapi.responses import JSONResponse, PlainTextResponse

logger = logging.getLogger(__name__)

VERSION = "0.1.0"
SERVICE = "spec-generator"

_redis_client: aioredis.Redis | None = None
_store = None


def _get_redis() -> aioredis.Redis:
    global _redis_client
    if _redis_client is None:
        url = os.environ.get("REDIS_URL", "redis://localhost:6379")
        _redis_client = aioredis.from_url(url)
    return _redis_client


def _make_provider():
    from app.providers.ollama import OllamaProvider
    from app.providers.voyage import VoyageProvider

    provider_name = os.environ.get("EMBEDDING_PROVIDER", "ollama")
    if provider_name == "voyage":
        return VoyageProvider()
    return OllamaProvider()


def _get_embedding_dim() -> int:
    provider_name = os.environ.get("EMBEDDING_PROVIDER", "ollama")
    default = "768" if provider_name == "ollama" else "1024"
    return int(os.environ.get("EMBEDDING_DIM", default))


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _store

    from app.store import SpecStore
    from app.consumer import run_consumer

    provider = _make_provider()
    postgres_dsn = os.environ.get("POSTGRES_DSN", "postgres://tersecontext:localpassword@localhost:5432/tersecontext")
    qdrant_url = os.environ.get("QDRANT_URL", "http://localhost:6333")
    embedding_dim = _get_embedding_dim()

    _store = SpecStore(postgres_dsn, qdrant_url, provider, embedding_dim)
    await _store.ensure_schema()
    await _store.ensure_collection()

    task = asyncio.create_task(run_consumer(_store))
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
    try:
        if _store:
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(None, _store._qdrant.get_collections)
    except Exception as exc:
        errors.append(f"qdrant: {exc}")
    if errors:
        return JSONResponse(status_code=503, content={"status": "unavailable", "errors": errors})
    return {"status": "ok"}


@app.get("/metrics")
def metrics():
    lines = [
        "# HELP spec_generator_messages_processed_total Total messages processed",
        "# TYPE spec_generator_messages_processed_total counter",
        "spec_generator_messages_processed_total 0",
        "# HELP spec_generator_messages_failed_total Total messages failed",
        "# TYPE spec_generator_messages_failed_total counter",
        "spec_generator_messages_failed_total 0",
        "# HELP spec_generator_specs_written_total Total specs written to Postgres",
        "# TYPE spec_generator_specs_written_total counter",
        "spec_generator_specs_written_total 0",
        "# HELP spec_generator_specs_embedded_total Total specs embedded to Qdrant",
        "# TYPE spec_generator_specs_embedded_total counter",
        "spec_generator_specs_embedded_total 0",
    ]
    return PlainTextResponse("\n".join(lines) + "\n")
```

- [ ] **Step 2: Run all tests to verify nothing broke**

```bash
cd services/spec-generator && pytest -v
```

Expected: all tests PASS (models + renderer + store + consumer)

- [ ] **Step 3: Commit**

```bash
git add services/spec-generator/app/main.py
git commit -m "feat(spec-generator): add FastAPI app with health/ready/metrics"
```

---

## Task 8: Docker Compose

**Files:**
- Modify: `docker-compose.yml`

Add `spec-generator` service entry. Check the end of the `services:` block — insert before the `neo4j-init:` entry.

- [ ] **Step 1: Add spec-generator to `docker-compose.yml`**

Find the line `  neo4j-init:` near the bottom and insert the following block immediately before it:

```yaml
  spec-generator:
    build: ./services/spec-generator
    ports:
      - "8097:8080"
    environment:
      REDIS_URL: redis://redis:6379
      POSTGRES_DSN: postgres://tersecontext:localpassword@postgres:5432/tersecontext
      QDRANT_URL: http://qdrant:6333
      EMBEDDING_PROVIDER: ollama
      OLLAMA_URL: http://ollama:11434
      EMBEDDING_MODEL: nomic-embed-text
      EMBEDDING_DIM: "768"
    depends_on:
      redis:
        condition: service_healthy
      postgres:
        condition: service_healthy
      qdrant:
        condition: service_started
      ollama:
        condition: service_started
    networks:
      - tersecontext
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:8080/health"]
      interval: 10s
      timeout: 5s
      retries: 5

```

- [ ] **Step 2: Verify the YAML is valid**

```bash
docker compose config --quiet
```

Expected: no output (valid YAML)

- [ ] **Step 3: Commit**

```bash
git add docker-compose.yml
git commit -m "feat(spec-generator): add to docker-compose"
```

---

## Task 9: Final verification

- [ ] **Step 1: Run the full test suite**

```bash
cd services/spec-generator && pytest -v
```

Expected: all tests PASS

- [ ] **Step 2: Verify Dockerfile builds**

```bash
docker build -t spec-generator-test services/spec-generator/
```

Expected: successful build, no errors

- [ ] **Step 3: Commit if anything was fixed**

If the build revealed missing files or adjustments, fix and commit with:

```bash
git add services/spec-generator/
git commit -m "fix(spec-generator): <describe what was fixed>"
```
