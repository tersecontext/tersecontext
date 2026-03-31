from __future__ import annotations

from .models import SideEffect, TraceEvent

_DB_READ_PATTERNS = {"fetchall", "fetchone", "select", "fetch"}
_DB_WRITE_PATTERNS = {"execute", "executemany", "insert", "update", "delete", "commit"}
_CACHE_SET_PATTERNS = {"set", "hset", "rpush", "lpush", "zadd", "setex"}
_CACHE_READ_PATTERNS = {"get", "hget", "lrange", "zrange", "smembers"}
_HTTP_PATTERNS = {"request", "send", "post", "put", "patch"}
_FS_WRITE_PATTERNS = {"write", "open"}


def _match(fn_lower: str, patterns: set[str]) -> bool:
    return any(p in fn_lower for p in patterns)


def classify_side_effects(events: list[TraceEvent]) -> list[SideEffect]:
    effects: list[SideEffect] = []
    seen: set[tuple] = set()

    for depth, ev in enumerate(events):
        if ev.type != "call":
            continue
        fn_lower = ev.fn.lower()

        def _add(kind, detail):
            key = (kind, detail)
            if key not in seen:
                seen.add(key)
                effects.append(SideEffect(type=kind, detail=detail, hop_depth=depth))

        if _match(fn_lower, _DB_READ_PATTERNS):
            _add("db_read", f"fn:{ev.fn}")
        elif _match(fn_lower, _DB_WRITE_PATTERNS):
            _add("db_write", f"fn:{ev.fn}")
        elif _match(fn_lower, _CACHE_SET_PATTERNS):
            _add("cache_set", f"fn:{ev.fn}")
        elif _match(fn_lower, _CACHE_READ_PATTERNS) and ("redis" in fn_lower or "cache" in fn_lower or "hget" in fn_lower or "lrange" in fn_lower):
            _add("cache_read", f"fn:{ev.fn}")
        elif _match(fn_lower, _HTTP_PATTERNS) and ("http" in fn_lower or "request" in fn_lower or "send" in fn_lower or "client" in fn_lower):
            _add("http_out", f"fn:{ev.fn}")
        elif _match(fn_lower, _FS_WRITE_PATTERNS) and ("write" in fn_lower or "open" in fn_lower):
            _add("fs_write", f"fn:{ev.fn}")

    return effects
