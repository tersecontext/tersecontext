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
