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


# ── Neo4j query constants ──────────────────────────────────────────────────────

LOOKUP_EXACT_QUERY = """
MATCH (n:Node {repo: $repo, qualified_name: $name, active: true})
RETURN n.stable_id AS stable_id LIMIT 1
"""

LOOKUP_PATH_QUERY = """
MATCH (n:Node {repo: $repo, name: $name, active: true})
WHERE n.file_path CONTAINS $path_hint
RETURN n.stable_id AS stable_id LIMIT 1
"""

WRITE_IMPORT_EDGE_QUERY = """
MATCH (a:Node {stable_id: $importer_id})
MATCH (b:Node {stable_id: $imported_id})
MERGE (a)-[r:IMPORTS]->(b)
SET r.source = 'static', r.updated_at = datetime()
RETURN count(r) AS c
"""

WRITE_PACKAGE_EDGE_QUERY = """
MATCH (a:Node {stable_id: $importer_id})
MERGE (pkg:Package {name: $pkg_name, repo: $repo})
MERGE (a)-[r:IMPORTS]->(pkg)
SET r.source = 'static', r.updated_at = datetime()
RETURN count(r) AS c
"""


# ── Neo4j write functions ──────────────────────────────────────────────────────

def resolve_import(driver, importer_stable_id: str, target_name: str, path_hint: str, repo: str) -> bool:
    """
    Attempt to write an IMPORTS edge from the import node to the target symbol.

    Strategy:
      1. Try exact qualified_name lookup in Neo4j.
      2. If miss, try name + file_path CONTAINS path_hint.
      3. If target found, write the edge. Returns True if edge written, False otherwise.

    Returns False if:
      - target not found (both lookups miss)
      - importer node not yet in Neo4j (MATCH in write query returns 0 rows)
    """
    with driver.session() as session:
        # Attempt 1: exact qualified_name match
        rows = session.run(LOOKUP_EXACT_QUERY, repo=repo, name=target_name).data()
        if not rows and path_hint:
            # Attempt 2: name + file path hint
            rows = session.run(LOOKUP_PATH_QUERY, repo=repo, name=target_name, path_hint=path_hint).data()

        if not rows:
            return False

        imported_id = rows[0]["stable_id"]
        rows = session.run(
            WRITE_IMPORT_EDGE_QUERY,
            importer_id=importer_stable_id,
            imported_id=imported_id,
        ).data()
        return bool(rows) and rows[0]["c"] > 0


def write_package_edge(driver, importer_stable_id: str, pkg_name: str, repo: str) -> bool:
    """
    MERGE a Package node and write an IMPORTS edge from the import node to it.
    Returns True if the edge was written (importer node existed), False otherwise.
    Package nodes: no embed_text or vectors.
    """
    with driver.session() as session:
        rows = session.run(
            WRITE_PACKAGE_EDGE_QUERY,
            importer_id=importer_stable_id,
            pkg_name=pkg_name,
            repo=repo,
        ).data()
        return bool(rows) and rows[0]["c"] > 0
