from __future__ import annotations
from typing import Literal, Optional
from pydantic import BaseModel, ConfigDict


class CallSequenceItem(BaseModel):
    stable_id: str
    name: str
    qualified_name: str
    hop: int
    frequency_ratio: float
    avg_ms: float


class SideEffect(BaseModel):
    type: Literal["db_read", "db_write", "cache_read", "cache_set", "http_out", "fs_write"]
    detail: str
    hop_depth: int


class EdgeRef(BaseModel):
    source: str
    target: str


class ExecutionPath(BaseModel):
    model_config = ConfigDict(extra="ignore")

    entrypoint_stable_id: str
    commit_sha: str
    repo: str
    call_sequence: list[CallSequenceItem]
    side_effects: list[SideEffect]
    dynamic_only_edges: list[EdgeRef]
    never_observed_static_edges: list[EdgeRef]
    timing_p50_ms: float
    timing_p99_ms: float
    coverage_pct: Optional[float] = None
