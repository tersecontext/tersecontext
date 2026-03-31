You are building the API gateway service for TerseContext. Read CLAUDE.md fully before writing any code.

This is the last service built and the only one exposed to the host on port 8090. Your job: orchestrate the four query services in sequence and stream the context document back.

Start here:

1. Write the four gRPC clients: understander (HTTP POST), retriever, expander, serializer. Test each client independently against the running services.

2. Wire the orchestration sequence in handlers/query.go: understand → retrieve → expand → serialize. Use context with timeout throughout.

3. Implement streaming: the serializer response should stream as chunked text. Set Transfer-Encoding: chunked and stream io.Copy from the gRPC stream.

4. Add X-Trace-Id header generation and propagation as gRPC metadata to all downstream calls.

5. Implement rate limiting: 10 requests/minute per repo+IP in memory.

6. Add POST /index endpoint.

7. Write Go tests and Dockerfile.

Run the full end-to-end test from the verification block: POST /query and verify a streaming six-layer context document comes back with X-Trace-Id header.
