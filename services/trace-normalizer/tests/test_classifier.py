from app.models import TraceEvent


def _ev(fn, type="call", file="x.py", line=1, ts=0.0):
    return TraceEvent(type=type, fn=fn, file=file, line=line, timestamp_ms=ts)


def test_detects_db_read_from_execute():
    from app.classifier import classify_side_effects
    events = [_ev("execute"), _ev("fetchall")]
    effects = classify_side_effects(events)
    types = {e.type for e in effects}
    assert "db_read" in types or "db_write" in types  # execute may be db_write, fetchall is db_read


def test_detects_db_read_from_fetchall():
    from app.classifier import classify_side_effects
    events = [_ev("fetchall")]
    effects = classify_side_effects(events)
    types = {e.type for e in effects}
    assert "db_read" in types


def test_detects_cache_set():
    from app.classifier import classify_side_effects
    events = [_ev("redis_set"), _ev("hset")]
    effects = classify_side_effects(events)
    types = {e.type for e in effects}
    assert "cache_set" in types


def test_detects_http_out():
    from app.classifier import classify_side_effects
    events = [_ev("_send_request"), _ev("httpx_request")]
    effects = classify_side_effects(events)
    types = {e.type for e in effects}
    assert "http_out" in types


def test_detects_fs_write():
    from app.classifier import classify_side_effects
    events = [_ev("write_file"), _ev("open")]
    effects = classify_side_effects(events)
    types = {e.type for e in effects}
    assert "fs_write" in types


def test_no_side_effects_for_plain_business_logic():
    from app.classifier import classify_side_effects
    events = [_ev("authenticate"), _ev("validate_token"), _ev("hash_password")]
    effects = classify_side_effects(events)
    assert effects == []
