from app.models import InstrumentRequest


def test_instrument_request_new_fields_optional():
    req = InstrumentRequest(stable_id="s", file_path="f.py", repo="r")
    assert req.capture_args == []
    assert req.coverage_filter is None


def test_instrument_request_with_new_fields():
    req = InstrumentRequest(
        stable_id="s", file_path="f.py", repo="r",
        capture_args=["*handler*"], coverage_filter=["src/auth.py"],
    )
    assert req.capture_args == ["*handler*"]
    assert req.coverage_filter == ["src/auth.py"]
