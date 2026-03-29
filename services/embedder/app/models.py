from __future__ import annotations
from typing import Optional
from pydantic import BaseModel, ConfigDict


class ParsedNode(BaseModel):
    stable_id: str
    node_hash: str
    type: str
    name: str
    signature: str
    docstring: str = ""
    body: str
    line_start: int
    line_end: int
    parent_id: Optional[str] = None


class IntraFileEdge(BaseModel):
    source_stable_id: str
    target_stable_id: str
    type: str


class ParsedFileEvent(BaseModel):
    model_config = ConfigDict(extra="ignore")

    repo: str
    commit_sha: str
    file_path: str
    language: str
    nodes: list[ParsedNode]
    intra_file_edges: list[IntraFileEdge]


class EmbeddedNode(BaseModel):
    stable_id: str
    vector: list[float]
    embed_text: str
    node_hash: str


class EmbeddedNodesEvent(BaseModel):
    repo: str
    commit_sha: str
    nodes: list[EmbeddedNode]
