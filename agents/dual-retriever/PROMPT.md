You are building the Dual retriever service for TerseContext. Read CLAUDE.md fully before writing any code.

This is the first Go service. Your job: receive a QueryIntent, fan out to Qdrant and Neo4j in parallel using goroutines, merge results with RRF, return top seed nodes.

Start here:

1. Implement the gRPC server stub from the proto files generated in Foundation.

2. Write qdrant.go — search the nodes collection using the embed_query vector (call POST /embed on the embedder service to get the vector first).

3. Write neo4j.go — full-text index search using keywords and symbols from QueryIntent.

4. Wire both into goroutines with a WaitGroup and 500ms timeout in retriever.go.

5. Implement rrf.go — the Reciprocal Rank Fusion merge with k=60. Test that a node in both result sets ranks higher than one in only one set.

6. Write Go tests and Dockerfile.

Run the verification block from CLAUDE.md with grpcurl and confirm all checks pass.
