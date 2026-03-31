from __future__ import annotations
from typing import Literal, Optional
from pydantic import BaseModel


class TraceEvent(BaseModel):
    type: Literal["call", "return", "exception", "async_call", "async_return"]
    fn: str
    file: str
    line: int
    timestamp_ms: float
    exc_type: Optional[str] = None
    task_id: Optional[int] = None
    args: Optional[str] = None
    return_val: Optional[str] = None


class RawTrace(BaseModel):
    entrypoint_stable_id: str
    commit_sha: str
    repo: Optional[str] = None
    duration_ms: float
    events: list[TraceEvent]
    coverage_pct: Optional[float] = None


class CallNode(BaseModel):
    stable_id: str
    hop: int
    frequency_ratio: float
    avg_ms: float


class SideEffect(BaseModel):
    type: Literal["db_read", "db_write", "cache_read", "cache_set", "http_out", "fs_write"]
    detail: str
    hop_depth: int


class Edge(BaseModel):
    source: str
    target: str


class ExecutionPath(BaseModel):
    entrypoint_stable_id: str
    commit_sha: str
    repo: str = ""
    call_sequence: list[CallNode]
    side_effects: list[SideEffect]
    dynamic_only_edges: list[Edge]
    never_observed_static_edges: list[Edge]
    timing_p50_ms: float
    timing_p99_ms: float
    coverage_pct: Optional[float] = None
