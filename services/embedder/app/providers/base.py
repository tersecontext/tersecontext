class EmbeddingProvider:
    async def embed(self, texts: list[str]) -> list[list[float]]:
        raise NotImplementedError
