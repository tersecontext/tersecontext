import asyncio
import logging
import os
from typing import Optional

import neo4j

logger = logging.getLogger(__name__)

_QUERY = """
UNWIND $ids AS id
MATCH (n:Node {stable_id: id})
RETURN n.stable_id AS stable_id, n.node_hash AS node_hash
"""


class Neo4jClient:
    def __init__(self) -> None:
        url = os.environ.get("NEO4J_URL", "bolt://localhost:7687")
        user = os.environ.get("NEO4J_USER", "neo4j")
        password = os.environ["NEO4J_PASSWORD"]
        self._driver = neo4j.GraphDatabase.driver(url, auth=(user, password))

    def verify_connectivity(self) -> None:
        self._driver.verify_connectivity()

    def close(self) -> None:
        self._driver.close()

    async def get_node_hashes(self, stable_ids: list[str]) -> dict[str, str]:
        """Return {stable_id: node_hash} for all known nodes.

        Nodes absent from the result are new (not yet in Neo4j).
        Runs the sync bolt driver in a thread via run_in_executor.
        """
        if not stable_ids:
            return {}
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, self._query, stable_ids)

    def _query(self, stable_ids: list[str]) -> dict[str, str]:
        with self._driver.session() as session:
            result = session.run(_QUERY, ids=stable_ids)
            return {
                record["stable_id"]: record["node_hash"]
                for record in result
            }
