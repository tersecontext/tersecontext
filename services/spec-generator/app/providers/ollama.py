import os
import httpx
from .base import EmbeddingProvider


class OllamaProvider(EmbeddingProvider):
    def __init__(self) -> None:
        self._url = os.environ.get("OLLAMA_URL", "http://ollama:11434")
        self._model = os.environ.get("EMBEDDING_MODEL", "nomic-embed-text")

    async def embed(self, texts: list[str]) -> list[list[float]]:
        async with httpx.AsyncClient(timeout=60.0) as client:
            response = await client.post(
                f"{self._url}/api/embed",
                json={"model": self._model, "input": texts},
            )
            response.raise_for_status()
            return response.json()["embeddings"]
