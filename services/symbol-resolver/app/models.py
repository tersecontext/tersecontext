from __future__ import annotations
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict


class ParsedNode(BaseModel):
    model_config = ConfigDict(extra="ignore")

    stable_id: str
    type: str
    name: str
    qualified_name: str = ""
    body: str


class ParsedFileEvent(BaseModel):
    model_config = ConfigDict(extra="ignore")

    repo: str
    commit_sha: str
    file_path: str
    language: str
    nodes: list[ParsedNode]


class PendingRef(BaseModel):
    source_stable_id: str   # stable_id of the import node (type="import")
    target_name: str        # imported symbol name (e.g. "AuthService")
    path_hint: str          # slash-separated path for fallback query (e.g. "auth/service")
    edge_type: str = "IMPORTS"
    repo: str
    attempted_at: datetime
    attempt_count: int = 0
