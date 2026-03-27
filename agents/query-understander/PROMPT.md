You are building the Query understander service for TerseContext. Read CLAUDE.md fully before writing any code.

This service is fully independent of the ingestion pipeline — you do not need the parser or graph to be running. You only need Redis (for cache) and Ollama (for LLM calls).

Start here:

1. Write understander.py first as a standalone module with no FastAPI. It should take a question string and return a QueryIntent dict. Hard-code the Ollama URL for now. Test it manually against 5 different questions covering lookup, flow, and impact types. Verify query_type is classified correctly each time.

2. Add the fallback: if Ollama fails or returns invalid JSON, extract keywords from the raw query and return a best-effort QueryIntent.

3. Add Redis caching. Verify the second call to the same question does not call Ollama.

4. Wrap in FastAPI with /health, /ready, /metrics and the POST /understand endpoint.

5. Write tests covering: flow/lookup/impact classification, cache hit, Ollama fallback, and schema validation of the output.

6. Write a Dockerfile.

Do not move to the next step until the current one works.

When complete, run the full verification block from CLAUDE.md and confirm every check passes.
