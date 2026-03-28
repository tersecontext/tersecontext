.PHONY: up down proto verify logs

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
PYTHON_OUT    := services/query-understander/gen

proto:
	mkdir -p $(GO_OUT) $(PYTHON_OUT)
	protoc \
		--proto_path=$(PROTO_DIR) \
		--go_out=$(GO_OUT) --go_opt=paths=source_relative \
		--go-grpc_out=$(GO_OUT) --go-grpc_opt=paths=source_relative \
		$(PROTO_DIR)/*.proto
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
