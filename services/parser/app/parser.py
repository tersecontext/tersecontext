import tree_sitter_go as tsgo
import tree_sitter_python as tspython
from tree_sitter import Language, Parser, Node

PARSERS: dict[str, Language] = {
    "python": Language(tspython.language()),
    "go": Language(tsgo.language()),
}


def parse(source_bytes: bytes, language: str) -> Node:
    """Parse source bytes for the given language. Returns the root AST node.

    Raises ValueError for unsupported languages.
    Raises SyntaxError if the tree contains parse errors (root.has_error).

    Grammar objects are loaded once at module import (they are expensive).
    A new Parser instance is created per call — this is intentional; Parser
    objects are not safe to share across concurrent calls.
    """
    if language not in PARSERS:
        raise ValueError(f"Unsupported language: {language!r}")
    parser = Parser(PARSERS[language])
    tree = parser.parse(source_bytes)
    if tree.root_node.has_error:
        raise SyntaxError(f"Tree-sitter parse error in {language!r} source")
    return tree.root_node
