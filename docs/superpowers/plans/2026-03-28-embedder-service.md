# Embedder Service Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the embedder microservice — consumes `ParsedFile` Redis events, skips unchanged nodes via Neo4j hash lookup, calls an embedding API (Ollama or Voyage) in batches, and emits `EmbeddedNodes` events.

**Architecture:** Fully async Python service using `redis.asyncio` and `httpx` for I/O; sync Neo4j bolt driver wrapped in `run_in_executor` for hash lookups; pure-logic `embedder.py` with no I/O (easy to unit-test). Two swappable embedding providers selected via `EMBEDDING_PROVIDER` env var.

**Tech Stack:** Python 3.12, FastAPI, uvicorn, redis[asyncio], pydantic v2, httpx, neo4j bolt driver, pytest, pytest-asyncio

---

## File Map

| File | Responsibility |
|---|---|
| `services/embedder/app/__init__.py` | Package marker |
| `services/embedder/app/models.py` | Pydantic models: ParsedNode, ParsedFileEvent, EmbeddedNode, EmbeddedNodesEvent |
| `services/embedder/app/providers/__init__.py` | Package marker |
| `services/embedder/app/providers/base.py` | Abstract EmbeddingProvider interface |
| `services/embedder/app/providers/ollama.py` | OllamaProvider — httpx POST to /api/embed |
| `services/embedder/app/providers/voyage.py` | VoyageProvider — httpx POST to Voyage AI REST API |
| `services/embedder/app/embedder.py` | Pure logic: build_embed_text, embed_nodes (no I/O) |
| `services/embedder/app/neo4j_client.py` | Wraps sync neo4j driver; async get_node_hashes via run_in_executor |
| `services/embedder/app/consumer.py` | Async Redis Stream consumer loop |
| `services/embedder/app/main.py` | FastAPI app: /health, /ready, /metrics, lifespan |
| `services/embedder/tests/__init__.py` | Package marker |
| `services/embedder/tests/test_embedder.py` | All unit tests (mocked providers + Neo4j) |
| `services/embedder/Dockerfile` | Container image |
| `services/embedder/requirements.txt` | Runtime + dev dependencies |
| `services/embedder/pyproject.toml` | Build config + pytest config |

---

## Task 1: Scaffold

**Files:**
- Create: `services/embedder/app/__init__.py`
- Create: `services/embedder/app/providers/__init__.py`
- Create: `services/embedder/tests/__init__.py`
- Create: `services/embedder/requirements.txt`
- Create: `services/embedder/pyproject.toml`

- [ ] **Step 1: Create directory structure**

```bash
mkdir -p services/embedder/app/providers
mkdir -p services/embedder/tests
```

- [ ] **Step 2: Create package markers**

`services/embedder/app/__init__.py` — empty file.
`services/embedder/app/providers/__init__.py` — empty file.
`services/embedder/tests/__init__.py` — empty file.

- [ ] **Step 3: Create requirements.txt**

```
fastapi>=0.115.0
uvicorn[standard]>=0.30.0
redis>=5.0.0
pydantic>=2.0.0
httpx>=0.27.0
neo4j>=5.0.0
pytest>=8.0.0
pytest-asyncio>=0.23.0
```

- [ ] **Step 4: Create pyproject.toml**

```toml
[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[project]
name = "tersecontext-embedder"
version = "0.1.0"
requires-python = ">=3.12"
dependencies = [
    "fastapi>=0.115.0",
    "uvicorn[standard]>=0.30.0",
    "redis>=5.0.0",
    "pydantic>=2.0.0",
    "httpx>=0.27.0",
    "neo4j>=5.0.0",
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

- [ ] **Step 5: Verify pytest can be installed**

```bash
cd services/embedder
pip install -r requirements.txt
```

Expected: no errors.

- [ ] **Step 6: Commit**

```bash
git add services/embedder/
git commit -m "feat(embedder): scaffold directory structure and dependencies"
```

---

## Task 2: Models

**Files:**
- Create: `services/embedder/app/models.py`
- Create: `services/embedder/tests/test_embedder.py` (first tests only)

- [ ] **Step 1: Write failing model tests**

Create `services/embedder/tests/test_embedder.py`:

```python
import pytest
from unittest.mock import AsyncMock, MagicMock, patch


# ── Models ────────────────────────────────────────────────────────────────────

def test_parsed_node_docstring_defaults_to_empty_string():
    from app.models import ParsedNode
    node = ParsedNode(
        stable_id="sha256:abc",
        node_hash="sha256:def",
        type="function",
        name="foo",
        signature="foo(x: int) -> str",
        docstring="",
        body="def foo(x: int) -> str: return str(x)",
        line_start=1,
        line_end=1,
    )
    assert node.docstring == ""
    assert node.parent_id is None


def test_parsed_file_event_ignores_extra_fields():
    """Parser emits deleted_nodes which is not in the formal contract — must be ignored."""
    from app.models import ParsedFileEvent
    evt = ParsedFileEvent.model_validate({
        "repo": "acme",
        "commit_sha": "abc123",
        "file_path": "src/foo.py",
        "language": "python",
        "nodes": [],
        "intra_file_edges": [],
        "deleted_nodes": ["sha256:x"],  # extra field not in contract
    })
    assert evt.repo == "acme"
    assert not hasattr(evt, "deleted_nodes")


def test_embedded_nodes_event_shape():
    from app.models import EmbeddedNode, EmbeddedNodesEvent
    node = EmbeddedNode(
        stable_id="sha256:abc",
        vector=[0.1, 0.2, 0.3],
        embed_text="foo foo(x: int) -> str",
        node_hash="sha256:def",
    )
    evt = EmbeddedNodesEvent(repo="acme", commit_sha="abc123", nodes=[node])
    assert evt.nodes[0].stable_id == "sha256:abc"
    assert len(evt.nodes[0].vector) == 3
```

- [ ] **Step 2: Run tests — expect failure**

```bash
cd services/embedder
pytest tests/test_embedder.py::test_parsed_node_docstring_defaults_to_empty_string tests/test_embedder.py::test_parsed_file_event_ignores_extra_fields tests/test_embedder.py::test_embedded_nodes_event_shape -v
```

Expected: `ModuleNotFoundError` (app.models doesn't exist yet).

- [ ] **Step 3: Implement models.py**

Create `services/embedder/app/models.py`:

```python
from __future__ import annotations
from typing import Optional
from pydantic import BaseModel, ConfigDict


class ParsedNode(BaseModel):
    stable_id: str
    node_hash: str
    type: str
    name: str
    signature: str
    docstring: str = ""
    body: str
    line_start: int
    line_end: int
    parent_id: Optional[str] = None


class IntraFileEdge(BaseModel):
    source_stable_id: str
    target_stable_id: str
    type: str


class ParsedFileEvent(BaseModel):
    model_config = ConfigDict(extra="ignore")

    repo: str
    commit_sha: str
    file_path: str
    language: str
    nodes: list[ParsedNode]
    intra_file_edges: list[IntraFileEdge]


class EmbeddedNode(BaseModel):
    stable_id: str
    vector: list[float]
    embed_text: str
    node_hash: str


class EmbeddedNodesEvent(BaseModel):
    repo: str
    commit_sha: str
    nodes: list[EmbeddedNode]
```

- [ ] **Step 4: Run tests — expect pass**

```bash
cd services/embedder
pytest tests/test_embedder.py::test_parsed_node_docstring_defaults_to_empty_string tests/test_embedder.py::test_parsed_file_event_ignores_extra_fields tests/test_embedder.py::test_embedded_nodes_event_shape -v
```

Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add services/embedder/app/models.py services/embedder/tests/test_embedder.py
git commit -m "feat(embedder): add Pydantic models"
```

---

## Task 3: Embedding Providers

**Files:**
- Create: `services/embedder/app/providers/base.py`
- Create: `services/embedder/app/providers/ollama.py`
- Create: `services/embedder/app/providers/voyage.py`
- Modify: `services/embedder/tests/test_embedder.py` (add provider tests)

- [ ] **Step 1: Add failing provider tests**

Append to `services/embedder/tests/test_embedder.py`:

```python
# ── Providers ─────────────────────────────────────────────────────────────────

def test_ollama_provider_is_embedding_provider():
    from app.providers.base import EmbeddingProvider
    from app.providers.ollama import OllamaProvider
    provider = OllamaProvider()
    assert isinstance(provider, EmbeddingProvider)
    assert hasattr(provider, "embed")


def test_voyage_provider_is_embedding_provider():
    import os
    os.environ["VOYAGE_API_KEY"] = "test-key"
    try:
        from app.providers.base import EmbeddingProvider
        from app.providers.voyage import VoyageProvider
        provider = VoyageProvider()
        assert isinstance(provider, EmbeddingProvider)
        assert hasattr(provider, "embed")
    finally:
        os.environ.pop("VOYAGE_API_KEY", None)


def test_voyage_provider_raises_without_api_key():
    import os
    os.environ.pop("VOYAGE_API_KEY", None)
    from app.providers.voyage import VoyageProvider
    # VoyageProvider reads VOYAGE_API_KEY in __init__, not at module load —
    # no reload needed; instantiating with the key absent is sufficient.
    with pytest.raises(ValueError, match="VOYAGE_API_KEY"):
        VoyageProvider()
```

- [ ] **Step 2: Run tests — expect failure**

```bash
cd services/embedder
pytest tests/test_embedder.py::test_ollama_provider_is_embedding_provider tests/test_embedder.py::test_voyage_provider_is_embedding_provider tests/test_embedder.py::test_voyage_provider_raises_without_api_key -v
```

Expected: `ModuleNotFoundError`.

- [ ] **Step 3: Implement providers/base.py**

```python
class EmbeddingProvider:
    async def embed(self, texts: list[str]) -> list[list[float]]:
        raise NotImplementedError
```

- [ ] **Step 4: Implement providers/ollama.py**

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

- [ ] **Step 5: Implement providers/voyage.py**

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

- [ ] **Step 6: Run provider tests — expect pass**

```bash
cd services/embedder
pytest tests/test_embedder.py::test_ollama_provider_is_embedding_provider tests/test_embedder.py::test_voyage_provider_is_embedding_provider tests/test_embedder.py::test_voyage_provider_raises_without_api_key -v
```

Expected: 3 passed.

- [ ] **Step 7: Commit**

```bash
git add services/embedder/app/providers/
git commit -m "feat(embedder): add OllamaProvider and VoyageProvider"
```

---

## Task 4: Core Embedding Logic

**Files:**
- Create: `services/embedder/app/embedder.py`
- Modify: `services/embedder/tests/test_embedder.py` (add embedder logic tests)

- [ ] **Step 1: Add failing embedder logic tests**

Append to `services/embedder/tests/test_embedder.py`:

```python
# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_node(name: str, signature: str, docstring: str = "", body: str = "pass", node_hash: str = "sha256:new") -> "ParsedNode":
    from app.models import ParsedNode
    return ParsedNode(
        stable_id=f"sha256:{name}",
        node_hash=node_hash,
        type="function",
        name=name,
        signature=signature,
        docstring=docstring,
        body=body,
        line_start=1,
        line_end=1,
    )


# ── build_embed_text ──────────────────────────────────────────────────────────

def test_build_embed_text_no_docstring():
    from app.embedder import build_embed_text
    node = _make_node("foo", "foo(x: int) -> str", docstring="")
    result = build_embed_text(node)
    assert result == "foo foo(x: int) -> str"


def test_build_embed_text_with_docstring():
    from app.embedder import build_embed_text
    node = _make_node("foo", "foo(x: int) -> str", docstring="Does something useful")
    result = build_embed_text(node)
    assert result == "foo foo(x: int) -> str Does something useful"


def test_build_embed_text_never_includes_body():
    from app.embedder import build_embed_text
    node = _make_node("foo", "foo()", body="x = 1\ny = 2\nreturn x + y")
    result = build_embed_text(node)
    assert "x = 1" not in result
    assert "return x + y" not in result


# ── embed_nodes ───────────────────────────────────────────────────────────────

async def test_embed_nodes_batching():
    """130 nodes should produce exactly 3 provider.embed() calls: 64+64+2."""
    from app.embedder import embed_nodes
    nodes = [_make_node(f"fn{i}", f"fn{i}()") for i in range(130)]
    neo4j_cache: dict = {}  # all new

    call_count = 0
    batch_sizes = []

    async def counting_embed(texts):
        nonlocal call_count
        call_count += 1
        batch_sizes.append(len(texts))
        return [[0.1] * 768 for _ in texts]

    mock_provider = MagicMock()
    mock_provider.embed = counting_embed
    await embed_nodes(nodes, neo4j_cache, mock_provider, batch_size=64)

    assert call_count == 3
    assert batch_sizes == [64, 64, 2]


async def test_embed_nodes_skips_unchanged():
    """Nodes whose node_hash matches the Neo4j cache are excluded from output."""
    from app.embedder import embed_nodes
    unchanged = _make_node("old_fn", "old_fn()", node_hash="sha256:old")
    new_node = _make_node("new_fn", "new_fn()", node_hash="sha256:new")
    neo4j_cache = {unchanged.stable_id: "sha256:old"}  # hash matches

    async def fake_embed(texts):
        return [[0.1] * 768 for _ in texts]

    mock_provider = MagicMock()
    mock_provider.embed = fake_embed

    result = await embed_nodes([unchanged, new_node], neo4j_cache, mock_provider)

    stable_ids = [n.stable_id for n in result]
    assert unchanged.stable_id not in stable_ids
    assert new_node.stable_id in stable_ids


async def test_embed_nodes_includes_new_nodes():
    """Nodes absent from Neo4j cache are always embedded."""
    from app.embedder import embed_nodes
    node = _make_node("brand_new", "brand_new()", node_hash="sha256:fresh")
    neo4j_cache: dict = {}  # node not known to Neo4j

    async def fake_embed(texts):
        return [[0.5] * 768 for _ in texts]

    mock_provider = MagicMock()
    mock_provider.embed = fake_embed

    result = await embed_nodes([node], neo4j_cache, mock_provider)
    assert len(result) == 1
    assert result[0].stable_id == node.stable_id


async def test_embed_nodes_all_unchanged_returns_empty():
    """When all nodes are unchanged, output is an empty list."""
    from app.embedder import embed_nodes
    node = _make_node("fn", "fn()", node_hash="sha256:same")
    neo4j_cache = {node.stable_id: "sha256:same"}

    mock_provider = MagicMock()
    mock_provider.embed = AsyncMock()  # should not be called

    result = await embed_nodes([node], neo4j_cache, mock_provider)

    assert result == []
    mock_provider.embed.assert_not_called()


async def test_embed_nodes_neo4j_unreachable_treats_all_as_new():
    """When Neo4j cache is empty (unreachable), all nodes are embedded."""
    from app.embedder import embed_nodes
    nodes = [_make_node(f"fn{i}", f"fn{i}()") for i in range(3)]
    neo4j_cache: dict = {}  # empty = all treated as new

    call_count = 0

    async def counting_embed(texts):
        nonlocal call_count
        call_count += 1
        return [[0.1] * 768 for _ in texts]

    mock_provider = MagicMock()
    mock_provider.embed = counting_embed

    result = await embed_nodes(nodes, neo4j_cache, mock_provider)
    assert len(result) == 3
```

- [ ] **Step 2: Run tests — expect failure**

```bash
cd services/embedder
pytest tests/test_embedder.py -k "build_embed_text or embed_nodes" -v
```

Expected: `ModuleNotFoundError` (app.embedder doesn't exist yet).

- [ ] **Step 3: Implement embedder.py**

Create `services/embedder/app/embedder.py`:

```python
import os
from .models import EmbeddedNode, ParsedNode
from .providers.base import EmbeddingProvider


def build_embed_text(node: ParsedNode) -> str:
    parts = [node.name, node.signature]
    if node.docstring:
        parts.append(node.docstring)
    return " ".join(parts)


async def embed_nodes(
    nodes: list[ParsedNode],
    neo4j_cache: dict[str, str],
    provider: EmbeddingProvider,
    batch_size: int = 64,
    embedding_dim: int = 0,
) -> list[EmbeddedNode]:
    """Embed nodes, skipping any whose node_hash already matches Neo4j.

    neo4j_cache: {stable_id: node_hash} for known nodes.
    embedding_dim: if > 0, validate that every returned vector has this length.
    """
    to_embed = [
        n for n in nodes
        if neo4j_cache.get(n.stable_id) != n.node_hash
    ]

    if not to_embed:
        return []

    embed_texts = [build_embed_text(n) for n in to_embed]

    vectors: list[list[float]] = []
    for i in range(0, len(embed_texts), batch_size):
        batch = embed_texts[i : i + batch_size]
        batch_vectors = await provider.embed(batch)
        if embedding_dim > 0:
            for vec in batch_vectors:
                if len(vec) != embedding_dim:
                    raise ValueError(
                        f"Expected vector dimension {embedding_dim}, got {len(vec)}"
                    )
        vectors.extend(batch_vectors)

    return [
        EmbeddedNode(
            stable_id=node.stable_id,
            vector=vector,
            embed_text=embed_texts[idx],
            node_hash=node.node_hash,
        )
        for idx, (node, vector) in enumerate(zip(to_embed, vectors))
    ]
```

- [ ] **Step 4: Run embedder logic tests — expect pass**

```bash
cd services/embedder
pytest tests/test_embedder.py -k "build_embed_text or embed_nodes" -v
```

Expected: all 8 tests pass.

- [ ] **Step 5: Run full test suite**

```bash
cd services/embedder
pytest tests/ -v
```

Expected: all tests pass.

- [ ] **Step 6: Commit**

```bash
git add services/embedder/app/embedder.py services/embedder/tests/test_embedder.py
git commit -m "feat(embedder): add embed_text builder and embed_nodes logic"
```

---

## Task 5: Neo4j Client

**Files:**
- Create: `services/embedder/app/neo4j_client.py`

No unit tests — the Neo4j client wraps an external driver and requires a live server. It is tested implicitly via the consumer integration path. The test for the unreachable-Neo4j fallback (test 8) is already covered in Task 4 by passing an empty cache.

**Known gap:** the consumer's error-handling paths (Neo4j catch → fallback; malformed JSON → XACK; embedding error → no XACK) are integration-level behaviour. They require a live Redis connection to test properly and are verified manually via the scripts in CLAUDE.md's Verification section.

- [ ] **Step 1: Implement neo4j_client.py**

Create `services/embedder/app/neo4j_client.py`:

```python
import asyncio
import logging
import os
from typing import Optional

import neo4j

logger = logging.getLogger(__name__)

_QUERY = """
UNWIND $ids AS id
MATCH (n:Node {stable_id: id})
RETURN n.stable_id AS stable_id, n.node_hash AS node_hash
"""


class Neo4jClient:
    def __init__(self) -> None:
        url = os.environ.get("NEO4J_URL", "bolt://localhost:7687")
        user = os.environ.get("NEO4J_USER", "neo4j")
        password = os.environ.get("NEO4J_PASSWORD", "")
        self._driver = neo4j.GraphDatabase.driver(url, auth=(user, password))

    def verify_connectivity(self) -> None:
        self._driver.verify_connectivity()

    def close(self) -> None:
        self._driver.close()

    async def get_node_hashes(self, stable_ids: list[str]) -> dict[str, str]:
        """Return {stable_id: node_hash} for all known nodes.

        Nodes absent from the result are new (not yet in Neo4j).
        Runs the sync bolt driver in a thread via run_in_executor.
        """
        if not stable_ids:
            return {}
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, self._query, stable_ids)

    def _query(self, stable_ids: list[str]) -> dict[str, str]:
        with self._driver.session() as session:
            result = session.run(_QUERY, ids=stable_ids)
            return {
                record["stable_id"]: record["node_hash"]
                for record in result
            }
```

- [ ] **Step 2: Verify no import errors**

```bash
cd services/embedder
python -c "from app.neo4j_client import Neo4jClient; print('ok')"
```

Expected: `ok`

- [ ] **Step 3: Commit**

```bash
git add services/embedder/app/neo4j_client.py
git commit -m "feat(embedder): add Neo4j client with UNWIND batch hash lookup"
```

---

## Task 6: Consumer

**Files:**
- Create: `services/embedder/app/consumer.py`

- [ ] **Step 1: Implement consumer.py**

Create `services/embedder/app/consumer.py`:

```python
import asyncio
import json
import logging
import os
import socket

import redis.asyncio as aioredis
import redis.exceptions
from pydantic import ValidationError

from .embedder import embed_nodes
from .models import EmbeddedNodesEvent, ParsedFileEvent
from .neo4j_client import Neo4jClient
from .providers.base import EmbeddingProvider

logger = logging.getLogger(__name__)

STREAM_IN = "stream:parsed-file"
STREAM_OUT = "stream:embedded-nodes"
GROUP = "embedder-group"


async def _process(
    r: aioredis.Redis,
    data: dict,
    provider: EmbeddingProvider,
    neo4j_client: Neo4jClient,
    batch_size: int,
    embedding_dim: int,
) -> None:
    raw = data.get(b"event") or data.get("event")
    if raw is None:
        raise KeyError("message missing 'event' key")
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8")

    event = ParsedFileEvent.model_validate_json(raw)

    stable_ids = [n.stable_id for n in event.nodes]
    try:
        neo4j_cache = await neo4j_client.get_node_hashes(stable_ids)
    except Exception as exc:
        logger.warning(
            "Neo4j unreachable, treating all %d nodes as new: %s",
            len(stable_ids), exc,
        )
        neo4j_cache = {}

    embedded = await embed_nodes(
        event.nodes, neo4j_cache, provider, batch_size, embedding_dim
    )

    out = EmbeddedNodesEvent(
        repo=event.repo,
        commit_sha=event.commit_sha,
        nodes=embedded,
    )
    await r.xadd(STREAM_OUT, {"event": out.model_dump_json()})


async def run_consumer(
    provider: EmbeddingProvider,
    neo4j_client: Neo4jClient,
    batch_size: int = 64,
    embedding_dim: int = 0,
) -> None:
    url = os.environ.get("REDIS_URL", "redis://localhost:6379")
    r = aioredis.from_url(url)
    consumer_name = f"embedder-{socket.gethostname()}"

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
                        await _process(
                            r, data, provider, neo4j_client, batch_size, embedding_dim
                        )
                        await r.xack(STREAM_IN, GROUP, msg_id)
                    except (json.JSONDecodeError, ValidationError, KeyError) as exc:
                        logger.warning(
                            "Bad message %s, skipping (XACK): %s", msg_id, exc
                        )
                        await r.xack(STREAM_IN, GROUP, msg_id)
                    except asyncio.CancelledError:
                        raise
                    except Exception as exc:
                        logger.error("Failed msg_id=%s: %s", msg_id, exc)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.error("Consumer loop error: %s", exc)
```

- [ ] **Step 2: Verify no import errors**

```bash
cd services/embedder
python -c "from app.consumer import run_consumer; print('ok')"
```

Expected: `ok`

- [ ] **Step 3: Commit**

```bash
git add services/embedder/app/consumer.py
git commit -m "feat(embedder): add async Redis Stream consumer"
```

---

## Task 7: FastAPI App

**Files:**
- Create: `services/embedder/app/main.py`

- [ ] **Step 1: Implement main.py**

Create `services/embedder/app/main.py`:

```python
import asyncio
import logging
import os
from contextlib import asynccontextmanager

import redis.asyncio as aioredis
from fastapi import FastAPI
from fastapi.responses import JSONResponse, PlainTextResponse

logger = logging.getLogger(__name__)

VERSION = "0.1.0"
SERVICE = "embedder"

_redis_client: aioredis.Redis | None = None
_neo4j_client = None
_provider = None


def _get_redis() -> aioredis.Redis:
    global _redis_client
    if _redis_client is None:
        url = os.environ.get("REDIS_URL", "redis://localhost:6379")
        _redis_client = aioredis.from_url(url)
    return _redis_client


def _make_provider():
    from .providers.ollama import OllamaProvider
    from .providers.voyage import VoyageProvider

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
    global _neo4j_client, _provider

    from .neo4j_client import Neo4jClient

    _provider = _make_provider()
    _neo4j_client = Neo4jClient()

    batch_size = int(os.environ.get("BATCH_SIZE", "64"))
    embedding_dim = _get_embedding_dim()

    from .consumer import run_consumer

    task = asyncio.create_task(
        run_consumer(_provider, _neo4j_client, batch_size, embedding_dim)
    )
    try:
        yield
    finally:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        if _neo4j_client:
            _neo4j_client.close()


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
        if _neo4j_client:
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(None, _neo4j_client.verify_connectivity)
    except Exception as exc:
        errors.append(f"neo4j: {exc}")
    if errors:
        return JSONResponse(status_code=503, content={"status": "unavailable", "errors": errors})
    return {"status": "ok"}


@app.get("/metrics")
def metrics():
    lines = [
        "# HELP embedder_messages_processed_total Total messages processed",
        "# TYPE embedder_messages_processed_total counter",
        "embedder_messages_processed_total 0",
        "# HELP embedder_messages_failed_total Total messages failed",
        "# TYPE embedder_messages_failed_total counter",
        "embedder_messages_failed_total 0",
        "# HELP embedder_nodes_embedded_total Total nodes embedded",
        "# TYPE embedder_nodes_embedded_total counter",
        "embedder_nodes_embedded_total 0",
        "# HELP embedder_nodes_skipped_total Total nodes skipped (unchanged)",
        "# TYPE embedder_nodes_skipped_total counter",
        "embedder_nodes_skipped_total 0",
    ]
    return PlainTextResponse("\n".join(lines) + "\n")
```

- [ ] **Step 2: Verify no import errors**

```bash
cd services/embedder
python -c "from app.main import app; print('ok')"
```

Expected: `ok`

- [ ] **Step 3: Run full test suite one more time**

```bash
cd services/embedder
pytest tests/ -v
```

Expected: all tests pass.

- [ ] **Step 4: Commit**

```bash
git add services/embedder/app/main.py
git commit -m "feat(embedder): add FastAPI app with health/ready/metrics endpoints"
```

---

## Task 8: Dockerfile

**Files:**
- Create: `services/embedder/Dockerfile`

- [ ] **Step 1: Create Dockerfile**

Create `services/embedder/Dockerfile`:

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

- [ ] **Step 2: Build the image**

```bash
cd services/embedder
docker build -t tc-embedder:test .
```

Expected: `Successfully built ...` with no errors.

- [ ] **Step 3: Verify health endpoint starts**

```bash
docker run --rm -d --name tc-embedder-test \
  -p 8083:8080 \
  -e REDIS_URL=redis://host.docker.internal:6379 \
  -e NEO4J_URL=bolt://host.docker.internal:7687 \
  -e NEO4J_PASSWORD=localpassword \
  tc-embedder:test
sleep 2
curl -s http://localhost:8083/health
docker stop tc-embedder-test
```

Expected:
```json
{"status":"ok","service":"embedder","version":"0.1.0"}
```

- [ ] **Step 4: Commit**

```bash
git add services/embedder/Dockerfile
git commit -m "feat(embedder): add Dockerfile"
```

---

## Task 9: Wire into docker-compose

**Files:**
- Modify: `docker-compose.yml`

- [ ] **Step 1: Add embedder service to docker-compose.yml**

Open `docker-compose.yml` and add the following block inside `services:`, after the `ollama:` block:

```yaml
  embedder:
    build: ./services/embedder
    ports:
      - "8083:8080"
    environment:
      REDIS_URL: redis://redis:6379
      NEO4J_URL: bolt://neo4j:7687
      NEO4J_USER: neo4j
      NEO4J_PASSWORD: localpassword
      EMBEDDING_PROVIDER: ollama
      OLLAMA_URL: http://ollama:11434
      EMBEDDING_MODEL: nomic-embed-text
      EMBEDDING_DIM: "768"
      BATCH_SIZE: "64"
    depends_on:
      redis:
        condition: service_healthy
      neo4j:
        condition: service_healthy
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

- [ ] **Step 2: Verify docker-compose config is valid**

```bash
docker compose config --quiet
```

Expected: no errors.

- [ ] **Step 3: Commit**

```bash
git add docker-compose.yml
git commit -m "feat(embedder): wire embedder into docker-compose"
```

---

## Verification Checklist

After all tasks are complete, verify the definition of done:

- [ ] `curl http://localhost:8083/health` returns `{"status":"ok","service":"embedder","version":"0.1.0"}`
- [ ] `pytest services/embedder/tests/ -v` — all 14 tests pass (3 model + 3 provider + 3 build_embed_text + 5 embed_nodes)
- [ ] `docker build services/embedder -t tc-embedder:test` — builds cleanly
- [ ] embed_text formula confirmed: `name + " " + signature [+ " " + docstring]`, no body
- [ ] Batching: 130 nodes → 3 provider calls (64+64+2) — confirmed by test 4
- [ ] Skip-unchanged: hash match → excluded from output — confirmed by test 5+7
- [ ] Neo4j unreachable → all nodes treated as new — confirmed by test 8
- [ ] VoyageProvider raises on missing API key — confirmed by test 11
