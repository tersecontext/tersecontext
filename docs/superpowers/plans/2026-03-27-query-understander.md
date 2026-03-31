# Query Understander Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the `query-understander` FastAPI service that converts a natural language question into a structured `QueryIntent` using Ollama, with Redis caching and keyword-extraction fallback.

**Architecture:** Single Python service with four focused modules (`models`, `cache`, `understander`, `main`). `understander.py` calls Ollama HTTP API and owns retry/fallback logic. `main.py` wires FastAPI endpoints, Redis client, and startup health check.

**Tech Stack:** Python 3.12, FastAPI, Pydantic v2, httpx (async Ollama client), redis[asyncio], prometheus-fastapi-instrumentator, pytest + pytest-asyncio + fakeredis

---

## File Map

| File | Responsibility |
|------|---------------|
| `services/query-understander/requirements.txt` | All prod + dev dependencies |
| `services/query-understander/Dockerfile` | Container build |
| `services/query-understander/app/__init__.py` | Package marker |
| `services/query-understander/app/models.py` | Pydantic request/response models |
| `services/query-understander/app/cache.py` | Redis get/set helpers |
| `services/query-understander/app/understander.py` | Ollama call, parse, retry, fallback |
| `services/query-understander/app/main.py` | FastAPI app, lifespan, all endpoints |
| `services/query-understander/tests/__init__.py` | Package marker |
| `services/query-understander/tests/conftest.py` | Shared pytest fixtures |
| `services/query-understander/tests/test_understander.py` | All unit tests |

---

## Task 1: Scaffold

**Files:**
- Create: `services/query-understander/app/__init__.py`
- Create: `services/query-understander/tests/__init__.py`
- Create: `services/query-understander/requirements.txt`
- Create: `services/query-understander/Dockerfile`

- [ ] **Step 1: Create directory structure**

```bash
mkdir -p services/query-understander/app
mkdir -p services/query-understander/tests
touch services/query-understander/app/__init__.py
touch services/query-understander/tests/__init__.py
```

- [ ] **Step 2: Write `requirements.txt`**

```
fastapi>=0.100.0
uvicorn[standard]>=0.23.0
httpx>=0.24.0
redis[asyncio]>=5.0.0
prometheus-fastapi-instrumentator>=6.1.0
pytest>=7.0.0
pytest-asyncio>=0.21.0
fakeredis[aioredis]>=2.20.0
```

- [ ] **Step 3: Write `Dockerfile`**

```dockerfile
FROM python:3.12-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir uv

COPY requirements.txt .
RUN uv pip install --system --no-cache -r requirements.txt

COPY app/ ./app/

EXPOSE 8080
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080"]
```

- [ ] **Step 4: Commit**

```bash
git add services/query-understander/
git commit -m "feat(query-understander): scaffold service directory"
```

---

## Task 2: Pydantic models

**Files:**
- Create: `services/query-understander/app/models.py`
- Test: `services/query-understander/tests/test_understander.py`

- [ ] **Step 1: Write the failing test**

Create `services/query-understander/tests/test_understander.py`:

```python
import pytest
from pydantic import ValidationError

from app.models import QueryIntent, UnderstandRequest


def test_query_intent_valid():
    intent = QueryIntent(
        raw_query="how does auth work",
        keywords=["auth", "login"],
        symbols=["AuthService"],
        query_type="flow",
        embed_query="authentication flow login verify token",
        scope=None,
    )
    assert intent.query_type == "flow"
    assert intent.scope is None


def test_query_intent_rejects_bad_query_type():
    with pytest.raises(ValidationError):
        QueryIntent(
            raw_query="q",
            keywords=[],
            symbols=[],
            query_type="unknown",
            embed_query="q",
        )


def test_understand_request_valid():
    req = UnderstandRequest(question="where is JWT configured", repo="acme-api")
    assert req.question == "where is JWT configured"
    assert req.repo == "acme-api"
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd services/query-understander
python -m pytest tests/test_understander.py::test_query_intent_valid -v
```

Expected: `ModuleNotFoundError: No module named 'app'`

- [ ] **Step 3: Create `app/models.py`**

```python
from typing import Literal, Optional
from pydantic import BaseModel


class UnderstandRequest(BaseModel):
    question: str
    repo: str


class QueryIntent(BaseModel):
    raw_query: str
    keywords: list[str]
    symbols: list[str]
    query_type: Literal["lookup", "flow", "impact"]
    embed_query: str
    scope: Optional[str] = None
```

- [ ] **Step 4: Install deps and run tests**

```bash
cd services/query-understander
pip install -r requirements.txt
python -m pytest tests/test_understander.py -k "query_intent or understand_request" -v
```

Expected: 3 PASSED

- [ ] **Step 5: Commit**

```bash
git add services/query-understander/app/models.py services/query-understander/tests/test_understander.py
git commit -m "feat(query-understander): add Pydantic models"
```

---

## Task 3: Cache helpers

**Files:**
- Create: `services/query-understander/app/cache.py`
- Modify: `services/query-understander/tests/test_understander.py`
- Create: `services/query-understander/tests/conftest.py`

- [ ] **Step 1: Create `tests/conftest.py`**

```python
import pytest
import fakeredis.aioredis


@pytest.fixture
async def fake_redis():
    client = fakeredis.aioredis.FakeRedis(decode_responses=True)
    yield client
    await client.aclose()
```

Add a `pytest.ini` file at the service root:

```ini
[pytest]
asyncio_mode = auto
testpaths = tests
```

- [ ] **Step 2: Write failing cache tests** (append to `test_understander.py`)

```python
import hashlib
from app.cache import cache_get, cache_set, _make_key
from app.models import QueryIntent


SAMPLE_INTENT = QueryIntent(
    raw_query="how does auth work",
    keywords=["auth", "login"],
    symbols=["AuthService"],
    query_type="flow",
    embed_query="authentication flow login verify token credentials",
    scope=None,
)


async def test_cache_miss_returns_none(fake_redis):
    result = await cache_get(fake_redis, "any question", "any-repo")
    assert result is None


async def test_cache_roundtrip(fake_redis):
    await cache_set(fake_redis, "how does auth work", "acme-api", SAMPLE_INTENT)
    result = await cache_get(fake_redis, "how does auth work", "acme-api")
    assert result is not None
    assert result.raw_query == "how does auth work"
    assert result.query_type == "flow"


def test_cache_key_is_deterministic():
    k1 = _make_key("how does auth work", "acme-api")
    k2 = _make_key("how does auth work", "acme-api")
    assert k1 == k2
    assert k1.startswith("query_intent:")


def test_cache_key_normalises_case():
    k1 = _make_key("How Does Auth Work", "acme-api")
    k2 = _make_key("how does auth work", "acme-api")
    assert k1 == k2


def test_cache_key_includes_repo():
    k1 = _make_key("how does auth work", "repo-a")
    k2 = _make_key("how does auth work", "repo-b")
    assert k1 != k2


async def test_cache_respects_ttl(fake_redis):
    # fakeredis supports TTL; verify key was stored with one
    await cache_set(fake_redis, "q", "repo", SAMPLE_INTENT)
    key = _make_key("q", "repo")
    ttl = await fake_redis.ttl(key)
    assert ttl > 0
```

- [ ] **Step 3: Run cache tests to verify they fail**

```bash
python -m pytest tests/test_understander.py -k "cache" -v
```

Expected: `ImportError: cannot import name 'cache_get'`

- [ ] **Step 4: Create `app/cache.py`**

```python
import hashlib
from typing import Optional

import redis.asyncio as redis

from .models import QueryIntent

CACHE_TTL = 3600


def _make_key(question: str, repo: str) -> str:
    raw = question.lower().strip() + "|" + repo
    sha = hashlib.sha256(raw.encode()).hexdigest()
    return f"query_intent:{sha}"


async def cache_get(client: redis.Redis, question: str, repo: str) -> Optional[QueryIntent]:
    key = _make_key(question, repo)
    data = await client.get(key)
    if data is None:
        return None
    return QueryIntent.model_validate_json(data)


async def cache_set(client: redis.Redis, question: str, repo: str, intent: QueryIntent) -> None:
    key = _make_key(question, repo)
    await client.setex(key, CACHE_TTL, intent.model_dump_json())
```

- [ ] **Step 5: Run cache tests**

```bash
python -m pytest tests/test_understander.py -k "cache" -v
```

Expected: 6 PASSED

- [ ] **Step 6: Commit**

```bash
git add services/query-understander/app/cache.py \
        services/query-understander/tests/conftest.py \
        services/query-understander/pytest.ini
git commit -m "feat(query-understander): add Redis cache helpers"
```

---

## Task 4: Understander logic

**Files:**
- Create: `services/query-understander/app/understander.py`
- Modify: `services/query-understander/tests/test_understander.py`

- [ ] **Step 1: Write failing understander tests** (append to `test_understander.py`)

```python
import json
from unittest.mock import AsyncMock, patch

from app.understander import understand, _fallback_intent


VALID_OLLAMA_RESPONSE = {
    "keywords": ["auth", "authenticate", "login", "jwt", "token"],
    "symbols": ["AuthService", "authenticate"],
    "query_type": "flow",
    "embed_query": "authentication flow login jwt token verification user credentials",
    "scope": None,
}

LOOKUP_RESPONSE = {
    "keywords": ["jwt", "config", "configure"],
    "symbols": ["JWTConfig"],
    "query_type": "lookup",
    "embed_query": "JWT configuration setup options settings location",
    "scope": None,
}

IMPACT_RESPONSE = {
    "keywords": ["authenticate", "break", "change"],
    "symbols": ["authenticate"],
    "query_type": "impact",
    "embed_query": "authenticate function callers dependents breaking changes side effects",
    "scope": None,
}


async def test_understand_ollama_success():
    raw_json = json.dumps(VALID_OLLAMA_RESPONSE)
    with patch("app.understander._call_ollama", new=AsyncMock(return_value=raw_json)):
        intent, from_ollama = await understand("how does authentication work")
    assert from_ollama is True
    assert intent.query_type == "flow"
    assert intent.raw_query == "how does authentication work"
    assert len(intent.embed_query) > len("how does authentication work")


async def test_understand_lookup_query_type():
    raw_json = json.dumps(LOOKUP_RESPONSE)
    with patch("app.understander._call_ollama", new=AsyncMock(return_value=raw_json)):
        intent, from_ollama = await understand("where is JWT configured")
    assert from_ollama is True
    assert intent.query_type == "lookup"


async def test_understand_impact_query_type():
    raw_json = json.dumps(IMPACT_RESPONSE)
    with patch("app.understander._call_ollama", new=AsyncMock(return_value=raw_json)):
        intent, from_ollama = await understand("what breaks if I change authenticate")
    assert from_ollama is True
    assert intent.query_type == "impact"
    assert "authenticate" in intent.symbols


async def test_understand_retry_on_bad_json():
    """First Ollama response is invalid JSON; second (strict prompt) is valid."""
    good_json = json.dumps(VALID_OLLAMA_RESPONSE)
    call_mock = AsyncMock(side_effect=["not valid json", good_json])
    with patch("app.understander._call_ollama", new=call_mock):
        intent, from_ollama = await understand("how does authentication work")
    assert from_ollama is True
    assert call_mock.call_count == 2
    # Second call uses strict=True
    assert call_mock.call_args_list[1].kwargs.get("strict") or call_mock.call_args_list[1].args[1]


async def test_understand_fallback_on_double_failure():
    """Both Ollama attempts fail; fallback keyword extraction is used."""
    call_mock = AsyncMock(side_effect=Exception("connection refused"))
    with patch("app.understander._call_ollama", new=call_mock):
        intent, from_ollama = await understand("how does authentication work")
    assert from_ollama is False
    assert intent.query_type == "flow"
    assert intent.raw_query == "how does authentication work"
    assert isinstance(intent.keywords, list)
    assert len(intent.keywords) > 0


async def test_understand_fallback_on_invalid_json_twice():
    """Both Ollama responses are invalid JSON; fallback is used."""
    call_mock = AsyncMock(return_value="{ invalid }")
    with patch("app.understander._call_ollama", new=call_mock):
        intent, from_ollama = await understand("how does authentication work")
    assert from_ollama is False
    assert intent.query_type == "flow"


def test_fallback_filters_stop_words():
    intent = _fallback_intent("how does authentication work in the system")
    # "how", "does", "in", "the" are stop words and should be filtered
    assert "how" not in intent.keywords
    assert "does" not in intent.keywords
    assert "the" not in intent.keywords
    assert "authentication" in intent.keywords


def test_fallback_returns_valid_intent():
    intent = _fallback_intent("what breaks if I change authenticate")
    assert intent.query_type == "flow"
    assert intent.embed_query == "what breaks if I change authenticate"
    assert intent.symbols == []
```

- [ ] **Step 2: Run to verify they fail**

```bash
python -m pytest tests/test_understander.py -k "understand or fallback" -v
```

Expected: `ImportError: cannot import name 'understand'`

- [ ] **Step 3: Create `app/understander.py`**

```python
import json
import logging
import os
from typing import Optional

import httpx

from .models import QueryIntent

logger = logging.getLogger(__name__)

STOP_WORDS = {
    "a", "an", "the", "is", "are", "was", "were", "be", "been", "being",
    "have", "has", "had", "do", "does", "did", "will", "would", "could",
    "should", "may", "might", "shall", "can", "to", "of", "in", "for",
    "on", "with", "at", "by", "from", "and", "but", "or", "if", "how",
    "what", "where", "when", "who", "why", "which", "this", "that",
    "i", "my", "me", "we", "our", "you", "it", "its", "they", "there",
    "get", "let", "make", "change", "work",
}

_BASE_PROMPT = """\
You are a code analysis assistant. Extract structured information from this codebase query.
Return JSON only. No explanation. No markdown. No code fences.

Schema:
{{
  "keywords": ["string"],
  "symbols": ["string"],
  "query_type": "lookup | flow | impact",
  "embed_query": "string",
  "scope": "string or null"
}}

Rules:
- keywords: lowercase terms relevant to the query, 3-8 items
- symbols: any identifiers (function names, class names) mentioned or implied
- query_type: lookup=find something, flow=understand execution, impact=understand consequences
- embed_query: rewrite the query with synonyms and related terms for better search, 8-15 words
- scope: file path if user mentioned one, otherwise null

Query: "{question}\""""

_STRICT_SUFFIX = "\nYou must return only a JSON object. Any other output is invalid."


def _make_prompt(question: str, strict: bool = False) -> str:
    prompt = _BASE_PROMPT.format(question=question)
    if strict:
        prompt += _STRICT_SUFFIX
    return prompt


def _fallback_intent(question: str) -> QueryIntent:
    words = question.lower().split()
    keywords = [w for w in words if w not in STOP_WORDS and len(w) > 1]
    return QueryIntent(
        raw_query=question,
        keywords=keywords[:8],
        symbols=[],
        query_type="flow",
        embed_query=question,
        scope=None,
    )


def _parse_response(raw: str, question: str) -> Optional[QueryIntent]:
    try:
        data = json.loads(raw)
        data["raw_query"] = question
        return QueryIntent.model_validate(data)
    except Exception:
        return None


async def _call_ollama(question: str, strict: bool = False) -> str:
    base_url = os.getenv("LLM_BASE_URL", "http://ollama:11434")
    model = os.getenv("LLM_MODEL", "qwen2.5-coder:7b")
    payload = {
        "model": model,
        "prompt": _make_prompt(question, strict),
        "stream": False,
    }
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.post(f"{base_url}/api/generate", json=payload)
        resp.raise_for_status()
        return resp.json().get("response", "")


async def understand(question: str) -> tuple[QueryIntent, bool]:
    """Returns (QueryIntent, is_ollama_success).
    is_ollama_success is False when keyword fallback was used."""
    # First attempt
    try:
        raw = await _call_ollama(question, strict=False)
        intent = _parse_response(raw, question)
        if intent:
            return intent, True
    except Exception as exc:
        logger.warning("Ollama first attempt failed: %s", exc)

    # Retry with stricter prompt
    try:
        raw = await _call_ollama(question, strict=True)
        intent = _parse_response(raw, question)
        if intent:
            return intent, True
    except Exception as exc:
        logger.warning("Ollama retry failed: %s", exc)

    # Fallback to keyword extraction
    logger.warning("Falling back to keyword extraction for: %r", question)
    return _fallback_intent(question), False
```

- [ ] **Step 4: Run understander tests**

```bash
python -m pytest tests/test_understander.py -k "understand or fallback" -v
```

Expected: all PASSED

- [ ] **Step 5: Commit**

```bash
git add services/query-understander/app/understander.py
git commit -m "feat(query-understander): add Ollama integration with retry and fallback"
```

---

## Task 5: FastAPI app

**Files:**
- Create: `services/query-understander/app/main.py`
- Modify: `services/query-understander/tests/test_understander.py`
- Modify: `services/query-understander/tests/conftest.py`

- [ ] **Step 1: Write failing endpoint tests** (append to `test_understander.py`)

```python
from unittest.mock import AsyncMock, patch

import fakeredis.aioredis
import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.models import QueryIntent

FLOW_INTENT = QueryIntent(
    raw_query="how does authentication work",
    keywords=["auth", "login", "jwt"],
    symbols=["AuthService"],
    query_type="flow",
    embed_query="authentication flow login jwt token verification user credentials",
    scope=None,
)


@pytest.fixture
def client_with_ollama():
    """TestClient with Ollama reachable and fakeredis."""
    fake_redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
    with patch("app.main._check_ollama", new=AsyncMock(return_value=True)):
        with patch("app.main.aioredis.from_url", return_value=fake_redis):
            with TestClient(app) as client:
                yield client, fake_redis


@pytest.fixture
def client_no_ollama():
    """TestClient with Ollama unreachable."""
    fake_redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
    with patch("app.main._check_ollama", new=AsyncMock(return_value=False)):
        with patch("app.main.aioredis.from_url", return_value=fake_redis):
            with TestClient(app) as client:
                yield client


def test_health(client_with_ollama):
    client, _ = client_with_ollama
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok", "service": "query-understander", "version": "0.1.0"}


def test_ready_when_ollama_up(client_with_ollama):
    client, _ = client_with_ollama
    resp = client.get("/ready")
    assert resp.status_code == 200


def test_ready_when_ollama_down(client_no_ollama):
    resp = client_no_ollama.get("/ready")
    assert resp.status_code == 503


def test_understand_success(client_with_ollama):
    client, _ = client_with_ollama
    with patch("app.main.understand", new=AsyncMock(return_value=(FLOW_INTENT, True))):
        resp = client.post("/understand", json={"question": "how does authentication work", "repo": "acme-api"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["query_type"] == "flow"
    assert data["raw_query"] == "how does authentication work"
    assert len(data["embed_query"]) > len("how does authentication work")


def test_understand_caches_ollama_result(client_with_ollama):
    client, fake_redis = client_with_ollama
    call_count = 0

    async def mock_understand(question):
        nonlocal call_count
        call_count += 1
        return FLOW_INTENT, True

    with patch("app.main.understand", new=mock_understand):
        client.post("/understand", json={"question": "how does authentication work", "repo": "acme-api"})
        client.post("/understand", json={"question": "how does authentication work", "repo": "acme-api"})

    # understand() should only be called once — second request is served from cache
    assert call_count == 1


def test_understand_does_not_cache_fallback(client_with_ollama):
    client, fake_redis = client_with_ollama
    fallback_intent = QueryIntent(
        raw_query="how does authentication work",
        keywords=["authentication"],
        symbols=[],
        query_type="flow",
        embed_query="how does authentication work",
        scope=None,
    )
    call_count = 0

    async def mock_understand(question):
        nonlocal call_count
        call_count += 1
        return fallback_intent, False  # is_ollama_success=False

    with patch("app.main.understand", new=mock_understand):
        client.post("/understand", json={"question": "how does authentication work", "repo": "acme-api"})
        client.post("/understand", json={"question": "how does authentication work", "repo": "acme-api"})

    # understand() called twice — fallback result was not cached
    assert call_count == 2
```

- [ ] **Step 2: Run to verify they fail**

```bash
python -m pytest tests/test_understander.py -k "health or ready or understand_success or cach" -v
```

Expected: `ImportError: cannot import name 'app' from 'app.main'` or `ModuleNotFoundError`

- [ ] **Step 3: Create `app/main.py`**

```python
import asyncio
import logging
import os
from contextlib import asynccontextmanager

import httpx
import redis.asyncio as aioredis
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from prometheus_fastapi_instrumentator import Instrumentator

from .cache import cache_get, cache_set
from .models import QueryIntent, UnderstandRequest
from .understander import understand

logger = logging.getLogger(__name__)


async def _check_ollama(base_url: str) -> bool:
    delays = [1, 2, 4, 8, 16]
    for delay in delays:
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.get(f"{base_url}/api/tags")
                if resp.status_code == 200:
                    return True
        except Exception:
            pass
        await asyncio.sleep(delay)
    return False


@asynccontextmanager
async def lifespan(app: FastAPI):
    redis_url = os.getenv("REDIS_URL", "redis://localhost:6379")
    llm_base_url = os.getenv("LLM_BASE_URL", "http://ollama:11434")

    app.state.redis = aioredis.from_url(redis_url, decode_responses=True)
    app.state.ollama_ready = await _check_ollama(llm_base_url)

    if not app.state.ollama_ready:
        logger.warning("Ollama unreachable at startup — running in fallback mode")

    yield

    await app.state.redis.aclose()


app = FastAPI(title="query-understander", version="0.1.0", lifespan=lifespan)
Instrumentator().instrument(app).expose(app)


@app.get("/health")
async def health():
    return {"status": "ok", "service": "query-understander", "version": "0.1.0"}


@app.get("/ready")
async def ready(request: Request):
    if request.app.state.ollama_ready:
        return {"status": "ready"}
    return JSONResponse(
        status_code=503,
        content={"status": "unavailable", "reason": "ollama unreachable"},
    )


@app.post("/understand", response_model=QueryIntent)
async def understand_endpoint(req: UnderstandRequest, request: Request):
    redis_client = request.app.state.redis

    cached = await cache_get(redis_client, req.question, req.repo)
    if cached:
        logger.info("Cache hit for question: %r", req.question)
        return cached

    intent, from_ollama = await understand(req.question)

    if from_ollama:
        await cache_set(redis_client, req.question, req.repo, intent)

    return intent
```

- [ ] **Step 4: Run all tests**

```bash
python -m pytest tests/ -v
```

Expected: all PASSED (models + cache + understander + endpoint tests)

- [ ] **Step 5: Commit**

```bash
git add services/query-understander/app/main.py services/query-understander/tests/test_understander.py
git commit -m "feat(query-understander): add FastAPI app with all endpoints"
```

---

## Task 6: Docker build

**Files:**
- No new files

- [ ] **Step 1: Build the image**

```bash
cd services/query-understander
docker build -t tersecontext-query-understander .
```

Expected: `Successfully built ...` with no errors

- [ ] **Step 2: Confirm image runs (no live Redis/Ollama needed for health)**

```bash
docker run --rm -d --name qu-test \
  -e REDIS_URL=redis://host.docker.internal:6379 \
  -e LLM_BASE_URL=http://host.docker.internal:11434 \
  -p 8086:8080 tersecontext-query-understander

# Give it 5s to start (will hit Ollama backoff then proceed in fallback mode)
sleep 5
curl -s http://localhost:8086/health
```

Expected:
```json
{"status":"ok","service":"query-understander","version":"0.1.0"}
```

- [ ] **Step 3: Check /ready (Ollama unreachable → 503)**

```bash
curl -s -o /dev/null -w "%{http_code}" http://localhost:8086/ready
```

Expected: `503`

- [ ] **Step 4: Stop test container**

```bash
docker stop qu-test
```

- [ ] **Step 5: Final test suite run**

```bash
cd services/query-understander
python -m pytest tests/ -v
```

Expected: all PASSED

- [ ] **Step 6: Commit**

```bash
cd ../..
git add services/query-understander/
git commit -m "feat(query-understander): service complete — all tests passing, Dockerfile verified"
```
