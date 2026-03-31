# tests/test_neo4j_client.py
from datetime import datetime, timezone
from unittest.mock import MagicMock
from app.neo4j_client import query_entrypoints, query_changed_dep_ids


def _make_driver(records):
    """Return a mock neo4j driver whose session().run() yields the given records."""
    mock_record = lambda d: MagicMock(**{"__getitem__.side_effect": d.__getitem__, "data.return_value": d})
    mock_result = MagicMock()
    mock_result.__iter__ = MagicMock(return_value=iter([mock_record(r) for r in records]))
    mock_session = MagicMock()
    mock_session.__enter__ = MagicMock(return_value=mock_session)
    mock_session.__exit__ = MagicMock(return_value=False)
    mock_session.run.return_value = mock_result
    mock_driver = MagicMock()
    mock_driver.session.return_value = mock_session
    return mock_driver, mock_session


def test_query_entrypoints_returns_list():
    rows = [
        {"stable_id": "id_a", "name": "test_login", "file_path": "tests/test_auth.py"},
        {"stable_id": "id_b", "name": "app.route_home", "file_path": "views.py"},
    ]
    driver, session = _make_driver(rows)
    result = query_entrypoints(driver, "myrepo")
    assert len(result) == 2
    assert result[0]["stable_id"] == "id_a"


def test_query_entrypoints_passes_repo_param():
    driver, session = _make_driver([])
    query_entrypoints(driver, "myrepo")
    call_kwargs = session.run.call_args
    # repo param must be passed
    params = call_kwargs[0][1] if len(call_kwargs[0]) > 1 else call_kwargs[1]
    assert params.get("repo") == "myrepo"


def test_query_changed_dep_ids_returns_set():
    rows = [{"stable_id": "id_a"}, {"stable_id": "id_c"}]
    driver, session = _make_driver(rows)
    traced = [
        {"stable_id": "id_a", "last_traced_at": datetime(2026, 1, 1, tzinfo=timezone.utc)},
        {"stable_id": "id_b", "last_traced_at": datetime(2026, 2, 1, tzinfo=timezone.utc)},
    ]
    result = query_changed_dep_ids(driver, "myrepo", traced)
    assert isinstance(result, set)
    assert "id_a" in result
    assert "id_c" in result


def test_query_changed_dep_ids_empty_when_no_traced():
    driver, _ = _make_driver([])
    result = query_changed_dep_ids(driver, "myrepo", [])
    assert result == set()
