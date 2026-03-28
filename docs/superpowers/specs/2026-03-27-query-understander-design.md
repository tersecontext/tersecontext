# Query Understander — Design Spec
**Date:** 2026-03-27
**Branch:** feature/query-understander
**Service port:** 8086 (internal :8080)

---

## Purpose

First service in the TerseContext query pipeline. Accepts a raw natural language question and returns a structured `QueryIntent` that the dual retriever can act on precisely. Calls a local Ollama instance for parsing. Most important output is `embed_query` — a semantically enriched rewrite that produces better vector search results than the raw question.

**Reads:** Redis (cache), Ollama HTTP API
**Writes:** Redis (cache only)
**Independent of:** ingestion pipeline, Neo4j, Qdrant, Postgres

---

## Output Schema

Defined in `contracts/requests/query_intent.json`:

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

- `keywords`: lowercase terms for graph keyword search, no duplicates
- `symbols`: PascalCase or snake_case identifiers mentioned or implied
- `query_type`: `lookup` | `flow` | `impact`
- `embed_query`: expanded query for embedding, 8–15 words
- `scope`: file/module path if specified, otherwise null

---

## Service Structure

```
services/query-understander/
  app/
    main.py          — FastAPI app, lifespan, endpoints
    understander.py  — Ollama call, validation, retry, fallback
    cache.py         — Redis cache helpers
    models.py        — Pydantic models matching query_intent.json
  tests/
    test_understander.py
  Dockerfile
  requirements.txt
```

---

## Endpoints

| Method | Path | Description |
|--------|------|-------------|
| POST | /understand | Main endpoint — returns QueryIntent |
| GET | /health | Always 200 while process is alive |
| GET | /ready | 200 if Ollama reachable, 503 otherwise |
| GET | /metrics | Prometheus metrics (request count, latency) |

### POST /understand

**Request:** `{ "question": str, "repo": str }`
**Response:** QueryIntent JSON

---

## Request Flow

```
POST /understand
  1. check Redis cache (key: sha256(question.lower().strip() + "|" + repo))
     → HIT: return cached result immediately
  2. call Ollama with structured prompt
     → parse response as JSON
     → validate against QueryIntent schema
  3. on parse/validation failure: retry once with stricter prompt
  4. on second failure: fallback
     → split question on spaces, filter stop words → keywords
     → symbols = []
     → query_type = "flow"
     → embed_query = raw_query
  5. on Ollama success: write result to Redis (TTL 3600s)
  6. return QueryIntent (raw_query = original question)
```

Cache is only written on successful Ollama responses, not after fallback.

---

## Ollama Integration

**Env vars:**
- `LLM_BASE_URL` — default `http://ollama:11434`
- `LLM_MODEL` — default `qwen2.5-coder:7b`

**Prompt template:**

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

---

## Caching

- **Key format:** `query_intent:{sha256_hex}`
- **Hash input:** `question.lower().strip() + "|" + repo`
- **TTL:** 3600 seconds
- Cache is checked before any Ollama call. Cache is written only on successful Ollama responses.

---

## Startup Behaviour

On startup (FastAPI lifespan), check `LLM_BASE_URL/api/tags`:
- Retry with exponential backoff: 1s, 2s, 4s, 8s, 16s (5 attempts)
- If all fail: set `ollama_ready = False`, log warning, service starts
- `/ready` returns 503 while `ollama_ready = False`
- Fallback mode still works — keyword extraction only

---

## Dockerfile

Based on `docker/python.Dockerfile` pattern:
- Base: `python:3.12-slim`
- Install `uv`, copy `requirements.txt`, install dependencies
- Copy app source
- `CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080"]`

---

## Definition of Done

- [ ] `/health` returns 200
- [ ] `/ready` returns 503 when Ollama is unreachable
- [ ] Flow/lookup/impact queries each return correct `query_type`
- [ ] `embed_query` is longer and richer than the raw question
- [ ] Second identical request returns from cache (no Ollama call in logs)
- [ ] Fallback returns valid QueryIntent when Ollama is down
- [ ] All unit tests pass
- [ ] Dockerfile builds cleanly
