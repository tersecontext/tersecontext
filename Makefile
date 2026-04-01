.PHONY: up down proto verify logs ps demo-up demo link-repos install-hook

# ── Infrastructure ─────────────────────────────────────────────────────────────

up:
	mkdir -p data/neo4j data/qdrant data/redis data/postgres data/ollama
	docker compose up -d

down:
	docker compose down

logs:
	docker compose logs -f

# ── Proto generation ───────────────────────────────────────────────────────────

PROTO_DIR     := proto
GO_OUT        := services/dual-retriever/gen
SERIALIZER_GO_OUT := services/serializer/gen
PYTHON_OUT    := services/query-understander/gen

proto:
	mkdir -p $(GO_OUT) $(SERIALIZER_GO_OUT) $(PYTHON_OUT)
	protoc \
		--proto_path=$(PROTO_DIR) \
		--go_out=$(GO_OUT) --go_opt=paths=source_relative \
		--go-grpc_out=$(GO_OUT) --go-grpc_opt=paths=source_relative \
		$(PROTO_DIR)/query.proto
	protoc \
		--proto_path=$(PROTO_DIR) \
		--go_out=$(SERIALIZER_GO_OUT) --go_opt=paths=source_relative \
		--go-grpc_out=$(SERIALIZER_GO_OUT) --go-grpc_opt=paths=source_relative \
		$(PROTO_DIR)/query.proto
	python -m grpc_tools.protoc \
		--proto_path=$(PROTO_DIR) \
		--python_out=$(PYTHON_OUT) \
		--grpc_python_out=$(PYTHON_OUT) \
		$(PROTO_DIR)/*.proto

# ── Verification ───────────────────────────────────────────────────────────────

verify:
	@echo "--- neo4j ---"
	@curl -sf http://localhost:7474 > /dev/null && echo "neo4j browser: OK" || echo "neo4j browser: FAIL"
	@curl -sf -u neo4j:localpassword \
		http://localhost:7474/db/neo4j/tx/commit \
		-H 'Content-Type: application/json' \
		-d '{"statements":[{"statement":"RETURN 1"}]}' | grep -q '"row":\[1\]' \
		&& echo "neo4j query: OK" || echo "neo4j query: FAIL"
	@echo "--- qdrant ---"
	@curl -sf http://localhost:6333/healthz | grep -q 'passed' \
		&& echo "qdrant: OK" || echo "qdrant: FAIL"
	@echo "--- constraints ---"
	@curl -sf -u neo4j:localpassword \
		http://localhost:7474/db/neo4j/tx/commit \
		-H 'Content-Type: application/json' \
		-d '{"statements":[{"statement":"SHOW CONSTRAINTS"}]}' | grep -q 'stable_id_unique' \
		&& echo "stable_id_unique constraint: OK" || echo "stable_id_unique constraint: FAIL"

ps:
	docker compose ps

# ── Demo ────────────────────────────────────────────────────────────────────────

demo-up:
	mkdir -p data/neo4j data/qdrant data/redis data/postgres data/ollama
	docker compose -f docker-compose.yml -f docker-compose.demo.yml up -d

demo: demo-up
	@bash scripts/demo.sh

# ── Repo management ─────────────────────────────────────────────────────────────

link-repos:
	@bash scripts/link-repos.sh

# Install a post-commit git hook in a repo so the watcher is notified on commits.
# Usage: make install-hook REPO=gastown
#        make install-hook REPO=/absolute/path/to/repo
#        WATCHER_URL=http://myhost:8091 make install-hook REPO=gastown
install-hook:
	@test -n "$(REPO)" || (echo "Usage: make install-hook REPO=<name|path>" && exit 1)
	@bash scripts/install-hook.sh "$(REPO)"
