from __future__ import annotations

from tree_sitter import Node

from .models import FileChangedEvent, IntraFileEdge, ParsedNode
from .py_stable_id import _sha256, stable_id, node_hash
from .py_nodes import _text, extract_nodes
from .py_edges import infer_calls_edges


# ── Main extraction ────────────────────────────────────────────────────────────

def extract(
    root: Node,
    source: bytes,
    repo: str,
    file_path: str,
) -> tuple[list[ParsedNode], list[IntraFileEdge]]:
    """Walk the AST and return (nodes, edges) for all top-level constructs."""
    nodes, fn_pairs, name_to_sid = extract_nodes(root, source, repo, file_path)
    edges = infer_calls_edges(fn_pairs, name_to_sid)
    return nodes, edges


# ── diff_type filtering ────────────────────────────────────────────────────────

def apply_diff_filter(
    nodes: list[ParsedNode],
    edges: list[IntraFileEdge],
    event: FileChangedEvent,
) -> tuple[list[ParsedNode], list[IntraFileEdge]]:
    """Filter nodes and edges based on diff_type.

    For diff_type='deleted', returns ([], []).
    IMPORTANT: the caller (consumer) must still emit a ParsedFileEvent with
    deleted_nodes forwarded unchanged from the input event. Returning ([], [])
    does NOT mean skip emitting — the graph-writer reads deleted_nodes.
    """
    if event.diff_type in ("full_rescan", "added"):
        return nodes, edges
    elif event.diff_type == "deleted":
        return [], []
    elif event.diff_type == "modified":
        emit_set = set(event.changed_nodes) | set(event.added_nodes)
        filtered_nodes = [n for n in nodes if n.stable_id in emit_set]
        filtered_sids = {n.stable_id for n in filtered_nodes}
        filtered_edges = [e for e in edges if e.source_stable_id in filtered_sids]
        return filtered_nodes, filtered_edges
    else:
        # Unknown diff_type — emit all
        return nodes, edges
