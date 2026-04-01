from __future__ import annotations

from typing import Optional
from tree_sitter import Node

from .models import ParsedNode
from .py_stable_id import stable_id, node_hash


def _text(source: bytes, node: Node) -> str:
    return source[node.start_byte:node.end_byte].decode("utf-8")


def _get_docstring(body_node: Optional[Node], source: bytes) -> str:
    """Return the first string literal in a block node, or ''."""
    if body_node is None:
        return ""
    for child in body_node.named_children:
        if child.type == "expression_statement":
            for expr in child.named_children:
                if expr.type == "string":
                    # Prefer string_content child (strips quotes automatically)
                    for sc in expr.named_children:
                        if sc.type == "string_content":
                            return _text(source, sc)
                    # Fallback: strip outer quote markers manually
                    raw = _text(source, expr)
                    for q in ('"""', "'''", '"', "'"):
                        if raw.startswith(q) and raw.endswith(q) and len(raw) >= 2 * len(q):
                            return raw[len(q):-len(q)]
                    return raw
        elif child.type == "comment":
            continue
        else:
            break
    return ""


def _get_signature(fn_node: Node, source: bytes, name: str) -> str:
    """Reconstruct function signature: name(params) -> return_type."""
    params_node = fn_node.child_by_field_name("parameters")
    return_node = fn_node.child_by_field_name("return_type")

    params_text = _text(source, params_node) if params_node else "()"
    sig = name + params_text

    if return_node:
        sig += " -> " + _text(source, return_node)

    return sig


def _get_import_name(node: Node, source: bytes) -> str:
    # Note: for multi-import statements (e.g. "import os, sys"), only the first
    # module name is extracted. Each statement produces one import node.
    if node.type == "import_statement":
        name_node = node.child_by_field_name("name")
        if name_node:
            # aliased_import: pick the dotted_name inside it
            if name_node.type == "aliased_import":
                inner = name_node.child_by_field_name("name")
                return _text(source, inner) if inner else _text(source, name_node)
            return _text(source, name_node)
    elif node.type == "import_from_statement":
        module_node = node.child_by_field_name("module_name")
        if module_node:
            return _text(source, module_node)
    return _text(source, node)


def extract_nodes(
    root: Node,
    source: bytes,
    repo: str,
    file_path: str,
) -> tuple[list[ParsedNode], list[tuple[ParsedNode, Node]], dict[str, str]]:
    """Walk the AST and return (nodes, fn_pairs, name_to_sid).

    fn_pairs is a list of (ParsedNode, ast_node) for functions/methods,
    used by the edge-inference pass.
    name_to_sid maps qualified/simple names to stable_ids for intra-file resolution.
    """
    nodes: list[ParsedNode] = []
    fn_pairs: list[tuple[ParsedNode, Node]] = []
    name_to_sid: dict[str, str] = {}

    for child in root.named_children:
        # ── Imports ──────────────────────────────────────────────────────────
        if child.type in ("import_statement", "import_from_statement"):
            name = _get_import_name(child, source)
            sid = stable_id(repo, file_path, "import", name)
            body = _text(source, child)
            nodes.append(ParsedNode(
                stable_id=sid,
                node_hash=node_hash(name, "", body),
                type="import",
                name=name,
                qualified_name=name,
                signature="",
                docstring="",
                body=body,
                line_start=child.start_point[0] + 1,
                line_end=child.end_point[0] + 1,
            ))

        # ── Classes ───────────────────────────────────────────────────────────
        elif child.type == "class_definition":
            cls_name_node = child.child_by_field_name("name")
            cls_name = _text(source, cls_name_node)
            cls_sid = stable_id(repo, file_path, "class", cls_name)
            cls_body_node = child.child_by_field_name("body")
            cls_docstring = _get_docstring(cls_body_node, source)
            cls_body_text = _text(source, child)

            nodes.append(ParsedNode(
                stable_id=cls_sid,
                node_hash=node_hash(cls_name, "", cls_body_text),
                type="class",
                name=cls_name,
                qualified_name=cls_name,
                signature="",
                docstring=cls_docstring,
                body=cls_body_text,
                line_start=child.start_point[0] + 1,
                line_end=child.end_point[0] + 1,
            ))
            name_to_sid[cls_name] = cls_sid

            # Methods
            if cls_body_node:
                for method in cls_body_node.named_children:
                    if method.type == "function_definition":
                        m_name_node = method.child_by_field_name("name")
                        m_name = _text(source, m_name_node)
                        m_qname = f"{cls_name}.{m_name}"
                        m_sid = stable_id(repo, file_path, "method", m_qname)
                        m_sig = _get_signature(method, source, m_name)
                        m_body_node = method.child_by_field_name("body")
                        m_docstring = _get_docstring(m_body_node, source)
                        m_body_text = _text(source, method)

                        pn = ParsedNode(
                            stable_id=m_sid,
                            node_hash=node_hash(m_name, m_sig, m_body_text),
                            type="method",
                            name=m_name,
                            qualified_name=m_qname,
                            signature=m_sig,
                            docstring=m_docstring,
                            body=m_body_text,
                            line_start=method.start_point[0] + 1,
                            line_end=method.end_point[0] + 1,
                            parent_id=cls_sid,
                        )
                        nodes.append(pn)
                        fn_pairs.append((pn, method))
                        name_to_sid[m_name] = m_sid
                        name_to_sid[m_qname] = m_sid

        # ── Top-level functions ───────────────────────────────────────────────
        elif child.type == "function_definition":
            fn_name_node = child.child_by_field_name("name")
            fn_name = _text(source, fn_name_node)
            fn_sid = stable_id(repo, file_path, "function", fn_name)
            fn_sig = _get_signature(child, source, fn_name)
            fn_body_node = child.child_by_field_name("body")
            fn_docstring = _get_docstring(fn_body_node, source)
            fn_body_text = _text(source, child)

            pn = ParsedNode(
                stable_id=fn_sid,
                node_hash=node_hash(fn_name, fn_sig, fn_body_text),
                type="function",
                name=fn_name,
                qualified_name=fn_name,
                signature=fn_sig,
                docstring=fn_docstring,
                body=fn_body_text,
                line_start=child.start_point[0] + 1,
                line_end=child.end_point[0] + 1,
            )
            nodes.append(pn)
            fn_pairs.append((pn, child))
            name_to_sid[fn_name] = fn_sid

    return nodes, fn_pairs, name_to_sid
