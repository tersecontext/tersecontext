from __future__ import annotations
import asyncio
import uuid

import asyncpg
from qdrant_client import QdrantClient
from qdrant_client.models import Distance, PointStruct, VectorParams

from app.models import CallSequenceItem, ExecutionPath
from app.providers.base import EmbeddingProvider

COLLECTION = "specs"

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS behavior_specs (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    node_stable_id  TEXT NOT NULL,
    repo            TEXT NOT NULL,
    commit_sha      TEXT NOT NULL,
    version         INTEGER NOT NULL DEFAULT 1,
    spec_text       TEXT NOT NULL,
    branch_coverage FLOAT,
    observed_calls  INTEGER,
    generated_at    TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE (node_stable_id, repo, commit_sha)
);
CREATE INDEX IF NOT EXISTS behavior_specs_node_repo_idx
    ON behavior_specs (node_stable_id, repo);
"""

UPSERT_SQL = """
INSERT INTO behavior_specs
    (node_stable_id, repo, commit_sha, spec_text, branch_coverage, observed_calls)
VALUES ($1, $2, $3, $4, $5, $6)
ON CONFLICT (node_stable_id, repo, commit_sha) DO UPDATE SET
    version         = behavior_specs.version + 1,
    spec_text       = EXCLUDED.spec_text,
    branch_coverage = EXCLUDED.branch_coverage,
    observed_calls  = EXCLUDED.observed_calls,
    generated_at    = NOW();
"""


def _compute_branch_coverage(items: list[CallSequenceItem]) -> float | None:
    if not items:
        return None
    observed = sum(1 for item in items if item.frequency_ratio > 0.0)
    return observed / len(items)


class SpecStore:
    def __init__(
        self,
        postgres_dsn: str,
        qdrant_url: str,
        provider: EmbeddingProvider,
        embedding_dim: int,
    ) -> None:
        self._postgres_dsn = postgres_dsn
        self._provider = provider
        self._embedding_dim = embedding_dim
        self._qdrant = QdrantClient(url=qdrant_url)
        self._pool: asyncpg.Pool | None = None
        self._pool_lock = asyncio.Lock()

    async def _get_pool(self) -> asyncpg.Pool:
        if self._pool is None:
            async with self._pool_lock:
                if self._pool is None:
                    self._pool = await asyncpg.create_pool(self._postgres_dsn)
        return self._pool

    async def ensure_schema(self) -> None:
        pool = await self._get_pool()
        async with pool.acquire() as conn:
            async with conn.transaction():
                await conn.execute(SCHEMA_SQL)

    def _ensure_collection_sync(self) -> None:
        existing = {c.name for c in self._qdrant.get_collections().collections}
        if COLLECTION in existing:
            return
        try:
            self._qdrant.create_collection(
                collection_name=COLLECTION,
                vectors_config=VectorParams(size=self._embedding_dim, distance=Distance.COSINE),
            )
        except Exception as exc:
            if getattr(exc, "status_code", None) == 409:
                return
            raise

    async def ensure_collection(self) -> None:
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, self._ensure_collection_sync)

    async def upsert_spec(self, path: ExecutionPath, spec_text: str) -> None:
        branch_coverage = _compute_branch_coverage(path.call_sequence)
        observed_calls = len(path.call_sequence)
        pool = await self._get_pool()
        async with pool.acquire() as conn:
            await conn.execute(
                UPSERT_SQL,
                path.entrypoint_stable_id,
                path.repo,
                path.commit_sha,
                spec_text,
                branch_coverage,
                observed_calls,
            )

    def _upsert_qdrant_sync(
        self, path: ExecutionPath, entrypoint_name: str, spec_text: str, vector: list[float]
    ) -> None:
        point_id = str(uuid.uuid5(uuid.NAMESPACE_OID, f"{path.entrypoint_stable_id}:{path.repo}"))
        self._qdrant.upsert(
            collection_name=COLLECTION,
            points=[
                PointStruct(
                    id=point_id,
                    vector=vector,
                    payload={
                        "node_stable_id": path.entrypoint_stable_id,
                        "entrypoint_name": entrypoint_name,
                        "repo": path.repo,
                        "commit_sha": path.commit_sha,
                    },
                )
            ],
        )

    async def upsert_qdrant(
        self, path: ExecutionPath, entrypoint_name: str, spec_text: str
    ) -> None:
        vectors = await self._provider.embed([spec_text])
        vector = vectors[0]
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(
            None, self._upsert_qdrant_sync, path, entrypoint_name, spec_text, vector
        )

    async def close(self) -> None:
        if self._pool:
            await self._pool.close()
            self._pool = None
