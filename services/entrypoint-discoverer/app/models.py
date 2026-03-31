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
    language: str = "python"


def detect_language(file_path: str) -> str:
    """Detect language from file extension."""
    if file_path.endswith(".go"):
        return "go"
    return "python"


class DiscoverRequest(BaseModel):
    repo: str
    trigger: Literal["schedule", "pr_open"]


class DiscoverResponse(BaseModel):
    discovered: int
    queued: int
