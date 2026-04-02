from __future__ import annotations
import logging

logger = logging.getLogger(__name__)

RESOLVE_QUERY = """
MATCH (n:Node {repo: $repo, file_path: $file_path, active: true})
WHERE n.start_line <= $line AND n.end_line >= $line
WITH n, (n.end_line - n.start_line) AS span
ORDER BY span ASC
LIMIT 1
RETURN n.stable_id AS stable_id
"""


def resolve_stable_id(driver, repo: str, file_path: str, line: int) -> str | None:
    """Return the stable_id of the narrowest node enclosing file_path:line, or None."""
    with driver.session() as session:
        result = session.run(RESOLVE_QUERY, repo=repo, file_path=file_path, line=line)
        for record in result:
            return record.get("stable_id")
    return None
