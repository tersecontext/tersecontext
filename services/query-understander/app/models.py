from typing import Literal, Optional
from pydantic import BaseModel


class UnderstandRequest(BaseModel):
    question: str
    repo: str


class QueryIntent(BaseModel):
    raw_query: str
    keywords: list[str]
    symbols: list[str]
    query_type: Literal["lookup", "flow", "impact"]
    embed_query: str
    scope: Optional[str] = None
