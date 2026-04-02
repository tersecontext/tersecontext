# services/repo-watcher/tests/test_watcher.py
import json
import subprocess
from unittest.mock import MagicMock


def _head_sha(repo):
    return subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True, check=True
    ).stdout.strip()


def _make_commit(repo, filename, content, message="update"):
    (repo / filename).write_text(content)
    subprocess.run(["git", "add", filename], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", message], cwd=repo, check=True, capture_output=True)
    return _head_sha(repo)


def _get_emitted_events(mock_redis):
    """Extract FileChangedEvent dicts from stream:file-changed xadd calls on mock_redis."""
    events = []
    for call in mock_redis.xadd.call_args_list:
        if call[0][0] != "stream:file-changed":
            continue
        payload = call[0][1]
        events.append(json.loads(payload["event"]))
    return events


# ── full rescan (prev_sha=None) ───────────────────────────────────────────────

def test_process_commit_full_rescan_emits_one_event_per_file(sample_repo):
    from app.watcher import process_commit
    mock_redis = MagicMock()
    mock_redis.get.return_value = None
    sha = _head_sha(sample_repo)

    events_emitted, files_changed = process_commit(mock_redis, str(sample_repo), None, sha)

    assert events_emitted == 1  # only auth.py in sample_repo
    assert files_changed == 1
    emitted = _get_emitted_events(mock_redis)
    assert emitted[0]["diff_type"] == "full_rescan"
    assert emitted[0]["path"] == "auth.py"
    assert len(emitted[0]["added_nodes"]) > 0
    assert emitted[0]["changed_nodes"] == []
    assert emitted[0]["deleted_nodes"] == []


# ── incremental diff ──────────────────────────────────────────────────────────

def test_process_commit_modified_file_emits_changed_nodes(sample_repo):
    from app.watcher import process_commit
    prev_sha = _head_sha(sample_repo)
    curr_sha = _make_commit(
        sample_repo, "auth.py",
        "import hashlib\n\n"
        "class AuthService:\n"
        "    def authenticate(self, username: str, password: str) -> str:\n"
        "        return 'changed_' + hashlib.sha256(password.encode()).hexdigest()\n"
    )
    mock_redis = MagicMock()

    events_emitted, files_changed = process_commit(mock_redis, str(sample_repo), prev_sha, curr_sha)

    assert events_emitted == 1
    emitted = _get_emitted_events(mock_redis)
    assert emitted[0]["diff_type"] == "modified"
    assert len(emitted[0]["changed_nodes"]) > 0


def test_process_commit_deleted_file_emits_deleted_nodes(sample_repo):
    from app.watcher import process_commit
    prev_sha = _head_sha(sample_repo)
    subprocess.run(["git", "rm", "auth.py"], cwd=sample_repo, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "del"], cwd=sample_repo, check=True, capture_output=True)
    curr_sha = _head_sha(sample_repo)
    mock_redis = MagicMock()

    process_commit(mock_redis, str(sample_repo), prev_sha, curr_sha)

    emitted = _get_emitted_events(mock_redis)
    assert emitted[0]["diff_type"] == "deleted"
    assert len(emitted[0]["deleted_nodes"]) > 0
    assert emitted[0]["changed_nodes"] == []
    assert emitted[0]["added_nodes"] == []


def test_process_commit_added_file_emits_added_nodes(sample_repo):
    from app.watcher import process_commit
    prev_sha = _head_sha(sample_repo)
    curr_sha = _make_commit(sample_repo, "new.py", "def hello(): pass\n", "add")
    mock_redis = MagicMock()

    process_commit(mock_redis, str(sample_repo), prev_sha, curr_sha)

    emitted = _get_emitted_events(mock_redis)
    new_file_event = next(e for e in emitted if e["path"] == "new.py")
    assert new_file_event["diff_type"] == "added"
    assert len(new_file_event["added_nodes"]) > 0
    assert new_file_event["changed_nodes"] == []


def test_process_commit_emits_repo_indexed(sample_repo):
    """After all file-changed events, process_commit emits one stream:repo-indexed entry."""
    from app.watcher import process_commit

    sha = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=sample_repo,
        capture_output=True, text=True, check=True
    ).stdout.strip()

    mock_redis = MagicMock()
    process_commit(mock_redis, str(sample_repo), None, sha)

    # stream:repo-indexed should have exactly one xadd call for this stream
    repo_indexed_calls = [
        c for c in mock_redis.xadd.call_args_list
        if c[0][0] == "stream:repo-indexed"
    ]
    assert len(repo_indexed_calls) == 1
    payload = repo_indexed_calls[0][0][1]
    assert payload["repo"] == sample_repo.name
    assert payload["commit_sha"] == sha
    assert "path" in payload
    assert payload["path"] == str(sample_repo)


def test_process_commit_emits_repo_indexed_incremental(sample_repo):
    """Incremental path also emits stream:repo-indexed after file-changed events."""
    from app.watcher import process_commit

    # Create a second commit so we have a prev→current diff
    (sample_repo / "new_file.py").write_text("x = 1\n")
    subprocess.run(["git", "add", "new_file.py"], cwd=sample_repo, check=True)
    subprocess.run(
        ["git", "commit", "--no-gpg-sign", "-m", "add file"],
        cwd=sample_repo, check=True,
        env={**__import__("os").environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
             "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"},
    )
    prev_sha = subprocess.run(
        ["git", "rev-parse", "HEAD~1"], cwd=sample_repo,
        capture_output=True, text=True, check=True
    ).stdout.strip()
    current_sha = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=sample_repo,
        capture_output=True, text=True, check=True
    ).stdout.strip()

    mock_redis = MagicMock()
    process_commit(mock_redis, str(sample_repo), prev_sha, current_sha)

    repo_indexed_calls = [
        c for c in mock_redis.xadd.call_args_list
        if c[0][0] == "stream:repo-indexed"
    ]
    assert len(repo_indexed_calls) == 1
    payload = repo_indexed_calls[0][0][1]
    assert payload["repo"] == sample_repo.name
    assert payload["commit_sha"] == current_sha
    assert "path" in payload
