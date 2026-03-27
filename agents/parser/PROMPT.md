You are building the Parser service for TerseContext. Read CLAUDE.md fully before writing any code.

Your job: consume FileChanged events from Redis, parse Python source files with Tree-sitter, emit ParsedFile events with structured nodes and intra-file edges.

Start here:

1. Create services/parser/ with the directory structure in CLAUDE.md.

2. Write a standalone parser.py first — no Redis, no FastAPI. Just a function that takes a file path and returns a list of nodes. Test it against a real Python file. Verify stable_ids are deterministic and node_hashes change when body changes.

3. Add the intra-file edge extractor. Test that calling one function defined in the same file produces a CALLS edge.

4. Wrap in FastAPI with /health, /ready, /metrics. Wire in the Redis Stream consumer as a background task on startup.

5. Write tests/test_parser.py covering all cases in CLAUDE.md.

6. Write a Dockerfile.

Do not move to the next step until the current one is verified.

When complete, run the full verification block from the bottom of CLAUDE.md and confirm every check passes. Paste the results.
