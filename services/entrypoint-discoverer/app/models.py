# app/models.py
from __future__ import annotations
from typing import Literal
from pydantic import BaseModel


class EntrypointJob(BaseModel):
    stable_id: str
    name: str
    file_path: str
    priority: int
    repo: str


class DiscoverRequest(BaseModel):
    repo: str
    trigger: Literal["schedule", "pr_open"]


class DiscoverResponse(BaseModel):
    discovered: int
    queued: int
