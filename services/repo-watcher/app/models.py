# services/repo-watcher/app/models.py
from __future__ import annotations
import os
import re
from pathlib import Path
from typing import Literal, Optional
from pydantic import BaseModel, field_validator


def _allowed_repo_roots() -> list[str]:
    """Return list of allowed base directories for repo paths."""
    raw = os.environ.get("ALLOWED_REPO_ROOTS", "/repos")
    return [p.strip() for p in raw.split(",") if p.strip()]


def _validate_repo_path(v: str) -> str:
    """Validate repo_path is under an allowed root and contains no traversal."""
    if not v:
        raise ValueError("repo_path must not be empty")
    resolved = str(Path(v).resolve())
    roots = _allowed_repo_roots()
    if not any(resolved == root or resolved.startswith(root + "/") for root in roots):
        raise ValueError(f"repo_path must be under one of: {roots}")
    return resolved


class FileChangedEvent(BaseModel):
    repo: str
    commit_sha: str
    path: str
    language: str
    diff_type: Literal["added", "modified", "deleted", "full_rescan"]
    changed_nodes: list[str]
    added_nodes: list[str]
    deleted_nodes: list[str]


class ParsedNode(BaseModel):
    stable_id: str
    node_hash: str
    type: str
    name: str
    qualified_name: str = ""
    signature: str
    docstring: str
    body: str
    line_start: int
    line_end: int
    parent_id: Optional[str] = None


class IntraFileEdge(BaseModel):
    source_stable_id: str
    target_stable_id: str
    type: Literal["CALLS"]


class HookRequest(BaseModel):
    repo_path: str
    commit_sha: str

    @field_validator("repo_path")
    @classmethod
    def check_repo_path(cls, v: str) -> str:
        return _validate_repo_path(v)

    @field_validator("commit_sha")
    @classmethod
    def check_commit_sha(cls, v: str) -> str:
        if not re.fullmatch(r"[0-9a-f]{4,40}", v):
            raise ValueError("commit_sha must be a valid hex SHA")
        return v


class IndexRequest(BaseModel):
    repo_path: str
    full_rescan: bool = False

    @field_validator("repo_path")
    @classmethod
    def check_repo_path(cls, v: str) -> str:
        return _validate_repo_path(v)



class StatusResponse(BaseModel):
    repo: str
    last_sha: str
    last_indexed_at: str
    pending: bool
