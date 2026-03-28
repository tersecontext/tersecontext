import pytest
from pydantic import ValidationError

from app.models import QueryIntent, UnderstandRequest


def test_query_intent_valid():
    intent = QueryIntent(
        raw_query="how does auth work",
        keywords=["auth", "login"],
        symbols=["AuthService"],
        query_type="flow",
        embed_query="authentication flow login verify token",
        scope=None,
    )
    assert intent.query_type == "flow"
    assert intent.scope is None


def test_query_intent_rejects_bad_query_type():
    with pytest.raises(ValidationError):
        QueryIntent(
            raw_query="q",
            keywords=[],
            symbols=[],
            query_type="unknown",
            embed_query="q",
        )


def test_understand_request_valid():
    req = UnderstandRequest(question="where is JWT configured", repo="acme-api")
    assert req.question == "where is JWT configured"
    assert req.repo == "acme-api"


import hashlib
from app.cache import cache_get, cache_set, _make_key


SAMPLE_INTENT = QueryIntent(
    raw_query="how does auth work",
    keywords=["auth", "login"],
    symbols=["AuthService"],
    query_type="flow",
    embed_query="authentication flow login verify token credentials",
    scope=None,
)


async def test_cache_miss_returns_none(fake_redis):
    result = await cache_get(fake_redis, "any question", "any-repo")
    assert result is None


async def test_cache_roundtrip(fake_redis):
    await cache_set(fake_redis, "how does auth work", "acme-api", SAMPLE_INTENT)
    result = await cache_get(fake_redis, "how does auth work", "acme-api")
    assert result is not None
    assert result.raw_query == "how does auth work"
    assert result.query_type == "flow"


def test_cache_key_is_deterministic():
    k1 = _make_key("how does auth work", "acme-api")
    k2 = _make_key("how does auth work", "acme-api")
    assert k1 == k2
    assert k1.startswith("query_intent:")


def test_cache_key_normalises_case():
    k1 = _make_key("How Does Auth Work", "acme-api")
    k2 = _make_key("how does auth work", "acme-api")
    assert k1 == k2


def test_cache_key_includes_repo():
    k1 = _make_key("how does auth work", "repo-a")
    k2 = _make_key("how does auth work", "repo-b")
    assert k1 != k2


async def test_cache_respects_ttl(fake_redis):
    # fakeredis supports TTL; verify key was stored with one
    await cache_set(fake_redis, "q", "repo", SAMPLE_INTENT)
    key = _make_key("q", "repo")
    ttl = await fake_redis.ttl(key)
    assert ttl > 0


import json
from unittest.mock import AsyncMock, patch

from app.understander import understand, _fallback_intent


VALID_OLLAMA_RESPONSE = {
    "keywords": ["auth", "authenticate", "login", "jwt", "token"],
    "symbols": ["AuthService", "authenticate"],
    "query_type": "flow",
    "embed_query": "authentication flow login jwt token verification user credentials",
    "scope": None,
}

LOOKUP_RESPONSE = {
    "keywords": ["jwt", "config", "configure"],
    "symbols": ["JWTConfig"],
    "query_type": "lookup",
    "embed_query": "JWT configuration setup options settings location",
    "scope": None,
}

IMPACT_RESPONSE = {
    "keywords": ["authenticate", "break", "change"],
    "symbols": ["authenticate"],
    "query_type": "impact",
    "embed_query": "authenticate function callers dependents breaking changes side effects",
    "scope": None,
}


async def test_understand_ollama_success():
    raw_json = json.dumps(VALID_OLLAMA_RESPONSE)
    with patch("app.understander._call_ollama", new=AsyncMock(return_value=raw_json)):
        intent, from_ollama = await understand("how does authentication work")
    assert from_ollama is True
    assert intent.query_type == "flow"
    assert intent.raw_query == "how does authentication work"
    assert len(intent.embed_query) > len("how does authentication work")


async def test_understand_lookup_query_type():
    raw_json = json.dumps(LOOKUP_RESPONSE)
    with patch("app.understander._call_ollama", new=AsyncMock(return_value=raw_json)):
        intent, from_ollama = await understand("where is JWT configured")
    assert from_ollama is True
    assert intent.query_type == "lookup"


async def test_understand_impact_query_type():
    raw_json = json.dumps(IMPACT_RESPONSE)
    with patch("app.understander._call_ollama", new=AsyncMock(return_value=raw_json)):
        intent, from_ollama = await understand("what breaks if I change authenticate")
    assert from_ollama is True
    assert intent.query_type == "impact"
    assert "authenticate" in intent.symbols


async def test_understand_retry_on_bad_json():
    """First Ollama response is invalid JSON; second (strict prompt) is valid."""
    good_json = json.dumps(VALID_OLLAMA_RESPONSE)
    call_mock = AsyncMock(side_effect=["not valid json", good_json])
    with patch("app.understander._call_ollama", new=call_mock):
        intent, from_ollama = await understand("how does authentication work")
    assert from_ollama is True
    assert call_mock.call_count == 2
    # Second call uses strict=True
    assert call_mock.call_args_list[1].kwargs.get("strict") or call_mock.call_args_list[1].args[1]


async def test_understand_fallback_on_double_failure():
    """Both Ollama attempts fail; fallback keyword extraction is used."""
    call_mock = AsyncMock(side_effect=Exception("connection refused"))
    with patch("app.understander._call_ollama", new=call_mock):
        intent, from_ollama = await understand("how does authentication work")
    assert from_ollama is False
    assert intent.query_type == "flow"
    assert intent.raw_query == "how does authentication work"
    assert isinstance(intent.keywords, list)
    assert len(intent.keywords) > 0


async def test_understand_fallback_on_invalid_json_twice():
    """Both Ollama responses are invalid JSON; fallback is used."""
    call_mock = AsyncMock(return_value="{ invalid }")
    with patch("app.understander._call_ollama", new=call_mock):
        intent, from_ollama = await understand("how does authentication work")
    assert from_ollama is False
    assert intent.query_type == "flow"


def test_fallback_filters_stop_words():
    intent = _fallback_intent("how does authentication work in the system")
    # "how", "does", "in", "the" are stop words and should be filtered
    assert "how" not in intent.keywords
    assert "does" not in intent.keywords
    assert "the" not in intent.keywords
    assert "authentication" in intent.keywords


def test_fallback_returns_valid_intent():
    intent = _fallback_intent("what breaks if I change authenticate")
    assert intent.query_type == "flow"
    assert intent.embed_query == "what breaks if I change authenticate"
    assert intent.symbols == []
