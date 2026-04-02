from __future__ import annotations
import logging

logger = logging.getLogger(__name__)

WRITE_EDGE_QUERY = """
MATCH (a:Node {stable_id: $src, active: true})
MATCH (b:Node {stable_id: $tgt, active: true})
MERGE (a)-[r:CALLS]->(b)
SET r.source = 'lsp', r.confirmed_at = datetime()
"""


def write_lsp_edges(driver, edges: list[tuple[str, str]]) -> int:
    """Write CALLS edges with source='lsp'. Skips edges where either node is missing.
    Returns count of edges attempted."""
    if not edges:
        return 0
    written = 0
    with driver.session() as session:
        for src, tgt in edges:
            try:
                result = session.run(WRITE_EDGE_QUERY, src=src, tgt=tgt)
                result.consume()
                written += 1
            except Exception as exc:
                logger.warning("Failed to write lsp edge %s->%s: %s", src, tgt, exc)
    return written
