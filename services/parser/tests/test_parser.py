import pytest
from pathlib import Path

FIXTURES = Path(__file__).parent / "fixtures"


# ── Models ────────────────────────────────────────────────────────────────────

def test_parsed_node_requires_fields():
    from app.models import ParsedNode
    node = ParsedNode(
        stable_id="sha256:abc",
        node_hash="sha256:def",
        type="function",
        name="foo",
        signature="foo()",
        docstring="",
        body="def foo(): pass",
        line_start=1,
        line_end=1,
    )
    assert node.parent_id is None


def test_parsed_file_event_has_deleted_nodes():
    from app.models import ParsedFileEvent
    evt = ParsedFileEvent(
        file_path="a.py",
        language="python",
        repo="repo",
        commit_sha="abc",
        nodes=[],
        intra_file_edges=[],
        deleted_nodes=["sha256:x"],
    )
    assert evt.deleted_nodes == ["sha256:x"]
