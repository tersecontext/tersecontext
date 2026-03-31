# app/neo4j_client.py
from __future__ import annotations

ENTRYPOINT_QUERY = """
MATCH (n:Node {repo: $repo, active: true})
WHERE n.name STARTS WITH 'test_'
   OR n.decorators CONTAINS 'app.route'
   OR n.decorators CONTAINS 'router.'
   OR n.decorators CONTAINS 'click.command'
RETURN n.stable_id AS stable_id, n.name AS name, n.file_path AS file_path
"""

CHANGED_DEPS_QUERY = """
UNWIND $traced_entries AS entry
MATCH (ep:Node {repo: $repo, stable_id: entry.stable_id})-[:CALLS*1..]->(dep:Node)
WHERE dep.updated_at > entry.last_traced_at
RETURN DISTINCT ep.stable_id AS stable_id
"""


def query_entrypoints(driver, repo: str) -> list[dict]:
    """Return all active entrypoint nodes for the given repo."""
    with driver.session() as session:
        result = session.run(ENTRYPOINT_QUERY, {"repo": repo})
        return [dict(record.data()) for record in result]


def query_changed_dep_ids(driver, repo: str, traced_entries: list[dict]) -> set[str]:
    """
    Return stable_ids of traced entrypoints whose dependency graph changed.

    traced_entries: list of {"stable_id": str, "last_traced_at": datetime}
    """
    if not traced_entries:
        return set()
    # Convert datetimes to ISO strings for Cypher datetime() comparison
    entries = [
        {"stable_id": e["stable_id"], "last_traced_at": e["last_traced_at"].isoformat()}
        for e in traced_entries
    ]
    with driver.session() as session:
        result = session.run(CHANGED_DEPS_QUERY, {"repo": repo, "traced_entries": entries})
        return {record["stable_id"] for record in result}
