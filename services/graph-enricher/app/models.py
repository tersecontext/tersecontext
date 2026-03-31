from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict


class CallNode(BaseModel):
    model_config = ConfigDict(extra="forbid")

    stable_id: str
    hop: int
    frequency_ratio: float
    avg_ms: float


class SideEffect(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["db_read", "db_write", "cache_read", "cache_set", "http_out", "fs_write"]
    detail: str
    hop_depth: int


class DynamicEdge(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source: str
    target: str


class ExecutionPath(BaseModel):
    model_config = ConfigDict(extra="forbid")

    entrypoint_stable_id: str
    commit_sha: str
    call_sequence: list[CallNode]
    side_effects: list[SideEffect]
    dynamic_only_edges: list[DynamicEdge]
    never_observed_static_edges: list[DynamicEdge]
    timing_p50_ms: float
    timing_p99_ms: float
