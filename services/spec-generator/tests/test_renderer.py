from app.models import CallSequenceItem, ExecutionPath, SideEffect


def _make_path(call_sequence=None, side_effects=None):
    return ExecutionPath(
        entrypoint_stable_id="sha256:fn_login",
        commit_sha="abc123",
        repo="acme",
        call_sequence=call_sequence or [],
        side_effects=side_effects or [],
        dynamic_only_edges=[],
        never_observed_static_edges=[],
        timing_p50_ms=1.0,
        timing_p99_ms=4.0,
    )


def _item(name, hop, freq=1.0, avg_ms=5.0, qualified_name=None):
    return CallSequenceItem(
        stable_id=f"sha256:{name}",
        name=name,
        qualified_name=qualified_name or f"mod.{name}",
        hop=hop,
        frequency_ratio=freq,
        avg_ms=avg_ms,
    )


def _effect(type_, detail, hop_depth=1):
    return SideEffect(type=type_, detail=detail, hop_depth=hop_depth)


# ── PATH section ──────────────────────────────────────────────────────────────

def test_path_section_header():
    from app.renderer import render_spec_text
    path = _make_path(call_sequence=[_item("login", hop=0, avg_ms=12.5)])
    text = render_spec_text(path, "login")
    first_line = text.splitlines()[0]
    assert first_line == "PATH login"


def test_path_section_lists_items_in_hop_order():
    from app.renderer import render_spec_text
    path = _make_path(call_sequence=[
        _item("login", hop=0, freq=1.0, avg_ms=10.0),
        _item("authenticate", hop=1, freq=1.0, avg_ms=5.0),
    ])
    text = render_spec_text(path, "login")
    lines = text.splitlines()
    login_line = next(l for l in lines if "login" in l and "1." in l)
    auth_line = next(l for l in lines if "authenticate" in l)
    assert lines.index(login_line) < lines.index(auth_line)


def test_path_section_shows_frequency_ratio():
    from app.renderer import render_spec_text
    path = _make_path(call_sequence=[
        _item("login", hop=0, freq=1.0, avg_ms=10.0),
        _item("notify", hop=1, freq=0.5, avg_ms=3.0),
    ])
    text = render_spec_text(path, "login")
    assert "0.50" in text or "50%" in text or "1/2" in text


def test_path_section_shows_avg_ms():
    from app.renderer import render_spec_text
    path = _make_path(call_sequence=[_item("login", hop=0, avg_ms=12.5)])
    text = render_spec_text(path, "login")
    assert "12.5ms" in text or "~12.5ms" in text


def test_empty_call_sequence_renders_without_error():
    from app.renderer import render_spec_text
    path = _make_path(call_sequence=[])
    text = render_spec_text(path, "sha256:fn_login")
    assert "PATH sha256:fn_login" in text


# ── SIDE_EFFECTS section ──────────────────────────────────────────────────────

def test_side_effects_all_six_types_render():
    from app.renderer import render_spec_text
    effects = [
        _effect("db_read", "SELECT id FROM users WHERE id = $1"),
        _effect("db_write", "INSERT INTO audit_log VALUES (...)"),
        _effect("cache_read", "session:{user_id}"),
        _effect("cache_set", "session:{user_id} TTL 3600"),
        _effect("http_out", "POST https://notifications.internal/send"),
        _effect("fs_write", "/tmp/export.csv"),
    ]
    path = _make_path(side_effects=effects)
    text = render_spec_text(path, "login")
    assert "DB READ" in text
    assert "DB WRITE" in text
    assert "CACHE READ" in text
    assert "CACHE SET" in text
    assert "HTTP OUT" in text
    assert "FS WRITE" in text


def test_conditional_side_effect_annotated():
    from app.renderer import render_spec_text
    path = _make_path(side_effects=[_effect("http_out", "POST https://svc/send", hop_depth=2)])
    text = render_spec_text(path, "login")
    assert "(conditional)" in text


def test_non_conditional_side_effect_not_annotated():
    from app.renderer import render_spec_text
    path = _make_path(side_effects=[_effect("db_read", "SELECT 1", hop_depth=1)])
    text = render_spec_text(path, "login")
    assert "(conditional)" not in text


def test_empty_side_effects_section_absent_or_empty():
    from app.renderer import render_spec_text
    path = _make_path(side_effects=[])
    text = render_spec_text(path, "login")
    # Either the section header is absent or it has no entries beneath it
    if "SIDE_EFFECTS:" in text:
        header_idx = text.index("SIDE_EFFECTS:")
        remainder = text[header_idx + len("SIDE_EFFECTS:"):].strip()
        # Next section or empty
        assert remainder == "" or remainder.startswith("CHANGE_IMPACT")


# ── CHANGE_IMPACT section ─────────────────────────────────────────────────────

def test_change_impact_includes_db_tables():
    from app.renderer import render_spec_text
    effects = [
        _effect("db_read", "SELECT id FROM users WHERE id = $1"),
        _effect("db_write", "INSERT INTO audit_log VALUES (...)"),
    ]
    path = _make_path(side_effects=effects)
    text = render_spec_text(path, "login")
    assert "CHANGE_IMPACT:" in text
    assert "users" in text
    assert "audit_log" in text


def test_change_impact_includes_http_services():
    from app.renderer import render_spec_text
    effects = [_effect("http_out", "POST https://notifications.internal/send")]
    path = _make_path(side_effects=effects)
    text = render_spec_text(path, "login")
    assert "CHANGE_IMPACT:" in text
    assert "notifications.internal" in text


def test_change_impact_deduplicates_tables():
    from app.renderer import render_spec_text
    effects = [
        _effect("db_read", "SELECT id FROM users WHERE id = $1"),
        _effect("db_write", "UPDATE users SET active = false"),
    ]
    path = _make_path(side_effects=effects)
    text = render_spec_text(path, "login")
    assert "CHANGE_IMPACT:" in text
    change_impact_section = text.split("CHANGE_IMPACT:")[1]
    assert change_impact_section.count("users") == 1
