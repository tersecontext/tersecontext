from __future__ import annotations

import dataclasses
import json
from typing import Any

try:
    from pydantic import BaseModel as _PydanticBase
except ImportError:
    _PydanticBase = None


def safe_serialize(obj: Any, *, max_depth: int = 2, max_bytes: int = 1024) -> str:
    """Serialize *obj* to a JSON string, safely handling cycles, depth, and size."""
    seen: set[int] = set()

    def _walk(o: Any, depth: int) -> Any:
        if isinstance(o, (str, int, float, bool, type(None))):
            return o

        obj_id = id(o)
        if obj_id in seen:
            return "<circular>"
        seen.add(obj_id)

        try:
            if depth >= max_depth:
                return f"<{type(o).__name__}>"

            if _PydanticBase is not None and isinstance(o, _PydanticBase):
                return _walk(o.model_dump(), depth)

            if dataclasses.is_dataclass(o) and not isinstance(o, type):
                return _walk(dataclasses.asdict(o), depth)

            if isinstance(o, dict):
                return {str(k): _walk(v, depth + 1) for k, v in o.items()}

            if isinstance(o, (list, tuple, set, frozenset)):
                return [_walk(item, depth + 1) for item in o]

            return f"<{type(o).__name__.capitalize()}>"
        finally:
            seen.discard(obj_id)

    try:
        converted = _walk(obj, 0)
        raw = json.dumps(converted, default=str)
    except Exception as exc:
        return f'"<unserializable: {exc}>"'

    if len(raw) <= max_bytes:
        return raw

    suffix = '...[truncated]"'
    truncated = raw[: max_bytes - len(suffix)] + suffix
    return truncated
