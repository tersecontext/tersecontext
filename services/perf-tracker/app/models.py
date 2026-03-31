from __future__ import annotations
from datetime import datetime
from typing import Literal, Optional
from pydantic import BaseModel, ConfigDict


class TraceEvent(BaseModel):
    type: Literal["call", "return", "exception"]
    fn: str
    file: str
    line: int
    timestamp_ms: float
    exc_type: Optional[str] = None


class RawTrace(BaseModel):
    model_config = ConfigDict(extra="ignore")

    entrypoint_stable_id: str
    commit_sha: str
    repo: str
    duration_ms: float
    events: list[TraceEvent]


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


class PerfMetric(BaseModel):
    metric_type: Literal["pipeline", "user_code"]
    metric_name: str
    entity_id: str
    repo: str
    value: float
    unit: str
    commit_sha: Optional[str] = None


class Bottleneck(BaseModel):
    entity_id: str
    entity_name: str
    category: Literal["pipeline", "slow_fn", "regression", "hot_path", "deep_chain"]
    severity: Literal["critical", "warning", "info"]
    value: float
    unit: str
    detail: str
    repo: str
    detected_at: datetime
