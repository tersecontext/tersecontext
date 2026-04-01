from __future__ import annotations

from tree_sitter import Node

from .models import IntraFileEdge, ParsedNode


def _find_call_names_go(node: Node) -> list[str]:
    """Walk AST for call_expression nodes; return called function/method names."""
    names: list[str] = []

    def walk(n: Node) -> None:
        if n.type == "call_expression":
            func = n.child_by_field_name("function")
            if func:
                if func.type == "identifier":
                    names.append(func.text.decode("utf-8"))
                elif func.type == "selector_expression":
                    field = func.child_by_field_name("field")
                    if field:
                        names.append(field.text.decode("utf-8"))
        for child in n.children:
            walk(child)

    walk(node)
    return names


def infer_calls_edges_go(
    fn_pairs: list[tuple[ParsedNode, Node]],
    name_to_sid: dict[str, str],
) -> list[IntraFileEdge]:
    """Infer CALLS edges from a list of (ParsedNode, ast_node) pairs for Go."""
    edges: list[IntraFileEdge] = []
    seen_edges: set[tuple[str, str]] = set()
    for pn, ast_node in fn_pairs:
        for called_name in _find_call_names_go(ast_node):
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
