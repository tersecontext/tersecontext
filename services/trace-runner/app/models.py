from __future__ import annotations
from typing import Optional
from pydantic import BaseModel


class EntrypointJob(BaseModel):
    stable_id: str
    name: str
    file_path: str
    priority: int
    repo: str


class TraceEvent(BaseModel):
    type: str          # "call" | "return" | "exception"
    fn: str
    file: str
    line: int
    timestamp_ms: float
    exc_type: Optional[str] = None


class RawTrace(BaseModel):
    entrypoint_stable_id: str
    commit_sha: str
    repo: str
    duration_ms: float
    events: list[TraceEvent]
