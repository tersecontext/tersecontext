# app/postgres_client.py
from __future__ import annotations
from datetime import datetime

LAST_TRACED_QUERY = """
SELECT node_stable_id, MAX(generated_at) AS last_traced
FROM behavior_specs
WHERE repo = %s
GROUP BY node_stable_id
"""


def query_last_traced(conn, repo: str) -> dict[str, datetime]:
    """
    Return a map of stable_id -> last traced datetime for the given repo.
    Returns empty dict if behavior_specs has no rows for this repo.
    """
    with conn.cursor() as cur:
        cur.execute(LAST_TRACED_QUERY, (repo,))
        rows = cur.fetchall()
    return {row[0]: row[1] for row in rows}
