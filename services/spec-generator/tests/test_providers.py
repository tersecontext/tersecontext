import os
import pytest
from unittest.mock import AsyncMock, patch, MagicMock

os.environ.setdefault("POSTGRES_DSN", "postgres://test:test@localhost/test")


# ── OllamaProvider ──────────────────────────────────────────────────────────


async def test_ollama_constructs_correct_request():
    """OllamaProvider POSTs to /api/embed with model and input."""
    mock_response = MagicMock()
    mock_response.json.return_value = {"embeddings": [[0.1, 0.2, 0.3]]}
    mock_response.raise_for_status = MagicMock()

    mock_client = AsyncMock()
    mock_client.post = AsyncMock(return_value=mock_response)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    with patch.dict(os.environ, {"OLLAMA_URL": "http://test-ollama:11434", "EMBEDDING_MODEL": "test-model"}):
        from app.providers.ollama import OllamaProvider
        provider = OllamaProvider()

    with patch("app.providers.ollama.httpx.AsyncClient", return_value=mock_client):
        result = await provider.embed(["hello world"])

    mock_client.post.assert_called_once_with(
        "http://test-ollama:11434/api/embed",
        json={"model": "test-model", "input": ["hello world"]},
    )
    assert result == [[0.1, 0.2, 0.3]]


async def test_ollama_default_url_and_model():
    """OllamaProvider uses default URL and model when env vars are absent."""
    with patch.dict(os.environ, {}, clear=False):
        # Remove specific keys if set
        env = os.environ.copy()
        env.pop("OLLAMA_URL", None)
        env.pop("EMBEDDING_MODEL", None)
        with patch.dict(os.environ, env, clear=True):
            from app.providers.ollama import OllamaProvider
            provider = OllamaProvider()

    assert provider._url == "http://ollama:11434"
    assert provider._model == "nomic-embed-text"


async def test_ollama_multiple_texts():
    """OllamaProvider sends all texts in a single request."""
    mock_response = MagicMock()
    mock_response.json.return_value = {"embeddings": [[0.1], [0.2]]}
    mock_response.raise_for_status = MagicMock()

    mock_client = AsyncMock()
    mock_client.post = AsyncMock(return_value=mock_response)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    with patch.dict(os.environ, {"OLLAMA_URL": "http://ollama:11434"}):
        from app.providers.ollama import OllamaProvider
        provider = OllamaProvider()

    with patch("app.providers.ollama.httpx.AsyncClient", return_value=mock_client):
        result = await provider.embed(["text1", "text2"])

    sent_json = mock_client.post.call_args[1]["json"]
    assert sent_json["input"] == ["text1", "text2"]
    assert result == [[0.1], [0.2]]


# ── VoyageProvider ──────────────────────────────────────────────────────────


async def test_voyage_constructs_correct_request_with_api_key():
    """VoyageProvider sends Authorization header and correct payload."""
    mock_response = MagicMock()
    mock_response.json.return_value = {"data": [{"embedding": [0.5, 0.6]}]}
    mock_response.raise_for_status = MagicMock()

    mock_client = AsyncMock()
    mock_client.post = AsyncMock(return_value=mock_response)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    with patch.dict(os.environ, {"VOYAGE_API_KEY": "test-key-123", "EMBEDDING_MODEL": "voyage-code-3"}):
        from app.providers.voyage import VoyageProvider
        provider = VoyageProvider()

    with patch("app.providers.voyage.httpx.AsyncClient", return_value=mock_client):
        result = await provider.embed(["test input"])

    call_args = mock_client.post.call_args
    assert call_args[0][0] == "https://api.voyageai.com/v1/embeddings"
    assert call_args[1]["headers"]["Authorization"] == "Bearer test-key-123"
    assert call_args[1]["json"]["model"] == "voyage-code-3"
    assert call_args[1]["json"]["input"] == ["test input"]
    assert result == [[0.5, 0.6]]


async def test_voyage_raises_on_missing_api_key():
    """VoyageProvider raises ValueError when VOYAGE_API_KEY is not set."""
    with patch.dict(os.environ, {}, clear=False):
        env = os.environ.copy()
        env.pop("VOYAGE_API_KEY", None)
        with patch.dict(os.environ, env, clear=True):
            from app.providers.voyage import VoyageProvider
            with pytest.raises(ValueError, match="VOYAGE_API_KEY"):
                VoyageProvider()


async def test_voyage_multiple_texts():
    """VoyageProvider extracts embeddings from each data item."""
    mock_response = MagicMock()
    mock_response.json.return_value = {
        "data": [{"embedding": [0.1]}, {"embedding": [0.2]}, {"embedding": [0.3]}]
    }
    mock_response.raise_for_status = MagicMock()

    mock_client = AsyncMock()
    mock_client.post = AsyncMock(return_value=mock_response)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    with patch.dict(os.environ, {"VOYAGE_API_KEY": "key"}):
        from app.providers.voyage import VoyageProvider
        provider = VoyageProvider()

    with patch("app.providers.voyage.httpx.AsyncClient", return_value=mock_client):
        result = await provider.embed(["a", "b", "c"])

    assert len(result) == 3
    assert result == [[0.1], [0.2], [0.3]]


async def test_voyage_default_model():
    """VoyageProvider defaults to voyage-code-3 model."""
    with patch.dict(os.environ, {"VOYAGE_API_KEY": "key"}):
        env = os.environ.copy()
        env.pop("EMBEDDING_MODEL", None)
        env["VOYAGE_API_KEY"] = "key"
        with patch.dict(os.environ, env, clear=True):
            from app.providers.voyage import VoyageProvider
            provider = VoyageProvider()

    assert provider._model == "voyage-code-3"
