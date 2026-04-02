#!/usr/bin/env python3
"""One-time migration: drop the node_search fulltext index from existing deployments."""
import os
import sys
from neo4j import GraphDatabase

url = os.environ.get("NEO4J_URL", "bolt://localhost:7687")
user = os.environ.get("NEO4J_USER", "neo4j")
password = os.environ.get("NEO4J_PASSWORD", "")
if not password:
    print("NEO4J_PASSWORD is required", file=sys.stderr)
    sys.exit(1)

driver = GraphDatabase.driver(url, auth=(user, password))
with driver.session() as s:
    s.run("DROP INDEX node_search IF EXISTS")
print("node_search index dropped (or was already absent)")
driver.close()
