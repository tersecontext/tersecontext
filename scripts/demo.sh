#!/usr/bin/env bash
# scripts/demo.sh — index the bundled demo repo and run canned queries.
# Requires: services running (make demo-up starts them with the demo compose override).
set -euo pipefail

GATEWAY_URL="${GATEWAY_URL:-http://localhost:8090}"
REPO_WATCHER_URL="${REPO_WATCHER_URL:-http://localhost:8091}"
DEMO_REPO_PATH="${DEMO_REPO_PATH:-/repos/demo-repo}"
NEO4J_URL="${NEO4J_URL:-http://localhost:7474}"
NEO4J_AUTH="${NEO4J_AUTH:-neo4j:localpassword}"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
QUERIES_FILE="$SCRIPT_DIR/../demo/queries.txt"

# ── 0. Ensure demo/sample_repo is a git repo (required by repo-watcher) ──────
DEMO_REPO_HOST="$SCRIPT_DIR/../demo/sample_repo"
if [ ! -d "$DEMO_REPO_HOST/.git" ]; then
  echo "📝  Initializing demo git repo (first run)..."
  git -C "$DEMO_REPO_HOST" init
  git -C "$DEMO_REPO_HOST" config user.email "demo@tersecontext.local"
  git -C "$DEMO_REPO_HOST" config user.name "TerseContext Demo"
  git -C "$DEMO_REPO_HOST" add .
  git -C "$DEMO_REPO_HOST" commit -m "demo project"
fi

# ── 1. Wait for api-gateway and repo-watcher to be ready ─────────────────────
echo "Waiting for services to be healthy..."
for svc in "$GATEWAY_URL/health" "$REPO_WATCHER_URL/health"; do
  for i in $(seq 1 30); do
    if curl -sf "$svc" > /dev/null 2>&1; then
      break
    fi
    if [ "$i" -eq 30 ]; then
      echo "Service not ready: $svc"
      echo "Run 'make demo-up' first, then retry 'make demo'."
      exit 1
    fi
    sleep 2
  done
done
echo "Services healthy."

# ── 2. Index the demo repo ────────────────────────────────────────────────────
echo ""
echo "Indexing demo repo ($DEMO_REPO_PATH)..."
INDEX_RESP=$(curl -sf -X POST "$REPO_WATCHER_URL/index" \
  -H 'Content-Type: application/json' \
  -d "{\"repo_path\":\"$DEMO_REPO_PATH\",\"full_rescan\":true}" 2>&1) || {
    echo "Failed to trigger index: $INDEX_RESP"
    exit 1
}
echo "  $INDEX_RESP"

# ── 3. Wait for indexing to settle (poll Neo4j node count) ────────────────────
echo ""
echo "Waiting for indexing to complete (up to 60s)..."
for i in $(seq 1 30); do
  COUNT=$(curl -sf -u "$NEO4J_AUTH" \
    "$NEO4J_URL/db/neo4j/tx/commit" \
    -H 'Content-Type: application/json' \
    -d '{"statements":[{"statement":"MATCH (n:Node {repo:\"demo-repo\"}) RETURN count(n) as c"}]}' \
    2>/dev/null | python3 -c "import sys,json; d=json.load(sys.stdin); print(d['results'][0]['data'][0]['row'][0])" 2>/dev/null || echo 0)
  if [ "$COUNT" -gt 3 ]; then
    echo "$COUNT nodes indexed."
    break
  fi
  if [ "$i" -eq 30 ]; then
    echo "Indexing may still be in progress ($COUNT nodes so far). Proceeding with queries..."
  fi
  sleep 2
done

# ── 4. Run canned queries and print output ────────────────────────────────────
echo ""
echo "================================================================"
echo "  TerseContext Demo Queries"
echo "================================================================"

while IFS= read -r QUERY || [ -n "$QUERY" ]; do
  [ -z "$QUERY" ] && continue
  echo ""
  echo ">> $QUERY"
  echo "----------------------------------------------------------------"
  BODY=$(python3 -c "import json,sys; q=sys.argv[1]; print(json.dumps({'repo':'demo-repo','question':q,'options':{'max_tokens':2000}}))" "$QUERY")
  curl -sf -X POST "$GATEWAY_URL/query" \
    -H 'Content-Type: application/json' \
    -d "$BODY" \
    || echo "(no results — indexing may not be complete yet)"
  echo ""
done < "$QUERIES_FILE"

echo "================================================================"
echo "  Demo complete. Run your own queries:"
echo "  curl -X POST http://localhost:8090/query \\"
echo "    -H 'Content-Type: application/json' \\"
echo "    -d '{\"repo\":\"demo-repo\",\"question\":\"your question here\"}'"
echo "================================================================"
