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
