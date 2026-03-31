from app.models import ExecutionPath, CallSequenceItem, SideEffect, EdgeRef
from app.renderer import render_spec_text


def _make_path(call_sequence, side_effects=None):
    return ExecutionPath(
        entrypoint_stable_id="test",
        commit_sha="abc",
        repo="test",
        call_sequence=call_sequence,
        side_effects=side_effects or [],
        dynamic_only_edges=[],
        never_observed_static_edges=[],
        timing_p50_ms=10.0,
        timing_p99_ms=50.0,
    )


def test_render_with_args():
    path = _make_path([
        CallSequenceItem(
            stable_id="s1", name="login_handler", qualified_name="auth.login_handler",
            hop=0, frequency_ratio=1.0, avg_ms=4.0,
            args='{"username": "str", "password": "str"}',
        ),
        CallSequenceItem(
            stable_id="s2", name="validate", qualified_name="auth.validate",
            hop=1, frequency_ratio=0.9, avg_ms=2.0,
        ),
    ])
    text = render_spec_text(path, "login_handler")
    assert 'args: {"username": "str", "password": "str"}' in text
    lines = text.split("\n")
    validate_line = next(l for l in lines if "validate" in l)
    assert "args:" not in validate_line


def test_render_without_args_backward_compat():
    path = _make_path([
        CallSequenceItem(
            stable_id="s1", name="handler", qualified_name="handler",
            hop=0, frequency_ratio=1.0, avg_ms=5.0,
        ),
    ])
    text = render_spec_text(path, "handler")
    assert "args:" not in text
