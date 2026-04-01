#!/usr/bin/env bash
# add-repo.sh — add a repo to repos.conf, sync links, and install the git hook
# Usage: make add-repo REPO=/path/to/repo

set -euo pipefail

REPO_ARG="${1:-}"
CONF="${HOME}/.tersecontext/repos.conf"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if [[ -z "$REPO_ARG" ]]; then
    echo "Usage: make add-repo REPO=<path>" >&2
    exit 1
fi

# Expand ~ and resolve to absolute path
eval "REPO_PATH=$REPO_ARG"
REPO_PATH="$(realpath "$REPO_PATH")"

if [[ ! -d "$REPO_PATH" ]]; then
    echo "Error: directory not found: $REPO_PATH" >&2
    exit 1
fi

# Bootstrap conf if missing
if [[ ! -f "$CONF" ]]; then
    mkdir -p "$(dirname "$CONF")"
    touch "$CONF"
fi

# Append if not already present
if grep -qxF "$REPO_PATH" "$CONF" 2>/dev/null; then
    echo "Already in repos.conf: $REPO_PATH"
else
    echo "$REPO_PATH" >> "$CONF"
    echo "Added to repos.conf: $REPO_PATH"
fi

# Sync symlinks
bash "$SCRIPT_DIR/link-repos.sh"

# Install git hook
bash "$SCRIPT_DIR/install-hook.sh" "$REPO_PATH"
