from __future__ import annotations
from pydantic import BaseModel, ConfigDict


class EmbeddedNode(BaseModel):
    stable_id: str
    vector: list[float]
    name: str
    type: str
    file_path: str
    language: str
    node_hash: str


class EmbeddedNodesEvent(BaseModel):
    repo: str
    commit_sha: str | None = None
    nodes: list[EmbeddedNode]


class FileChangedEvent(BaseModel):
    model_config = ConfigDict(extra="ignore")

    repo: str
    deleted_nodes: list[str] = []
