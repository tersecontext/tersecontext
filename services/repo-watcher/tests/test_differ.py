# services/repo-watcher/tests/test_differ.py
import subprocess
import pytest


# ── detect_language ──────────────────────────────────────────────────────────

def test_detect_python():
    from app.differ import detect_language
    assert detect_language("foo.py") == "python"


def test_detect_typescript():
    from app.differ import detect_language
    assert detect_language("bar.ts") == "typescript"


def test_detect_tsx():
    from app.differ import detect_language
    assert detect_language("Comp.tsx") == "typescript"


def test_detect_unknown_returns_none():
    from app.differ import detect_language
    assert detect_language("README.md") is None


# ── get_all_files ─────────────────────────────────────────────────────────────

def test_get_all_files_returns_supported(sample_repo):
    from app.differ import get_all_files
    files = get_all_files(str(sample_repo))
    assert "auth.py" in files


def test_get_all_files_excludes_unsupported(sample_repo):
    (sample_repo / "README.md").write_text("hello")
    subprocess.run(["git", "add", "README.md"], cwd=sample_repo, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "add readme"], cwd=sample_repo, check=True, capture_output=True)
    from app.differ import get_all_files
    files = get_all_files(str(sample_repo))
    assert "README.md" not in files


# ── helpers ───────────────────────────────────────────────────────────────────

def _make_commit(repo, filename, content, message="update"):
    (repo / filename).write_text(content)
    subprocess.run(["git", "add", filename], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", message], cwd=repo, check=True, capture_output=True)
    return subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True, check=True
    ).stdout.strip()


def _head_sha(repo):
    return subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True, check=True
    ).stdout.strip()


# ── get_changed_files ─────────────────────────────────────────────────────────

def test_get_changed_files_modified(sample_repo):
    from app.differ import get_changed_files
    prev_sha = _head_sha(sample_repo)
    curr_sha = _make_commit(sample_repo, "auth.py", "# changed\ndef foo(): pass\n")
    files = get_changed_files(str(sample_repo), prev_sha, curr_sha)
    assert any(f["path"] == "auth.py" and f["diff_type"] == "modified" for f in files)


def test_get_changed_files_added(sample_repo):
    from app.differ import get_changed_files
    prev_sha = _head_sha(sample_repo)
    curr_sha = _make_commit(sample_repo, "new_module.py", "def bar(): pass\n", "add module")
    files = get_changed_files(str(sample_repo), prev_sha, curr_sha)
    assert any(f["path"] == "new_module.py" and f["diff_type"] == "added" for f in files)


def test_get_changed_files_deleted(sample_repo):
    from app.differ import get_changed_files
    prev_sha = _head_sha(sample_repo)
    subprocess.run(["git", "rm", "auth.py"], cwd=sample_repo, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "delete"], cwd=sample_repo, check=True, capture_output=True)
    curr_sha = _head_sha(sample_repo)
    files = get_changed_files(str(sample_repo), prev_sha, curr_sha)
    assert any(f["path"] == "auth.py" and f["diff_type"] == "deleted" for f in files)


def test_get_changed_files_skips_unsupported(sample_repo):
    from app.differ import get_changed_files
    prev_sha = _head_sha(sample_repo)
    curr_sha = _make_commit(sample_repo, "README.md", "# hello\n", "add readme")
    files = get_changed_files(str(sample_repo), prev_sha, curr_sha)
    assert not any(f["path"] == "README.md" for f in files)


# ── git_show ──────────────────────────────────────────────────────────────────

def test_git_show_returns_file_content(sample_repo):
    from app.differ import git_show
    sha = _head_sha(sample_repo)
    content = git_show(str(sample_repo), sha, "auth.py")
    assert b"AuthService" in content


def test_git_show_missing_file_returns_empty(sample_repo):
    from app.differ import git_show
    sha = _head_sha(sample_repo)
    content = git_show(str(sample_repo), sha, "nonexistent.py")
    assert content == b""


# ── _parse_nodes ──────────────────────────────────────────────────────────────

def test_parse_nodes_python_returns_nodes(sample_repo):
    from app.differ import git_show, _parse_nodes
    sha = _head_sha(sample_repo)
    content = git_show(str(sample_repo), sha, "auth.py")
    nodes = _parse_nodes(content, "auth.py", "testrepo")
    assert len(nodes) > 0
    node_types = {n.type for n in nodes}
    assert "class" in node_types or "method" in node_types


def test_parse_nodes_typescript_returns_empty_list():
    # TypeScript AST diff is deferred to v2 (tree-sitter-typescript not wired).
    # TS files still get FileChanged events, but node lists will be empty.
    from app.differ import _parse_nodes
    content = b"export function hello() { return 1; }\n"
    nodes = _parse_nodes(content, "component.ts", "testrepo")
    assert nodes == []


# ── get_changed_nodes ─────────────────────────────────────────────────────────

def test_get_changed_nodes_detects_modified_method(sample_repo):
    from app.differ import get_changed_nodes
    prev_sha = _head_sha(sample_repo)
    # Change body of authenticate method
    curr_sha = _make_commit(
        sample_repo, "auth.py",
        "import hashlib\n\n"
        "class AuthService:\n"
        "    def authenticate(self, username: str, password: str) -> str:\n"
        "        return 'new_' + hashlib.sha256(password.encode()).hexdigest()\n"
    )
    changed, added, deleted = get_changed_nodes(
        str(sample_repo), "auth.py", prev_sha, curr_sha, "testrepo"
    )
    # authenticate body changed — at least one stable_id must appear in changed
    assert len(changed) > 0


def test_get_changed_nodes_comment_only_no_changed_nodes(sample_repo):
    from app.differ import get_changed_nodes
    prev_sha = _head_sha(sample_repo)
    # Insert a module-level comment that does NOT touch any function/class body
    curr_sha = _make_commit(
        sample_repo, "auth.py",
        "import hashlib\n"
        "# a module comment\n\n"
        "class AuthService:\n"
        "    def authenticate(self, username: str, password: str) -> str:\n"
        "        return hashlib.sha256(password.encode()).hexdigest()\n"
    )
    changed, added, deleted = get_changed_nodes(
        str(sample_repo), "auth.py", prev_sha, curr_sha, "testrepo"
    )
    # No node bodies changed — only a comment between import and class was added
    assert changed == []
    assert deleted == []


def test_get_changed_nodes_added_file(sample_repo):
    from app.differ import get_changed_nodes
    prev_sha = _head_sha(sample_repo)
    curr_sha = _make_commit(sample_repo, "new.py", "def hello(): pass\n", "add new")
    changed, added, deleted = get_changed_nodes(
        str(sample_repo), "new.py", prev_sha, curr_sha, "testrepo"
    )
    # File is new: all nodes in curr are "added", none in prev
    assert len(added) > 0
    assert changed == []
    assert deleted == []


def test_get_changed_nodes_deleted_file(sample_repo):
    from app.differ import get_changed_nodes
    prev_sha = _head_sha(sample_repo)
    subprocess.run(["git", "rm", "auth.py"], cwd=sample_repo, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "del"], cwd=sample_repo, check=True, capture_output=True)
    curr_sha = _head_sha(sample_repo)
    changed, added, deleted = get_changed_nodes(
        str(sample_repo), "auth.py", prev_sha, curr_sha, "testrepo"
    )
    # File deleted: all prev nodes appear in deleted
    assert len(deleted) > 0
    assert changed == []
    assert added == []
