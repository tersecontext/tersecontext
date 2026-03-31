import pytest
from unittest.mock import MagicMock
from app.store import _resolve_branch_coverage


def test_resolve_branch_coverage_prefers_coverage_pct():
    path = MagicMock()
    path.coverage_pct = 0.85
    path.call_sequence = [MagicMock(frequency_ratio=1.0)]
    result = _resolve_branch_coverage(path)
    assert result == 0.85


def test_resolve_branch_coverage_falls_back_to_frequency():
    path = MagicMock()
    path.coverage_pct = None
    path.call_sequence = [
        MagicMock(frequency_ratio=1.0),
        MagicMock(frequency_ratio=0.0),
    ]
    result = _resolve_branch_coverage(path)
    assert result == 0.5
