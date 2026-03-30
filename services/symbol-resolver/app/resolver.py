# services/symbol-resolver/app/resolver.py
from __future__ import annotations
import ast
import logging
from pathlib import PurePosixPath

logger = logging.getLogger(__name__)


# ── Import parsing ─────────────────────────────────────────────────────────────

def parse_import_body(body: str) -> dict:
    """
    Parse an import statement and return a dict describing it.

    Returns one of:
      {"kind": "from",  "module": "auth.service", "dots": 0, "names": ["AuthService"]}
      {"kind": "plain", "names": ["os"]}   — for bare 'import X' statements

    For aliased imports ('from x import Y as Z'), names contains Y (.name), not Z (.asname).
    For multi-module plain imports ('import os, sys'), names contains both.
    For multi-symbol from-imports ('from x import A, B'), names contains both.
    """
    try:
        tree = ast.parse(body.strip(), mode="single")
    except SyntaxError:
        logger.warning("Could not parse import body: %r", body)
        return {"kind": "plain", "names": []}

    if not tree.body:
        return {"kind": "plain", "names": []}

    stmt = tree.body[0]
    if isinstance(stmt, ast.ImportFrom):
        names = [alias.name for alias in stmt.names]
        return {
            "kind": "from",
            "module": stmt.module or "",
            "dots": stmt.level,
            "names": names,
        }
    if isinstance(stmt, ast.Import):
        names = [alias.name for alias in stmt.names]
        return {"kind": "plain", "names": names}

    return {"kind": "plain", "names": []}


def compute_path_hint(module: str, file_path: str, dots: int) -> str:
    """
    Convert a module name and relative-import level into a slash-form path hint
    suitable for Neo4j CONTAINS queries.

    dots=0  (absolute): "auth.service"  → "auth/service"
    dots=1  (relative): strip filename, use parent dir. "auth/service.py" → "auth/"
    dots=2+: strip filename + (dots-1) more components.
    """
    if dots == 0:
        return module.replace(".", "/")

    # Relative import: start from the importing file's directory.
    parts = PurePosixPath(file_path).parts  # e.g. ("auth", "service.py")
    # Strip filename
    dir_parts = list(parts[:-1])
    # Strip (dots - 1) more directory components for each additional dot
    for _ in range(dots - 1):
        if dir_parts:
            dir_parts.pop()
    if not dir_parts:
        return ""
    return "/".join(dir_parts) + "/"
