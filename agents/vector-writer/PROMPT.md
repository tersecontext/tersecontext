You are building the Vector writer service for TerseContext. Read CLAUDE.md fully before writing any code.

Your job: consume EmbeddedNodes events and upsert vectors into Qdrant. This service runs in parallel with graph-writer — same input stream, different output.

Start here:

1. Write writer.py as a standalone module. Create the nodes collection if it does not exist. Implement the upsert using stable_id converted to a deterministic UUID. Test against a real Qdrant instance.

2. Add the delete handler for FileChanged events with deleted_nodes.

3. Set up two Redis Stream consumers: one for stream:embedded-nodes, one for stream:file-changed.

4. Wrap in FastAPI with /health, /ready, /metrics.

5. Write tests and Dockerfile.

Run the verification block from CLAUDE.md and confirm all checks pass.
