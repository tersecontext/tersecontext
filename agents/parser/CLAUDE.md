# CLAUDE.md — Parser service

## TerseContext — system overview

TerseContext produces the minimum sufficient context window for an LLM to understand
and safely modify a codebase. Three pipelines: Static ingestion (Python) → Query pipeline
(Go+Python) → Dynamic analysis (Python). Stores: Neo4j, Qdrant, Redis, Postgres.

Locked stable_id:  sha256(repo + ":" + file_path + ":" + node_type + ":" + qualified_name)
Locked node_hash:  sha256(name + signature + body)
All services expose: GET /health, GET /ready, GET /metrics

---

## Your role — Parser service

You are the first service in the static ingestion pipeline. You consume FileChanged events
from Redis Streams, parse the source file using Tree-sitter, extract structured nodes and
intra-file edges, and emit ParsedFile events downstream.

You are the only service that touches source code directly. Everything downstream
depends on the quality and correctness of what you produce.

Port: 8081 (internal :8080, mapped 8081:8080)
Language: Python
Input stream:  stream:file-changed
Output stream: stream:parsed-file
Reads from disk: source file at the path specified in the FileChanged event
Writes to: nothing (no store writes — emit events only)

---

## Input event schema (contracts/events/file_changed.json)

```json
{
  "repo": "acme-api",
  "commit_sha": "a4f91c",
  "path": "auth/service.py",
  "language": "python",
  "diff_type": "modified",
  "changed_nodes": ["sha256:..."],
  "added_nodes": [],
  "deleted_nodes": []
}
```

When diff_type is "full_rescan", process ALL nodes in the file regardless of changed_nodes.
When diff_type is "deleted", emit no ParsedFile — the graph-writer handles tombstones via
the deleted_nodes list on this event (forward it unchanged to stream:parsed-file).

---

## Output event schema (contracts/events/parsed_file.json)

```json
{
  "file_path": "auth/service.py",
  "language": "python",
  "repo": "acme-api",
  "commit_sha": "a4f91c",
  "nodes": [
    {
      "stable_id": "sha256:...",
      "node_hash": "sha256:...",
      "type": "function",
      "name": "authenticate",
      "qualified_name": "AuthService.authenticate",
      "signature": "authenticate(self, user: User, pw: str) -> Token",
      "docstring": "Validates credentials and returns JWT token.",
      "body": "def authenticate(self, user, pw):\n    ...",
      "line_start": 34,
      "line_end": 52,
      "parent_id": "sha256:...cls_stable_id..."
    }
  ],
  "intra_file_edges": [
    {
      "source_stable_id": "sha256:fn_authenticate",
      "target_stable_id": "sha256:fn_hash_pw",
      "type": "CALLS"
    }
  ]
}
```

---

## Node extraction rules

Extract these node types from Python files:
- `function` — top-level functions (FunctionDef)
- `method`   — functions inside a class (treat as function, set parent_id to class stable_id)
- `class`    — ClassDef nodes
- `import`   — Import and ImportFrom statements (name = module path, no body)

For each node:
- `name`: the bare name (authenticate, not AuthService.authenticate)
- `qualified_name`: include class prefix if method (AuthService.authenticate)
- `signature`: reconstruct from AST args + return annotation. Include type hints if present.
- `docstring`: first string literal in the node body. Empty string if absent.
- `body`: full source text of the node (use source positions from AST to slice original file)
- `stable_id`: sha256(repo + ":" + file_path + ":" + type + ":" + qualified_name)
- `node_hash`: sha256(name + signature + body)
- `parent_id`: for methods, the stable_id of their containing class. Null otherwise.

## Intra-file edge extraction

Detect CALLS edges: walk the function body AST for Call nodes. If the called name
matches another function/method defined in the SAME file, emit a CALLS edge.
Do not attempt cross-file resolution — that is the symbol resolver's job.

---

## Service structure

```
services/parser/
  app/
    main.py           — FastAPI app, /health /ready /metrics endpoints
    consumer.py       — Redis Stream consumer loop (background task)
    parser.py         — Tree-sitter parsing logic
    extractor.py      — Node and edge extraction from AST
    models.py         — Pydantic models matching contracts/
  tests/
    test_parser.py
    fixtures/
      sample.py       — a real Python file to parse in tests
  Dockerfile
  requirements.txt
  pyproject.toml
```

---

## Redis Stream consumer pattern

Use a consumer group named "parser-group" on stream:file-changed.
Consumer name: "parser-{hostname}".
On successful processing: XACK the message.
On error: do NOT acknowledge — let it retry. Log the error with the message ID.
Batch size: read up to 10 messages at a time with XREADGROUP.

```python
# Consumer loop pattern
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

Create the consumer group on startup with MKSTREAM if it does not exist.

---

## Key implementation notes

- Use `tree_sitter` and `tree_sitter_languages` Python packages.
- Load the Python grammar once at startup — it is expensive to reload per file.
- Only process nodes listed in `changed_nodes` + `added_nodes` when diff_type is "modified".
  Full file parse still happens, but only emit events for changed/added nodes.
- For `diff_type: deleted`, forward the FileChanged event to stream:parsed-file
  with empty nodes[] and intra_file_edges[] — the graph-writer reads deleted_nodes.
- The body field must be the actual source text, not a reconstruction. Slice the
  original file bytes using node.start_byte and node.end_byte from Tree-sitter.

---

## Verification — confirm before approving this worktree

```bash
# 1. Service starts
cd services/parser
docker build -t tersecontext-parser .
docker run -d --name parser-test \
  -e REDIS_URL=redis://host.docker.internal:6379 \
  -e REPO_ROOT=/repos \
  -p 8081:8080 tersecontext-parser

# 2. Health checks
curl http://localhost:8081/health
# expect: {"status":"ok","service":"parser","version":"0.1.0"}

curl http://localhost:8081/ready
# expect: 200 if Redis reachable, 503 if not

# 3. Parse a real file — push a FileChanged event and check output
python scripts/push_file_changed.py \
  --path tests/fixtures/sample.py \
  --repo test-repo \
  --diff-type full_rescan

# Wait 2 seconds, then read from stream:parsed-file
python scripts/read_stream.py stream:parsed-file
# expect: ParsedFile event with nodes[] containing functions and classes from sample.py

# 4. Verify stable_id is deterministic
# Run the same push twice — both ParsedFile events must have identical stable_ids

# 5. Verify node_hash changes when body changes
# Manually edit sample.py body, push again, check node_hash is different

# 6. Unit tests pass
pytest services/parser/tests/ -v
# All tests must pass. Required test cases:
#   - function with typed args and return annotation
#   - class with methods (methods have parent_id set)
#   - file with imports (import nodes present)
#   - function with no docstring (docstring is empty string, not None)
#   - diff_type=modified only emits changed nodes
#   - diff_type=deleted emits empty nodes[]
```

---

## Definition of done

- [ ] Service starts and /health returns 200
- [ ] /ready returns 503 when Redis is unreachable (test by stopping Redis)
- [ ] Parsing sample.py produces correctly shaped ParsedFile event
- [ ] stable_id is deterministic — same file twice produces identical IDs
- [ ] node_hash changes when function body changes
- [ ] Methods have parent_id pointing to their class
- [ ] diff_type=modified only emits changed/added nodes
- [ ] diff_type=deleted emits empty nodes[]
- [ ] All unit tests pass
- [ ] Dockerfile builds cleanly
