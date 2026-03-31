# app/scorer.py
from __future__ import annotations
from datetime import datetime

from .models import EntrypointJob


def score_entrypoints(
    entrypoints: list[dict],
    last_traced_map: dict[str, datetime],
    changed_dep_ids: set[str],
    repo: str,
) -> list[EntrypointJob]:
    """
    Score and rank entrypoints by tracing priority.

    Priority rules:
      3 — dependency graph changed since last trace
      2 — never traced (no entry in behavior_specs)
      1 — traced (any age), no dep change — lowest priority
    """
    jobs: list[EntrypointJob] = []
    for ep in entrypoints:
        sid = ep["stable_id"]
        last_traced = last_traced_map.get(sid)

        if last_traced is None:
            priority = 2
        elif sid in changed_dep_ids:
            priority = 3
        else:
            priority = 1  # traced (any age), no dep change — lowest priority

        jobs.append(EntrypointJob(
            stable_id=sid,
            name=ep["name"],
            file_path=ep["file_path"],
            priority=priority,
            repo=repo,
        ))

    return sorted(jobs, key=lambda j: j.priority, reverse=True)
