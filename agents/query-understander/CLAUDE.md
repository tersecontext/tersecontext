# CLAUDE.md — Query understander

## TerseContext — system overview

TerseContext produces the minimum sufficient context window for an LLM to understand
and safely modify a codebase. Three pipelines: Static ingestion (Python) → Query pipeline
(Go+Python) → Dynamic analysis (Python). Stores: Neo4j, Qdrant, Redis, Postgres.

All services expose: GET /health, GET /ready, GET /metrics

---

## Your role — Query understander

You are the first service in the query pipeline. You take a raw natural language question
and turn it into a structured QueryIntent that the dual retriever can act on precisely.

You call a local Ollama instance to parse the question. Your most important output is
embed_query — an expanded, semantically rich rewrite of the raw question that produces
better vector search results than the raw question alone.

Port: 8086 (internal :8080, mapped 8086:8080)
Language: Python
Input:  POST /understand  { question: str, repo: str }
Output: QueryIntent JSON
Reads:  Redis (cache), Ollama HTTP API
Writes: Redis (cache only)

This service is INDEPENDENT of the ingestion pipeline. It can be built in parallel
with the parser. It does not read from any graph or vector store.

---

## Output schema (contracts/requests/query_intent.json)

```json
{
  "raw_query": "how does authentication work",
  "keywords": ["auth", "authenticate", "login", "jwt", "token"],
  "symbols": ["AuthService", "authenticate", "verify_token"],
  "query_type": "flow",
  "embed_query": "authentication flow login jwt token verification user credentials",
  "scope": null
}
```

Fields:
- `keywords`: terms for graph keyword search — lower-case, no duplicates
- `symbols`: PascalCase or snake_case identifiers the user referenced explicitly or implied
- `query_type`: one of "lookup" | "flow" | "impact"
  - lookup: "where is X", "find X", "where is X defined"
  - flow:   "how does X work", "what happens when", "walk me through"
  - impact: "what breaks if I change X", "what calls X", "what depends on X"
- `embed_query`: expanded query optimised for embedding — more words = more semantic surface
- `scope`: file or module path if user specified one ("in auth/service.py, ...")

---

## Ollama integration

Base URL: LLM_BASE_URL env var, default http://ollama:11434
Model: LLM_MODEL env var, default qwen2.5-coder:7b

Prompt to send (fill in the actual question):

```
You are a code analysis assistant. Extract structured information from this codebase query.
Return JSON only. No explanation. No markdown. No code fences.

Schema:
{
  "keywords": ["string"],
  "symbols": ["string"],
  "query_type": "lookup | flow | impact",
  "embed_query": "string",
  "scope": "string or null"
}

Rules:
- keywords: lowercase terms relevant to the query, 3-8 items
- symbols: any identifiers (function names, class names) mentioned or implied
- query_type: lookup=find something, flow=understand execution, impact=understand consequences
- embed_query: rewrite the query with synonyms and related terms for better search, 8-15 words
- scope: file path if user mentioned one, otherwise null

Query: "{question}"
```

Validation: parse the response as JSON and validate against QueryIntent schema.
If JSON is invalid or schema validation fails: retry once with a stricter prompt.
If still failing after retry: fall back to simple keyword extraction (split on spaces,
filter stop words, set query_type="flow", embed_query=raw_query).

---

## Caching

Cache key: sha256(question.lower().strip() + "|" + repo)
TTL: 3600 seconds
Redis key format: "query_intent:{sha256_hex}"

Check cache before calling Ollama. Return cached result immediately if found.
Write to cache after every successful Ollama call (not after fallback).

---

## Startup behaviour

On startup: check Ollama is reachable by hitting LLM_BASE_URL/api/tags.
Retry with exponential backoff: 1s, 2s, 4s, 8s, 16s (5 attempts).
If Ollama is unreachable after all retries: service starts but /ready returns 503.
Log a warning. Fallback mode still works — it just uses keyword extraction.

---

## Service structure

```
services/query-understander/
  app/
    main.py       — FastAPI app
    understander.py — core logic, Ollama call, validation, fallback
    cache.py      — Redis cache helpers
    models.py     — Pydantic models matching contracts/requests/query_intent.json
  tests/
    test_understander.py
  Dockerfile
  requirements.txt
```

---

## Verification — confirm before approving this worktree

```bash
# 1. Service starts
cd services/query-understander
docker build -t tersecontext-query-understander .
docker run -d --name qu-test \
  -e REDIS_URL=redis://host.docker.internal:6379 \
  -e LLM_BASE_URL=http://host.docker.internal:11434 \
  -e LLM_MODEL=qwen2.5-coder:7b \
  -p 8086:8080 tersecontext-query-understander

# 2. Health checks
curl http://localhost:8086/health
# expect: {"status":"ok","service":"query-understander","version":"0.1.0"}

# 3. Flow query
curl -X POST http://localhost:8086/understand \
  -H 'Content-Type: application/json' \
  -d '{"question": "how does authentication work", "repo": "acme-api"}'
# expect: query_type="flow", keywords includes "auth" or "authenticate"
# embed_query must be longer than the raw question

# 4. Lookup query
curl -X POST http://localhost:8086/understand \
  -H 'Content-Type: application/json' \
  -d '{"question": "where is JWT configured", "repo": "acme-api"}'
# expect: query_type="lookup"

# 5. Impact query
curl -X POST http://localhost:8086/understand \
  -H 'Content-Type: application/json' \
  -d '{"question": "what breaks if I change authenticate", "repo": "acme-api"}'
# expect: query_type="impact", symbols includes "authenticate"

# 6. Cache hit — second identical request must NOT call Ollama
# (verify by checking logs — no Ollama call on second request)
curl -X POST http://localhost:8086/understand \
  -H 'Content-Type: application/json' \
  -d '{"question": "how does authentication work", "repo": "acme-api"}'

# 7. Fallback — stop Ollama and verify service still responds
docker stop ollama
curl -X POST http://localhost:8086/understand \
  -H 'Content-Type: application/json' \
  -d '{"question": "how does authentication work", "repo": "acme-api"}'
# expect: valid QueryIntent returned (from fallback, not error)
# /ready should return 503 while Ollama is down

# 8. Unit tests
pytest services/query-understander/tests/ -v
```

---

## Definition of done

- [ ] /health returns 200
- [ ] /ready returns 503 when Ollama is unreachable
- [ ] Flow/lookup/impact queries each return correct query_type
- [ ] embed_query is longer and richer than the raw question
- [ ] Second identical request returns from cache (no Ollama call)
- [ ] Fallback mode returns valid QueryIntent when Ollama is down
- [ ] All unit tests pass
- [ ] Dockerfile builds cleanly
