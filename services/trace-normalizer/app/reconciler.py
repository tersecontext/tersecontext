from __future__ import annotations

import logging

from .models import Edge

logger = logging.getLogger(__name__)

_STATIC_EDGES_QUERY = """
MATCH (a:Node {stable_id: $entrypoint_id, repo: $repo})-[:CALLS*1..]->(b:Node {repo: $repo})
RETURN a.name AS source, b.name AS target,
       a.stable_id AS source_id, b.stable_id AS target_id
"""


def reconcile(
    driver,
    repo: str,
    entrypoint_stable_id: str,
    observed_fns: set[str],
) -> tuple[list[Edge], list[Edge]]:
    """
    Compare observed function names against static CALLS edges in Neo4j.

    Returns:
        dynamic_only_edges: functions observed in trace but not in static graph as targets
        never_observed_static_edges: static edges whose target was never observed
    """
    if driver is None:
        return [], []

    static_edges: list[tuple[str, str]] = []
    try:
        with driver.session() as session:
            result = session.run(
                _STATIC_EDGES_QUERY,
                repo=repo,
                entrypoint_id=entrypoint_stable_id,
            )
            for record in result:
                static_edges.append((record["source"], record["target"]))
    except Exception as exc:
        logger.warning("Neo4j reconcile query failed: %s", exc)
        return [], []

    static_targets = {tgt for _, tgt in static_edges}
    static_sources = {src for src, _ in static_edges}

    # Observed but not referenced in static graph at all
    dynamic_only: list[Edge] = []
    for fn in observed_fns:
        if fn not in static_targets and fn not in static_sources:
            dynamic_only.append(Edge(source=entrypoint_stable_id, target=fn))

    # Static edges whose target was never observed in trace
    never_observed: list[Edge] = []
    for src, tgt in static_edges:
        if tgt not in observed_fns:
            never_observed.append(Edge(source=src, target=tgt))

    return dynamic_only, never_observed
