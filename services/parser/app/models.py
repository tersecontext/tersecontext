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
    qualified_name: str = ""   # populated by extractor
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


class ParsedFileEvent(BaseModel):
    file_path: str
    language: str
    repo: str
    commit_sha: str
    nodes: list[ParsedNode]
    intra_file_edges: list[IntraFileEdge]
    deleted_nodes: list[str]
