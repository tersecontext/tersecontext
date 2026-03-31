from __future__ import annotations

import builtins
import os
from dataclasses import dataclass
from typing import Any
from unittest.mock import MagicMock


@dataclass
class IOEvent:
    action: str  # "mock_db", "mock_http", "redirect_writes"
    detail: str


def _make_db_side_effect(events: list[IOEvent]):
    def side_effect(*args, **kwargs):
        sql = str(args[0]) if args else str(kwargs)
        events.append(IOEvent(action="mock_db", detail=sql))
        cursor = MagicMock()
        cursor.fetchall.return_value = []
        cursor.fetchone.return_value = None
        return cursor
    return side_effect


def _make_http_side_effect(events: list[IOEvent]):
    def side_effect(*args, **kwargs):
        method = args[0] if args else kwargs.get("method", "?")
        url = args[1] if len(args) > 1 else kwargs.get("url", "?")
        events.append(IOEvent(action="mock_http", detail=f"{method} {url}"))
        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = {}
        resp.text = ""
        resp.content = b""
        return resp
    return side_effect


def _make_open_side_effect(events: list[IOEvent], tempdir: str):
    _real_open = builtins.open

    def side_effect(path, mode="r", *args, **kwargs):
        if "w" in mode or "a" in mode or "x" in mode:
            basename = os.path.basename(path)
            redirected = os.path.join(tempdir, basename)
            events.append(IOEvent(action="redirect_writes", detail=f"{path} -> {redirected}"))
            return _real_open(redirected, mode, *args, **kwargs)
        return _real_open(path, mode, *args, **kwargs)

    return side_effect


_MOCK_FACTORIES = {
    "mock_db": _make_db_side_effect,
    "mock_http": _make_http_side_effect,
}


def create_mock_patches(events: list[IOEvent], tempdir: str) -> list[dict]:
    """Build a list of patch descriptors with side_effect callables."""
    from .config import PATCH_CATALOG

    result = []
    for entry in PATCH_CATALOG:
        target = entry["target"]
        action = entry["action"]

        if action in _MOCK_FACTORIES:
            se = _MOCK_FACTORIES[action](events)
        elif action == "redirect_writes":
            se = _make_open_side_effect(events, tempdir)
        else:
            continue

        result.append({"target": target, "side_effect": se})

    return result
