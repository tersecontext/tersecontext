from __future__ import annotations
import asyncio
import uuid

from qdrant_client import QdrantClient
from qdrant_client.http.models import Distance, VectorParams, PointStruct, PointIdsList

from .models import EmbeddedNodesEvent

COLLECTION = "nodes"


class QdrantWriter:
    def __init__(self, url: str, embedding_dim: int) -> None:
        self.client = QdrantClient(url=url)
        self.embedding_dim = embedding_dim

    # ── ensure_collection ─────────────────────────────────────────────────────

    def _ensure_collection_sync(self) -> None:
        existing = {c.name for c in self.client.get_collections().collections}
        if COLLECTION in existing:
            return
        try:
            self.client.create_collection(
                collection_name=COLLECTION,
                vectors_config=VectorParams(size=self.embedding_dim, distance=Distance.COSINE),
            )
        except Exception as exc:
            if getattr(exc, "status_code", None) == 409:
                return  # another replica created it first
            raise

    async def ensure_collection(self) -> None:
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, self._ensure_collection_sync)

    # ── upsert_points ─────────────────────────────────────────────────────────

    def _upsert_sync(self, event: EmbeddedNodesEvent) -> None:
        points = [
            PointStruct(
                id=str(uuid.uuid5(uuid.NAMESPACE_OID, node.stable_id)),
                vector=node.vector,
                payload={
                    "stable_id": node.stable_id,
                    "name": node.name,
                    "type": node.type,
                    "file_path": node.file_path,
                    "language": node.language,
                    "repo": event.repo,
                    "node_hash": node.node_hash,
                },
            )
            for node in event.nodes
        ]
        self.client.upsert(collection_name=COLLECTION, points=points)

    async def upsert_points(self, event: EmbeddedNodesEvent) -> None:
        if not event.nodes:
            return
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, self._upsert_sync, event)

    # ── delete_points ─────────────────────────────────────────────────────────

    def _delete_sync(self, stable_ids: list[str]) -> None:
        point_ids = [str(uuid.uuid5(uuid.NAMESPACE_OID, sid)) for sid in stable_ids]
        self.client.delete(
            collection_name=COLLECTION,
            points_selector=PointIdsList(points=point_ids),
        )

    async def delete_points(self, stable_ids: list[str]) -> None:
        if not stable_ids:
            return
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, self._delete_sync, stable_ids)

    # ── get_collections (used by /ready) ──────────────────────────────────────

    async def get_collections(self):
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, self.client.get_collections)
