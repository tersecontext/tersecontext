# API Gateway — Design Spec
_Date: 2026-03-30_

## Overview

The api-gateway is the single external entry point for TerseContext. It runs on port 8090 (the only service with a host-exposed port), orchestrates the four query services in sequence, and streams the resulting context document back to the caller. It also accepts index requests that trigger the ingestion pipeline asynchronously.

---

## File Layout

```
services/api-gateway/
  cmd/main.go
  internal/
    handlers/
      query.go
      index.go
      health.go
    clients/
      understander.go      # HTTP JSON client → query-understander :8086
      retriever.go         # gRPC client → dual-retriever :8087
      expander.go          # gRPC client → subgraph-expander :8088
      serializer.go        # gRPC client → serializer :8089
    ratelimit/
      bucket.go
    middleware/
      logging.go
      tracing.go
  gen/
    query.pb.go            # copied verbatim from serializer/gen (identical across all Go services)
    query_grpc.pb.go
  go.mod
  Dockerfile
```

---

## POST /query — Orchestration

### Request
```json
{
  "repo": "acme-api",
  "question": "how does authentication work",
  "options": {
    "max_tokens": 2000,
    "hop_depth": 2,
    "query_type": null,
    "scope": null
  }
}
```

### Response
```
Content-Type: text/plain
Transfer-Encoding: chunked
X-Trace-Id: {uuid}

{six-layer context document}
```

### Orchestration sequence

1. Tracing middleware injects UUID `traceID` into request context; sets `X-Trace-Id` response header immediately.
2. Rate limiter checks `repo + clientIP` key — returns 429 with `Retry-After: 60` if exceeded.
3. Validate: 400 if `repo` or `question` missing.
4. **Understand** — HTTP POST to query-understander `/understand` with `{question, repo}` → `QueryIntent`.
5. **Retrieve** — gRPC `QueryService.Retrieve(intent, repo, max_seeds=20)` → `SeedNodesResponse`.
6. **Expand** — gRPC `QueryService.Expand(seeds, intent, max_tokens)` → `RankedSubgraphResponse`.
7. **Serialize** — gRPC `QueryService.Serialize(subgraph, intent, repo)` → `ContextDocResponse`.
8. Set `Content-Type: text/plain`, `Transfer-Encoding: chunked`, flush headers.
9. Write `ContextDocResponse.Document` to the HTTP response, flushing as we go.

If any step 4–7 fails, return a JSON error body before writing any streamed content. No partial output ever reaches the caller.

**TraceID propagation:**
- HTTP header `X-Trace-Id` → understander
- gRPC metadata key `x-trace-id` → retriever, expander, serializer
- `slog` structured field on every step log line (with service name and duration)

---

## POST /index

### Request
```json
{ "repo_path": "/path/to/repo", "full_rescan": false }
```

### Response
```
202 Accepted
{ "job_id": "{uuid}", "message": "indexing started" }
```

1. Validate: 400 if `repo_path` missing.
2. Generate `job_id` UUID.
3. Redis `XADD stream:file-changed` with fields: `repo_path`, `diff_type` (`full_rescan` or `incremental`), `job_id`.
4. Return 202 immediately — indexing is async.

---

## Rate Limiting

- Token bucket per `"repo:clientIP"` key
- 10 tokens max, refills 10 per minute (lazy refill on each request using elapsed time)
- Stored in `sync.Map` — no background goroutine, no external dependency
- Exceeded: 429 + `Retry-After: 60` header

---

## Error Handling

| Condition | Status | Body |
|-----------|--------|------|
| Missing required field | 400 | `{"error": "<message>"}` |
| Rate limit exceeded | 429 | `{"error": "rate limit exceeded"}` + `Retry-After: 60` |
| Downstream gRPC failure | 500 | `{"error": "upstream failure", "service": "<name>", "trace_id": "<id>"}` |

All errors logged via `slog.Error` with traceID and duration.

---

## Testing

- `ratelimit/bucket_test.go` — unit: fill, exhaust, time-based refill
- `handlers/query_test.go` — table-driven with mock client interfaces; verifies orchestration order, error short-circuit, no partial output on failure
- `handlers/index_test.go` — verifies 202 and Redis XADD payload shape

Thin client wrappers (`clients/`) are covered by the CLAUDE.md integration verification steps rather than unit tests.

---

## Infrastructure

### go.mod
- `go 1.24.0` + `toolchain go1.24.1` (matches serializer, subgraph-expander)
- Key deps: `google.golang.org/grpc v1.79.3`, `google.golang.org/protobuf v1.36.11`, `github.com/google/uuid`, `github.com/redis/go-redis/v9`

### Dockerfile
- `FROM golang:1.24-alpine` → build → minimal runtime image (mirrors subgraph-expander pattern)

### docker-compose.yml changes
1. Remove `"8090:8090"` host mapping from `serializer` (port conflicts with api-gateway; keep `8089:8089` for gRPC)
2. Add `query-understander` service (currently missing from docker-compose)
3. Add `api-gateway` service: port `8090:8090`, env vars for all 4 downstream addresses + Redis URL, depends_on all four query services

---

## Definition of Done

- [ ] `/health` returns 200 with `{"status":"ok","service":"api-gateway","version":"0.1.0"}`
- [ ] `POST /query` returns streaming plain text context document
- [ ] `X-Trace-Id` header present on every response
- [ ] Trace ID propagated to all downstream calls (visible in their logs)
- [ ] Rate limiting: 429 after 10 requests/minute per repo+IP
- [ ] `POST /index` returns 202 immediately
- [ ] All four query services called in sequence
- [ ] All Go tests pass
- [ ] Dockerfile builds cleanly
- [ ] docker-compose port conflict resolved
