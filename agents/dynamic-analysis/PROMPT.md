You are building the dynamic analysis pipeline for TerseContext. Read CLAUDE.md fully before writing any code.

There are six services in this pipeline. They can all be built in parallel since they communicate only through shared stores and streams — never through direct calls to each other.

Assign one service per person (or work through them sequentially). Start with trace-normalizer last since it depends on trace-runner being complete.

Suggested order if working solo:
1. entrypoint-discoverer — simplest, just queries Neo4j+Postgres and pushes to Redis queue
2. instrumenter — sys.settrace setup and I/O patching
3. trace-runner — execute entrypoints, emit RawTrace events
4. spec-generator — Postgres schema + spec rendering + Qdrant upsert
5. graph-enricher — dynamic properties + conflict detector in Neo4j
6. trace-normalizer — most complex: reconstruct call tree, compute frequencies, reconcile with static graph

For each service:
1. Read its section in CLAUDE.md carefully
2. Build the core logic as a standalone module first (no FastAPI, no streams)
3. Test the core logic with sample data
4. Add stream consumer/producer
5. Wrap in FastAPI
6. Write tests and Dockerfile
7. Run the verification commands in CLAUDE.md

When all six are done, run the full dynamic analysis cycle:
- POST /discover to entrypoint-discoverer
- Watch trace-runner consume jobs and emit RawTraces
- Watch trace-normalizer emit ExecutionPaths
- Verify graph-enricher updates Neo4j with dynamic properties
- Verify spec-generator populates behavior_specs table
- Run a query through the API gateway — Layer 5 should now show execution paths instead of raw code bodies
