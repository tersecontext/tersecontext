"""
End-to-end test for Go dynamic tracing pipeline.
Requires: go-instrumenter (8098) and go-trace-runner (8099) running.
"""
import os
import pytest
import httpx


@pytest.fixture
def instrumenter():
    return httpx.Client(base_url="http://localhost:8098", timeout=60)


@pytest.fixture
def trace_runner():
    return httpx.Client(base_url="http://localhost:8099", timeout=60)


def test_instrumenter_health(instrumenter):
    resp = instrumenter.get("/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


def test_trace_runner_health(trace_runner):
    resp = trace_runner.get("/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


@pytest.mark.skipif(
    not os.path.exists(os.path.expanduser("~/workspaces/gastown")),
    reason="gastown repo not available at ~/workspaces/gastown",
)
def test_instrument_gastown(instrumenter):
    resp = instrumenter.post("/instrument", json={
        "repo": "gastown",
        "repo_path": "/repos/gastown",
        "commit_sha": "HEAD",
        "entrypoints": ["TestCheckServerReachable_NoServer"],
        "language": "go",
        "boundary_patterns": ["*.Handler*", "db.*"],
        "include_deps": [],
    })
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["session_id"]
    assert data["stats"]["Instrumented"] > 0
