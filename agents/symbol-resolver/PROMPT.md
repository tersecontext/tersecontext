You are building the Symbol resolver service for TerseContext. Read CLAUDE.md fully before writing any code.

Build this AFTER graph-writer is stable and the end-to-end query pipeline is working. Your job: resolve cross-file import and call edges that the parser left unresolved.

Start here:

1. Write resolver.py as a standalone module. Implement Python import path resolution: given "from auth.service import AuthService", find the AuthService node in Neo4j. Test against a real multi-file project in Neo4j.

2. Implement the pending_refs queue in Redis. When resolution fails, push to the queue. When new ParsedFile events arrive, retry pending_refs for that repo.

3. Implement external package detection: "import bcrypt" → create Package node, write IMPORTS edge.

4. Add Redis Stream consumer for stream:parsed-file.

5. Wrap in FastAPI with /health, /ready, /metrics.

6. Write tests covering: successful resolution, pending_refs accumulation, pending_refs resolution on next file, external package handling.

7. Write Dockerfile.

Run the verification block from CLAUDE.md. Specifically verify that pending refs from one file resolve when a later file is indexed.
