import json
import logging
import os
from typing import Optional

import httpx

from .models import QueryIntent

logger = logging.getLogger(__name__)

STOP_WORDS = {
    "a", "an", "the", "is", "are", "was", "were", "be", "been", "being",
    "have", "has", "had", "do", "does", "did", "will", "would", "could",
    "should", "may", "might", "shall", "can", "to", "of", "in", "for",
    "on", "with", "at", "by", "from", "and", "but", "or", "if", "how",
    "what", "where", "when", "who", "why", "which", "this", "that",
    "i", "my", "me", "we", "our", "you", "it", "its", "they", "there",
    "get", "let", "make", "change", "work",
}

_BASE_PROMPT = """\
You are a code analysis assistant. Extract structured information from this codebase query.
Return JSON only. No explanation. No markdown. No code fences.

Schema:
{{
  "keywords": ["string"],
  "symbols": ["string"],
  "query_type": "lookup | flow | impact",
  "embed_query": "string",
  "scope": "string or null"
}}

Rules:
- keywords: lowercase terms relevant to the query, 3-8 items
- symbols: any identifiers (function names, class names) mentioned or implied
- query_type: lookup=find something, flow=understand execution, impact=understand consequences
- embed_query: rewrite the query with synonyms and related terms for better search, 8-15 words
- scope: file path if user mentioned one, otherwise null

Query: "{question}\""""

_STRICT_SUFFIX = "\nYou must return only a JSON object. Any other output is invalid."


def _make_prompt(question: str, strict: bool = False) -> str:
    prompt = _BASE_PROMPT.format(question=question)
    if strict:
        prompt += _STRICT_SUFFIX
    return prompt


def _fallback_intent(question: str) -> QueryIntent:
    words = question.lower().split()
    keywords = [w for w in words if w not in STOP_WORDS and len(w) > 1]
    return QueryIntent(
        raw_query=question,
        keywords=keywords[:8],
        symbols=[],
        query_type="flow",
        embed_query=question,
        scope=None,
    )


def _parse_response(raw: str, question: str) -> Optional[QueryIntent]:
    try:
        data = json.loads(raw)
        data["raw_query"] = question
        return QueryIntent.model_validate(data)
    except Exception:
        return None


async def _call_ollama(question: str, strict: bool = False) -> str:
    base_url = os.getenv("LLM_BASE_URL", "http://ollama:11434")
    model = os.getenv("LLM_MODEL", "qwen2.5-coder:7b")
    payload = {
        "model": model,
        "prompt": _make_prompt(question, strict),
        "stream": False,
    }
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.post(f"{base_url}/api/generate", json=payload)
        resp.raise_for_status()
        return resp.json().get("response", "")


async def understand(question: str) -> tuple[QueryIntent, bool]:
    """Returns (QueryIntent, is_ollama_success).
    is_ollama_success is False when keyword fallback was used."""
    # First attempt
    try:
        raw = await _call_ollama(question, strict=False)
        intent = _parse_response(raw, question)
        if intent:
            return intent, True
    except Exception as exc:
        logger.warning("Ollama first attempt failed: %s", exc)

    # Retry with stricter prompt
    try:
        raw = await _call_ollama(question, strict=True)
        intent = _parse_response(raw, question)
        if intent:
            return intent, True
    except Exception as exc:
        logger.warning("Ollama retry failed: %s", exc)

    # Fallback to keyword extraction
    logger.warning("Falling back to keyword extraction for: %r", question)
    return _fallback_intent(question), False
