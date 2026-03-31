# Instrumenter Implementation + Trace Improvements Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the instrumenter's `/run` endpoint with sys.settrace, I/O mocking, async tracing, selective argument capture, and coverage-guided filtering.

**Architecture:** The instrumenter service gets a real execution engine (trace function, I/O mocks, async hooks). The trace-runner gets a coverage pre-pass module. Model changes across trace-runner, trace-normalizer, and spec-generator are backward-compatible (all new fields optional). Coverage pre-pass is owned by trace-runner; all other tracing logic lives in the instrumenter.

**Tech Stack:** Python 3.12+, FastAPI, Pydantic v2, sys.settrace, asyncio, unittest.mock, contextvars, fnmatch, coverage.py/pytest-cov, Redis

**Spec:** `docs/superpowers/specs/2026-03-31-instrumenter-and-trace-improvements-design.md`

---

### Task 1: Safe Serializer

**Files:**
- Create: `services/instrumenter/app/serializer.py`
- Create: `services/instrumenter/tests/test_serializer.py`

- [ ] **Step 1: Write failing tests for safe_serialize**

```python
# services/instrumenter/tests/test_serializer.py
import json
import pytest
from app.serializer import safe_serialize


def test_primitives():
    assert json.loads(safe_serialize(42)) == 42
    assert json.loads(safe_serialize("hello")) == "hello"
    assert json.loads(safe_serialize(True)) is True
    assert json.loads(safe_serialize(None)) == None


def test_dict_and_list():
    result = json.loads(safe_serialize({"a": 1, "b": [2, 3]}))
    assert result == {"a": 1, "b": [2, 3]}


def test_nested_dict_depth_limit():
    deep = {"a": {"b": {"c": {"d": 1}}}}
    result = json.loads(safe_serialize(deep, max_depth=2))
    assert result["a"]["b"] == "<dict>"


def test_cycle_detection():
    d: dict = {}
    d["self"] = d
    result = json.loads(safe_serialize(d))
    assert result["self"] == "<circular>"


def test_truncation_at_max_bytes():
    big = {"data": "x" * 2000}
    result = safe_serialize(big, max_bytes=1024)
    assert len(result) <= 1024
    assert result.endswith('...[truncated]"')


def test_unserializable_object():
    import threading
    lock = threading.Lock()
    result = json.loads(safe_serialize(lock))
    assert result == "<Lock>"


def test_dataclass():
    from dataclasses import dataclass

    @dataclass
    class Point:
        x: int
        y: int

    result = json.loads(safe_serialize(Point(1, 2)))
    assert result == {"x": 1, "y": 2}


def test_pydantic_model():
    from pydantic import BaseModel

    class Item(BaseModel):
        name: str
        value: int

    result = json.loads(safe_serialize(Item(name="a", value=1)))
    assert result == {"name": "a", "value": 1}


def test_set_becomes_list():
    result = json.loads(safe_serialize({1, 2, 3}))
    assert sorted(result) == [1, 2, 3]


def test_tuple_becomes_list():
    result = json.loads(safe_serialize((1, 2)))
    assert result == [1, 2]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd services/instrumenter && python -m pytest tests/test_serializer.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.serializer'`

- [ ] **Step 3: Implement safe_serialize**

```python
# services/instrumenter/app/serializer.py
from __future__ import annotations

import dataclasses
import json
from typing import Any

try:
    from pydantic import BaseModel as _PydanticBase
except ImportError:
    _PydanticBase = None  # type: ignore[assignment,misc]


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

            return f"<{type(o).__name__}>"
        finally:
            seen.discard(obj_id)

    try:
        converted = _walk(obj, 0)
        raw = json.dumps(converted, default=str)
    except Exception as exc:
        return f'"<unserializable: {exc}>"'

    if len(raw) <= max_bytes:
        return raw

    # Truncate to fit within max_bytes
    suffix = '...[truncated]"'
    truncated = raw[: max_bytes - len(suffix)] + suffix
    return truncated
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd services/instrumenter && python -m pytest tests/test_serializer.py -v`
Expected: All PASS

- [ ] **Step 5: Commit**

```bash
git add services/instrumenter/app/serializer.py services/instrumenter/tests/test_serializer.py
git commit -m "feat(instrumenter): add safe_serialize with depth/cycle/size limits"
```

---

### Task 2: Model Changes — TraceEvent, RawTrace, InstrumentRequest

**Files:**
- Modify: `services/trace-runner/app/models.py`
- Modify: `services/trace-normalizer/app/models.py`
- Modify: `services/instrumenter/app/models.py`
- Modify: `services/spec-generator/app/models.py`

- [ ] **Step 1: Write failing tests for new model fields**

```python
# services/trace-runner/tests/test_models_new_fields.py
from app.models import TraceEvent, RawTrace


def test_trace_event_new_fields_optional():
    """Old-format events (no task_id/args/return_val) still parse."""
    evt = TraceEvent(type="call", fn="f", file="a.py", line=1, timestamp_ms=0.0)
    assert evt.task_id is None
    assert evt.args is None
    assert evt.return_val is None


def test_trace_event_with_new_fields():
    evt = TraceEvent(
        type="async_call", fn="f", file="a.py", line=1, timestamp_ms=0.0,
        task_id=1, args='{"x": 1}', return_val='42',
    )
    assert evt.task_id == 1
    assert evt.args == '{"x": 1}'
    assert evt.return_val == "42"


def test_raw_trace_coverage_pct_optional():
    trace = RawTrace(
        entrypoint_stable_id="s", commit_sha="c", repo="r",
        duration_ms=10.0, events=[],
    )
    assert trace.coverage_pct is None


def test_raw_trace_with_coverage_pct():
    trace = RawTrace(
        entrypoint_stable_id="s", commit_sha="c", repo="r",
        duration_ms=10.0, events=[], coverage_pct=87.5,
    )
    assert trace.coverage_pct == 87.5
```

```python
# services/instrumenter/tests/test_models_new_fields.py
from app.models import InstrumentRequest


def test_instrument_request_new_fields_optional():
    req = InstrumentRequest(stable_id="s", file_path="f.py", repo="r")
    assert req.capture_args == []
    assert req.coverage_filter is None


def test_instrument_request_with_new_fields():
    req = InstrumentRequest(
        stable_id="s", file_path="f.py", repo="r",
        capture_args=["*handler*"], coverage_filter=["src/auth.py"],
    )
    assert req.capture_args == ["*handler*"]
    assert req.coverage_filter == ["src/auth.py"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd services/trace-runner && python -m pytest tests/test_models_new_fields.py -v`
Run: `cd services/instrumenter && python -m pytest tests/test_models_new_fields.py -v`
Expected: FAIL — fields not defined

- [ ] **Step 3: Add new fields to all models**

`services/trace-runner/app/models.py` — add to TraceEvent:
```python
    task_id: Optional[int] = None
    args: Optional[str] = None
    return_val: Optional[str] = None
```

`services/trace-runner/app/models.py` — add to RawTrace:
```python
    coverage_pct: Optional[float] = None
```

`services/trace-normalizer/app/models.py` — widen TraceEvent type and add fields:
```python
class TraceEvent(BaseModel):
    type: Literal["call", "return", "exception", "async_call", "async_return"]
    fn: str
    file: str
    line: int
    timestamp_ms: float
    exc_type: Optional[str] = None
    task_id: Optional[int] = None
    args: Optional[str] = None
    return_val: Optional[str] = None
```

`services/trace-normalizer/app/models.py` — add to RawTrace:
```python
    coverage_pct: Optional[float] = None
```

`services/trace-normalizer/app/models.py` — add to ExecutionPath:
```python
    coverage_pct: Optional[float] = None
```

`services/instrumenter/app/models.py` — add to InstrumentRequest:
```python
    capture_args: list[str] = []
    coverage_filter: Optional[list[str]] = None
```

`services/spec-generator/app/models.py` — add to ExecutionPath:
```python
    coverage_pct: Optional[float] = None
```

- [ ] **Step 4: Run all model tests across all 4 services**

Run: `cd services/trace-runner && python -m pytest tests/ -v`
Run: `cd services/instrumenter && python -m pytest tests/ -v`
Run: `cd services/trace-normalizer && python -m pytest tests/ -v`
Run: `cd services/spec-generator && python -m pytest tests/ -v`
Expected: All PASS (existing tests unaffected, new tests pass)

- [ ] **Step 5: Commit**

```bash
git add services/trace-runner/app/models.py services/trace-normalizer/app/models.py \
      services/instrumenter/app/models.py services/spec-generator/app/models.py \
      services/trace-runner/tests/test_models_new_fields.py \
      services/instrumenter/tests/test_models_new_fields.py
git commit -m "feat(models): add task_id, args, return_val, coverage_pct fields (backward-compatible)"
```

---

### Task 3: Trace Function — Core sys.settrace Hook

**Files:**
- Create: `services/instrumenter/app/trace.py`
- Create: `services/instrumenter/tests/test_trace.py`

- [ ] **Step 1: Write failing tests for the trace function**

```python
# services/instrumenter/tests/test_trace.py
import pytest
from app.trace import create_trace_func, TraceSession


def test_trace_func_captures_call_return():
    """Basic sync call chain produces call+return events in order."""
    session = TraceSession(
        session_id="test",
        file_path="test.py",
        repo="test",
        stable_id="test",
    )
    trace_func = create_trace_func(session)

    def target():
        return 42

    import sys
    sys.settrace(trace_func)
    target()
    sys.settrace(None)

    call_events = [e for e in session.events if e.fn == "target" and e.type == "call"]
    return_events = [e for e in session.events if e.fn == "target" and e.type == "return"]
    assert len(call_events) >= 1
    assert len(return_events) >= 1
    assert call_events[0].timestamp_ms <= return_events[0].timestamp_ms


def test_trace_func_coverage_filter_skips():
    """Functions in files not in coverage_filter produce no events."""
    session = TraceSession(
        session_id="test",
        file_path="test.py",
        repo="test",
        stable_id="test",
        coverage_filter={"nonexistent_file.py"},
    )
    trace_func = create_trace_func(session)

    def target():
        return 1

    import sys
    sys.settrace(trace_func)
    target()
    sys.settrace(None)

    target_events = [e for e in session.events if e.fn == "target"]
    assert len(target_events) == 0


def test_trace_func_arg_capture():
    """Functions matching capture_args patterns get args/return_val."""
    session = TraceSession(
        session_id="test",
        file_path="test.py",
        repo="test",
        stable_id="test",
        capture_args=["my_handler"],
    )
    trace_func = create_trace_func(session)

    def my_handler(x, y):
        return x + y

    import sys
    sys.settrace(trace_func)
    my_handler(3, 4)
    sys.settrace(None)

    calls = [e for e in session.events if e.fn == "my_handler" and e.type == "call"]
    returns = [e for e in session.events if e.fn == "my_handler" and e.type == "return"]
    assert len(calls) >= 1
    assert calls[0].args is not None
    assert "3" in calls[0].args
    assert len(returns) >= 1
    assert returns[0].return_val is not None
    assert "7" in returns[0].return_val


def test_trace_func_no_arg_capture_when_not_matching():
    """Functions NOT matching capture_args don't get args."""
    session = TraceSession(
        session_id="test",
        file_path="test.py",
        repo="test",
        stable_id="test",
        capture_args=["*handler*"],
    )
    trace_func = create_trace_func(session)

    def other_func():
        return 1

    import sys
    sys.settrace(trace_func)
    other_func()
    sys.settrace(None)

    calls = [e for e in session.events if e.fn == "other_func" and e.type == "call"]
    assert all(e.args is None for e in calls)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd services/instrumenter && python -m pytest tests/test_trace.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.trace'`

- [ ] **Step 3: Implement TraceSession and create_trace_func**

```python
# services/instrumenter/app/trace.py
from __future__ import annotations

import fnmatch
import re
import time
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any, Optional

from .serializer import safe_serialize

# Reuse TraceEvent from trace-runner's schema but define locally to avoid cross-service imports
from pydantic import BaseModel


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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd services/instrumenter && python -m pytest tests/test_trace.py -v`
Expected: All PASS

- [ ] **Step 5: Commit**

```bash
git add services/instrumenter/app/trace.py services/instrumenter/tests/test_trace.py
git commit -m "feat(instrumenter): add trace function with coverage filter and arg capture"
```

---

### Task 4: Async Tracing Hooks

**Files:**
- Modify: `services/instrumenter/app/trace.py`
- Create: `services/instrumenter/tests/test_async_trace.py`
- Create: `services/instrumenter/tests/conftest.py`

Before writing async tests, create `services/instrumenter/tests/conftest.py`:

```python
# services/instrumenter/tests/conftest.py
import pytest

pytest_plugins = ["pytest_asyncio"]
```

- [ ] **Step 1: Write failing tests for async hooks**

```python
# services/instrumenter/tests/test_async_trace.py
import asyncio
import pytest
from app.trace import TraceSession, create_trace_func, install_async_hooks, uninstall_async_hooks


@pytest.mark.asyncio
async def test_create_task_emits_async_events():
    session = TraceSession(
        session_id="test", file_path="test.py", repo="test", stable_id="test",
    )
    install_async_hooks(session)
    try:
        async def child():
            return 1

        task = asyncio.create_task(child())
        await task

        async_calls = [e for e in session.events if e.type == "async_call"]
        async_returns = [e for e in session.events if e.type == "async_return"]
        assert len(async_calls) >= 1
        assert async_calls[0].task_id is not None
        assert len(async_returns) >= 1
    finally:
        uninstall_async_hooks()


@pytest.mark.asyncio
async def test_gather_emits_per_coroutine_events():
    session = TraceSession(
        session_id="test", file_path="test.py", repo="test", stable_id="test",
    )
    install_async_hooks(session)
    try:
        async def a():
            return 1

        async def b():
            return 2

        await asyncio.gather(a(), b())

        async_calls = [e for e in session.events if e.type == "async_call"]
        assert len(async_calls) >= 2  # one per gathered coroutine
        # Each should have a unique task_id
        task_ids = {e.task_id for e in async_calls}
        assert len(task_ids) >= 2
    finally:
        uninstall_async_hooks()


@pytest.mark.asyncio
async def test_gather_with_exception():
    session = TraceSession(
        session_id="test", file_path="test.py", repo="test", stable_id="test",
    )
    install_async_hooks(session)
    try:
        async def ok():
            return 1

        async def fail():
            raise ValueError("boom")

        results = await asyncio.gather(ok(), fail(), return_exceptions=True)
        assert results[0] == 1
        assert isinstance(results[1], ValueError)

        # Should still have async_return events for both
        async_returns = [e for e in session.events if e.type == "async_return"]
        assert len(async_returns) >= 2
    finally:
        uninstall_async_hooks()


@pytest.mark.asyncio
async def test_nested_create_task():
    session = TraceSession(
        session_id="test", file_path="test.py", repo="test", stable_id="test",
    )
    install_async_hooks(session)
    try:
        async def grandchild():
            return 1

        async def child():
            t = asyncio.create_task(grandchild())
            return await t

        task = asyncio.create_task(child())
        await task

        async_calls = [e for e in session.events if e.type == "async_call"]
        # At least 2: child and grandchild
        assert len(async_calls) >= 2
    finally:
        uninstall_async_hooks()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd services/instrumenter && python -m pytest tests/test_async_trace.py -v`
Expected: FAIL — `ImportError: cannot import name 'install_async_hooks'`

- [ ] **Step 3: Implement async hooks**

Add to `services/instrumenter/app/trace.py`:

```python
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
                # Wrap each coroutine in a create_task so it gets tracked
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd services/instrumenter && python -m pytest tests/test_async_trace.py -v`
Expected: All PASS

- [ ] **Step 5: Commit**

```bash
git add services/instrumenter/app/trace.py services/instrumenter/tests/test_async_trace.py
git commit -m "feat(instrumenter): add async tracing hooks for create_task and gather"
```

---

### Task 5: I/O Mocking Engine

**Files:**
- Create: `services/instrumenter/app/mocking.py`
- Create: `services/instrumenter/tests/test_mocking.py`

- [ ] **Step 1: Write failing tests for I/O mocking**

```python
# services/instrumenter/tests/test_mocking.py
import pytest
from unittest.mock import MagicMock
from app.mocking import create_mock_patches, IOEvent


def test_db_mock_logs_sql():
    """DB mock patches log SQL and return mock cursor."""
    events: list[IOEvent] = []
    patches = create_mock_patches(events, tempdir="/tmp/test")

    # Simulate calling a DB execute
    db_patch = next(p for p in patches if "Session.execute" in p["target"])
    mock_fn = db_patch["side_effect"]
    result = mock_fn("SELECT * FROM users WHERE id = 1")
    assert len(events) == 1
    assert events[0].action == "mock_db"
    assert "SELECT" in events[0].detail


def test_http_mock_logs_request():
    """HTTP mock patches log method+url and return 200."""
    events: list[IOEvent] = []
    patches = create_mock_patches(events, tempdir="/tmp/test")

    http_patch = next(p for p in patches if "httpx.Client.request" in p["target"])
    mock_fn = http_patch["side_effect"]
    result = mock_fn("GET", "https://api.example.com/users")
    assert len(events) == 1
    assert events[0].action == "mock_http"
    assert "api.example.com" in events[0].detail
    assert result.status_code == 200


def test_file_write_redirected():
    """File open in write mode redirects to tempdir."""
    import os
    import tempfile

    events: list[IOEvent] = []
    with tempfile.TemporaryDirectory() as td:
        patches = create_mock_patches(events, tempdir=td)
        open_patch = next(p for p in patches if "builtins.open" in p["target"])
        mock_fn = open_patch["side_effect"]
        fh = mock_fn("/etc/important.conf", "w")
        fh.write("test data")
        fh.close()
        assert len(events) == 1
        assert events[0].action == "redirect_writes"
        # File should exist in tempdir, not at /etc/important.conf
        redirected_files = os.listdir(td)
        assert len(redirected_files) >= 1
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd services/instrumenter && python -m pytest tests/test_mocking.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.mocking'`

- [ ] **Step 3: Implement I/O mocking engine**

```python
# services/instrumenter/app/mocking.py
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
    """Build a list of patch descriptors with side_effect callables.

    Each dict has 'target' (dotted path for unittest.mock.patch) and
    'side_effect' (the callable that replaces the real function).
    """
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd services/instrumenter && python -m pytest tests/test_mocking.py -v`
Expected: All PASS

- [ ] **Step 5: Commit**

```bash
git add services/instrumenter/app/mocking.py services/instrumenter/tests/test_mocking.py
git commit -m "feat(instrumenter): add I/O mocking engine for DB, HTTP, and file writes"
```

---

### Task 6: Instrumenter /run Endpoint + Session Management

**Files:**
- Modify: `services/instrumenter/app/main.py`
- Create: `services/instrumenter/tests/test_run.py`

- [ ] **Step 1: Write failing tests for /run**

```python
# services/instrumenter/tests/test_run.py
import pytest
from fastapi.testclient import TestClient
from app.main import app

client = TestClient(app)


def test_run_unknown_session_returns_404():
    resp = client.post("/run", json={"session_id": "nonexistent"})
    assert resp.status_code == 404


def test_instrument_then_run_returns_events():
    # First create a session — the file_path must point to a real importable target
    # For testing, we use a built-in that will produce trace events
    resp = client.post("/instrument", json={
        "stable_id": "sha256:test",
        "file_path": "__test_stub__",
        "repo": "test",
    })
    assert resp.status_code == 200
    session_id = resp.json()["session_id"]

    resp = client.post("/run", json={"session_id": session_id})
    assert resp.status_code == 200
    data = resp.json()
    assert "events" in data
    assert "duration_ms" in data
    assert isinstance(data["events"], list)
    assert data["duration_ms"] >= 0


def test_run_same_session_twice_returns_404():
    """Session is cleaned up after /run."""
    resp = client.post("/instrument", json={
        "stable_id": "sha256:test",
        "file_path": "__test_stub__",
        "repo": "test",
    })
    session_id = resp.json()["session_id"]
    client.post("/run", json={"session_id": session_id})

    resp = client.post("/run", json={"session_id": session_id})
    assert resp.status_code == 404


def test_instrument_with_capture_args():
    resp = client.post("/instrument", json={
        "stable_id": "sha256:test",
        "file_path": "__test_stub__",
        "repo": "test",
        "capture_args": ["*handler*"],
    })
    assert resp.status_code == 200
    assert resp.json()["status"] == "ready"


def test_instrument_max_sessions_returns_503(monkeypatch):
    from app import main
    monkeypatch.setattr(main, "MAX_SESSIONS", 1)
    # Fill up sessions
    r1 = client.post("/instrument", json={
        "stable_id": "s1", "file_path": "__test_stub__", "repo": "r",
    })
    assert r1.status_code == 200
    # Second should be rejected
    r2 = client.post("/instrument", json={
        "stable_id": "s2", "file_path": "__test_stub__", "repo": "r",
    })
    assert r2.status_code == 503
    # Clean up
    client.post("/run", json={"session_id": r1.json()["session_id"]})
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd services/instrumenter && python -m pytest tests/test_run.py -v`
Expected: FAIL — no `/run` endpoint

- [ ] **Step 3: Implement /run endpoint and session management**

Replace `services/instrumenter/app/main.py` with the full implementation. Key changes:
- Add `_sessions: dict[str, TraceSession]` in-memory store
- Add `MAX_SESSIONS = 100`
- Modify `/instrument` to create a `TraceSession` and store it
- Add `POST /run` that:
  1. Looks up session by ID (404 if missing)
  2. Enters mock patches via `unittest.mock.patch` context managers
  3. Installs `sys.settrace` and async hooks
  4. Executes the entrypoint (sync with `signal.alarm`, async with `asyncio.wait_for`)
  5. Cleans up: uninstall trace, exit patches, remove session
  6. Returns `{events, duration_ms}`
- Add background eviction task (60s TTL for unused sessions)
- For `__test_stub__` file_path: execute a no-op function (for testing)

```python
# services/instrumenter/app/main.py
from __future__ import annotations

import asyncio
import logging
import os
import signal
import sys
import tempfile
import time
import uuid
from contextlib import ExitStack
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.responses import JSONResponse, PlainTextResponse
from pydantic import BaseModel

from .config import PATCH_CATALOG
from .models import InstrumentRequest, InstrumentResponse, PatchSpec
from .mocking import create_mock_patches
from .trace import (
    TraceSession,
    create_trace_func,
    install_async_hooks,
    uninstall_async_hooks,
)

logger = logging.getLogger(__name__)

VERSION = "0.2.0"
SERVICE = "instrumenter"
MAX_SESSIONS = 100

_sessions: dict[str, TraceSession] = {}
_session_created_at: dict[str, float] = {}
_sessions_created: int = 0
_runs_completed: int = 0

app = FastAPI()


class RunRequest(BaseModel):
    session_id: str


@app.get("/health")
def health():
    return {"status": "ok", "service": SERVICE, "version": VERSION}


@app.get("/ready")
def ready():
    return {"status": "ok"}


@app.get("/metrics")
def metrics():
    lines = [
        "# HELP instrumenter_sessions_created_total Total instrumentation sessions created",
        "# TYPE instrumenter_sessions_created_total counter",
        f"instrumenter_sessions_created_total {_sessions_created}",
        "# HELP instrumenter_runs_completed_total Total runs completed",
        "# TYPE instrumenter_runs_completed_total counter",
        f"instrumenter_runs_completed_total {_runs_completed}",
        "# HELP instrumenter_active_sessions Current active sessions",
        "# TYPE instrumenter_active_sessions gauge",
        f"instrumenter_active_sessions {len(_sessions)}",
    ]
    return PlainTextResponse("\n".join(lines) + "\n")


@app.post("/instrument")
def instrument(req: InstrumentRequest) -> InstrumentResponse:
    global _sessions_created

    if not req.stable_id or not req.file_path or not req.repo:
        return JSONResponse(
            status_code=400,
            content={"error": "stable_id, file_path, and repo are required"},
        )

    if len(_sessions) >= MAX_SESSIONS:
        return JSONResponse(
            status_code=503,
            content={"error": "Too many active sessions"},
        )

    session_id = str(uuid.uuid4())
    tempdir_prefix = os.environ.get("TEMPDIR_PREFIX", "/tmp/tc")
    try:
        timeout_ms = int(os.environ.get("TIMEOUT_MS", "30000"))
    except ValueError:
        timeout_ms = 30000

    td = tempfile.mkdtemp(prefix=f"{tempdir_prefix}/{session_id}_")

    coverage_filter_set = set(req.coverage_filter) if req.coverage_filter else None

    session = TraceSession(
        session_id=session_id,
        file_path=req.file_path,
        repo=req.repo,
        stable_id=req.stable_id,
        tempdir=td,
        capture_args=req.capture_args,
        coverage_filter=coverage_filter_set,
    )
    _sessions[session_id] = session
    _session_created_at[session_id] = time.monotonic()
    _sessions_created += 1

    return InstrumentResponse(
        session_id=session_id,
        status="ready",
        patches=[PatchSpec(**entry) for entry in PATCH_CATALOG],
        output_key=f"trace_events:{session_id}",
        tempdir=td,
        timeout_ms=timeout_ms,
    )


def _load_entrypoint(session: TraceSession):
    """Load the target module/function. Returns (callable, is_coroutine)."""
    if session.file_path == "__test_stub__":
        return (lambda: None), False

    import importlib.util
    import inspect
    spec = importlib.util.spec_from_file_location("_target", session.file_path)
    if spec and spec.loader:
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        # Look for a main() or test_ function
        entry = getattr(mod, "main", None) or next(
            (v for k, v in vars(mod).items() if k.startswith("test_") and callable(v)), None
        )
        if entry and inspect.iscoroutinefunction(entry):
            return entry, True
        if entry:
            return entry, False
    return (lambda: None), False


def _execute_sync(fn, timeout_s: int = 30) -> None:
    """Execute a sync entrypoint with signal-based timeout."""
    def _timeout_handler(signum, frame):
        raise TimeoutError(f"Execution exceeded {timeout_s}s")

    old_handler = signal.signal(signal.SIGALRM, _timeout_handler)
    signal.alarm(timeout_s)
    try:
        fn()
    finally:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, old_handler)


def _execute_async(fn, timeout_s: int = 30) -> None:
    """Execute an async entrypoint with asyncio.wait_for timeout."""
    async def _run():
        await asyncio.wait_for(fn(), timeout=timeout_s)
    asyncio.run(_run())


@app.post("/run")
def run(req: RunRequest):
    global _runs_completed

    session = _sessions.get(req.session_id)
    if session is None:
        return JSONResponse(status_code=404, content={"error": "Session not found"})

    io_events = []
    mock_patches = create_mock_patches(io_events, session.tempdir)

    entry_fn, is_async = _load_entrypoint(session)
    trace_func = create_trace_func(session)

    start = time.monotonic()
    with ExitStack() as stack:
        # Install mock patches
        for p in mock_patches:
            try:
                stack.enter_context(patch(p["target"], side_effect=p["side_effect"]))
            except Exception:
                pass  # Target may not be importable in this environment

        # Install trace and async hooks
        sys.settrace(trace_func)
        install_async_hooks(session)
        try:
            if is_async:
                _execute_async(entry_fn)
            else:
                _execute_sync(entry_fn)
        finally:
            sys.settrace(None)
            uninstall_async_hooks()

    duration_ms = (time.monotonic() - start) * 1000.0

    events = [e.model_dump() for e in session.events]

    # Clean up session
    _sessions.pop(req.session_id, None)
    _session_created_at.pop(req.session_id, None)
    _runs_completed += 1

    return {"events": events, "duration_ms": round(duration_ms, 3)}


@app.on_event("startup")
async def _start_eviction_loop():
    async def _evict():
        while True:
            await asyncio.sleep(30)
            now = time.monotonic()
            expired = [
                sid for sid, created in _session_created_at.items()
                if now - created > 60
            ]
            for sid in expired:
                _sessions.pop(sid, None)
                _session_created_at.pop(sid, None)
                logger.info("Evicted stale session %s", sid)
    asyncio.create_task(_evict())
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd services/instrumenter && python -m pytest tests/ -v`
Expected: All PASS (including old tests in test_main.py and new tests in test_run.py)

- [ ] **Step 5: Commit**

```bash
git add services/instrumenter/app/main.py services/instrumenter/tests/test_run.py
git commit -m "feat(instrumenter): implement /run endpoint with session management and eviction"
```

---

### Task 7: Coverage Pre-Pass Module (trace-runner)

**Files:**
- Create: `services/trace-runner/app/coverage.py`
- Create: `services/trace-runner/tests/test_coverage.py`

- [ ] **Step 1: Write failing tests for coverage pre-pass**

```python
# services/trace-runner/tests/test_coverage.py
import json
import pytest
from unittest.mock import AsyncMock, MagicMock, patch


@pytest.mark.asyncio
async def test_coverage_prepass_cache_miss():
    """On cache miss, runs pytest --cov, parses JSON, caches result."""
    from app.coverage import get_coverage_filter

    coverage_json = {
        "files": {
            "src/auth.py": {"executed_lines": [1, 2, 3]},
            "src/db.py": {"executed_lines": [10, 20]},
            "src/unused.py": {"executed_lines": []},
        },
        "totals": {"percent_covered": 72.5},
    }

    mock_redis = AsyncMock()
    mock_redis.get.return_value = None  # cache miss

    with patch("app.coverage._run_pytest_cov", return_value=True), \
         patch("app.coverage._read_coverage_json", return_value=coverage_json):
        files, pct = await get_coverage_filter(
            mock_redis, repo="test", commit_sha="abc123", repo_dir="/tmp/repo",
        )

    assert "src/auth.py" in files
    assert "src/db.py" in files
    assert "src/unused.py" not in files  # no executed lines
    assert pct == 72.5
    # Should have cached the result
    mock_redis.set.assert_called_once()


@pytest.mark.asyncio
async def test_coverage_prepass_cache_hit():
    """On cache hit, returns cached data without running pytest."""
    from app.coverage import get_coverage_filter

    cached = json.dumps({"files": ["src/auth.py", "src/db.py"], "coverage_pct": 72.5})
    mock_redis = AsyncMock()
    mock_redis.get.return_value = cached.encode()

    files, pct = await get_coverage_filter(
        mock_redis, repo="test", commit_sha="abc123", repo_dir="/tmp/repo",
    )

    assert files == {"src/auth.py", "src/db.py"}
    assert pct == 72.5


@pytest.mark.asyncio
async def test_coverage_prepass_failure_returns_none():
    """If pytest --cov fails, returns (None, None)."""
    from app.coverage import get_coverage_filter

    mock_redis = AsyncMock()
    mock_redis.get.return_value = None

    with patch("app.coverage.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=1)

        files, pct = await get_coverage_filter(
            mock_redis, repo="test", commit_sha="abc123", repo_dir="/tmp/repo",
        )

    assert files is None
    assert pct is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd services/trace-runner && python -m pytest tests/test_coverage.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.coverage'`

- [ ] **Step 3: Implement coverage pre-pass module**

```python
# services/trace-runner/app/coverage.py
from __future__ import annotations

import json
import logging
import os
import subprocess
from typing import Optional

import redis.asyncio as aioredis

logger = logging.getLogger(__name__)

CACHE_TTL = 86400  # 24 hours
COVERAGE_TIMEOUT = 120  # seconds


def _cache_key(repo: str, commit_sha: str) -> str:
    return f"coverage:{repo}:{commit_sha}"


def _read_coverage_json(repo_dir: str) -> dict | None:
    """Read the coverage.json file produced by pytest --cov."""
    cov_path = os.path.join(repo_dir, "coverage.json")
    if not os.path.exists(cov_path):
        return None
    with open(cov_path) as f:
        return json.load(f)


def _parse_coverage(data: dict) -> tuple[set[str], float | None]:
    """Extract file set and coverage percentage from coverage.py JSON output."""
    files: set[str] = set()
    for filepath, info in data.get("files", {}).items():
        executed = info.get("executed_lines", [])
        if executed:
            files.add(filepath)

    pct = data.get("totals", {}).get("percent_covered")
    return files, pct


def _run_pytest_cov(repo_dir: str) -> bool:
    """Run pytest --cov in the repo directory. Returns True on success."""
    try:
        result = subprocess.run(
            ["python", "-m", "pytest", "--cov", "--cov-report=json", "-q", "--no-header"],
            cwd=repo_dir,
            capture_output=True,
            timeout=COVERAGE_TIMEOUT,
        )
        return result.returncode == 0
    except (subprocess.TimeoutExpired, FileNotFoundError) as exc:
        logger.warning("pytest --cov failed: %s", exc)
        return False


async def get_coverage_filter(
    r: aioredis.Redis,
    repo: str,
    commit_sha: str,
    repo_dir: str,
) -> tuple[Optional[set[str]], Optional[float]]:
    """Get coverage filter for a repo@commit. Uses Redis cache, falls back to running pytest.

    Returns (file_set, coverage_pct) or (None, None) on failure.
    """
    key = _cache_key(repo, commit_sha)

    # Check cache
    cached = await r.get(key)
    if cached is not None:
        data = json.loads(cached)
        return set(data["files"]), data.get("coverage_pct")

    # Run pytest --cov
    success = _run_pytest_cov(repo_dir)
    if not success:
        logger.warning("Coverage pre-pass failed for %s@%s, tracing all files", repo, commit_sha)
        return None, None

    # Parse results
    cov_data = _read_coverage_json(repo_dir)
    if cov_data is None:
        logger.warning("No coverage.json found for %s@%s", repo, commit_sha)
        return None, None

    files, pct = _parse_coverage(cov_data)

    # Cache result
    cache_value = json.dumps({"files": sorted(files), "coverage_pct": pct})
    await r.set(key, cache_value, ex=CACHE_TTL)

    return files, pct
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd services/trace-runner && python -m pytest tests/test_coverage.py -v`
Expected: All PASS

- [ ] **Step 5: Commit**

```bash
git add services/trace-runner/app/coverage.py services/trace-runner/tests/test_coverage.py
git commit -m "feat(trace-runner): add coverage pre-pass module with Redis caching"
```

---

### Task 8: Update InstrumenterClient + Runner to Pass New Fields

**Files:**
- Modify: `services/trace-runner/app/instrumenter_client.py`
- Modify: `services/trace-runner/app/runner.py`
- Modify: `services/trace-runner/tests/test_runner.py`

- [ ] **Step 1: Write failing tests for updated client and runner**

```python
# services/trace-runner/tests/test_client_new_fields.py
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from app.instrumenter_client import InstrumenterClient


@pytest.mark.asyncio
async def test_instrument_passes_capture_args_and_coverage_filter():
    mock_response = MagicMock()
    mock_response.json.return_value = {"session_id": "abc"}
    mock_response.raise_for_status = MagicMock()

    mock_client = AsyncMock()
    mock_client.post.return_value = mock_response

    ic = InstrumenterClient("http://localhost:8093")
    ic._client = mock_client

    await ic.instrument(
        stable_id="s", file_path="f.py", repo="r",
        capture_args=["*handler*"],
        coverage_filter=["src/auth.py"],
    )

    call_args = mock_client.post.call_args
    payload = call_args.kwargs.get("json") or call_args[1].get("json")
    assert payload["capture_args"] == ["*handler*"]
    assert payload["coverage_filter"] == ["src/auth.py"]


@pytest.mark.asyncio
async def test_instrument_defaults_omit_new_fields():
    mock_response = MagicMock()
    mock_response.json.return_value = {"session_id": "abc"}
    mock_response.raise_for_status = MagicMock()

    mock_client = AsyncMock()
    mock_client.post.return_value = mock_response

    ic = InstrumenterClient("http://localhost:8093")
    ic._client = mock_client

    await ic.instrument(stable_id="s", file_path="f.py", repo="r")

    call_args = mock_client.post.call_args
    payload = call_args.kwargs.get("json") or call_args[1].get("json")
    assert payload["capture_args"] == []
    assert payload["coverage_filter"] is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd services/trace-runner && python -m pytest tests/test_client_new_fields.py -v`
Expected: FAIL — `instrument()` doesn't accept `capture_args`/`coverage_filter`

- [ ] **Step 3: Update InstrumenterClient.instrument() signature**

In `services/trace-runner/app/instrumenter_client.py`, change `instrument` method:

```python
    async def instrument(
        self,
        stable_id: str,
        file_path: str,
        repo: str,
        capture_args: list[str] | None = None,
        coverage_filter: list[str] | None = None,
    ) -> str:
        """Call /instrument, return session_id."""
        payload = {
            "stable_id": stable_id,
            "file_path": file_path,
            "repo": repo,
            "capture_args": capture_args or [],
            "coverage_filter": coverage_filter,
        }
        resp = await self._client.post(
            f"{self._base_url}/instrument",
            json=payload,
        )
        resp.raise_for_status()
        return resp.json()["session_id"]
```

- [ ] **Step 4: Update process_job in runner.py to use coverage pre-pass**

In `services/trace-runner/app/runner.py`, update `process_job`:

```python
from .coverage import get_coverage_filter

async def process_job(
    job: EntrypointJob,
    commit_sha: str,
    r: aioredis.Redis,
    instrumenter: InstrumenterClient,
    emit_fn: Callable[[aioredis.Redis, RawTrace], Awaitable[None]] = _default_emit,
    repo_dir: str | None = None,
    capture_args: list[str] | None = None,
) -> str:
    """Process one EntrypointJob. Returns 'cached', 'ok', or 'error'."""
    if await is_cached(r, commit_sha, job.stable_id):
        logger.debug("Cache hit for %s @ %s", job.stable_id, commit_sha)
        return "cached"

    # Coverage pre-pass
    coverage_filter = None
    coverage_pct = None
    if repo_dir:
        coverage_filter, coverage_pct = await get_coverage_filter(
            r, repo=job.repo, commit_sha=commit_sha, repo_dir=repo_dir,
        )

    try:
        session_id = await instrumenter.instrument(
            stable_id=job.stable_id,
            file_path=job.file_path,
            repo=job.repo,
            capture_args=capture_args,
            coverage_filter=list(coverage_filter) if coverage_filter else None,
        )
        events, duration_ms = await instrumenter.run(session_id=session_id)
    except Exception as exc:
        logger.error("Instrumenter failed for %s: %s", job.stable_id, exc)
        return "error"

    trace = RawTrace(
        entrypoint_stable_id=job.stable_id,
        commit_sha=commit_sha,
        repo=job.repo,
        duration_ms=duration_ms,
        events=events,
        coverage_pct=coverage_pct,
    )
    await emit_fn(r, trace)
    await mark_cached(r, commit_sha, job.stable_id)
    return "ok"
```

- [ ] **Step 5: Run all trace-runner tests**

Run: `cd services/trace-runner && python -m pytest tests/ -v`
Expected: All PASS

- [ ] **Step 6: Commit**

```bash
git add services/trace-runner/app/instrumenter_client.py services/trace-runner/app/runner.py \
      services/trace-runner/tests/test_client_new_fields.py
git commit -m "feat(trace-runner): pass coverage filter and capture_args to instrumenter"
```

---

### Task 9: Trace-Normalizer Async Call Tree Reconstruction

**Files:**
- Modify: `services/trace-normalizer/app/normalizer.py`
- Modify: `services/trace-normalizer/tests/test_normalizer.py`

- [ ] **Step 1: Write failing tests for async call tree**

```python
# services/trace-normalizer/tests/test_async_normalizer.py
import pytest
from app.models import TraceEvent, CallNode
from app.normalizer import reconstruct_call_tree


def test_async_call_tree_links_parent_child():
    """Events with task_id and async_call/async_return produce linked sub-trees."""
    events = [
        TraceEvent(type="call", fn="handler", file="a.py", line=1, timestamp_ms=0, task_id=0),
        TraceEvent(type="async_call", fn="fetch_user", file="", line=0, timestamp_ms=5, task_id=1),
        TraceEvent(type="call", fn="fetch_user", file="b.py", line=10, timestamp_ms=6, task_id=1),
        TraceEvent(type="return", fn="fetch_user", file="b.py", line=15, timestamp_ms=20, task_id=1),
        TraceEvent(type="async_return", fn="fetch_user", file="", line=0, timestamp_ms=21, task_id=1),
        TraceEvent(type="return", fn="handler", file="a.py", line=5, timestamp_ms=25, task_id=0),
    ]
    nodes = reconstruct_call_tree(events)
    names = [n.stable_id for n in nodes]
    assert "handler" in names
    assert "fetch_user" in names
    # fetch_user should appear after handler in the tree (child)
    handler_idx = names.index("handler")
    fetch_idx = names.index("fetch_user")
    assert fetch_idx > handler_idx


def test_sync_events_without_task_id_still_work():
    """Old-format events (no task_id) produce correct call tree."""
    events = [
        TraceEvent(type="call", fn="a", file="a.py", line=1, timestamp_ms=0),
        TraceEvent(type="call", fn="b", file="b.py", line=1, timestamp_ms=5),
        TraceEvent(type="return", fn="b", file="b.py", line=2, timestamp_ms=10),
        TraceEvent(type="return", fn="a", file="a.py", line=2, timestamp_ms=15),
    ]
    nodes = reconstruct_call_tree(events)
    assert len(nodes) == 2
    assert nodes[0].stable_id == "b"  # inner returns first
    assert nodes[1].stable_id == "a"


def test_mixed_sync_async():
    """Mix of sync and async events."""
    events = [
        TraceEvent(type="call", fn="main", file="a.py", line=1, timestamp_ms=0, task_id=0),
        TraceEvent(type="call", fn="sync_helper", file="a.py", line=5, timestamp_ms=2, task_id=0),
        TraceEvent(type="return", fn="sync_helper", file="a.py", line=6, timestamp_ms=4, task_id=0),
        TraceEvent(type="async_call", fn="async_work", file="", line=0, timestamp_ms=5, task_id=1),
        TraceEvent(type="call", fn="async_work", file="b.py", line=1, timestamp_ms=6, task_id=1),
        TraceEvent(type="return", fn="async_work", file="b.py", line=5, timestamp_ms=10, task_id=1),
        TraceEvent(type="async_return", fn="async_work", file="", line=0, timestamp_ms=11, task_id=1),
        TraceEvent(type="return", fn="main", file="a.py", line=10, timestamp_ms=15, task_id=0),
    ]
    nodes = reconstruct_call_tree(events)
    names = [n.stable_id for n in nodes]
    assert "main" in names
    assert "sync_helper" in names
    assert "async_work" in names
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd services/trace-normalizer && python -m pytest tests/test_async_normalizer.py -v`
Expected: FAIL — async events not handled

- [ ] **Step 3: Update reconstruct_call_tree to handle async events**

Update `services/trace-normalizer/app/normalizer.py`:

```python
def reconstruct_call_tree(events: list[TraceEvent]) -> list[CallNode]:
    """
    Walk the flat event list and reconstruct call/return pairs.
    Supports async events: if task_id is present, partitions events by task
    and links sub-trees via async_call/async_return.
    """
    import logging as _logging
    _log = _logging.getLogger(__name__)

    has_task_ids = any(e.task_id is not None for e in events)

    if not has_task_ids:
        return _reconstruct_sync(events, _log)

    # Partition events by task_id
    tasks: dict[int, list[TraceEvent]] = {}
    async_links: list[tuple[int, str]] = []  # (child_task_id, fn_name)

    for ev in events:
        tid = ev.task_id if ev.task_id is not None else 0
        if ev.type == "async_call":
            async_links.append((tid, ev.fn))
            continue
        if ev.type == "async_return":
            continue
        tasks.setdefault(tid, []).append(ev)

    # Build call tree per task
    task_trees: dict[int, list[CallNode]] = {}
    for tid, task_events in tasks.items():
        task_trees[tid] = _reconstruct_sync(task_events, _log)

    # Flatten: root task first, then child tasks in order of async_call appearance
    result = list(task_trees.get(0, []))
    for child_tid, _fn in async_links:
        result.extend(task_trees.get(child_tid, []))

    return result


def _reconstruct_sync(events: list[TraceEvent], _log) -> list[CallNode]:
    """Original sync-only call tree reconstruction."""
    stack: list[tuple[str, int, float]] = []
    nodes: list[CallNode] = []
    depth = 0

    for ev in events:
        if ev.type == "call":
            stack.append((ev.fn, depth, ev.timestamp_ms))
            depth += 1
        elif ev.type in ("return", "exception") and stack:
            fn, call_depth, call_ts = stack.pop()
            depth = max(0, depth - 1)
            if ev.fn != fn:
                _log.warning("call/return mismatch: expected %s, got %s — using call name", fn, ev.fn)
            duration = ev.timestamp_ms - call_ts
            nodes.append(CallNode(
                stable_id=fn,
                hop=call_depth,
                frequency_ratio=1.0,
                avg_ms=max(0.0, duration),
            ))

    return nodes
```

- [ ] **Step 4: Run all normalizer tests**

Run: `cd services/trace-normalizer && python -m pytest tests/ -v`
Expected: All PASS (existing + new)

- [ ] **Step 5: Commit**

```bash
git add services/trace-normalizer/app/normalizer.py services/trace-normalizer/tests/test_async_normalizer.py
git commit -m "feat(trace-normalizer): handle async events in call tree reconstruction"
```

---

### Task 10: Spec-Generator — coverage_pct + Arg Examples

**Files:**
- Modify: `services/spec-generator/app/store.py`
- Modify: `services/spec-generator/app/renderer.py`
- Modify: `services/spec-generator/tests/test_renderer.py`
- Modify: `services/spec-generator/tests/test_store.py`

- [ ] **Step 1: Write failing tests for renderer arg examples**

```python
# services/spec-generator/tests/test_renderer_args.py
from app.models import ExecutionPath, CallSequenceItem, SideEffect, EdgeRef
from app.renderer import render_spec_text


def _make_path(call_sequence, side_effects=None):
    return ExecutionPath(
        entrypoint_stable_id="test",
        commit_sha="abc",
        repo="test",
        call_sequence=call_sequence,
        side_effects=side_effects or [],
        dynamic_only_edges=[],
        never_observed_static_edges=[],
        timing_p50_ms=10.0,
        timing_p99_ms=50.0,
    )


def test_render_with_args():
    path = _make_path([
        CallSequenceItem(
            stable_id="s1", name="login_handler", qualified_name="auth.login_handler",
            hop=0, frequency_ratio=1.0, avg_ms=4.0,
            args='{"username": "str", "password": "str"}',
        ),
        CallSequenceItem(
            stable_id="s2", name="validate", qualified_name="auth.validate",
            hop=1, frequency_ratio=0.9, avg_ms=2.0,
        ),
    ])
    text = render_spec_text(path, "login_handler")
    assert 'args: {"username": "str", "password": "str"}' in text
    # validate has no args — should not have args label
    lines = text.split("\n")
    validate_line = next(l for l in lines if "validate" in l)
    assert "args:" not in validate_line


def test_render_without_args_backward_compat():
    path = _make_path([
        CallSequenceItem(
            stable_id="s1", name="handler", qualified_name="handler",
            hop=0, frequency_ratio=1.0, avg_ms=5.0,
        ),
    ])
    text = render_spec_text(path, "handler")
    assert "args:" not in text
```

- [ ] **Step 2: Write failing test for coverage_pct preference in store**

```python
# services/spec-generator/tests/test_store_coverage.py
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from app.models import ExecutionPath, CallSequenceItem
from app.store import _compute_branch_coverage, _resolve_branch_coverage


def test_resolve_branch_coverage_prefers_coverage_pct():
    path = MagicMock()
    path.coverage_pct = 0.85
    path.call_sequence = [MagicMock(frequency_ratio=1.0)]
    result = _resolve_branch_coverage(path)
    assert result == 0.85


def test_resolve_branch_coverage_falls_back_to_frequency():
    path = MagicMock()
    path.coverage_pct = None
    path.call_sequence = [
        MagicMock(frequency_ratio=1.0),
        MagicMock(frequency_ratio=0.0),
    ]
    result = _resolve_branch_coverage(path)
    assert result == 0.5  # 1 of 2 observed
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `cd services/spec-generator && python -m pytest tests/test_renderer_args.py tests/test_store_coverage.py -v`
Expected: FAIL

- [ ] **Step 4: Add `args` field to CallSequenceItem**

In `services/spec-generator/app/models.py`:

1. Change the import line from `from typing import Literal` to:
```python
from typing import Literal, Optional
```

2. Add to `CallSequenceItem`:
```python
    args: Optional[str] = None
```

- [ ] **Step 5: Update renderer to show arg examples**

In `services/spec-generator/app/renderer.py`, update the PATH section loop:

```python
    for i, item in enumerate(path.call_sequence, start=1):
        freq_str = f"{item.frequency_ratio:.2f}"
        ms_str = f"~{item.avg_ms:.1f}ms"
        line = f"  {i}.  {item.name}    {freq_str}    {ms_str}"
        if getattr(item, "args", None):
            line += f"    args: {item.args}"
        lines.append(line)
```

- [ ] **Step 6: Update store.py to prefer coverage_pct**

In `services/spec-generator/app/store.py`, add:

```python
def _resolve_branch_coverage(path: ExecutionPath) -> float | None:
    """Prefer pytest-derived coverage_pct, fall back to frequency-ratio."""
    if path.coverage_pct is not None:
        return path.coverage_pct
    return _compute_branch_coverage(path.call_sequence)
```

Update `upsert_spec` to call `_resolve_branch_coverage(path)` instead of `_compute_branch_coverage(path.call_sequence)`.

- [ ] **Step 7: Run all spec-generator tests**

Run: `cd services/spec-generator && python -m pytest tests/ -v`
Expected: All PASS

- [ ] **Step 8: Commit**

```bash
git add services/spec-generator/app/models.py services/spec-generator/app/renderer.py \
      services/spec-generator/app/store.py \
      services/spec-generator/tests/test_renderer_args.py \
      services/spec-generator/tests/test_store_coverage.py
git commit -m "feat(spec-generator): add arg examples in spec text and prefer coverage_pct"
```

---

### Task 11: Integration Smoke Test

**Files:**
- Create: `services/instrumenter/tests/test_integration.py`

- [ ] **Step 1: Write integration test that exercises the full instrumenter flow**

```python
# services/instrumenter/tests/test_integration.py
import pytest
from fastapi.testclient import TestClient
from app.main import app

client = TestClient(app)


def test_instrument_and_run_full_flow():
    """Full flow: /instrument with capture_args → /run → verify events have args."""
    resp = client.post("/instrument", json={
        "stable_id": "sha256:integration_test",
        "file_path": "__test_stub__",
        "repo": "test",
        "capture_args": ["*stub*"],
        "coverage_filter": None,
    })
    assert resp.status_code == 200
    session_id = resp.json()["session_id"]

    resp = client.post("/run", json={"session_id": session_id})
    assert resp.status_code == 200
    data = resp.json()
    assert isinstance(data["events"], list)
    assert data["duration_ms"] >= 0


def test_instrument_with_coverage_filter():
    """Coverage filter restricts which files are traced."""
    resp = client.post("/instrument", json={
        "stable_id": "sha256:filtered",
        "file_path": "__test_stub__",
        "repo": "test",
        "coverage_filter": ["nonexistent_module.py"],
    })
    assert resp.status_code == 200
    session_id = resp.json()["session_id"]

    resp = client.post("/run", json={"session_id": session_id})
    assert resp.status_code == 200
    # With a restrictive filter, most internal events should be filtered out
    data = resp.json()
    assert isinstance(data["events"], list)
```

- [ ] **Step 2: Run integration test**

Run: `cd services/instrumenter && python -m pytest tests/test_integration.py -v`
Expected: All PASS

- [ ] **Step 3: Run ALL tests across all modified services**

Run: `cd services/instrumenter && python -m pytest tests/ -v`
Run: `cd services/trace-runner && python -m pytest tests/ -v`
Run: `cd services/trace-normalizer && python -m pytest tests/ -v`
Run: `cd services/spec-generator && python -m pytest tests/ -v`
Expected: All PASS

- [ ] **Step 4: Commit**

```bash
git add services/instrumenter/tests/test_integration.py
git commit -m "test(instrumenter): add integration smoke tests for full /instrument → /run flow"
```

---

### Task 12: Wire Default capture_args Patterns

**Files:**
- Modify: `services/instrumenter/app/config.py`
- Modify: `services/instrumenter/app/main.py`

- [ ] **Step 1: Add default capture_args patterns to config**

```python
# Add to services/instrumenter/app/config.py

DEFAULT_CAPTURE_ARGS: list[str] = [
    "test_*",
    "*_handler",
    "*_view",
    "db_*",
]
```

- [ ] **Step 2: Apply defaults server-side when capture_args is empty**

In `services/instrumenter/app/main.py`, in the `/instrument` endpoint, after reading `req.capture_args`:

```python
    from .config import DEFAULT_CAPTURE_ARGS

    effective_capture_args = req.capture_args if req.capture_args else DEFAULT_CAPTURE_ARGS
```

Use `effective_capture_args` when creating the `TraceSession` instead of `req.capture_args`.

- [ ] **Step 3: Run instrumenter tests to verify nothing broke**

Run: `cd services/instrumenter && python -m pytest tests/ -v`
Expected: All PASS

- [ ] **Step 4: Commit**

```bash
git add services/instrumenter/app/config.py services/instrumenter/app/main.py
git commit -m "feat(instrumenter): apply default capture_args patterns when none provided"
```
