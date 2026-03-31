from __future__ import annotations

from tree_sitter import Node

from .extractor import _sha256, _text, stable_id, node_hash
from .models import IntraFileEdge, ParsedNode


# ── Go comment / docstring extraction ─────────────────────────────────────────

def _get_go_doc(node: Node, source: bytes) -> str:
    """Return the comment block immediately preceding a declaration node."""
    lines: list[str] = []
    sibling = node.prev_named_sibling
    # Walk backwards collecting adjacent comment lines
    while sibling and sibling.type == "comment":
        text = _text(source, sibling)
        # Strip leading // and optional space
        if text.startswith("//"):
            text = text[2:].lstrip(" ")
        lines.append(text)
        sibling = sibling.prev_named_sibling
    lines.reverse()
    return "\n".join(lines)


# ── Go signature reconstruction ───────────────────────────────────────────────

def _get_func_signature(node: Node, source: bytes, name: str) -> str:
    """Reconstruct Go function signature: func name(params) returns."""
    params_node = node.child_by_field_name("parameters")
    result_node = node.child_by_field_name("result")

    params_text = _text(source, params_node) if params_node else "()"
    sig = f"func {name}{params_text}"

    if result_node:
        sig += " " + _text(source, result_node)

    return sig


def _get_method_signature(node: Node, source: bytes, name: str) -> str:
    """Reconstruct Go method signature: func (recv) name(params) returns."""
    receiver_node = node.child_by_field_name("receiver")
    params_node = node.child_by_field_name("parameters")
    result_node = node.child_by_field_name("result")

    recv_text = _text(source, receiver_node) if receiver_node else "()"
    params_text = _text(source, params_node) if params_node else "()"
    sig = f"func {recv_text} {name}{params_text}"

    if result_node:
        sig += " " + _text(source, result_node)

    return sig


# ── Receiver type extraction ──────────────────────────────────────────────────

def _get_receiver_type(node: Node, source: bytes) -> str | None:
    """Extract the receiver type name from a method declaration."""
    receiver = node.child_by_field_name("receiver")
    if receiver is None:
        return None
    # parameter_list → parameter_declaration → type
    for param in receiver.named_children:
        if param.type == "parameter_declaration":
            type_node = param.child_by_field_name("type")
            if type_node:
                # Could be pointer_type (*Config) or identifier (Config)
                if type_node.type == "pointer_type":
                    for c in type_node.named_children:
                        if c.type == "type_identifier":
                            return _text(source, c)
                elif type_node.type == "type_identifier":
                    return _text(source, type_node)
    return None


# ── Import extraction ─────────────────────────────────────────────────────────

def _extract_imports(node: Node, source: bytes, repo: str, file_path: str) -> list[ParsedNode]:
    """Extract import declarations (both single and grouped)."""
    nodes: list[ParsedNode] = []

    if node.type == "import_declaration":
        body = _text(source, node)
        for child in node.named_children:
            if child.type == "import_spec":
                path_node = child.child_by_field_name("path")
                if path_node:
                    name = _text(source, path_node).strip('"')
                    sid = stable_id(repo, file_path, "import", name)
                    nodes.append(ParsedNode(
                        stable_id=sid,
                        node_hash=node_hash(name, "", body),
                        type="import",
                        name=name,
                        qualified_name=name,
                        signature="",
                        docstring="",
                        body=_text(source, child),
                        line_start=child.start_point[0] + 1,
                        line_end=child.end_point[0] + 1,
                    ))
            elif child.type == "import_spec_list":
                for spec in child.named_children:
                    if spec.type == "import_spec":
                        path_node = spec.child_by_field_name("path")
                        if path_node:
                            name = _text(source, path_node).strip('"')
                            sid = stable_id(repo, file_path, "import", name)
                            nodes.append(ParsedNode(
                                stable_id=sid,
                                node_hash=node_hash(name, "", _text(source, spec)),
                                type="import",
                                name=name,
                                qualified_name=name,
                                signature="",
                                docstring="",
                                body=_text(source, spec),
                                line_start=spec.start_point[0] + 1,
                                line_end=spec.end_point[0] + 1,
                            ))

    return nodes


# ── CALLS edge detection ─────────────────────────────────────────────────────

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


# ── Type spec extraction (structs / interfaces) ──────────────────────────────

def _extract_type_decl(
    node: Node, source: bytes, repo: str, file_path: str,
) -> list[ParsedNode]:
    """Extract struct and interface type declarations."""
    nodes: list[ParsedNode] = []

    for child in node.named_children:
        if child.type == "type_spec":
            name_node = child.child_by_field_name("name")
            type_node = child.child_by_field_name("type")
            if not name_node or not type_node:
                continue

            name = _text(source, name_node)

            if type_node.type == "struct_type":
                node_type = "struct"
            elif type_node.type == "interface_type":
                node_type = "interface"
            else:
                continue

            sid = stable_id(repo, file_path, node_type, name)
            body = _text(source, child)
            docstring = _get_go_doc(node, source)

            nodes.append(ParsedNode(
                stable_id=sid,
                node_hash=node_hash(name, "", body),
                type=node_type,
                name=name,
                qualified_name=name,
                signature="",
                docstring=docstring,
                body=body,
                line_start=child.start_point[0] + 1,
                line_end=child.end_point[0] + 1,
            ))

    return nodes


# ── Main extraction ──────────────────────────────────────────────────────────

def extract_go(
    root: Node,
    source: bytes,
    repo: str,
    file_path: str,
) -> tuple[list[ParsedNode], list[IntraFileEdge]]:
    """Walk the Go AST and return (nodes, edges) for all top-level constructs."""
    nodes: list[ParsedNode] = []
    fn_pairs: list[tuple[ParsedNode, Node]] = []
    name_to_sid: dict[str, str] = {}
    # Track struct stable_ids for parent_id on methods
    struct_name_to_sid: dict[str, str] = {}

    for child in root.named_children:
        # ── Imports ──────────────────────────────────────────────────────
        if child.type == "import_declaration":
            nodes.extend(_extract_imports(child, source, repo, file_path))

        # ── Type declarations (struct / interface) ───────────────────────
        elif child.type == "type_declaration":
            type_nodes = _extract_type_decl(child, source, repo, file_path)
            for tn in type_nodes:
                nodes.append(tn)
                name_to_sid[tn.name] = tn.stable_id
                if tn.type == "struct":
                    struct_name_to_sid[tn.name] = tn.stable_id

        # ── Functions ────────────────────────────────────────────────────
        elif child.type == "function_declaration":
            fn_name_node = child.child_by_field_name("name")
            if not fn_name_node:
                continue
            fn_name = _text(source, fn_name_node)
            fn_sid = stable_id(repo, file_path, "function", fn_name)
            fn_sig = _get_func_signature(child, source, fn_name)
            fn_docstring = _get_go_doc(child, source)
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

        # ── Methods (functions with receivers) ───────────────────────────
        elif child.type == "method_declaration":
            m_name_node = child.child_by_field_name("name")
            if not m_name_node:
                continue
            m_name = _text(source, m_name_node)
            recv_type = _get_receiver_type(child, source)
            m_qname = f"{recv_type}.{m_name}" if recv_type else m_name
            m_sid = stable_id(repo, file_path, "method", m_qname)
            m_sig = _get_method_signature(child, source, m_name)
            m_docstring = _get_go_doc(child, source)
            m_body_text = _text(source, child)

            parent_sid = struct_name_to_sid.get(recv_type) if recv_type else None

            pn = ParsedNode(
                stable_id=m_sid,
                node_hash=node_hash(m_name, m_sig, m_body_text),
                type="method",
                name=m_name,
                qualified_name=m_qname,
                signature=m_sig,
                docstring=m_docstring,
                body=m_body_text,
                line_start=child.start_point[0] + 1,
                line_end=child.end_point[0] + 1,
                parent_id=parent_sid,
            )
            nodes.append(pn)
            fn_pairs.append((pn, child))
            name_to_sid[m_name] = m_sid
            name_to_sid[m_qname] = m_sid

    # ── CALLS edges ──────────────────────────────────────────────────────
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

    return nodes, edges
