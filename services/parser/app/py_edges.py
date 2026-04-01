from __future__ import annotations

from tree_sitter import Node

from .models import IntraFileEdge, ParsedNode


def _find_call_names(fn_node: Node) -> list[str]:
    """Walk AST for call nodes; return list of called names (bare or self.method)."""
    names: list[str] = []

    def walk(n: Node) -> None:
        if n.type == "call":
            func = n.child_by_field_name("function")
            if func:
                if func.type == "identifier":
                    names.append(func.text.decode("utf-8"))
                elif func.type == "attribute":
                    obj = func.child_by_field_name("object")
                    attr = func.child_by_field_name("attribute")
                    if obj and attr and obj.text == b"self":
                        names.append(attr.text.decode("utf-8"))
        for child in n.children:
            walk(child)

    walk(fn_node)
    return names


def infer_calls_edges(
    fn_pairs: list[tuple[ParsedNode, Node]],
    name_to_sid: dict[str, str],
) -> list[IntraFileEdge]:
    """Infer CALLS edges from a list of (ParsedNode, ast_node) pairs."""
    edges: list[IntraFileEdge] = []
    seen_edges: set[tuple[str, str]] = set()
    for pn, ast_node in fn_pairs:
        for called_name in _find_call_names(ast_node):
            target_sid = name_to_sid.get(called_name)
            if target_sid and target_sid != pn.stable_id:
                key = (pn.stable_id, target_sid)
                if key not in seen_edges:
                    seen_edges.add(key)
                    edges.append(IntraFileEdge(
                        source_stable_id=pn.stable_id,
                        target_stable_id=target_sid,
                        type="CALLS",
                    ))
    return edges
