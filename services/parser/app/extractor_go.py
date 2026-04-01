from __future__ import annotations

from tree_sitter import Node

from .extractor import _sha256, _text, stable_id, node_hash
from .models import IntraFileEdge, ParsedNode
from .go_nodes import extract_nodes_go
from .go_edges import infer_calls_edges_go


# ── Main extraction ──────────────────────────────────────────────────────────

def extract_go(
    root: Node,
    source: bytes,
    repo: str,
    file_path: str,
) -> tuple[list[ParsedNode], list[IntraFileEdge]]:
    """Walk the Go AST and return (nodes, edges) for all top-level constructs."""
    nodes, fn_pairs, name_to_sid = extract_nodes_go(root, source, repo, file_path)
    edges = infer_calls_edges_go(fn_pairs, name_to_sid)
    return nodes, edges
