# Entrypoint Discoverer Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the entrypoint-discoverer service (port 8092) that queries Neo4j and Postgres to find and prioritise high-value entrypoints, then pushes ranked EntrypointJob items to Redis.

**Architecture:** FastAPI app on port 8080 (exposed as 8092). On POST /discover, queries Neo4j for entrypoints matching test_/route/click patterns, queries Postgres for last trace timestamps, scores each entrypoint (1–3), and RPUSH-es ranked jobs to `entrypoint_queue:{repo}` in Redis. All external clients (Neo4j, Postgres, Redis) are injected for testability.

**Tech Stack:** Python 3.12, FastAPI, neo4j (Python driver), psycopg2-binary, redis, pydantic, pytest, unittest.mock

---

## File Structure

```
services/entrypoint-discoverer/
├── app/
│   ├── __init__.py
│   ├── main.py          # FastAPI app: /health, /ready, /metrics, POST /discover
│   ├── models.py        # Pydantic: DiscoverRequest, DiscoverResponse, EntrypointJob
│   ├── neo4j_client.py  # query_entrypoints(), query_changed_dep_ids()
│   ├── postgres_client.py  # query_last_traced()
│   ├── scorer.py        # score_entrypoints() — pure scoring logic
│   └── queue.py         # push_jobs() — RPUSH to Redis
├── tests/
│   ├── __init__.py
│   ├── test_scorer.py
│   ├── test_queue.py
│   ├── test_neo4j_client.py
│   ├── test_postgres_client.py
│   └── test_main.py
├── requirements.txt
└── Dockerfile
```

---

### Task 1: Models

**Files:**
- Create: `services/entrypoint-discoverer/app/__init__.py`
- Create: `services/entrypoint-discoverer/app/models.py`
- Create: `services/entrypoint-discoverer/tests/__init__.py`

- [ ] **Step 1: Create app/__init__.py (empty)**

```python
```

- [ ] **Step 2: Create tests/__init__.py (empty)**

```python
```

- [ ] **Step 3: Write the failing test**

Create `services/entrypoint-discoverer/tests/test_models.py`:

```python
# tests/test_models.py
from app.models import EntrypointJob, DiscoverRequest, DiscoverResponse


def test_entrypoint_job_fields():
    job = EntrypointJob(
        stable_id="sha256:fn_test_login",
        name="test_login",
        file_path="tests/test_auth.py",
        priority=2,
        repo="myrepo",
    )
    assert job.stable_id == "sha256:fn_test_login"
    assert job.priority == 2


def test_discover_request_trigger_values():
    req = DiscoverRequest(repo="myrepo", trigger="schedule")
    assert req.trigger == "schedule"
    req2 = DiscoverRequest(repo="myrepo", trigger="pr_open")
    assert req2.trigger == "pr_open"


def test_discover_response_fields():
    resp = DiscoverResponse(discovered=10, queued=8)
    assert resp.discovered == 10
    assert resp.queued == 8
```

- [ ] **Step 4: Run test to verify it fails**

```bash
cd services/entrypoint-discoverer
python -m pytest tests/test_models.py -v
```
Expected: `ModuleNotFoundError` or `ImportError`

- [ ] **Step 5: Write minimal implementation**

Create `services/entrypoint-discoverer/app/models.py`:

```python
# app/models.py
from __future__ import annotations
from typing import Literal
from pydantic import BaseModel


class EntrypointJob(BaseModel):
    stable_id: str
    name: str
    file_path: str
    priority: int
    repo: str


class DiscoverRequest(BaseModel):
    repo: str
    trigger: Literal["schedule", "pr_open"]


class DiscoverResponse(BaseModel):
    discovered: int
    queued: int
```

- [ ] **Step 6: Run test to verify it passes**

```bash
cd services/entrypoint-discoverer
python -m pytest tests/test_models.py -v
```
Expected: PASS (3 tests)

- [ ] **Step 7: Commit**

```bash
git add services/entrypoint-discoverer/app/__init__.py \
        services/entrypoint-discoverer/app/models.py \
        services/entrypoint-discoverer/tests/__init__.py \
        services/entrypoint-discoverer/tests/test_models.py
git commit -m "feat(entrypoint-discoverer): add Pydantic models"
```

---

### Task 2: Scorer

**Files:**
- Create: `services/entrypoint-discoverer/app/scorer.py`
- Create: `services/entrypoint-discoverer/tests/test_scorer.py`

The scorer is pure logic — no I/O. It receives pre-fetched data and returns ranked jobs.

- [ ] **Step 1: Write the failing tests**

Create `services/entrypoint-discoverer/tests/test_scorer.py`:

```python
# tests/test_scorer.py
from datetime import datetime, timezone, timedelta
from app.scorer import score_entrypoints

ENTRYPOINTS = [
    {"stable_id": "id_a", "name": "test_login", "file_path": "tests/test_auth.py"},
    {"stable_id": "id_b", "name": "app.route_index", "file_path": "views.py"},
    {"stable_id": "id_c", "name": "test_signup", "file_path": "tests/test_signup.py"},
    {"stable_id": "id_d", "name": "test_logout", "file_path": "tests/test_auth.py"},
]
NOW = datetime.now(timezone.utc)


def test_never_traced_gets_score_2():
    jobs = score_entrypoints(
        entrypoints=ENTRYPOINTS[:1],
        last_traced_map={},
        changed_dep_ids=set(),
        repo="r",
    )
    assert jobs[0].priority == 2


def test_deps_changed_gets_score_3():
    last_traced_map = {"id_a": NOW - timedelta(days=5)}
    jobs = score_entrypoints(
        entrypoints=ENTRYPOINTS[:1],
        last_traced_map=last_traced_map,
        changed_dep_ids={"id_a"},
        repo="r",
    )
    assert jobs[0].priority == 3


def test_recently_traced_no_dep_change_gets_score_1():
    last_traced_map = {"id_a": NOW - timedelta(days=10)}
    jobs = score_entrypoints(
        entrypoints=ENTRYPOINTS[:1],
        last_traced_map=last_traced_map,
        changed_dep_ids=set(),
        repo="r",
    )
    assert jobs[0].priority == 1


def test_sorted_by_priority_descending():
    last_traced_map = {
        "id_b": NOW - timedelta(days=5),   # score 1
        "id_c": NOW - timedelta(days=2),   # score 1
        # id_a and id_d: never traced → score 2
    }
    changed_dep_ids = {"id_b"}  # id_b gets score 3
    jobs = score_entrypoints(
        entrypoints=ENTRYPOINTS,
        last_traced_map=last_traced_map,
        changed_dep_ids=changed_dep_ids,
        repo="r",
    )
    priorities = [j.priority for j in jobs]
    assert priorities == sorted(priorities, reverse=True)


def test_repo_propagated_to_jobs():
    jobs = score_entrypoints(
        entrypoints=ENTRYPOINTS[:1],
        last_traced_map={},
        changed_dep_ids=set(),
        repo="myrepo",
    )
    assert jobs[0].repo == "myrepo"


def test_stale_traced_no_dep_change_gets_score_1():
    """Traced > 30 days ago with no dep change still queues at priority 1."""
    last_traced_map = {"id_a": NOW - timedelta(days=60)}
    jobs = score_entrypoints(
        entrypoints=ENTRYPOINTS[:1],
        last_traced_map=last_traced_map,
        changed_dep_ids=set(),
        repo="r",
    )
    assert jobs[0].priority == 1
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd services/entrypoint-discoverer
python -m pytest tests/test_scorer.py -v
```
Expected: `ImportError` — scorer module doesn't exist

- [ ] **Step 3: Write minimal implementation**

Create `services/entrypoint-discoverer/app/scorer.py`:

```python
# app/scorer.py
from __future__ import annotations
from datetime import datetime, timezone, timedelta

from .models import EntrypointJob

_THIRTY_DAYS = timedelta(days=30)


def score_entrypoints(
    entrypoints: list[dict],
    last_traced_map: dict[str, datetime],
    changed_dep_ids: set[str],
    repo: str,
) -> list[EntrypointJob]:
    """
    Score and rank entrypoints by tracing priority.

    Priority rules:
      3 — dependency graph changed since last trace
      2 — never traced (no entry in behavior_specs)
      1 — traced within last 30 days, deps unchanged
    """
    jobs: list[EntrypointJob] = []
    for ep in entrypoints:
        sid = ep["stable_id"]
        last_traced = last_traced_map.get(sid)

        if last_traced is None:
            priority = 2
        elif sid in changed_dep_ids:
            priority = 3
        else:
            priority = 1  # traced (any age), no dep change — lowest priority

        jobs.append(EntrypointJob(
            stable_id=sid,
            name=ep["name"],
            file_path=ep["file_path"],
            priority=priority,
            repo=repo,
        ))

    return sorted(jobs, key=lambda j: j.priority, reverse=True)
```

- [ ] **Step 4: Run test to verify it passes**

```bash
cd services/entrypoint-discoverer
python -m pytest tests/test_scorer.py -v
```
Expected: PASS (6 tests)

- [ ] **Step 5: Commit**

```bash
git add services/entrypoint-discoverer/app/scorer.py \
        services/entrypoint-discoverer/tests/test_scorer.py
git commit -m "feat(entrypoint-discoverer): add priority scorer"
```

---

### Task 3: Queue

**Files:**
- Create: `services/entrypoint-discoverer/app/queue.py`
- Create: `services/entrypoint-discoverer/tests/test_queue.py`

- [ ] **Step 1: Write the failing tests**

Create `services/entrypoint-discoverer/tests/test_queue.py`:

```python
# tests/test_queue.py
import json
from unittest.mock import MagicMock
from app.queue import push_jobs
from app.models import EntrypointJob

JOBS = [
    EntrypointJob(stable_id="id_a", name="test_login", file_path="tests/test_auth.py", priority=3, repo="r"),
    EntrypointJob(stable_id="id_b", name="test_signup", file_path="tests/test_auth.py", priority=2, repo="r"),
]


def test_push_jobs_rpush_each_job():
    r = MagicMock()
    push_jobs(r, "r", JOBS)
    assert r.rpush.call_count == 2


def test_push_jobs_uses_correct_key():
    r = MagicMock()
    push_jobs(r, "myrepo", JOBS[:1])
    key = r.rpush.call_args[0][0]
    assert key == "entrypoint_queue:myrepo"


def test_push_jobs_encodes_json():
    r = MagicMock()
    push_jobs(r, "r", JOBS[:1])
    raw = r.rpush.call_args[0][1]
    payload = json.loads(raw)
    assert payload["stable_id"] == "id_a"
    assert payload["priority"] == 3


def test_push_jobs_returns_count():
    r = MagicMock()
    count = push_jobs(r, "r", JOBS)
    assert count == 2


def test_push_jobs_empty_list():
    r = MagicMock()
    count = push_jobs(r, "r", [])
    assert count == 0
    r.rpush.assert_not_called()
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd services/entrypoint-discoverer
python -m pytest tests/test_queue.py -v
```
Expected: `ImportError`

- [ ] **Step 3: Write minimal implementation**

Create `services/entrypoint-discoverer/app/queue.py`:

```python
# app/queue.py
from __future__ import annotations
import json
import redis

from .models import EntrypointJob


def push_jobs(r: redis.Redis, repo: str, jobs: list[EntrypointJob]) -> int:
    """RPUSH each job as JSON to entrypoint_queue:{repo}. Returns count pushed."""
    key = f"entrypoint_queue:{repo}"
    for job in jobs:
        r.rpush(key, json.dumps(job.model_dump()))
    return len(jobs)
```

- [ ] **Step 4: Run test to verify it passes**

```bash
cd services/entrypoint-discoverer
python -m pytest tests/test_queue.py -v
```
Expected: PASS (5 tests)

- [ ] **Step 5: Commit**

```bash
git add services/entrypoint-discoverer/app/queue.py \
        services/entrypoint-discoverer/tests/test_queue.py
git commit -m "feat(entrypoint-discoverer): add Redis queue push"
```

---

### Task 4: Neo4j Client

**Files:**
- Create: `services/entrypoint-discoverer/app/neo4j_client.py`
- Create: `services/entrypoint-discoverer/tests/test_neo4j_client.py`

Two functions:
- `query_entrypoints(driver, repo)` → list of `{"stable_id", "name", "file_path"}`
- `query_changed_dep_ids(driver, repo, traced_entries)` → set of stable_ids whose deps changed

`traced_entries` is a list of `{"stable_id": str, "last_traced_at": datetime}`.

- [ ] **Step 1: Write the failing tests**

Create `services/entrypoint-discoverer/tests/test_neo4j_client.py`:

```python
# tests/test_neo4j_client.py
from datetime import datetime, timezone
from unittest.mock import MagicMock
from app.neo4j_client import query_entrypoints, query_changed_dep_ids


def _make_driver(records):
    """Return a mock neo4j driver whose session().run() yields the given records."""
    mock_record = lambda d: MagicMock(**{"__getitem__.side_effect": d.__getitem__, "data.return_value": d})
    mock_result = MagicMock()
    mock_result.__iter__ = MagicMock(return_value=iter([mock_record(r) for r in records]))
    mock_session = MagicMock()
    mock_session.__enter__ = MagicMock(return_value=mock_session)
    mock_session.__exit__ = MagicMock(return_value=False)
    mock_session.run.return_value = mock_result
    mock_driver = MagicMock()
    mock_driver.session.return_value = mock_session
    return mock_driver, mock_session


def test_query_entrypoints_returns_list():
    rows = [
        {"stable_id": "id_a", "name": "test_login", "file_path": "tests/test_auth.py"},
        {"stable_id": "id_b", "name": "app.route_home", "file_path": "views.py"},
    ]
    driver, session = _make_driver(rows)
    result = query_entrypoints(driver, "myrepo")
    assert len(result) == 2
    assert result[0]["stable_id"] == "id_a"


def test_query_entrypoints_passes_repo_param():
    driver, session = _make_driver([])
    query_entrypoints(driver, "myrepo")
    call_kwargs = session.run.call_args
    # repo param must be passed
    params = call_kwargs[0][1] if len(call_kwargs[0]) > 1 else call_kwargs[1]
    assert params.get("repo") == "myrepo"


def test_query_changed_dep_ids_returns_set():
    rows = [{"stable_id": "id_a"}, {"stable_id": "id_c"}]
    driver, session = _make_driver(rows)
    traced = [
        {"stable_id": "id_a", "last_traced_at": datetime(2026, 1, 1, tzinfo=timezone.utc)},
        {"stable_id": "id_b", "last_traced_at": datetime(2026, 2, 1, tzinfo=timezone.utc)},
    ]
    result = query_changed_dep_ids(driver, "myrepo", traced)
    assert isinstance(result, set)
    assert "id_a" in result
    assert "id_c" in result


def test_query_changed_dep_ids_empty_when_no_traced():
    driver, _ = _make_driver([])
    result = query_changed_dep_ids(driver, "myrepo", [])
    assert result == set()
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd services/entrypoint-discoverer
python -m pytest tests/test_neo4j_client.py -v
```
Expected: `ImportError`

- [ ] **Step 3: Write minimal implementation**

Create `services/entrypoint-discoverer/app/neo4j_client.py`:

```python
# app/neo4j_client.py
from __future__ import annotations
from datetime import datetime

ENTRYPOINT_QUERY = """
MATCH (n:Node {repo: $repo, active: true})
WHERE n.name STARTS WITH 'test_'
   OR n.decorators CONTAINS 'app.route'
   OR n.decorators CONTAINS 'router.'
   OR n.decorators CONTAINS 'click.command'
RETURN n.stable_id AS stable_id, n.name AS name, n.file_path AS file_path
"""

CHANGED_DEPS_QUERY = """
UNWIND $traced_entries AS entry
MATCH (ep:Node {repo: $repo, stable_id: entry.stable_id})-[:CALLS*1..]->(dep:Node)
WHERE dep.updated_at > entry.last_traced_at
RETURN DISTINCT ep.stable_id AS stable_id
"""


def query_entrypoints(driver, repo: str) -> list[dict]:
    """Return all active entrypoint nodes for the given repo."""
    with driver.session() as session:
        result = session.run(ENTRYPOINT_QUERY, {"repo": repo})
        return [dict(record.data()) for record in result]


def query_changed_dep_ids(driver, repo: str, traced_entries: list[dict]) -> set[str]:
    """
    Return stable_ids of traced entrypoints whose dependency graph changed.

    traced_entries: list of {"stable_id": str, "last_traced_at": datetime}
    """
    if not traced_entries:
        return set()
    # Convert datetimes to ISO strings for Cypher datetime() comparison
    entries = [
        {"stable_id": e["stable_id"], "last_traced_at": e["last_traced_at"].isoformat()}
        for e in traced_entries
    ]
    with driver.session() as session:
        result = session.run(CHANGED_DEPS_QUERY, {"repo": repo, "traced_entries": entries})
        return {record["stable_id"] for record in result}
```

- [ ] **Step 4: Run test to verify it passes**

```bash
cd services/entrypoint-discoverer
python -m pytest tests/test_neo4j_client.py -v
```
Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
git add services/entrypoint-discoverer/app/neo4j_client.py \
        services/entrypoint-discoverer/tests/test_neo4j_client.py
git commit -m "feat(entrypoint-discoverer): add Neo4j client"
```

---

### Task 5: Postgres Client

**Files:**
- Create: `services/entrypoint-discoverer/app/postgres_client.py`
- Create: `services/entrypoint-discoverer/tests/test_postgres_client.py`

One function: `query_last_traced(conn, repo)` → `dict[str, datetime]` mapping stable_id to last traced timestamp.

- [ ] **Step 1: Write the failing tests**

Create `services/entrypoint-discoverer/tests/test_postgres_client.py`:

```python
# tests/test_postgres_client.py
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch
from app.postgres_client import query_last_traced


def _make_conn(rows):
    cursor = MagicMock()
    cursor.__enter__ = MagicMock(return_value=cursor)
    cursor.__exit__ = MagicMock(return_value=False)
    cursor.fetchall.return_value = rows
    conn = MagicMock()
    conn.cursor.return_value = cursor
    return conn, cursor


def test_query_last_traced_returns_dict():
    ts = datetime(2026, 3, 1, tzinfo=timezone.utc)
    conn, _ = _make_conn([("id_a", ts), ("id_b", ts)])
    result = query_last_traced(conn, "myrepo")
    assert isinstance(result, dict)
    assert result["id_a"] == ts
    assert result["id_b"] == ts


def test_query_last_traced_passes_repo():
    conn, cursor = _make_conn([])
    query_last_traced(conn, "myrepo")
    call_args = cursor.execute.call_args
    assert "myrepo" in str(call_args) or call_args[0][1] == ("myrepo",)


def test_query_last_traced_empty_table():
    conn, _ = _make_conn([])
    result = query_last_traced(conn, "myrepo")
    assert result == {}
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd services/entrypoint-discoverer
python -m pytest tests/test_postgres_client.py -v
```
Expected: `ImportError`

- [ ] **Step 3: Write minimal implementation**

Create `services/entrypoint-discoverer/app/postgres_client.py`:

```python
# app/postgres_client.py
from __future__ import annotations
from datetime import datetime

LAST_TRACED_QUERY = """
SELECT node_stable_id, MAX(generated_at) AS last_traced
FROM behavior_specs
WHERE repo = %s
GROUP BY node_stable_id
"""


def query_last_traced(conn, repo: str) -> dict[str, datetime]:
    """
    Return a map of stable_id -> last traced datetime for the given repo.
    Returns empty dict if behavior_specs has no rows for this repo.
    """
    with conn.cursor() as cur:
        cur.execute(LAST_TRACED_QUERY, (repo,))
        rows = cur.fetchall()
    return {row[0]: row[1] for row in rows}
```

- [ ] **Step 4: Run test to verify it passes**

```bash
cd services/entrypoint-discoverer
python -m pytest tests/test_postgres_client.py -v
```
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add services/entrypoint-discoverer/app/postgres_client.py \
        services/entrypoint-discoverer/tests/test_postgres_client.py
git commit -m "feat(entrypoint-discoverer): add Postgres client"
```

---

### Task 6: Discoverer (Orchestration)

**Files:**
- Create: `services/entrypoint-discoverer/app/discoverer.py`
- Create: `services/entrypoint-discoverer/tests/test_discoverer.py`

The discoverer ties together neo4j_client, postgres_client, scorer, and queue.

- [ ] **Step 1: Write the failing tests**

Create `services/entrypoint-discoverer/tests/test_discoverer.py`:

```python
# tests/test_discoverer.py
from datetime import datetime, timezone, timedelta
from unittest.mock import MagicMock, patch
from app.discoverer import run_discover


def test_run_discover_returns_discovered_and_queued():
    entrypoints = [
        {"stable_id": "id_a", "name": "test_login", "file_path": "tests/test_auth.py"},
        {"stable_id": "id_b", "name": "test_signup", "file_path": "tests/test_auth.py"},
    ]
    neo4j_driver = MagicMock()
    pg_conn = MagicMock()
    redis_client = MagicMock()

    with patch("app.discoverer.query_entrypoints", return_value=entrypoints), \
         patch("app.discoverer.query_changed_dep_ids", return_value=set()), \
         patch("app.discoverer.query_last_traced", return_value={}), \
         patch("app.discoverer.push_jobs", return_value=2) as mock_push:

        result = run_discover(neo4j_driver, pg_conn, redis_client, repo="r", trigger="schedule")

    assert result["discovered"] == 2
    assert result["queued"] == 2


def test_run_discover_skips_queuing_empty_entrypoints():
    neo4j_driver = MagicMock()
    pg_conn = MagicMock()
    redis_client = MagicMock()

    with patch("app.discoverer.query_entrypoints", return_value=[]), \
         patch("app.discoverer.query_changed_dep_ids", return_value=set()), \
         patch("app.discoverer.query_last_traced", return_value={}), \
         patch("app.discoverer.push_jobs", return_value=0) as mock_push:

        result = run_discover(neo4j_driver, pg_conn, redis_client, repo="r", trigger="schedule")

    assert result["discovered"] == 0
    assert result["queued"] == 0


def test_run_discover_passes_repo_to_all_clients():
    neo4j_driver = MagicMock()
    pg_conn = MagicMock()
    redis_client = MagicMock()

    with patch("app.discoverer.query_entrypoints", return_value=[]) as mock_ep, \
         patch("app.discoverer.query_changed_dep_ids", return_value=set()) as mock_cd, \
         patch("app.discoverer.query_last_traced", return_value={}) as mock_lt, \
         patch("app.discoverer.push_jobs", return_value=0):

        run_discover(neo4j_driver, pg_conn, redis_client, repo="target-repo", trigger="pr_open")

    mock_ep.assert_called_once_with(neo4j_driver, "target-repo")
    mock_lt.assert_called_once_with(pg_conn, "target-repo")
    assert mock_cd.call_args[0][1] == "target-repo"
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd services/entrypoint-discoverer
python -m pytest tests/test_discoverer.py -v
```
Expected: `ImportError`

- [ ] **Step 3: Write minimal implementation**

Create `services/entrypoint-discoverer/app/discoverer.py`:

```python
# app/discoverer.py
from __future__ import annotations

from .neo4j_client import query_entrypoints, query_changed_dep_ids
from .postgres_client import query_last_traced
from .scorer import score_entrypoints
from .queue import push_jobs


def run_discover(neo4j_driver, pg_conn, redis_client, repo: str, trigger: str) -> dict:
    """
    Orchestrate the full entrypoint discovery flow.

    Returns {"discovered": N, "queued": M}.
    """
    entrypoints = query_entrypoints(neo4j_driver, repo)
    last_traced_map = query_last_traced(pg_conn, repo)

    # Build traced_entries for dep-change check (only for entrypoints already traced)
    traced_entries = [
        {"stable_id": sid, "last_traced_at": ts}
        for sid, ts in last_traced_map.items()
    ]
    changed_dep_ids = query_changed_dep_ids(neo4j_driver, repo, traced_entries)

    jobs = score_entrypoints(
        entrypoints=entrypoints,
        last_traced_map=last_traced_map,
        changed_dep_ids=changed_dep_ids,
        repo=repo,
    )

    queued = push_jobs(redis_client, repo, jobs)
    return {"discovered": len(entrypoints), "queued": queued}
```

- [ ] **Step 4: Run test to verify it passes**

```bash
cd services/entrypoint-discoverer
python -m pytest tests/test_discoverer.py -v
```
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add services/entrypoint-discoverer/app/discoverer.py \
        services/entrypoint-discoverer/tests/test_discoverer.py
git commit -m "feat(entrypoint-discoverer): add discoverer orchestration"
```

---

### Task 7: FastAPI App

**Files:**
- Create: `services/entrypoint-discoverer/app/main.py`
- Create: `services/entrypoint-discoverer/tests/test_main.py`

- [ ] **Step 1: Write the failing tests**

Create `services/entrypoint-discoverer/tests/test_main.py`:

```python
# tests/test_main.py
from unittest.mock import MagicMock, patch
from fastapi.testclient import TestClient


def _make_client():
    # Patches only needed during import. Safe for /health, /metrics (don't call _get_* at
    # request time) and for 422 validation errors (FastAPI rejects before endpoint body runs).
    with patch("app.main._get_neo4j_driver", return_value=MagicMock()), \
         patch("app.main._get_pg_conn", return_value=MagicMock()), \
         patch("app.main._get_redis", return_value=MagicMock()):
        from app.main import app
        return TestClient(app)


def test_health_returns_200():
    client = _make_client()
    resp = client.get("/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"
    assert data["service"] == "entrypoint-discoverer"


def test_ready_returns_200_when_redis_ok():
    mock_r = MagicMock()
    mock_r.ping.return_value = True
    with patch("app.main._get_neo4j_driver", return_value=MagicMock()), \
         patch("app.main._get_pg_conn", return_value=MagicMock()), \
         patch("app.main._get_redis", return_value=mock_r):
        from app.main import app
        client = TestClient(app)
        resp = client.get("/ready")
    assert resp.status_code == 200


def test_metrics_returns_prometheus_text():
    client = _make_client()
    resp = client.get("/metrics")
    assert resp.status_code == 200
    assert "entrypoint_discoverer" in resp.text


def test_discover_returns_202():
    with patch("app.main._get_neo4j_driver", return_value=MagicMock()), \
         patch("app.main._get_pg_conn", return_value=MagicMock()), \
         patch("app.main._get_redis", return_value=MagicMock()), \
         patch("app.main.run_discover", return_value={"discovered": 5, "queued": 3}):
        from app.main import app
        client = TestClient(app)
        resp = client.post("/discover", json={"repo": "myrepo", "trigger": "schedule"})
    assert resp.status_code == 202
    data = resp.json()
    assert data["discovered"] == 5
    assert data["queued"] == 3


def test_discover_rejects_invalid_trigger():
    client = _make_client()
    resp = client.post("/discover", json={"repo": "myrepo", "trigger": "invalid"})
    assert resp.status_code == 422
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd services/entrypoint-discoverer
python -m pytest tests/test_main.py -v
```
Expected: `ImportError` or attribute errors

- [ ] **Step 3: Write minimal implementation**

Create `services/entrypoint-discoverer/app/main.py`:

```python
# app/main.py
from __future__ import annotations

import logging
import os

import psycopg2
import redis as redis_lib
from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse, PlainTextResponse
from neo4j import GraphDatabase

from .discoverer import run_discover
from .models import DiscoverRequest, DiscoverResponse

logger = logging.getLogger(__name__)

VERSION = "0.1.0"
SERVICE = "entrypoint-discoverer"

_neo4j_driver = None
_pg_conn = None
_redis_client = None


def _get_neo4j_driver():
    global _neo4j_driver
    if _neo4j_driver is None:
        uri = os.environ.get("NEO4J_URL", "bolt://localhost:7687")
        user = os.environ.get("NEO4J_USER", "neo4j")
        password = os.environ.get("NEO4J_PASSWORD", "localpassword")
        _neo4j_driver = GraphDatabase.driver(uri, auth=(user, password))
    return _neo4j_driver


def _get_pg_conn():
    global _pg_conn
    if _pg_conn is None or _pg_conn.closed:
        dsn = os.environ.get(
            "POSTGRES_DSN",
            "postgres://tersecontext:localpassword@localhost:5432/tersecontext",
        )
        _pg_conn = psycopg2.connect(dsn)
    return _pg_conn


def _get_redis() -> redis_lib.Redis:
    global _redis_client
    if _redis_client is None:
        url = os.environ.get("REDIS_URL", "redis://localhost:6379")
        _redis_client = redis_lib.from_url(url, decode_responses=False)
    return _redis_client


app = FastAPI()


@app.get("/health")
def health():
    return {"status": "ok", "service": SERVICE, "version": VERSION}


@app.get("/ready")
def ready():
    try:
        _get_redis().ping()
        return {"status": "ok"}
    except Exception as exc:
        return JSONResponse(status_code=503, content={"status": "unavailable", "error": str(exc)})


@app.get("/metrics")
def metrics():
    lines = [
        "# HELP entrypoint_discoverer_jobs_queued_total Total EntrypointJobs pushed to Redis",
        "# TYPE entrypoint_discoverer_jobs_queued_total counter",
        "entrypoint_discoverer_jobs_queued_total 0",
    ]
    return PlainTextResponse("\n".join(lines) + "\n")


@app.post("/discover", status_code=202, response_model=DiscoverResponse)
def discover(req: DiscoverRequest):
    try:
        result = run_discover(
            neo4j_driver=_get_neo4j_driver(),
            pg_conn=_get_pg_conn(),
            redis_client=_get_redis(),
            repo=req.repo,
            trigger=req.trigger,
        )
        return DiscoverResponse(**result)
    except Exception as exc:
        logger.error("Discover failed: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))
```

- [ ] **Step 4: Run test to verify it passes**

```bash
cd services/entrypoint-discoverer
python -m pytest tests/test_main.py -v
```
Expected: PASS (5 tests)

- [ ] **Step 5: Run the full test suite**

```bash
cd services/entrypoint-discoverer
python -m pytest tests/ -v
```
Expected: all tests green

- [ ] **Step 6: Commit**

```bash
git add services/entrypoint-discoverer/app/main.py \
        services/entrypoint-discoverer/tests/test_main.py
git commit -m "feat(entrypoint-discoverer): add FastAPI app with all endpoints"
```

---

### Task 8: Requirements and Dockerfile

**Files:**
- Create: `services/entrypoint-discoverer/requirements.txt`
- Create: `services/entrypoint-discoverer/Dockerfile`

- [ ] **Step 1: Create requirements.txt**

```
fastapi>=0.115.0
uvicorn[standard]>=0.30.0
redis>=5.0.0
neo4j>=5.0.0
psycopg2-binary>=2.9.0
pydantic>=2.0.0
pytest>=8.0.0
pytest-asyncio>=0.23.0
httpx>=0.27.0
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

- [ ] **Step 3: Verify Docker build**

```bash
cd services/entrypoint-discoverer
docker build -t entrypoint-discoverer:test .
```
Expected: build succeeds with no errors

- [ ] **Step 4: Commit**

```bash
git add services/entrypoint-discoverer/requirements.txt \
        services/entrypoint-discoverer/Dockerfile
git commit -m "feat(entrypoint-discoverer): add Dockerfile and requirements"
```

---

### Task 9: docker-compose Integration

**Files:**
- Modify: `docker-compose.yml`

- [ ] **Step 1: Add entrypoint-discoverer service to docker-compose.yml**

Add the following block after the `repo-watcher` service entry:

```yaml
  entrypoint-discoverer:
    build: ./services/entrypoint-discoverer
    ports:
      - "8092:8080"
    environment:
      REDIS_URL: redis://redis:6379
      NEO4J_URL: bolt://neo4j:7687
      NEO4J_USER: neo4j
      NEO4J_PASSWORD: localpassword
      POSTGRES_DSN: postgres://tersecontext:localpassword@postgres:5432/tersecontext
    depends_on:
      redis:
        condition: service_healthy
      neo4j:
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

- [ ] **Step 2: Verify docker-compose config parses cleanly**

```bash
docker compose config --quiet
```
Expected: no errors

- [ ] **Step 3: Commit**

```bash
git add docker-compose.yml
git commit -m "feat(entrypoint-discoverer): add to docker-compose"
```

---

## Verification

After all tasks are complete, run the full verification from CLAUDE.md:

```bash
# 1. Start the stack
docker compose up -d neo4j postgres redis entrypoint-discoverer

# 2. Wait for health
sleep 15
curl http://localhost:8092/health
# expect: {"status":"ok","service":"entrypoint-discoverer","version":"0.1.0"}

# 3. Run discover
curl -X POST http://localhost:8092/discover \
  -H 'Content-Type: application/json' \
  -d '{"repo":"test-repo","trigger":"schedule"}'
# expect: 202 {"discovered": N, "queued": M}

# 4. Check Redis queue
redis-cli LLEN entrypoint_queue:test-repo
# expect: >= 0 (may be 0 if Neo4j has no nodes for test-repo yet)

# 5. Run unit tests
cd services/entrypoint-discoverer && python -m pytest tests/ -v
# expect: all pass
```
