# services/repo-watcher/app/models.py
from __future__ import annotations
from typing import Literal, Optional
from pydantic import BaseModel


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


class IndexRequest(BaseModel):
    repo_path: str
    full_rescan: bool = False


class InstallHookRequest(BaseModel):
    repo_path: str


class StatusResponse(BaseModel):
    repo: str
    last_sha: str
    last_indexed_at: str
    pending: bool
