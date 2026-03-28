# Issue: diff_type=modified may miss CALLS edges from unchanged nodes

**Raised:** 2026-03-28
**Status:** Open — upstream concern, not a parser bug

## Description

When `diff_type=modified`, the parser only emits nodes listed in `changed_nodes + added_nodes`.
`intra_file_edges` are filtered to only edges where the source node is in the emitted set.

This means: if node A (unchanged) calls node B (newly added), the `A→B` CALLS edge will NOT
be emitted — because A is not in `changed_nodes`.

## Why it matters

The graph-writer downstream will have node B in the graph but no edge from A to B.
The call relationship is silently missing.

## Root cause

The parser trusts the `changed_nodes` list from the upstream repo-watcher/diff generator.
If the diff generator does not include A in `changed_nodes` when A's effective call graph
changes (e.g. a new callee is added to the file), the edge is lost.

## Resolution path

This is an **upstream responsibility**. The repo-watcher or diff generator must include
any node in `changed_nodes` whose reachable call graph has changed — not just nodes whose
source text changed.

Alternatively, a periodic `full_rescan` will correct any accumulated drift.

## Affected services

- repo-watcher (or whatever generates FileChanged events)
- graph-writer (consumer of ParsedFile events)
