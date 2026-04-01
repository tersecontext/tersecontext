#!/usr/bin/env bash
# link-repos.sh — sync ~/.tersecontext/repos/ symlinks from ~/.tersecontext/repos.conf
#
# repos.conf format (one entry per line, comments with #):
#   /absolute/path/to/repo
#   ~/relative/path/to/repo          (~ expanded)
#   /path/to/parent/*                 (glob: links every subdir)
#
# Usage:
#   make link-repos          — add/update links
#   make link-repos PRUNE=1  — also remove links not in conf

set -euo pipefail

CONF="${HOME}/.tersecontext/repos.conf"
LINK_DIR="${HOME}/.tersecontext/repos"
PRUNE="${PRUNE:-0}"

# ── Bootstrap ──────────────────────────────────────────────────────────────────

if [[ ! -f "$CONF" ]]; then
    mkdir -p "$(dirname "$CONF")"
    cat > "$CONF" <<'EOF'
# TerseContext repo paths
# One path per line. Glob patterns (e.g. ~/workspaces/*) expand to all subdirs.
# Lines starting with # are ignored.
#
# Examples:
#   ~/workspaces/gastown
#   ~/projects/*
#   /srv/repos/myapp
EOF
    echo "Created $CONF — add your repo paths and re-run."
    exit 0
fi

mkdir -p "$LINK_DIR"

# ── Link ───────────────────────────────────────────────────────────────────────

linked=0
skipped=0
errors=0
# note: use linked=$((linked+1)) not linked=$((linked+1)) — ((x++)) exits 1 when x==0 under set -e

while IFS= read -r raw || [[ -n "$raw" ]]; do
    # strip comments and blank lines
    line="${raw%%#*}"
    line="${line//[$'\t' ]/}"   # trim spaces/tabs
    [[ -z "$line" ]] && continue

    # expand ~ and globs
    eval "paths=( $line )" 2>/dev/null || { echo "  warn: could not expand: $raw"; errors=$((errors+1)); continue; }

    for path in "${paths[@]}"; do
        [[ -d "$path" ]] || { echo "  skip (not a dir): $path"; skipped=$((skipped+1)); continue; }
        name="$(basename "$path")"
        target="$LINK_DIR/$name"

        if [[ -L "$target" ]]; then
            current="$(readlink "$target")"
            if [[ "$current" == "$path" ]]; then
                echo "  ok   $name -> $path"
                linked=$((linked+1))
                continue
            else
                echo "  update $name: $current -> $path"
                ln -sfn "$path" "$target"
                linked=$((linked+1))
            fi
        elif [[ -e "$target" ]]; then
            echo "  warn: $target exists and is not a symlink — skipping"
            skipped=$((skipped+1))
        else
            echo "  link $name -> $path"
            ln -s "$path" "$target"
            linked=$((linked+1))
        fi
    done
done < "$CONF"

# ── Prune ──────────────────────────────────────────────────────────────────────

if [[ "$PRUNE" == "1" ]]; then
    while IFS= read -r -d '' link; do
        name="$(basename "$link")"
        resolved="$(readlink "$link")"
        # check if this target is still in conf
        if ! grep -qE "(^|/)${name}(/\*)?$" "$CONF" 2>/dev/null; then
            # also check via direct path match
            if ! grep -q "$resolved" "$CONF" 2>/dev/null; then
                echo "  prune $name (no longer in conf)"
                rm "$link"
            fi
        fi
    done < <(find "$LINK_DIR" -maxdepth 1 -type l -print0)
fi

echo ""
echo "Done. Linked: $linked  Skipped: $skipped  Errors: $errors"
echo "Repos dir: $LINK_DIR"
ls -la "$LINK_DIR"
