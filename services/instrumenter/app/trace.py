from __future__ import annotations

import asyncio
import fnmatch
import re
import time
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any, Optional

from pydantic import BaseModel

from .serializer import safe_serialize


class TraceEvent(BaseModel):
    type: str
    fn: str
    file: str
    line: int
    timestamp_ms: float
    exc_type: Optional[str] = None
    task_id: Optional[int] = None
    args: Optional[str] = None
    return_val: Optional[str] = None


_current_task_id: ContextVar[int] = ContextVar("_current_task_id", default=0)


@dataclass
class TraceSession:
    session_id: str
    file_path: str
    repo: str
    stable_id: str
    tempdir: str = ""
    events: list[TraceEvent] = field(default_factory=list)
    capture_args: list[str] = field(default_factory=list)
    coverage_filter: set[str] | None = None
    task_id_counter: int = 0
    _arg_pattern: re.Pattern | None = field(default=None, init=False, repr=False)

    def __post_init__(self) -> None:
        if self.capture_args:
            regex_parts = [fnmatch.translate(p) for p in self.capture_args]
            self._arg_pattern = re.compile("|".join(regex_parts))

    def matches_capture(self, fn_name: str) -> bool:
        if self._arg_pattern is None:
            return False
        return self._arg_pattern.match(fn_name) is not None

    def next_task_id(self) -> int:
        self.task_id_counter += 1
        return self.task_id_counter


def create_trace_func(session: TraceSession):
    """Return a trace function suitable for sys.settrace."""
    t0 = time.monotonic()

    def trace_func(frame, event, arg):
        if event not in ("call", "return", "exception"):
            return trace_func

        filename = frame.f_code.co_filename
        funcname = frame.f_code.co_name

        # Coverage filter: skip files not in the filter set
        if session.coverage_filter is not None and filename not in session.coverage_filter:
            return trace_func

        ts = (time.monotonic() - t0) * 1000.0

        evt = TraceEvent(
            type=event,
            fn=funcname,
            file=filename,
            line=frame.f_lineno,
            timestamp_ms=round(ts, 3),
            task_id=_current_task_id.get(),
        )

        # Selective argument capture
        if session.matches_capture(funcname):
            if event == "call":
                evt.args = safe_serialize(dict(frame.f_locals))
            elif event == "return":
                evt.return_val = safe_serialize(arg)

        # Exception type
        if event == "exception" and arg is not None:
            exc_type = arg[0] if isinstance(arg, tuple) and arg else None
            if exc_type is not None:
                evt.exc_type = getattr(exc_type, "__name__", str(exc_type))

        session.events.append(evt)
        return trace_func

    return trace_func
