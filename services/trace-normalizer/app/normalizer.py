from __future__ import annotations

import json
import statistics
from typing import Optional

import redis

from .models import CallNode, TraceEvent

AGG_TTL = 86400 * 7  # 7 days


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


def aggregate_frequencies(
    r: redis.Redis,
    repo: str,
    entrypoint_stable_id: str,
    nodes: list[CallNode],
    total_runs: int,
) -> list[CallNode]:
    """
    Load existing aggregates from Redis, merge this run's nodes, persist back.
    If total_runs >= 5, recompute frequency_ratio = observed_count / total_runs.
    Returns updated nodes list.
    """
    key = f"trace_agg:{repo}:{entrypoint_stable_id}"
    raw = r.get(key)
    agg: dict[str, dict] = json.loads(raw) if raw else {}

    for node in nodes:
        if node.stable_id not in agg:
            agg[node.stable_id] = {"count": 0, "total_ms": 0.0, "runs": 0}
        agg[node.stable_id]["count"] += 1
        agg[node.stable_id]["total_ms"] += node.avg_ms
        agg[node.stable_id]["runs"] += 1

    r.set(key, json.dumps(agg), ex=AGG_TTL)

    if total_runs < 5:
        return nodes

    updated = []
    for node in nodes:
        entry = agg.get(node.stable_id, {})
        runs = entry.get("runs", 1)
        total_ms = entry.get("total_ms", node.avg_ms)
        updated.append(CallNode(
            stable_id=node.stable_id,
            hop=node.hop,
            frequency_ratio=min(1.0, runs / total_runs),
            avg_ms=total_ms / runs,
        ))
    return updated


def compute_percentiles(durations_ms: list[float]) -> tuple[float, float]:
    """Return (p50, p99) for a list of duration samples."""
    if not durations_ms:
        return 0.0, 0.0
    if len(durations_ms) == 1:
        v = durations_ms[0]
        return v, v
    sorted_d = sorted(durations_ms)
    p50 = statistics.median(sorted_d)
    idx99 = int(len(sorted_d) * 0.99)
    p99 = sorted_d[min(idx99, len(sorted_d) - 1)]
    return p50, p99
