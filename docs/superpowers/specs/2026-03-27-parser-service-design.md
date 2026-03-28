# Parser Service Design

**Date:** 2026-03-27
**Service:** `services/parser`
**Status:** Approved

---

## Spec vs. CLAUDE.md Discrepancies

The project's `CLAUDE.md` contains several points that conflict with this spec. **This spec takes precedence.**

| CLAUDE.md statement | Correct behaviour (this spec) |
|---|---|
| Output JSON example includes `"qualified_name"` in `ParsedNode` | `qualified_name` is internal-only; it must NOT be serialized into the emitted `ParsedNode` |
| Import nodes have "no body" | All node types including imports must have `body` populated from `source_bytes[node.start_byte:node.end_byte].decode("utf-8")` |
| `diff_type=deleted`: "emit no ParsedFile" | A `ParsedFileEvent` IS emitted for deleted files — with empty `nodes[]` and empty `intra_file_edges[]` — it is NOT suppressed |

---

## Overview

The parser service is the first stage of TerseContext's static ingestion pipeline. It consumes `FileChanged` events from a Redis Stream, parses source files with Tree-sitter, extracts structured nodes and intra-file edges, and emits `ParsedFile` events downstream. It is the only service that reads source files directly.

- **Port:** 8081 (internal :8080, mapped 8081:8080)
- **Language:** Python 3.12
- **Input stream:** `stream:file-changed`
- **Output stream:** `stream:parsed-file`
- **Reads from disk:** `REPO_ROOT/{event.path}`
- **Writes to stores:** none

---

## File Structure

```
services/parser/
  app/
    main.py        FastAPI app — /health, /ready, /metrics; starts consumer background task
    consumer.py    Redis Stream consumer loop
    parser.py      Tree-sitter grammar loading and parse dispatch
    extractor.py   AST walking — node and edge extraction, stable_id/node_hash computation
    models.py      Pydantic models for FileChangedEvent, ParsedNode, IntraFileEdge, ParsedFileEvent
  tests/
    test_parser.py
    fixtures/
      sample.py    Fixture: classes, methods, top-level functions, imports
  Dockerfile
  requirements.txt
  pyproject.toml
```

---

## Data Flow

```
Redis stream:file-changed
        │
        ▼
consumer.py  (XREADGROUP, batch=10, consumer group "parser-group")
        │
        ▼
Read REPO_ROOT/{path} from disk
        │
        ▼
parser.py  (tree-sitter parse → syntax tree)
        │
        ▼
extractor.py  (walk AST → nodes + CALLS edges)
        │
        ├─ diff_type=full_rescan  → emit all nodes
        ├─ diff_type=modified     → filter nodes to changed_nodes + added_nodes stable_ids;
        │                           filter intra_file_edges to only edges where source_stable_id is in the emitted node set
        ├─ diff_type=added        → emit all nodes
        └─ diff_type=deleted      → emit empty nodes[] and intra_file_edges[], forward deleted_nodes unchanged
        │
        ▼
Publish ParsedFileEvent → stream:parsed-file
XACK input message
```

On extraction error: log with message ID, do NOT XACK (allow retry).

---

## Components

### `models.py`

Pydantic models matching the JSON contracts exactly:

- `FileChangedEvent` — fields: `repo`, `commit_sha`, `path`, `language`, `diff_type`, `changed_nodes`, `added_nodes`, `deleted_nodes`
- `ParsedNode` — fields: `stable_id`, `node_hash`, `type`, `name`, `signature`, `docstring`, `body`, `line_start`, `line_end`, `parent_id` (optional)
- `IntraFileEdge` — fields: `source_stable_id`, `target_stable_id`, `type`
- `ParsedFileEvent` — fields: `file_path`, `language`, `repo`, `commit_sha`, `nodes`, `intra_file_edges`, `deleted_nodes` (list of strings, always forwarded unchanged from the input `FileChangedEvent.deleted_nodes`, regardless of `diff_type`)

### `parser.py`

- Defines a `PARSERS` registry: `dict[str, Language]` mapping language name → tree-sitter `Language` object
- Loads `tree-sitter-python` grammar once at module import (expensive; never reload per file)
- Exposes `parse(source_bytes: bytes, language: str) -> Node` returning the tree root
- Raises `ValueError` for unsupported languages

### `extractor.py`

Extracts four node types from Python ASTs:

| Type | Tree-sitter node | `name` | `qualified_name` (internal only) | `parent_id` |
|---|---|---|---|---|
| `function` | `function_definition` at module level | bare name | bare name | null |
| `method` | `function_definition` inside `class_body` | bare name | `ClassName.method` | class stable_id |
| `class` | `class_definition` | bare name | bare name | null |
| `import` | `import_statement` / `import_from_statement` | module path | module path | null |

> **`qualified_name` is an internal-only value.** It is computed during extraction solely as an input to the `stable_id` hash. It must NOT appear as a field in the emitted `ParsedNode`.

**Per-node fields:**
- `body`: slice `source_bytes[node.start_byte:node.end_byte].decode("utf-8")` — applies to all node types including imports (raw source text of the statement)
- `signature`: reconstruct from AST `parameters` + return type annotation; include type hints if present; empty string for import nodes and class nodes (base classes are not included in signature — they belong in the graph layer, not here)
- `docstring`: first `expression_statement` containing a string literal in the node body; empty string if absent. Always `""` for `import` nodes (import statements have no child statements to contain a docstring)
- `line_start` / `line_end`: `node.start_point[0] + 1` / `node.end_point[0] + 1` (1-indexed)
- `stable_id`: `"sha256:" + sha256((f"{repo}:{file_path}:{type}:{qualified_name}").encode("utf-8"))`
- `node_hash`: `"sha256:" + sha256((name + signature + body).encode("utf-8"))`

All hash inputs are encoded as UTF-8 bytes before hashing.

**CALLS edge detection:**
- For each `function` or `method` node, walk the body AST for `call` nodes
- Extract the called name (handle `identifier` and `attribute` calls)
- If the name matches another node defined in the same file, emit a `CALLS` edge
- No cross-file resolution

### `consumer.py`

```python
# Startup
redis.xgroup_create("stream:file-changed", "parser-group", id="0", mkstream=True)
# (ignore BUSYGROUP error if group already exists)

consumer_name = f"parser-{socket.gethostname()}"

while True:
    messages = redis.xreadgroup(
        groupname="parser-group",
        consumername=consumer_name,
        streams={"stream:file-changed": ">"},
        count=10,
        block=1000,
    )
    for stream, events in (messages or []):
        for msg_id, data in events:
            try:
                process(data)
                redis.xack("stream:file-changed", "parser-group", msg_id)
            except Exception as e:
                log.error(f"Failed {msg_id}: {e}")
```

### `main.py`

FastAPI app with three endpoints:

- `GET /health` → `200 {"status": "ok", "service": "parser", "version": "0.1.0"}`
- `GET /ready` → `200` if Redis ping succeeds, `503` otherwise
- `GET /metrics` → Prometheus-format text (request count, processing latency, error count)

Consumer loop started as an `asyncio` background task on `startup`.

---

## Language Registry

```python
# parser.py
import tree_sitter_python as tspython
from tree_sitter import Language, Parser

PARSERS: dict[str, Language] = {
    "python": Language(tspython.language()),
}
```

Adding a new language: install `tree-sitter-<lang>`, add one entry to `PARSERS`.

---

## Environment Variables

| Variable | Required | Description |
|---|---|---|
| `REDIS_URL` | yes | e.g. `redis://localhost:6379` |
| `REPO_ROOT` | yes | Absolute path; files resolved as `REPO_ROOT/{event.path}` |

---

## Error Handling

| Scenario | Behaviour |
|---|---|
| Unsupported language | Log warning, do NOT XACK, skip |
| File not found | Log error with path, do NOT XACK |
| Tree-sitter parse error | Log error, do NOT XACK |
| Redis unreachable on startup | `/ready` returns 503; consumer retries with backoff |
| Redis disconnects mid-loop | Exception bubbles, logged, loop continues |

---

## Testing

Required test cases in `tests/test_parser.py`:

1. Function with typed args and return annotation → correct `signature`
2. Class with methods → methods have `parent_id` set to class `stable_id`
3. File with imports → import nodes present in output with `body` set to raw source slice (not empty string)
4. Function with no docstring → `docstring` is `""`, not `None`
5. `diff_type=modified` → only emits nodes whose `stable_id` is in `changed_nodes + added_nodes`; `intra_file_edges` contains only edges where `source_stable_id` is in the emitted node set
6. `diff_type=deleted` → emits `ParsedFileEvent` with empty `nodes[]` and empty `intra_file_edges[]`; `deleted_nodes` forwarded unchanged
7. `diff_type=added` → emits all nodes (same behaviour as `full_rescan`)
8. `diff_type=full_rescan` → emits all nodes (verified independently from `added`)
9. `stable_id` is deterministic — same input twice produces identical IDs
10. `node_hash` changes when function body changes

Fixture `tests/fixtures/sample.py` must contain: a class with at least two methods (one calling the other), a top-level function, and an import statement.

---

## Dockerfile

Based on `docker/python.Dockerfile` pattern:

```dockerfile
FROM python:3.12-slim
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends build-essential curl \
    && rm -rf /var/lib/apt/lists/*
RUN pip install --no-cache-dir uv
COPY pyproject.toml requirements.txt ./
RUN uv pip install --system -r requirements.txt
COPY app/ app/
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080"]
```

---

## Definition of Done

- [ ] Service starts and `/health` returns 200
- [ ] `/ready` returns 503 when Redis is unreachable
- [ ] Parsing `sample.py` produces correctly shaped `ParsedFile` event
- [ ] `stable_id` is deterministic — same file twice produces identical IDs
- [ ] `node_hash` changes when function body changes
- [ ] Methods have `parent_id` pointing to their class
- [ ] `diff_type=modified` only emits changed/added nodes
- [ ] `diff_type=deleted` emits empty `nodes[]` and empty `intra_file_edges[]`, with `deleted_nodes` forwarded unchanged
- [ ] All unit tests pass
- [ ] Dockerfile builds cleanly
