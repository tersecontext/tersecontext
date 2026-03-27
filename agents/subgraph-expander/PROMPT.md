You are building the Subgraph expander service for TerseContext. Read CLAUDE.md fully before writing any code.

This is the most complex query service. Your job: BFS traversal from seed nodes through Neo4j, score every discovered node, prune to token budget, return a RankedSubgraph.

Start here:

1. Write bfs.go first — application-side BFS with one Neo4j query per hop. Test against a real graph: given a seed node, verify nodes at hop 1 and hop 2 are returned correctly.

2. Write scoring.go — implement the scoring formula with decay, edge_type_weights, and provenance_weights exactly as specified in CLAUDE.md. Test that confirmed edges score higher than static-only at the same hop.

3. Write budget.go — token budget estimation and greedy pruning. Test that output never exceeds max_tokens. Test that conflict/dead edges are always included even when budget is exceeded.

4. Wire into gRPC server. Test impact query type follows CALLS in reverse.

5. Write Go tests and Dockerfile.

Run the verification block from CLAUDE.md with grpcurl and confirm all checks pass.
