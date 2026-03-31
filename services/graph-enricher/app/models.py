# services/graph-enricher/app/models.py
from __future__ import annotations
from pydantic import BaseModel, ConfigDict


class CallNode(BaseModel):
    stable_id: str
    hop: int
    frequency_ratio: float
    avg_ms: float


class SideEffect(BaseModel):
    type: str
    detail: str
    hop_depth: int


class DynamicEdge(BaseModel):
    source: str
    target: str


class ExecutionPath(BaseModel):
    model_config = ConfigDict(extra="ignore")

    entrypoint_stable_id: str
    commit_sha: str
    call_sequence: list[CallNode]
    side_effects: list[SideEffect]
    dynamic_only_edges: list[DynamicEdge]
    never_observed_static_edges: list[DynamicEdge]
    timing_p50_ms: float
    timing_p99_ms: float
