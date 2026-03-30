# services/repo-watcher/app/parser.py
import tree_sitter_python as tspython
from tree_sitter import Language, Parser, Node

PARSERS: dict[str, Language] = {
    "python": Language(tspython.language()),
}


def parse(source_bytes: bytes, language: str) -> Node:
    """Parse source bytes for the given language. Returns the root AST node."""
    if language not in PARSERS:
        raise ValueError(f"Unsupported language: {language!r}")
    parser = Parser(PARSERS[language])
    tree = parser.parse(source_bytes)
    if tree.root_node.has_error:
        raise SyntaxError(f"Tree-sitter parse error in {language!r} source")
    return tree.root_node
