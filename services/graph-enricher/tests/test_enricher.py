# services/graph-enricher/tests/test_enricher.py
from unittest.mock import MagicMock
import pytest

from app.enricher import (
    UPDATE_NODE_PROPS_QUERY,
    UPSERT_DYNAMIC_EDGES_QUERY,
    CONFIRM_STATIC_EDGES_QUERY,
    CONFLICT_DETECTOR_QUERY,
    STALENESS_DOWNGRADE_QUERY,
    update_node_props_batch,
    upsert_dynamic_edges,
    confirm_static_edges,
    run_conflict_detector,
    run_staleness_downgrade,
)


def _make_driver():
    driver = MagicMock()
    session = driver.session.return_value.__enter__.return_value
    return driver, session


# ── Test 1: update_node_props_batch calls driver with UPDATE_NODE_PROPS_QUERY ──

def test_update_node_props_batch_calls_driver():
    driver, session = _make_driver()
    records = [{"stable_id": "s1", "avg_latency_ms": 10.0, "branch_coverage": 0.8}]
    update_node_props_batch(driver, records)
    session.run.assert_called_once()
    query, = session.run.call_args[0]
    assert query == UPDATE_NODE_PROPS_QUERY
    _, kwargs = session.run.call_args
    assert kwargs["nodes"] == records


# ── Test 2: update_node_props_batch with empty list → no driver call ──

def test_update_node_props_batch_empty():
    driver, session = _make_driver()
    update_node_props_batch(driver, [])
    session.run.assert_not_called()


# ── Test 3: upsert_dynamic_edges calls driver with UPSERT_DYNAMIC_EDGES_QUERY ──

def test_upsert_dynamic_edges_calls_driver():
    driver, session = _make_driver()
    edges = [{"source": "s1", "target": "s2", "count": 3}]
    upsert_dynamic_edges(driver, edges)
    session.run.assert_called_once()
    query, = session.run.call_args[0]
    assert query == UPSERT_DYNAMIC_EDGES_QUERY
    _, kwargs = session.run.call_args
    assert kwargs["edges"] == edges


# ── Test 4: upsert_dynamic_edges with empty list → no driver call ──

def test_upsert_dynamic_edges_empty():
    driver, session = _make_driver()
    upsert_dynamic_edges(driver, [])
    session.run.assert_not_called()


# ── Test 5: confirm_static_edges calls driver with CONFIRM_STATIC_EDGES_QUERY ──

def test_confirm_static_edges_calls_driver():
    driver, session = _make_driver()
    stable_ids = ["s1", "s2", "s3"]
    confirm_static_edges(driver, stable_ids)
    session.run.assert_called_once()
    query, = session.run.call_args[0]
    assert query == CONFIRM_STATIC_EDGES_QUERY
    _, kwargs = session.run.call_args
    assert kwargs["stable_ids"] == stable_ids


# ── Test 6: confirm_static_edges with empty list → no driver call ──

def test_confirm_static_edges_empty():
    driver, session = _make_driver()
    confirm_static_edges(driver, [])
    session.run.assert_not_called()


# ── Test 7: run_conflict_detector calls driver with CONFLICT_DETECTOR_QUERY ──

def test_run_conflict_detector_calls_driver():
    driver, session = _make_driver()
    run_conflict_detector(driver)
    session.run.assert_called_once()
    query, = session.run.call_args[0]
    assert query == CONFLICT_DETECTOR_QUERY


# ── Test 8: run_staleness_downgrade calls driver with STALENESS_DOWNGRADE_QUERY ──

def test_run_staleness_downgrade_calls_driver():
    driver, session = _make_driver()
    run_staleness_downgrade(driver)
    session.run.assert_called_once()
    query, = session.run.call_args[0]
    assert query == STALENESS_DOWNGRADE_QUERY
