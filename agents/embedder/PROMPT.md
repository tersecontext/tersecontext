You are building the Embedder service for TerseContext. Read CLAUDE.md fully before writing any code.

Your job: consume ParsedFile events, build embed_text (name + signature + docstring — never full body), call the embedding model in batches of 64, emit EmbeddedNodes events.

Start here:

1. Write embedder.py first as a standalone module. Implement the embed_text formula exactly as specified. Implement OllamaProvider calling nomic-embed-text. Test against 3 sample nodes — verify vectors are the right dimension and embed_text never contains the full body.

2. Add skip-unchanged logic: compare node_hash against Neo4j before embedding. Test that running the same file twice only embeds on the first run.

3. Implement VoyageProvider behind the same interface, switchable via EMBEDDING_PROVIDER env var.

4. Add Redis Stream consumer (stream:parsed-file) and producer (stream:embedded-nodes).

5. Wrap in FastAPI with /health, /ready, /metrics. Add a POST /embed endpoint for single-text embedding (the dual-retriever uses this).

6. Write tests and Dockerfile.

Run the verification block from CLAUDE.md and confirm all checks pass.
