from __future__ import annotations

import asyncio
import fnmatch
import re
import sysconfig as _sysconfig
import time
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any, Optional

from pydantic import BaseModel

from .serializer import safe_serialize

# Full absolute path prefixes for stdlib and installed packages.
# Returning None from trace_func prunes CPython's trace for the entire
# subtree below that frame — no events emitted, significant overhead saved.
_EXCLUDE_PREFIXES: tuple[str, ...] = tuple(
    p for p in (
        _sysconfig.get_path("stdlib"),    # e.g. /usr/lib/python3.12
        _sysconfig.get_path("purelib"),   # e.g. /usr/local/lib/python3.12/dist-packages
        _sysconfig.get_path("platlib"),   # platform-specific site-packages
    )
    if p is not None
)


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
        # Prune stdlib/site-packages subtrees immediately.
        # Returning None stops CPython from calling trace_func for any
        # descendant frame — this is the critical subtree-pruning behavior.
        filename = frame.f_code.co_filename
        if filename.startswith(_EXCLUDE_PREFIXES):
            return None

        if event not in ("call", "return", "exception"):
            return trace_func
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


# --- Async hooks ---

_original_create_task = None
_original_gather = None
_active_session: TraceSession | None = None


def install_async_hooks(session: TraceSession) -> None:
    """Monkey-patch asyncio.create_task and asyncio.gather to track async task creation."""
    global _original_create_task, _original_gather, _active_session
    _active_session = session
    _original_create_task = asyncio.create_task
    _original_gather = asyncio.gather

    t0 = time.monotonic()

    def _patched_create_task(coro, *, name=None, context=None):
        parent_task_id = _current_task_id.get()
        child_task_id = session.next_task_id()
        ts = (time.monotonic() - t0) * 1000.0

        session.events.append(TraceEvent(
            type="async_call",
            fn=getattr(coro, "__qualname__", getattr(coro, "__name__", str(coro))),
            file="",
            line=0,
            timestamp_ms=round(ts, 3),
            task_id=child_task_id,
        ))

        async def _wrapper():
            _current_task_id.set(child_task_id)
            try:
                result = await coro
                return result
            except BaseException:
                raise
            finally:
                ts_end = (time.monotonic() - t0) * 1000.0
                session.events.append(TraceEvent(
                    type="async_return",
                    fn=getattr(coro, "__qualname__", getattr(coro, "__name__", str(coro))),
                    file="",
                    line=0,
                    timestamp_ms=round(ts_end, 3),
                    task_id=child_task_id,
                ))

        kwargs = {"name": name}
        if context is not None:
            kwargs["context"] = context
        return _original_create_task(_wrapper(), **kwargs)

    async def _patched_gather(*coros_or_futures, return_exceptions=False):
        wrapped = []
        for coro in coros_or_futures:
            if asyncio.iscoroutine(coro):
                task = _patched_create_task(coro)
                wrapped.append(task)
            else:
                wrapped.append(coro)
        return await _original_gather(*wrapped, return_exceptions=return_exceptions)

    asyncio.create_task = _patched_create_task
    asyncio.gather = _patched_gather


def uninstall_async_hooks() -> None:
    """Restore original asyncio.create_task and asyncio.gather."""
    global _original_create_task, _original_gather, _active_session
    if _original_create_task is not None:
        asyncio.create_task = _original_create_task
        _original_create_task = None
    if _original_gather is not None:
        asyncio.gather = _original_gather
        _original_gather = None
    _active_session = None
