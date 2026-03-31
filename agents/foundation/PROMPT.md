You are building the Foundation phase of TerseContext. Read CLAUDE.md fully before writing any code.

Your first task is to get docker-compose up and running with all five stores healthy, then build out the contracts and proto files.

Start here:

1. Create docker-compose.yml with neo4j:5, qdrant/qdrant, redis:7, postgres:16, ollama/ollama.
   - Only expose ports for neo4j (7474, 7687), qdrant (6333), ollama (11434) to the host.
   - Redis and Postgres are internal only.
   - All on a bridge network named "tersecontext".
   - Mount data volumes under ./data/

2. Run `docker-compose up -d` and confirm all five containers reach healthy state.

3. Create contracts/ with all JSON schemas defined in CLAUDE.md. Validate each one with jsonschema.

4. Create proto/ files and a `make proto` Makefile target that generates Go and Python stubs.

5. Create docker/neo4j-init.cypher and wire it into docker-compose so constraints are applied on first start.

At each step, verify before moving to the next. Do not move on if a step is failing.

When everything is done, run the full verification block from the bottom of CLAUDE.md and paste the output as a summary so the worktree can be reviewed and approved.
