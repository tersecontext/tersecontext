# app/discoverer.py
from __future__ import annotations

from .neo4j_client import query_entrypoints, query_changed_dep_ids
from .postgres_client import query_last_traced
from .scorer import score_entrypoints
from .queue import push_jobs


def detect_language(file_path: str) -> str:
    """Detect language from file extension."""
    if file_path.endswith(".go"):
        return "go"
    return "python"


def run_discover(neo4j_driver, pg_conn, redis_client, repo: str, trigger: str) -> dict:
    """
    Orchestrate the full entrypoint discovery flow.

    trigger: "schedule" or "pr_open" — accepted for future prioritisation but not
    currently used to alter scoring or queuing behaviour.

    Returns {"discovered": N, "queued": M}.
    """
    entrypoints = query_entrypoints(neo4j_driver, repo)
    last_traced_map = query_last_traced(pg_conn, repo)

    # Build traced_entries for dep-change check (only for entrypoints already traced)
    traced_entries = [
        {"stable_id": sid, "last_traced_at": ts}
        for sid, ts in last_traced_map.items()
    ]
    changed_dep_ids = query_changed_dep_ids(neo4j_driver, repo, traced_entries)

    jobs = score_entrypoints(
        entrypoints=entrypoints,
        last_traced_map=last_traced_map,
        changed_dep_ids=changed_dep_ids,
        repo=repo,
    )

    queued = push_jobs(redis_client, repo, jobs)
    return {"discovered": len(entrypoints), "queued": queued}
