from __future__ import annotations

import logging

from .models import EmbeddedNode, ParsedNode

logger = logging.getLogger(__name__)

UPSERT_NODES_QUERY = """
UNWIND $nodes AS n
MERGE (node:Node { stable_id: n.stable_id })
SET node += {
  name:           n.name,
  type:           n.type,
  signature:      n.signature,
  docstring:      n.docstring,
  body:           n.body,
  file_path:      n.file_path,
  qualified_name: n.qualified_name,
  language:       n.language,
  repo:           n.repo,
  embed_text:     n.embed_text,
  node_hash:      n.node_hash,
  active:         true,
  updated_at:     datetime()
}
"""

UPSERT_EDGES_QUERY = """
UNWIND $edges AS e
MATCH (a:Node { stable_id: e.source }), (b:Node { stable_id: e.target })
MERGE (a)-[r:CALLS]->(b)
SET r.source = 'static', r.updated_at = datetime()
"""

TOMBSTONE_QUERY = """
MATCH (n:Node) WHERE n.stable_id IN $stable_ids
SET n.active = false, n.deleted_at = datetime()
WITH n
MATCH (n)-[r]->() DELETE r
WITH n
MATCH ()-[r]->(n) DELETE r
"""


def build_node_records(
    embedded_nodes: list[EmbeddedNode],
    parsed_nodes_by_id: dict[str, ParsedNode],
    file_path: str,
    language: str,
    repo: str,
) -> list[dict]:
    """Merge embedded node data with parsed metadata. Nodes absent from parsed_nodes_by_id are skipped."""
    records = []
    for en in embedded_nodes:
        pn = parsed_nodes_by_id.get(en.stable_id)
        if pn is None:
            logger.warning("Node %s not found in parsed cache, skipping", en.stable_id)
            continue
        records.append({
            "stable_id": en.stable_id,
            "name": pn.name,
            "type": pn.type,
            "signature": pn.signature,
            "docstring": pn.docstring,
            "body": pn.body,
            "file_path": file_path,
            "qualified_name": pn.qualified_name or pn.name,
            "language": language,
            "repo": repo,
            "embed_text": en.embed_text,
            "node_hash": en.node_hash,
        })
    return records


def upsert_nodes(driver, nodes: list[dict]) -> None:
    if not nodes:
        return
    with driver.session() as session:
        session.run(UPSERT_NODES_QUERY, nodes=nodes)


def upsert_edges(driver, edges: list[dict]) -> None:
    if not edges:
        return
    with driver.session() as session:
        session.run(UPSERT_EDGES_QUERY, edges=edges)


def tombstone(driver, stable_ids: list[str]) -> None:
    if not stable_ids:
        return
    with driver.session() as session:
        session.run(TOMBSTONE_QUERY, stable_ids=stable_ids)
