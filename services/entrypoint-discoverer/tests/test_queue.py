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
