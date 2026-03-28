import tree_sitter_python as tspython
from tree_sitter import Language, Parser, Node

PARSERS: dict[str, Language] = {
    "python": Language(tspython.language()),
}


def parse(source_bytes: bytes, language: str) -> Node:
    """Parse source bytes for the given language. Returns the root AST node.

    Raises ValueError for unsupported languages.
    Grammar objects are loaded once at module import — never call this
    function with the intent to reload grammars per file.
    """
    if language not in PARSERS:
        raise ValueError(f"Unsupported language: {language!r}")
    parser = Parser(PARSERS[language])
    tree = parser.parse(source_bytes)
    return tree.root_node
