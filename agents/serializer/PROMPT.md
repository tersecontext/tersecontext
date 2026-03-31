You are building the Serializer service for TerseContext. Read CLAUDE.md fully before writing any code.

Your job: take a RankedSubgraph and render the exact six-layer plain-text context document. This is highly testable — write tests first using a hardcoded mock subgraph before touching any real store.

Start here:

1. Write a hardcoded mock RankedSubgraph in Go. Write serializer.go and render all six layers against it. Get the format exactly right before anything else. Compare output character-by-character against the spec in CLAUDE.md.

2. Implement short ID assignment — deterministic, fn:1 cls:1 file:1 etc. Test that the same subgraph always produces the same IDs.

3. Implement Layer 2 warnings — CONFLICT, DEAD, STALE. Test that warnings appear before the node registry.

4. Implement Layer 5 conditional: fetch BehaviorSpec from Postgres per node. If no spec, use code body. If spec is stale, add STALE to warnings.

5. Wire into gRPC server.

6. Write Go tests covering: all six layers, no-warnings case (Layer 2 omitted), stale spec case, mock-only test (no real stores needed).

7. Write Dockerfile.

Run the verification block from CLAUDE.md with grpcurl and confirm all checks pass.
