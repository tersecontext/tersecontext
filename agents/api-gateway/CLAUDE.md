# CLAUDE.md — API gateway

## TerseContext — system overview

TerseContext produces minimum sufficient LLM context. The API gateway is the single
external entry point. It orchestrates the query pipeline and is the only service
with a port exposed to the host machine.

All services expose: GET /health, GET /ready, GET /metrics

---

## Your role — API gateway

You are the last query service built. You orchestrate the four query services in
sequence and stream the result back to the caller. You also accept index requests
that trigger the ingestion pipeline.

Port: 8090 (exposed to host — the only service users directly interact with)
Language: Go
Calls (gRPC): query-understander (8086 HTTP), dual-retriever (8087), subgraph-expander (8088), serializer (8089)
Exposes (HTTP): POST /query, POST /index, GET /health, GET /ready, GET /metrics

---

## POST /query

```
Request:
  Content-Type: application/json
  {
    "repo":     "acme-api",
    "question": "how does authentication work",
    "options": {
      "max_tokens": 2000,
      "hop_depth":  2,
      "query_type": null,       // null = auto-detect via understander
      "scope":      null
    }
  }

Response (streaming):
  Content-Type: text/plain
  Transfer-Encoding: chunked
  X-Trace-Id: {uuid}

  {six-layer context document streamed as plain text}
```

### Orchestration sequence

```go
func handleQuery(w http.ResponseWriter, r *http.Request) {
    traceID := uuid.New().String()
    w.Header().Set("X-Trace-Id", traceID)
    w.Header().Set("Content-Type", "text/plain")
    w.Header().Set("Transfer-Encoding", "chunked")

    // 1. Understand
    intent, err := understander.Understand(ctx, req.Question, req.Repo)

    // 2. Retrieve seeds
    seeds, err := retriever.Retrieve(ctx, intent, req.Repo, req.Options.MaxSeeds)

    // 3. Expand subgraph
    subgraph, err := expander.Expand(ctx, seeds, intent.QueryType, req.Options)

    // 4. Serialize → stream
    stream, err := serializer.SerializeStream(ctx, subgraph, req.Question, req.Repo)
    io.Copy(w, stream)
}
```

Pass traceID as gRPC metadata on every downstream call.
Log each step with traceID, duration, and result summary.

---

## POST /index

```
Request:
  { "repo_path": "/path/to/repo", "full_rescan": false }

Response:
  202 Accepted
  { "job_id": "{uuid}", "message": "indexing started" }
```

Push a FileChanged event to stream:file-changed with diff_type="full_rescan" if
full_rescan=true, or trigger the repo-watcher to check for changes if false.
Return immediately — indexing is async.

---

## Rate limiting

Rate limit per repo + client IP: 10 requests/minute.
Use a token bucket in-memory. Return 429 with Retry-After header when exceeded.
No external dependency for rate limiting — keep it simple.

---

## Error handling

All downstream gRPC errors → 500 with JSON error body.
Input validation failures → 400.
Rate limit → 429.

Never stream partial output if a step failed. Return error before starting stream.

---

## Service structure

```
services/api-gateway/
  cmd/main.go
  internal/
    handlers/
      query.go
      index.go
      health.go
    clients/
      understander.go
      retriever.go
      expander.go
      serializer.go
    ratelimit/
      bucket.go
    middleware/
      logging.go
      tracing.go
  go.mod
  Dockerfile
```

---

## Verification

```bash
# Prerequisite: all four query services running (understander, retriever, expander, serializer)

# 1. Health
curl http://localhost:8090/health
# expect: {"status":"ok","service":"api-gateway","version":"0.1.0"}

# 2. Full query — end-to-end test
curl -X POST http://localhost:8090/query \
  -H 'Content-Type: application/json' \
  -d '{"repo":"test-repo","question":"how does authentication work"}' \
  --no-buffer
# expect: streaming plain text response with six-layer format
# X-Trace-Id header must be present in response

# 3. Verify trace ID propagates through all services
# Check logs of each downstream service — same trace_id should appear

# 4. Rate limit
for i in $(seq 1 12); do
  curl -s -o /dev/null -w "%{http_code}\n" \
    -X POST http://localhost:8090/query \
    -H 'Content-Type: application/json' \
    -d '{"repo":"test","question":"test"}'
done
# First 10 should be 200, last 2 should be 429

# 5. Index endpoint
curl -X POST http://localhost:8090/index \
  -H 'Content-Type: application/json' \
  -d '{"repo_path":"/repos/test","full_rescan":true}'
# expect: 202 {"job_id":"...","message":"indexing started"}

# 6. Unit tests
cd services/api-gateway && go test ./...
```

---

## Definition of done

- [ ] /health returns 200
- [ ] POST /query returns streaming plain text context document
- [ ] X-Trace-Id header present on every response
- [ ] Trace ID propagated to all downstream gRPC calls
- [ ] Rate limiting: 429 after 10 requests/minute
- [ ] POST /index returns 202 immediately
- [ ] All four query services called in sequence
- [ ] All Go tests pass
- [ ] Dockerfile builds cleanly
