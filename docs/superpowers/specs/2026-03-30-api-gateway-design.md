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
      health.go              # GET /health, GET /ready, GET /metrics
    clients/
      understander.go        # HTTP JSON client → query-understander :8080 (container-internal; host mapping 8086:8080 is irrelevant to service-to-service communication — UNDERSTANDER_URL must use port 8080)
      retriever.go           # gRPC client → dual-retriever :8087
      expander.go            # gRPC client → subgraph-expander :8088
      serializer.go          # gRPC client → serializer :8089
    ratelimit/
      bucket.go
    middleware/
      logging.go
      tracing.go
  gen/
    query.pb.go              # copied from serializer/gen, go_package updated to api-gateway module
    query_grpc.pb.go         # copied from serializer/gen, go_package updated to api-gateway module
  go.mod
  Dockerfile
```

### gen/ copy note
Copy `query.pb.go` and `query_grpc.pb.go` from `services/serializer/gen/` verbatim — no edits to the files are needed. The other Go services (dual-retriever, subgraph-expander) follow the same copy-and-use pattern; the embedded proto descriptor binary blob is not modified across copies and does not need to be. Set the api-gateway `go.mod` module to `github.com/tersecontext/tc/services/api-gateway` and import the gen package as `github.com/tersecontext/tc/services/api-gateway/gen`. All symbols and package name (`querypb`) are identical across copies.

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

All downstream calls use `r.Context()` as their base context so that a client disconnect cancels the pipeline immediately.

1. Tracing middleware injects UUID `traceID` into request context; sets `X-Trace-Id` response header immediately.
2. Rate limiter checks `repo + clientIP` key — returns 429 with `Retry-After: 60` if exceeded.
3. Validate: 400 if `repo` or `question` missing.
4. **Understand** — HTTP POST to query-understander `/understand` with JSON body `{"question": "...", "repo": "..."}`. The response is a JSON `QueryIntent` object (`raw_query`, `keywords`, `symbols`, `query_type`, `embed_query`, `scope`). `clients/understander.go` decodes this into a `querypb.QueryIntentResponse` proto message by direct field mapping (field names are identical). This is the primary responsibility of the understander client.
5. **Retrieve** — gRPC `QueryService.Retrieve(intent, repo, max_seeds=20)` → `SeedNodesResponse`.
6. **Expand** — gRPC `QueryService.Expand(seeds, intent, max_tokens)` → `RankedSubgraphResponse`. Note: `hop_depth` from request options has no corresponding field in the `ExpandRequest` proto; it is not forwarded.
7. **Serialize** — gRPC `QueryService.Serialize(subgraph, intent, repo)` → `ContextDocResponse`. The serializer Serialize RPC is unary (not streaming). The document is returned as `ContextDocResponse.Document` (a string).
8. Set `Content-Type: text/plain`, `Transfer-Encoding: chunked`, flush headers.
9. Write `ContextDocResponse.Document` to the HTTP response in a single write, then explicitly flush. The document is a single string returned by the unary gRPC call — no looping or chunking logic is needed; the chunked transfer encoding is handled automatically by the HTTP server. This is what produces the chunked streaming response to the caller.

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
3. Redis `XADD stream:file-changed` with fields:
   - `repo_path` — the value from the request
   - `diff_type` — `"full_rescan"` if `full_rescan=true`, `"incremental"` if `false`
   - `job_id` — the generated UUID

   `"incremental"` is the canonical stream value for non-full-rescan triggers. Consumers of `stream:file-changed` must handle both `"full_rescan"` and `"incremental"` as the `diff_type` values.

   _Note: CLAUDE.md describes the `full_rescan=false` path as "trigger the repo-watcher to check for changes" without specifying a mechanism. This spec uses Redis XADD for both cases because the repo-watcher has no callable interface — using a consistent stream event is the only implementable approach. Both cases now go through `stream:file-changed`._
4. Return 202 immediately — indexing is async.

---

## Health Endpoints

`health.go` exposes all three endpoints required by every TerseContext service:

- `GET /health` → `{"status":"ok","service":"api-gateway","version":"0.1.0"}`
- `GET /ready` → `{"status":"ready"}` (200) or `{"status":"not ready"}` (503) based on downstream connectivity
- `GET /metrics` → 200 OK (stub, consistent with other services)

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
| Downstream failure (any step) | 500 | `{"error": "upstream failure", "service": "<name>", "trace_id": "<id>"}` |

All errors logged via `slog.Error` with traceID and duration.

---

## Testing

- `ratelimit/bucket_test.go` — unit: fill, exhaust, time-based refill
- `handlers/query_test.go` — table-driven with mock client interfaces; verifies orchestration order, error short-circuit, no partial output on failure
- `handlers/index_test.go` — verifies 202 and Redis XADD payload shape (`repo_path`, `diff_type`, `job_id` fields). The Redis client is injected into the index handler as an interface (a minimal `StreamAdder` interface with a single `XAdd` method), so tests can pass a `miniredis`-backed client or a simple stub without a live Redis.
- `middleware/tracing_test.go` — verifies `X-Trace-Id` header set, traceID injected into context

Thin client wrappers (`clients/`) are covered by the CLAUDE.md integration verification steps rather than unit tests.

---

## Infrastructure

### go.mod
- `go 1.24.0` + `toolchain go1.24.1` (matches serializer, subgraph-expander)
- Key deps: `google.golang.org/grpc v1.79.3`, `google.golang.org/protobuf v1.36.11`, `github.com/google/uuid`, `github.com/redis/go-redis/v9`

### Dockerfile
- `FROM golang:1.24-alpine` → build → minimal runtime image (mirrors subgraph-expander pattern)

### docker-compose.yml changes

1. **Remove `"8090:8090"` host mapping from `serializer`** — the serializer's HTTP health server runs on container-internal port 8090 (gRPC port 8089 + 1) and its healthcheck tests `localhost:8090` within the container; this is unaffected. Only the host port mapping conflicts and must be removed. Keep `"8089:8089"` for gRPC access.

2. **Add `query-understander` service** — currently absent from docker-compose. It listens on port 8080 inside the container; expose as `"8086:8080"` on the host (matching CLAUDE.md). The api-gateway reaches it at `http://query-understander:8080` on the internal docker network.

3. **Add `api-gateway` service**:
   - Port: `"8090:8090"`
   - Environment:
     - `UNDERSTANDER_URL=http://query-understander:8080`
     - `RETRIEVER_ADDR=dual-retriever:8087`
     - `EXPANDER_ADDR=subgraph-expander:8088`
     - `SERIALIZER_ADDR=serializer:8089`
     - `REDIS_URL=redis://redis:6379`
     - `PORT=8090`
   - `depends_on`: query-understander (`condition: service_healthy`), dual-retriever (`condition: service_healthy`), subgraph-expander (`condition: service_healthy`), serializer (`condition: service_healthy`)

---

## Definition of Done

- [ ] `/health` returns 200 with `{"status":"ok","service":"api-gateway","version":"0.1.0"}`
- [ ] `/ready` and `/metrics` endpoints present and returning 200
- [ ] `POST /query` returns streaming plain text context document
- [ ] `X-Trace-Id` header present on every response
- [ ] Trace ID propagated to all downstream calls (visible in their logs)
- [ ] Rate limiting: 429 after 10 requests/minute per repo+IP
- [ ] `POST /index` returns 202 immediately
- [ ] All four query services called in sequence
- [ ] All Go tests pass
- [ ] Dockerfile builds cleanly
- [ ] docker-compose port conflict resolved; query-understander and api-gateway services added
