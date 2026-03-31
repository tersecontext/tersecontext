You are building the Graph writer service for TerseContext. Read CLAUDE.md fully before writing any code.

Your job: consume EmbeddedNodes and ParsedFile events from Redis Streams and write nodes + edges to Neo4j using MERGE (never INSERT).

Start here:

1. Write writer.py first as a standalone module. Implement the node MERGE and edge MERGE Cypher queries. Test against a real Neo4j instance — run the same node twice, verify count is 1 not 2.

2. Implement tombstone: active=false + delete edges atomically. Test delete then re-check the node is active=false.

3. Set up two Redis Stream consumers: one for stream:embedded-nodes (node upserts) and one for stream:parsed-file (edge upserts + deletes). Each in its own consumer group.

4. Wrap in FastAPI with /health, /ready, /metrics.

5. Write tests and Dockerfile.

Run the verification block from CLAUDE.md and confirm all checks pass.
