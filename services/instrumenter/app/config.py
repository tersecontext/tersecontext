# services/instrumenter/app/config.py
from __future__ import annotations

from .models import PatchSpec

PATCH_CATALOG: list[dict] = [
    {"target": "sqlalchemy.orm.Session.execute",   "action": "mock_db"},
    {"target": "sqlalchemy.engine.Engine.connect", "action": "mock_db"},
    {"target": "psycopg2.connect",                 "action": "mock_db"},
    {"target": "httpx.Client.request",             "action": "mock_http"},
    {"target": "httpx.AsyncClient.request",        "action": "mock_http"},
    {"target": "requests.request",                 "action": "mock_http"},
    {"target": "builtins.open",                    "action": "redirect_writes"},
]

# Validate entire catalog against PatchSpec on import — fails fast if catalog has typos
_validated: list[PatchSpec] = [PatchSpec(**entry) for entry in PATCH_CATALOG]

DEFAULT_CAPTURE_ARGS: list[str] = [
    "test_*",
    "*_handler",
    "*_view",
    "db_*",
]
