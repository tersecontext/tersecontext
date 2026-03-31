import os
import httpx
from .base import EmbeddingProvider

VOYAGE_API_URL = "https://api.voyageai.com/v1/embeddings"
DEFAULT_MODEL = "voyage-code-3"


class VoyageProvider(EmbeddingProvider):
    def __init__(self) -> None:
        api_key = os.environ.get("VOYAGE_API_KEY")
        if not api_key:
            raise ValueError("VOYAGE_API_KEY environment variable is required for VoyageProvider")
        self._api_key = api_key
        self._model = os.environ.get("EMBEDDING_MODEL", DEFAULT_MODEL)

    async def embed(self, texts: list[str]) -> list[list[float]]:
        async with httpx.AsyncClient(timeout=60.0) as client:
            response = await client.post(
                VOYAGE_API_URL,
                headers={"Authorization": f"Bearer {self._api_key}"},
                json={"model": self._model, "input": texts},
            )
            response.raise_for_status()
            data = response.json()["data"]
            return [item["embedding"] for item in data]
