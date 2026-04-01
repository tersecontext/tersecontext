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


# ── classify_io_events ──────────────────────────────────────────────────────


def test_io_events_select_is_db_read():
    from app.classifier import classify_io_events
    effects = classify_io_events([{"action": "mock_db", "detail": "SELECT * FROM users WHERE id = ?"}])
    assert len(effects) == 1
    assert effects[0].type == "db_read"
    assert "SELECT" in effects[0].detail


def test_io_events_insert_is_db_write():
    from app.classifier import classify_io_events
    effects = classify_io_events([{"action": "mock_db", "detail": "INSERT INTO events VALUES (?)"}])
    assert len(effects) == 1
    assert effects[0].type == "db_write"


def test_io_events_with_sql_is_db_read():
    from app.classifier import classify_io_events
    effects = classify_io_events([{"action": "mock_db", "detail": "WITH cte AS (SELECT 1) SELECT * FROM cte"}])
    assert effects[0].type == "db_read"


def test_io_events_http_is_http_out():
    from app.classifier import classify_io_events
    effects = classify_io_events([{"action": "mock_http", "detail": "POST https://api.example.com/send"}])
    assert len(effects) == 1
    assert effects[0].type == "http_out"
    assert effects[0].detail == "POST https://api.example.com/send"


def test_io_events_redirect_writes_is_fs_write():
    from app.classifier import classify_io_events
    effects = classify_io_events([{"action": "redirect_writes", "detail": "/tmp/out.csv -> /tmp/tc_123/out.csv"}])
    assert len(effects) == 1
    assert effects[0].type == "fs_write"


def test_io_events_deduplicates():
    from app.classifier import classify_io_events
    io = [
        {"action": "mock_db", "detail": "SELECT 1"},
        {"action": "mock_db", "detail": "SELECT 1"},
    ]
    effects = classify_io_events(io)
    assert len(effects) == 1


def test_io_events_unknown_action_ignored():
    from app.classifier import classify_io_events
    effects = classify_io_events([{"action": "unknown", "detail": "whatever"}])
    assert effects == []
