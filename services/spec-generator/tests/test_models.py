import pytest
from pydantic import ValidationError


def _make_path_dict(**overrides):
    base = {
        "entrypoint_stable_id": "sha256:fn_login",
        "commit_sha": "abc123",
        "repo": "acme",
        "call_sequence": [
            {
                "stable_id": "sha256:fn_login",
                "name": "login",
                "qualified_name": "auth.service.login",
                "hop": 0,
                "frequency_ratio": 1.0,
                "avg_ms": 12.5,
            }
        ],
        "side_effects": [],
        "dynamic_only_edges": [],
        "never_observed_static_edges": [],
        "timing_p50_ms": 12.5,
        "timing_p99_ms": 45.0,
    }
    base.update(overrides)
    return base


def test_execution_path_parses_valid():
    from app.models import ExecutionPath
    path = ExecutionPath.model_validate(_make_path_dict())
    assert path.repo == "acme"
    assert path.call_sequence[0].name == "login"
    assert path.call_sequence[0].qualified_name == "auth.service.login"


def test_execution_path_missing_repo_raises():
    from app.models import ExecutionPath
    data = _make_path_dict()
    del data["repo"]
    with pytest.raises(ValidationError):
        ExecutionPath.model_validate(data)


def test_execution_path_ignores_extra_fields():
    from app.models import ExecutionPath
    data = _make_path_dict()
    data["unknown_future_field"] = "ignored"
    path = ExecutionPath.model_validate(data)
    assert not hasattr(path, "unknown_future_field")


def test_side_effect_type_enum_all_values():
    from app.models import SideEffect
    for t in ("db_read", "db_write", "cache_read", "cache_set", "http_out", "fs_write"):
        se = SideEffect(type=t, detail="some detail", hop_depth=1)
        assert se.type == t


def test_side_effect_invalid_type_raises():
    from app.models import SideEffect
    with pytest.raises(ValidationError):
        SideEffect(type="env_read", detail="x", hop_depth=1)


def test_call_sequence_item_fields():
    from app.models import CallSequenceItem
    item = CallSequenceItem(
        stable_id="sha256:fn",
        name="fn",
        qualified_name="mod.fn",
        hop=1,
        frequency_ratio=0.5,
        avg_ms=3.2,
    )
    assert item.hop == 1
    assert item.frequency_ratio == 0.5
