#!/usr/bin/env bash
# install-hook.sh — write a post-commit git hook into a repo
# Called by: make install-hook REPO=<name|path>
#
# REPO can be:
#   - a name under ~/.tersecontext/repos/  (e.g. gastown)
#   - an absolute path                      (e.g. /srv/repos/myapp)
#   - a path relative to ~/.tersecontext/repos/

set -euo pipefail

REPO_ARG="${1:-}"
WATCHER_URL="${WATCHER_URL:-http://localhost:8091}"
LINK_DIR="${HOME}/.tersecontext/repos"

if [[ -z "$REPO_ARG" ]]; then
    echo "Usage: make install-hook REPO=<name|path>" >&2
    exit 1
fi

# Resolve repo path
if [[ -d "$REPO_ARG" ]]; then
    REPO_PATH="$(realpath "$REPO_ARG")"
elif [[ -d "$LINK_DIR/$REPO_ARG" ]]; then
    REPO_PATH="$(realpath "$LINK_DIR/$REPO_ARG")"
else
    echo "Error: cannot find repo '$REPO_ARG'" >&2
    echo "  Tried: $REPO_ARG" >&2
    echo "  Tried: $LINK_DIR/$REPO_ARG" >&2
    exit 1
fi

HOOK_DIR="$REPO_PATH/.git/hooks"
if [[ ! -d "$HOOK_DIR" ]]; then
    echo "Error: $REPO_PATH is not a git repository (no .git/hooks)" >&2
    exit 1
fi

HOOK_PATH="$HOOK_DIR/post-commit"

cat > "$HOOK_PATH" <<EOF
#!/bin/bash
curl -s -X POST ${WATCHER_URL}/hook \\
  -H 'Content-Type: application/json' \\
  -d "{\\"repo_path\\": \\"\$(pwd)\\", \\"commit_sha\\": \\"\$(git rev-parse HEAD)\\"}"
EOF

chmod +x "$HOOK_PATH"

echo "Installed post-commit hook: $HOOK_PATH"
echo "  Watcher URL: $WATCHER_URL"
echo "  Repo:        $REPO_PATH"
