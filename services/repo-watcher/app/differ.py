# services/repo-watcher/app/differ.py
from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Optional


LANGUAGE_MAP = {
    ".py": "python",
    ".ts": "typescript",
    ".tsx": "typescript",
}


def detect_language(file_path: str) -> Optional[str]:
    """Return language string for supported extensions, or None."""
    suffix = Path(file_path).suffix
    return LANGUAGE_MAP.get(suffix)


def get_all_files(repo_path: str) -> list[str]:
    """Return all supported-language files tracked by git."""
    result = subprocess.run(
        ["git", "ls-files"],
        cwd=repo_path,
        capture_output=True,
        text=True,
        check=True,
    )
    return [
        f for f in result.stdout.strip().split("\n")
        if f and detect_language(f) is not None
    ]


def get_changed_files(repo_path: str, prev_sha: str, current_sha: str) -> list[dict]:
    """Return list of {path, diff_type} for supported files changed between two SHAs."""
    result = subprocess.run(
        ["git", "diff", "--name-status", prev_sha, current_sha],
        cwd=repo_path,
        capture_output=True,
        text=True,
        check=True,
    )
    changed = []
    for line in result.stdout.strip().split("\n"):
        if not line:
            continue
        status, *paths = line.split("\t")
        path = paths[-1]  # for renames, take the new path
        if detect_language(path) is None:
            continue
        diff_type = {
            "A": "added",
            "M": "modified",
            "D": "deleted",
            "R": "modified",
        }.get(status[0], "modified")
        changed.append({"path": path, "diff_type": diff_type})
    return changed


def git_show(repo_path: str, sha: str, file_path: str) -> bytes:
    """Return file content at a given commit SHA. Returns b'' if file did not exist."""
    result = subprocess.run(
        ["git", "show", f"{sha}:{file_path}"],
        cwd=repo_path,
        capture_output=True,
    )
    if result.returncode != 0:
        return b""
    return result.stdout


def _parse_nodes(content: bytes, file_path: str, repo: str) -> list:
    """
    Parse content and return ParsedNode list.
    Returns [] on parse error or unsupported language.

    NOTE: TypeScript AST diff is deferred to v2. tree-sitter-typescript is not
    wired. TS files still produce FileChanged events but with empty node lists.
    """
    from .parser import parse
    from .extractor import extract
    language = detect_language(file_path)
    if language != "python":
        return []
    try:
        root = parse(content, language)
        nodes, _ = extract(root, content, repo, file_path)
        return nodes
    except (ValueError, SyntaxError):
        return []


def get_changed_nodes(
    repo_path: str,
    file_path: str,
    prev_sha: str,
    current_sha: str,
    repo: str,
) -> tuple[list[str], list[str], list[str]]:
    """
    Return (changed_stable_ids, added_stable_ids, deleted_stable_ids).
    Compares node_hash for each stable_id between prev and current commit.
    """
    prev_content = git_show(repo_path, prev_sha, file_path)
    curr_content = git_show(repo_path, current_sha, file_path)

    prev_nodes = _parse_nodes(prev_content, file_path, repo)
    curr_nodes = _parse_nodes(curr_content, file_path, repo)

    prev_hashes: dict[str, str] = {n.stable_id: n.node_hash for n in prev_nodes}
    curr_hashes: dict[str, str] = {n.stable_id: n.node_hash for n in curr_nodes}

    changed = [sid for sid, h in curr_hashes.items() if sid in prev_hashes and prev_hashes[sid] != h]
    added = [sid for sid in curr_hashes if sid not in prev_hashes]
    deleted = [sid for sid in prev_hashes if sid not in curr_hashes]

    return changed, added, deleted
