from __future__ import annotations

import re

from .models import SideEffect, TraceEvent

# Exact word-boundary patterns for DB to avoid false positives on names like
# "execute_workflow" or "preselect".
_DB_READ_EXACT = re.compile(r"(?:^|[_\.])(?:fetchall|fetchone|fetch|select)(?:[_\.]|$)")
_DB_WRITE_EXACT = re.compile(r"(?:^|[_\.])(?:execute(?:many)?|insert|update|delete|commit)(?:[_\.]|$)")

_CACHE_SET_PATTERNS = {"hset", "rpush", "lpush", "zadd", "setex"}
_CACHE_READ_PATTERNS = {"hget", "lrange", "zrange", "smembers"}
# "set"/"get" only count when clearly in a cache context
_CACHE_SET_GENERIC = re.compile(r"(?:redis|cache)[_\.]set|^set$")
_CACHE_READ_GENERIC = re.compile(r"(?:redis|cache)[_\.]get|^get$")

_HTTP_EXACT = re.compile(r"(?:http|httpx|requests?|client)[_\.](?:request|send|get|post|put|patch|delete)|_send_request")
_FS_WRITE_EXACT = re.compile(r"(?:^|[_\.])write(?:[_\.]|$)|(?:^|[_\.])open(?:[_\.]|$)")


_SQL_READ = re.compile(r"^\s*(SELECT|WITH)\b", re.IGNORECASE)


def classify_io_events(io_events: list[dict]) -> list[SideEffect]:
    """
    Classify intercepted I/O events from mock patches.
    Produces richer side-effect details than function-name matching:
    actual SQL strings, HTTP method+URL, file paths.
    """
    effects: list[SideEffect] = []
    seen: set[tuple] = set()

    for ev in io_events:
        action = ev.get("action", "")
        detail = ev.get("detail", "")

        if action == "mock_db":
            kind = "db_read" if _SQL_READ.match(detail) else "db_write"
        elif action == "mock_http":
            kind = "http_out"
        elif action == "redirect_writes":
            kind = "fs_write"
        else:
            continue

        key = (kind, detail)
        if key not in seen:
            seen.add(key)
            effects.append(SideEffect(type=kind, detail=detail, hop_depth=0))

    return effects


def classify_side_effects(events: list[TraceEvent]) -> list[SideEffect]:
    effects: list[SideEffect] = []
    seen: set[tuple] = set()
    call_depth = 0

    for ev in events:
        if ev.type == "call":
            current_depth = call_depth
            call_depth += 1
        elif ev.type in ("return", "exception"):
            call_depth = max(0, call_depth - 1)
            continue
        else:
            continue

        fn_lower = ev.fn.lower()

        def _add(kind, detail, depth=current_depth):
            key = (kind, detail)
            if key not in seen:
                seen.add(key)
                effects.append(SideEffect(type=kind, detail=detail, hop_depth=depth))

        if _DB_READ_EXACT.search(fn_lower):
            _add("db_read", f"fn:{ev.fn}")
        elif _DB_WRITE_EXACT.search(fn_lower):
            _add("db_write", f"fn:{ev.fn}")
        elif any(p in fn_lower for p in _CACHE_SET_PATTERNS) or _CACHE_SET_GENERIC.search(fn_lower):
            _add("cache_set", f"fn:{ev.fn}")
        elif any(p in fn_lower for p in _CACHE_READ_PATTERNS) or _CACHE_READ_GENERIC.search(fn_lower):
            _add("cache_read", f"fn:{ev.fn}")
        elif _HTTP_EXACT.search(fn_lower):
            _add("http_out", f"fn:{ev.fn}")
        elif _FS_WRITE_EXACT.search(fn_lower):
            _add("fs_write", f"fn:{ev.fn}")

    return effects
