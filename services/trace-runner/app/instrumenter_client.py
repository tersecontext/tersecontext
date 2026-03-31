from __future__ import annotations
import httpx
from .models import TraceEvent


class InstrumenterClient:
    def __init__(self, base_url: str, timeout: float = 35.0) -> None:
        self._base_url = base_url.rstrip("/")
        self._client = httpx.AsyncClient(timeout=timeout)

    async def instrument(self, stable_id: str, file_path: str, repo: str) -> str:
        """Call /instrument, return session_id."""
        resp = await self._client.post(
            f"{self._base_url}/instrument",
            json={"stable_id": stable_id, "file_path": file_path, "repo": repo},
        )
        resp.raise_for_status()
        return resp.json()["session_id"]

    async def run(self, session_id: str) -> tuple[list[TraceEvent], float]:
        """Call /run, return (events, duration_ms)."""
        resp = await self._client.post(
            f"{self._base_url}/run",
            json={"session_id": session_id},
        )
        resp.raise_for_status()
        data = resp.json()
        events = [TraceEvent(**e) for e in data["events"]]
        return events, float(data["duration_ms"])

    async def aclose(self) -> None:
        await self._client.aclose()
