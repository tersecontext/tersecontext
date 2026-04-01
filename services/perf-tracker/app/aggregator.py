from __future__ import annotations

from app.models import PerfMetric


def extract_metrics_from_raw_trace(data: dict) -> list[PerfMetric]:
    metrics: list[PerfMetric] = []
    repo = data.get("repo", "")
    commit_sha = data.get("commit_sha")
    entrypoint_id = data.get("entrypoint_stable_id", "")
    duration_ms = data.get("duration_ms", 0.0)
    events = data.get("events", [])

    metrics.append(PerfMetric(
        metric_type="user_code", metric_name="entrypoint_duration",
        entity_id=entrypoint_id, repo=repo,
        value=duration_ms, unit="ms", commit_sha=commit_sha,
    ))

    call_stack: list[dict] = []
    fn_durations: dict[str, list[float]] = {}
    fn_counts: dict[str, int] = {}

    for event in events:
        fn_key = f"{event['file']}:{event['fn']}"
        if event["type"] == "call":
            call_stack.append(event)
            fn_counts[fn_key] = fn_counts.get(fn_key, 0) + 1
        elif event["type"] == "return" and call_stack:
            for i in range(len(call_stack) - 1, -1, -1):
                if call_stack[i]["fn"] == event["fn"]:
                    call_event = call_stack.pop(i)
                    duration = event["timestamp_ms"] - call_event["timestamp_ms"]
                    fn_durations.setdefault(fn_key, []).append(duration)
                    break

    for fn_key, durations in fn_durations.items():
        avg = sum(durations) / len(durations)
        metrics.append(PerfMetric(
            metric_type="user_code", metric_name="fn_duration",
            entity_id=fn_key, repo=repo,
            value=avg, unit="ms", commit_sha=commit_sha,
        ))

    for fn_key, count in fn_counts.items():
        metrics.append(PerfMetric(
            metric_type="user_code", metric_name="call_frequency",
            entity_id=fn_key, repo=repo,
            value=count, unit="count", commit_sha=commit_sha,
        ))

    return metrics


def extract_metrics_from_execution_path(data: dict) -> list[PerfMetric]:
    metrics: list[PerfMetric] = []
    repo = data.get("repo", "")
    commit_sha = data.get("commit_sha")
    entrypoint_id = data.get("entrypoint_stable_id", "")
    call_sequence = data.get("call_sequence", [])
    side_effects = data.get("side_effects", [])

    max_hop = max((item["hop"] for item in call_sequence), default=-1)
    metrics.append(PerfMetric(
        metric_type="user_code", metric_name="call_depth",
        entity_id=entrypoint_id, repo=repo,
        value=max_hop + 1, unit="count", commit_sha=commit_sha,
    ))

    metrics.append(PerfMetric(
        metric_type="user_code", metric_name="side_effect_count",
        entity_id=entrypoint_id, repo=repo,
        value=len(side_effects), unit="count", commit_sha=commit_sha,
    ))

    timing_p50 = data.get("timing_p50_ms", 0.0)
    timing_p99 = data.get("timing_p99_ms", 0.0)
    metrics.append(PerfMetric(
        metric_type="user_code", metric_name="timing_p50",
        entity_id=entrypoint_id, repo=repo,
        value=timing_p50, unit="ms", commit_sha=commit_sha,
    ))
    metrics.append(PerfMetric(
        metric_type="user_code", metric_name="timing_p99",
        entity_id=entrypoint_id, repo=repo,
        value=timing_p99, unit="ms", commit_sha=commit_sha,
    ))

    return metrics
