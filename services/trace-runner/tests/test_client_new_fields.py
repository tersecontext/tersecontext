import pytest
from unittest.mock import AsyncMock, MagicMock
from app.instrumenter_client import InstrumenterClient


@pytest.mark.asyncio
async def test_instrument_passes_capture_args_and_coverage_filter():
    mock_response = MagicMock()
    mock_response.json.return_value = {"session_id": "abc"}
    mock_response.raise_for_status = MagicMock()

    mock_client = AsyncMock()
    mock_client.post.return_value = mock_response

    ic = InstrumenterClient("http://localhost:8093")
    ic._client = mock_client

    await ic.instrument(
        stable_id="s", file_path="f.py", repo="r",
        capture_args=["*handler*"],
        coverage_filter=["src/auth.py"],
    )

    call_args = mock_client.post.call_args
    payload = call_args.kwargs.get("json") or call_args[1].get("json")
    assert payload["capture_args"] == ["*handler*"]
    assert payload["coverage_filter"] == ["src/auth.py"]


@pytest.mark.asyncio
async def test_instrument_defaults_omit_new_fields():
    mock_response = MagicMock()
    mock_response.json.return_value = {"session_id": "abc"}
    mock_response.raise_for_status = MagicMock()

    mock_client = AsyncMock()
    mock_client.post.return_value = mock_response

    ic = InstrumenterClient("http://localhost:8093")
    ic._client = mock_client

    await ic.instrument(stable_id="s", file_path="f.py", repo="r")

    call_args = mock_client.post.call_args
    payload = call_args.kwargs.get("json") or call_args[1].get("json")
    assert payload["capture_args"] == []
    assert payload["coverage_filter"] is None
