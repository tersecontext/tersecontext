from __future__ import annotations
from typing import Any, Optional
from pydantic import BaseModel


class EntrypointJob(BaseModel):
    stable_id: str
    name: str
    file_path: str
    priority: int
    repo: str
    language: str = "python"


class TraceEvent(BaseModel):
    type: str          # "call" | "return" | "exception"
    fn: str
    file: str
    line: int
    timestamp_ms: float
    exc_type: Optional[str] = None
    goroutine_id: Optional[int] = None
    span_id: Optional[int] = None
    args: Optional[dict] = None
    return_val: Optional[Any] = None


class RawTrace(BaseModel):
    entrypoint_stable_id: str
    commit_sha: str
    repo: str
    duration_ms: float
    events: list[TraceEvent]
