from __future__ import annotations
from datetime import datetime, timezone

from app.models import Bottleneck


def detect_queue_buildup(
    prev_depths: dict[str, int],
    curr_depths: dict[str, int],
    repo: str,
) -> list[Bottleneck]:
    bottlenecks = []
    now = datetime.now(timezone.utc)
    for stream, curr in curr_depths.items():
        prev = prev_depths.get(stream, 0)
        if curr > prev and curr - prev > 5:
            bottlenecks.append(Bottleneck(
                entity_id=stream,
                entity_name=stream.split(":")[-1] + " stream",
                category="pipeline",
                severity="warning" if curr - prev < 50 else "critical",
                value=float(curr - prev),
                unit="messages",
                detail=f"Queue depth grew from {prev} to {curr}",
                repo=repo,
                detected_at=now,
            ))
    return bottlenecks


def detect_processing_lag(
    lag_values: list[float],
    threshold_s: float,
    repo: str,
) -> list[Bottleneck]:
    if not lag_values:
        return []
    avg_lag = sum(lag_values) / len(lag_values)
    if avg_lag <= threshold_s:
        return []
    now = datetime.now(timezone.utc)
    return [Bottleneck(
        entity_id="trace-normalizer",
        entity_name="trace-normalizer",
        category="pipeline",
        severity="critical" if avg_lag > threshold_s * 2 else "warning",
        value=avg_lag,
        unit="s",
        detail=f"Avg processing lag {avg_lag:.1f}s exceeds {threshold_s}s threshold",
        repo=repo,
        detected_at=now,
    )]


def detect_slow_functions(
    rows: list[dict],
    repo: str,
    critical_ms: float = 1000.0,
    warning_ms: float = 200.0,
) -> list[Bottleneck]:
    bottlenecks = []
    now = datetime.now(timezone.utc)
    for row in rows:
        value = row["value"]
        if value >= critical_ms:
            severity = "critical"
        elif value >= warning_ms:
            severity = "warning"
        else:
            severity = "info"
        bottlenecks.append(Bottleneck(
            entity_id=row["entity_id"],
            entity_name=row["entity_id"].split(":")[-1],
            category="slow_fn",
            severity=severity,
            value=value,
            unit=row.get("unit", "ms"),
            detail=f"Avg duration {value:.1f}ms",
            repo=repo,
            detected_at=now,
        ))
    return bottlenecks


def detect_deep_chains(
    rows: list[dict],
    max_depth: int,
    repo: str,
) -> list[Bottleneck]:
    bottlenecks = []
    now = datetime.now(timezone.utc)
    for row in rows:
        depth = row["value"]
        if depth > max_depth:
            bottlenecks.append(Bottleneck(
                entity_id=row["entity_id"],
                entity_name=row["entity_id"].split(":")[-1],
                category="deep_chain",
                severity="warning",
                value=depth,
                unit="levels",
                detail=f"Call depth {int(depth)} exceeds max {max_depth}",
                repo=repo,
                detected_at=now,
            ))
    return bottlenecks


def detect_throughput_drop(
    current_rate: float,
    rolling_avg: float,
    threshold_pct: float,
    stream: str,
    repo: str,
) -> list[Bottleneck]:
    if rolling_avg <= 0 or current_rate >= rolling_avg:
        return []
    drop_pct = ((rolling_avg - current_rate) / rolling_avg) * 100
    if drop_pct < threshold_pct:
        return []
    now = datetime.now(timezone.utc)
    return [Bottleneck(
        entity_id=stream,
        entity_name=stream.split(":")[-1] + " stream",
        category="pipeline",
        severity="critical" if drop_pct > threshold_pct * 1.5 else "warning",
        value=drop_pct,
        unit="%",
        detail=f"Throughput dropped {drop_pct:.0f}% (current {current_rate:.1f} msg/s vs avg {rolling_avg:.1f} msg/s)",
        repo=repo,
        detected_at=now,
    )]


def compute_trend_slope(points: list[dict]) -> tuple[float, str]:
    if len(points) < 2:
        return 0.0, "stable"

    n = len(points)
    x_mean = (n - 1) / 2.0
    y_mean = sum(p["value"] for p in points) / n

    numerator = sum((i - x_mean) * (p["value"] - y_mean) for i, p in enumerate(points))
    denominator = sum((i - x_mean) ** 2 for i in range(n))

    if denominator == 0:
        return 0.0, "stable"

    slope = numerator / denominator

    if slope > 0.01:
        direction = "increasing"
    elif slope < -0.01:
        direction = "decreasing"
    else:
        direction = "stable"

    return slope, direction
