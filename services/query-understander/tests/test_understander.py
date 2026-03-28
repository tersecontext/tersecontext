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
