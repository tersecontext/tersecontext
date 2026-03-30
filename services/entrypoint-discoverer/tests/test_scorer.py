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
