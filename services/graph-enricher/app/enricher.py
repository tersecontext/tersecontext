from __future__ import annotations
import logging

logger = logging.getLogger(__name__)

UPDATE_NODE_PROPS_QUERY = """
UNWIND $nodes AS n
MATCH (node:Node {stable_id: n.stable_id})
SET node.observed_calls  = coalesce(node.observed_calls, 0) + 1,
    node.avg_latency_ms  = n.avg_latency_ms,
    node.branch_coverage = n.branch_coverage,
    node.last_traced_at  = datetime()
"""

UPSERT_DYNAMIC_EDGES_QUERY = """
UNWIND $edges AS e
MATCH (a:Node {stable_id: e.source}), (b:Node {stable_id: e.target})
MERGE (a)-[r:CALLS]->(b)
SET r.source = CASE WHEN r.source IN ['static', 'confirmed', 'conflict'] THEN r.source ELSE 'dynamic' END,
    r.observed_count = coalesce(r.observed_count, 0) + e.count
"""

CONFIRM_STATIC_EDGES_QUERY = """
UNWIND $stable_ids AS id
MATCH (a:Node {stable_id: id})-[r:CALLS {source: 'static'}]->(b:Node)
WHERE b.stable_id IN $stable_ids
SET r.source = 'confirmed',
    r.observed_count = coalesce(r.observed_count, 0) + 1,
    r.confirmed_at = datetime()
"""

CONFLICT_DETECTOR_QUERY = """
MATCH (a)-[r:CALLS]->(b)
WHERE r.source = 'static' AND r.observed_count IS NULL
  AND a.last_traced_at IS NOT NULL
SET r.source = 'conflict', r.detail = 'in AST but never observed in traces'
"""

STALENESS_DOWNGRADE_QUERY = """
MATCH (a)-[r:CALLS {source: 'confirmed'}]->(b)
WHERE a.updated_at > r.confirmed_at
SET r.source = 'static'
REMOVE r.confirmed_at
"""


def update_node_props_batch(driver, records: list[dict]) -> None:
    if not records:
        return
    with driver.session() as session:
        session.run(UPDATE_NODE_PROPS_QUERY, nodes=records)


def upsert_dynamic_edges(driver, edges: list[dict]) -> None:
    if not edges:
        return
    with driver.session() as session:
        session.run(UPSERT_DYNAMIC_EDGES_QUERY, edges=edges)


def confirm_static_edges(driver, stable_ids: list[str]) -> None:
    if not stable_ids:
        return
    with driver.session() as session:
        session.run(CONFIRM_STATIC_EDGES_QUERY, stable_ids=stable_ids)


def run_conflict_detector(driver) -> None:
    with driver.session() as session:
        session.run(CONFLICT_DETECTOR_QUERY)


def run_staleness_downgrade(driver) -> None:
    with driver.session() as session:
        session.run(STALENESS_DOWNGRADE_QUERY)
