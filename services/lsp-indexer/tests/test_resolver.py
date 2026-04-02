from unittest.mock import MagicMock, patch
import pytest
from app.resolver import resolve_stable_id


def _make_driver(stable_id: str | None):
    """Return a mock Neo4j driver that returns stable_id (or nothing)."""
    record = MagicMock()
    record.get.return_value = stable_id

    result = MagicMock()
    result.__iter__ = MagicMock(return_value=iter([record] if stable_id else []))

    session = MagicMock()
    session.__enter__ = MagicMock(return_value=session)
    session.__exit__ = MagicMock(return_value=False)
    session.run.return_value = result

    driver = MagicMock()
    driver.session.return_value = session
    return driver


def test_resolve_stable_id_found():
    driver = _make_driver("sha256:fn_auth")
    result = resolve_stable_id(driver, "my-repo", "auth/service.py", 10)
    assert result == "sha256:fn_auth"


def test_resolve_stable_id_not_found():
    driver = _make_driver(None)
    result = resolve_stable_id(driver, "my-repo", "auth/service.py", 999)
    assert result is None
