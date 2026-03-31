# CLAUDE.md — Repo watcher

## TerseContext — system overview

TerseContext produces the minimum sufficient context window for an LLM to understand
and safely modify a codebase. Three pipelines: Static ingestion (Python) → Query pipeline
(Go+Python) → Dynamic analysis (Python). Stores: Neo4j, Qdrant, Redis, Postgres.

Locked stable_id:  sha256(repo + ":" + file_path + ":" + node_type + ":" + qualified_name)
Locked node_hash:  sha256(name + signature + body)
All services expose: GET /health, GET /ready, GET /metrics

---

## Your role — Repo watcher

You are the entry point of the static ingestion pipeline. You watch a git repository
for changes and emit FileChanged events to stream:file-changed. Every other ingestion
service depends on what you produce.

Before you existed, FileChanged events were pushed manually via scripts during
development. You automate that. By the time you are built, the full static pipeline
is already working end-to-end — you are simply replacing the manual trigger with an
automatic one.

Port: 8091 (internal :8080, mapped 8091:8080)
Language: Python
Trigger: git post-commit hook OR polling interval
Output stream: stream:file-changed
Reads: git repository on disk, Redis (last indexed SHA)
Writes: Redis (last indexed SHA), stream:file-changed

Build this AFTER symbol-resolver is stable. At that point the full static pipeline
exists and you just need to wire in the automatic trigger.

---

## Output event schema (contracts/events/file_changed.json)

```json
{
  "repo": "acme-api",
  "commit_sha": "a4f91c",
  "path": "auth/service.py",
  "language": "python",
  "diff_type": "modified",
  "changed_nodes": ["sha256:fn_authenticate", "sha256:fn_hash_pw"],
  "added_nodes": [],
  "deleted_nodes": []
}
```

Emit one FileChanged event per affected file per commit.
Do NOT batch multiple files into one event.

diff_type values:
- `added`       — file is new in this commit
- `modified`    — file existed before and changed
- `deleted`     — file was removed in this commit
- `full_rescan` — triggered manually, process all nodes regardless of hash

---

## Two trigger modes

### Mode 1 — Git hook (default, recommended)

Register a post-commit hook in the target repository:

```bash
# .git/hooks/post-commit
#!/bin/bash
curl -s -X POST http://localhost:8091/hook \
  -H 'Content-Type: application/json' \
  -d "{\"repo_path\": \"$(pwd)\", \"commit_sha\": \"$(git rev-parse HEAD)\"}"
```

The repo watcher exposes POST /hook which receives the commit SHA and repo path,
computes the diff, and emits FileChanged events.

### Mode 2 — Polling (fallback)

Poll the repository on a configurable interval (default 30 seconds).
Compare HEAD against last indexed SHA stored in Redis.
If different: compute diff and emit events.

Configurable via WATCH_MODE env var: "hook" | "poll" (default: "hook")
Polling interval: POLL_INTERVAL_SECONDS env var (default: 30)

---

## Diff computation — the core logic

Given two commit SHAs (previous and current), compute what changed:

```python
import subprocess

def get_changed_files(repo_path: str, prev_sha: str, current_sha: str) -> list[dict]:
    result = subprocess.run(
        ["git", "diff", "--name-status", prev_sha, current_sha],
        cwd=repo_path,
        capture_output=True,
        text=True,
    )
    changed = []
    for line in result.stdout.strip().split("\n"):
        if not line:
            continue
        status, *paths = line.split("\t")
        path = paths[-1]  # for renames, take the new path
        diff_type = {
            "A": "added",
            "M": "modified",
            "D": "deleted",
            "R": "modified",  # renamed = treat as modified
        }.get(status[0], "modified")
        changed.append({"path": path, "diff_type": diff_type})
    return changed
```

Filter to supported languages only. v1 supports Python (.py) and TypeScript (.ts, .tsx).
Skip all other file types silently.

---

## AST-level node diff — CRITICAL for incremental indexing

For `modified` files, do NOT emit changed_nodes=[] and let the parser re-process
everything. That defeats the purpose of incremental indexing.

Instead, compute which nodes actually changed:

```python
def get_changed_nodes(repo_path, file_path, prev_sha, current_sha, repo):
    # get file content at both commits
    prev_content = git_show(repo_path, prev_sha, file_path)
    curr_content = git_show(repo_path, current_sha, file_path)

    # parse both versions with tree-sitter
    prev_nodes = parse_nodes(prev_content, file_path, repo)
    curr_nodes = parse_nodes(curr_content, file_path, repo)

    # compare node_hashes
    prev_hashes = {n.stable_id: n.node_hash for n in prev_nodes}
    curr_hashes = {n.stable_id: n.node_hash for n in curr_nodes}

    changed = [sid for sid, h in curr_hashes.items()
               if prev_hashes.get(sid) != h]
    added   = [sid for sid in curr_hashes if sid not in prev_hashes]
    deleted = [sid for sid in prev_hashes if sid not in curr_hashes]

    return changed, added, deleted
```

This requires Tree-sitter to be available in the repo watcher — import the same
parsing logic used by the parser service. Consider extracting it to a shared
internal package under `services/shared/`.

For `added` files: changed_nodes=[], added_nodes=[all node stable_ids], deleted_nodes=[]
For `deleted` files: changed_nodes=[], added_nodes=[], deleted_nodes=[all node stable_ids from prev commit]

---

## Last indexed SHA tracking

Redis key: "last_indexed_sha:{repo}"

On startup: read last_indexed_sha from Redis. If missing, treat as first run.
After successfully emitting all events for a commit: write new SHA to Redis.
If emission fails partway through: do NOT update the SHA — retry on next trigger.

First run (no last_indexed_sha):
- Emit full_rescan for every supported file in the repo
- This is equivalent to `tersecontext index ./repo --full`

```python
def get_all_files(repo_path: str) -> list[str]:
    result = subprocess.run(
        ["git", "ls-files"],
        cwd=repo_path,
        capture_output=True,
        text=True,
    )
    return [f for f in result.stdout.strip().split("\n")
            if f.endswith((".py", ".ts", ".tsx"))]
```

---

## HTTP endpoints

```
POST /hook
  Body: { repo_path: str, commit_sha: str }
  Response: 202 Accepted { events_emitted: N, files_changed: M }
  Triggers diff computation and event emission for a specific commit.

POST /index
  Body: { repo_path: str, full_rescan: bool }
  Response: 202 Accepted { queued: true }
  Triggers a full rescan if full_rescan=true, or a diff from last SHA if false.
  This is what the API gateway calls when POST /index is hit.

GET /status
  Response: { repo: str, last_sha: str, last_indexed_at: str, pending: bool }

GET /health
GET /ready   — 503 if Redis unreachable or repo_path not mounted
GET /metrics
```

---

## Service structure

```
services/repo-watcher/
  app/
    main.py           — FastAPI app
    watcher.py        — polling loop as background task
    differ.py         — git diff + AST node diff logic
    emitter.py        — Redis Stream emission
    models.py         — Pydantic models
  tests/
    test_differ.py
    test_emitter.py
    fixtures/
      sample_repo/    — a minimal git repo for testing
  Dockerfile
  requirements.txt
```

---

## Installing the git hook

Provide a helper endpoint and CLI command for registering the hook:

```
POST /install-hook
  Body: { repo_path: str }
  Response: { installed: true, hook_path: str }
  Writes the post-commit hook script to {repo_path}/.git/hooks/post-commit
  and makes it executable.
```

The hook script POSTs to the repo watcher's /hook endpoint.
Document the URL format in README — users need to know what port to use.

---

## Environment variables

```
REDIS_URL           redis://redis:6379
REPO_ROOT           /repos              # base path where repos are mounted
WATCH_MODE          hook                # hook | poll
POLL_INTERVAL_SECONDS 30
WATCHER_URL         http://localhost:8091  # used in generated hook scripts
```

---

## Verification — confirm before approving this worktree

```bash
# 1. Service starts
cd services/repo-watcher
docker build -t tersecontext-repo-watcher .
docker run -d --name watcher-test \
  -e REDIS_URL=redis://host.docker.internal:6379 \
  -e REPO_ROOT=/repos \
  -v $(pwd)/tests/fixtures:/repos \
  -p 8091:8080 tersecontext-repo-watcher

# 2. Health checks
curl http://localhost:8091/health
# expect: {"status":"ok","service":"repo-watcher","version":"0.1.0"}

curl http://localhost:8091/ready
# expect: 200 if Redis reachable

# 3. Manual trigger — full rescan
curl -X POST http://localhost:8091/index \
  -H 'Content-Type: application/json' \
  -d '{"repo_path":"/repos/sample_repo","full_rescan":true}'
# expect: 202 {"queued":true}

sleep 3
python scripts/read_stream.py stream:file-changed
# expect: one FileChanged event per .py file with diff_type="full_rescan"

# 4. Simulate a commit — modify a file and trigger /hook
cd tests/fixtures/sample_repo
echo "# change" >> auth.py
git add auth.py && git commit -m "test change"
NEW_SHA=$(git rev-parse HEAD)

curl -X POST http://localhost:8091/hook \
  -H 'Content-Type: application/json' \
  -d "{\"repo_path\":\"/repos/sample_repo\",\"commit_sha\":\"$NEW_SHA\"}"
# expect: 202 {"events_emitted":1,"files_changed":1}

python scripts/read_stream.py stream:file-changed
# expect: FileChanged event for auth.py with diff_type="modified"
# changed_nodes should contain only the stable_ids of nodes whose body changed
# NOT all nodes in the file

# 5. Verify incremental — change a comment only (no node body change)
echo "# just a comment at module level" >> auth.py
git add auth.py && git commit -m "comment only"
NEW_SHA=$(git rev-parse HEAD)
curl -X POST http://localhost:8091/hook \
  -H 'Content-Type: application/json' \
  -d "{\"repo_path\":\"/repos/sample_repo\",\"commit_sha\":\"$NEW_SHA\"}"

python scripts/read_stream.py stream:file-changed
# expect: FileChanged event emitted BUT changed_nodes=[] (no node bodies changed)
# The parser will receive it and do nothing (no nodes to re-process)

# 6. Delete a file
git rm auth.py && git commit -m "delete auth"
NEW_SHA=$(git rev-parse HEAD)
curl -X POST http://localhost:8091/hook \
  -H 'Content-Type: application/json' \
  -d "{\"repo_path\":\"/repos/sample_repo\",\"commit_sha\":\"$NEW_SHA\"}"

python scripts/read_stream.py stream:file-changed
# expect: FileChanged with diff_type="deleted", deleted_nodes populated

# 7. Install hook
curl -X POST http://localhost:8091/install-hook \
  -H 'Content-Type: application/json' \
  -d '{"repo_path":"/repos/sample_repo"}'
cat /repos/sample_repo/.git/hooks/post-commit
# expect: hook script with curl call to /hook endpoint

# 8. Unit tests
pytest services/repo-watcher/tests/ -v
```

---

## Definition of done

- [ ] /health returns 200
- [ ] /ready returns 503 when Redis is unreachable
- [ ] Full rescan emits one FileChanged per supported file
- [ ] Modified file emits only changed_nodes whose node_hash changed — not all nodes
- [ ] Comment-only change emits event with changed_nodes=[]
- [ ] Deleted file emits diff_type=deleted with deleted_nodes populated
- [ ] last_indexed_sha updated in Redis after successful emission
- [ ] First run (no last SHA) triggers full rescan automatically
- [ ] /install-hook writes executable hook script to repo
- [ ] All unit tests pass
- [ ] Dockerfile builds cleanly
