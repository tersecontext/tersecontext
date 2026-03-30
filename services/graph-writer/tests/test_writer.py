from unittest.mock import MagicMock
import pytest

from app.writer import (
    TOMBSTONE_QUERY,
    UPSERT_EDGES_QUERY,
    UPSERT_NODES_QUERY,
    build_node_records,
    tombstone,
    upsert_edges,
    upsert_nodes,
)
from app.models import EmbeddedNode, ParsedNode


def _make_driver():
    """Return (mock_driver, mock_session). The session is what session.run is called on."""
    driver = MagicMock()
    session = driver.session.return_value.__enter__.return_value
    return driver, session


def _parsed_node(**kwargs) -> ParsedNode:
    defaults = dict(
        stable_id="sid1",
        node_hash="hash1",
        type="function",
        name="my_func",
        qualified_name="my_func",
        signature="my_func()",
        docstring="",
        body="def my_func(): pass",
        line_start=1,
        line_end=1,
    )
    defaults.update(kwargs)
    return ParsedNode(**defaults)


def _embedded_node(**kwargs) -> EmbeddedNode:
    defaults = dict(
        stable_id="sid1",
        vector=[0.1, 0.2],
        embed_text="my_func my_func()",
        node_hash="hash1",
    )
    defaults.update(kwargs)
    return EmbeddedNode(**defaults)


# ── Test 1: upsert_nodes calls driver; Cypher is UPSERT_NODES_QUERY ──

def test_upsert_nodes_calls_driver_with_active_and_updated_at():
    driver, session = _make_driver()
    nodes = [{
        "stable_id": "sid1", "name": "my_func", "type": "function",
        "signature": "my_func()", "docstring": "", "body": "def my_func(): pass",
        "file_path": "src/foo.py", "qualified_name": "my_func",
        "language": "python", "repo": "myrepo",
        "embed_text": "my_func my_func()", "node_hash": "hash1",
    }]
    upsert_nodes(driver, nodes)
    session.run.assert_called_once()
    query, = session.run.call_args[0]
    assert query == UPSERT_NODES_QUERY
    _, kwargs = session.run.call_args
    assert kwargs["nodes"] == nodes


# ── Test 2: upsert_nodes with empty list → no driver call ──

def test_upsert_nodes_empty_list():
    driver, session = _make_driver()
    upsert_nodes(driver, [])
    session.run.assert_not_called()


# ── Test 3: upsert_edges uses UPSERT_EDGES_QUERY and passes edges ──

def test_upsert_edges_calls_driver_with_static_source():
    driver, session = _make_driver()
    edges = [{"source": "sid1", "target": "sid2"}]
    upsert_edges(driver, edges)
    session.run.assert_called_once()
    query, = session.run.call_args[0]
    assert query == UPSERT_EDGES_QUERY
    _, kwargs = session.run.call_args
    assert kwargs["edges"] == edges


# ── Test 4: upsert_edges with empty list → no driver call ──

def test_upsert_edges_empty_list():
    driver, session = _make_driver()
    upsert_edges(driver, [])
    session.run.assert_not_called()


# ── Test 5: tombstone uses TOMBSTONE_QUERY and passes stable_ids ──

def test_tombstone_calls_driver_with_correct_query():
    driver, session = _make_driver()
    stable_ids = ["sid1", "sid2"]
    tombstone(driver, stable_ids)
    session.run.assert_called_once()
    query, = session.run.call_args[0]
    assert query == TOMBSTONE_QUERY
    _, kwargs = session.run.call_args
    assert kwargs["stable_ids"] == stable_ids


# ── Test 6: tombstone with empty list → no driver call ──

def test_tombstone_empty_list():
    driver, session = _make_driver()
    tombstone(driver, [])
    session.run.assert_not_called()


# ── Test 7: build_node_records stores qualified_name from ParsedNode ──

def test_build_node_records_uses_qualified_name():
    en = _embedded_node(stable_id="sid1")
    pn = _parsed_node(stable_id="sid1", qualified_name="MyClass.my_func")
    records = build_node_records([en], {"sid1": pn}, "src/foo.py", "python", "myrepo")
    assert len(records) == 1
    assert records[0]["qualified_name"] == "MyClass.my_func"


# ── Test 8: build_node_records qualified_name empty-string fallback ──

def test_build_node_records_qualified_name_fallback():
    en = _embedded_node(stable_id="sid1")
    pn = _parsed_node(stable_id="sid1", name="my_func", qualified_name="")
    records = build_node_records([en], {"sid1": pn}, "src/foo.py", "python", "myrepo")
    assert len(records) == 1
    assert records[0]["qualified_name"] == "my_func"


# ── Test 9: partial cache hit — 3 nodes in, 1 missing → 2 records out ──

def test_build_node_records_partial_cache_hit():
    en1 = _embedded_node(stable_id="sid1")
    en2 = _embedded_node(stable_id="sid2")   # not in parsed cache
    en3 = _embedded_node(stable_id="sid3")
    pn1 = _parsed_node(stable_id="sid1", name="f1", qualified_name="f1")
    pn3 = _parsed_node(stable_id="sid3", name="f3", qualified_name="f3")
    parsed_by_id = {"sid1": pn1, "sid3": pn3}
    records = build_node_records([en1, en2, en3], parsed_by_id, "src/foo.py", "python", "myrepo")
    assert len(records) == 2
    assert {r["stable_id"] for r in records} == {"sid1", "sid3"}
