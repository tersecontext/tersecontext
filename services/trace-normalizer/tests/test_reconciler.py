from unittest.mock import MagicMock


def _make_neo4j_driver(static_edges):
    """
    static_edges: list of (source_name, target_name) tuples
    Returns a mock neo4j driver whose session().run() yields those edges.
    """
    records = [
        {"source": s, "target": t, "source_id": f"sha256:{s}", "target_id": f"sha256:{t}"}
        for s, t in static_edges
    ]
    mock_session = MagicMock()
    mock_session.__enter__ = MagicMock(return_value=mock_session)
    mock_session.__exit__ = MagicMock(return_value=False)
    mock_session.run.return_value = records

    mock_driver = MagicMock()
    mock_driver.session.return_value = mock_session
    return mock_driver


def test_dynamic_only_edge_found_when_not_in_static():
    from app.reconciler import reconcile
    driver = _make_neo4j_driver([("login", "validate_token")])
    observed_fns = {"login", "validate_token", "audit_log"}  # audit_log is new

    dynamic_only, never_observed = reconcile(driver, "test", "sha256:ep_login", observed_fns)

    dynamic_ids = {(e.source, e.target) for e in dynamic_only}
    assert any("audit_log" in t for _, t in dynamic_ids)


def test_never_observed_static_edge_when_target_missing_from_trace():
    from app.reconciler import reconcile
    driver = _make_neo4j_driver([("login", "send_email")])
    observed_fns = {"login"}  # send_email never called

    dynamic_only, never_observed = reconcile(driver, "test", "sha256:ep_login", observed_fns)

    never_ids = {(e.source, e.target) for e in never_observed}
    assert any("send_email" in t for _, t in never_ids)


def test_confirmed_edge_appears_in_neither_list():
    from app.reconciler import reconcile
    driver = _make_neo4j_driver([("login", "validate_token")])
    observed_fns = {"login", "validate_token"}

    dynamic_only, never_observed = reconcile(driver, "test", "sha256:ep_login", observed_fns)

    assert dynamic_only == []
    assert never_observed == []


def test_reconcile_returns_empty_when_no_driver():
    from app.reconciler import reconcile
    dynamic_only, never_observed = reconcile(None, "test", "sha256:ep", {"login"})
    assert dynamic_only == []
    assert never_observed == []
