# services/instrumenter/app/models.py
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel


class InstrumentRequest(BaseModel):
    stable_id: str
    file_path: str
    repo: str


class PatchSpec(BaseModel):
    target: str
    action: Literal["mock_db", "mock_http", "redirect_writes"]


class InstrumentResponse(BaseModel):
    session_id: str
    status: Literal["ready"]
    patches: list[PatchSpec]
    output_key: str
    tempdir: str
    timeout_ms: int
