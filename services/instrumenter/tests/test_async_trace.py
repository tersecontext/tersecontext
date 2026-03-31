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
        assert len(async_calls) >= 2
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
        assert len(async_calls) >= 2
    finally:
        uninstall_async_hooks()
