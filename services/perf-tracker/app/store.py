from __future__ import annotations
import asyncio
import logging

import asyncpg
import redis.asyncio as aioredis

from app.models import PerfMetric

logger = logging.getLogger(__name__)

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS perf_metrics (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    metric_type     TEXT NOT NULL,
    metric_name     TEXT NOT NULL,
    entity_id       TEXT NOT NULL,
    repo            TEXT NOT NULL,
    value           FLOAT NOT NULL,
    unit            TEXT NOT NULL,
    commit_sha      TEXT,
    recorded_at     TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS perf_metrics_entity_repo_idx
    ON perf_metrics (entity_id, repo, recorded_at);
CREATE INDEX IF NOT EXISTS perf_metrics_type_name_idx
    ON perf_metrics (metric_type, metric_name, recorded_at);
"""

INSERT_SQL = """
INSERT INTO perf_metrics (metric_type, metric_name, entity_id, repo, value, unit, commit_sha)
VALUES ($1, $2, $3, $4, $5, $6, $7)
"""


class PerfStore:
    def __init__(self, postgres_dsn: str, redis_client: aioredis.Redis) -> None:
        self._postgres_dsn = postgres_dsn
        self._redis = redis_client
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

    async def write_metrics(self, metrics: list[PerfMetric]) -> None:
        if not metrics:
            return
        pool = await self._get_pool()
        rows = [
            (m.metric_type, m.metric_name, m.entity_id, m.repo, m.value, m.unit, m.commit_sha)
            for m in metrics
        ]
        async with pool.acquire() as conn:
            await conn.executemany(INSERT_SQL, rows)

    async def update_realtime(self, repo: str, snapshot: dict[str, str]) -> None:
        key = f"perf:realtime:{repo}"
        await self._redis.delete(key)
        await self._redis.hset(key, mapping=snapshot)

    async def update_hot_functions(self, repo: str, fn_scores: dict[str, float]) -> None:
        key = f"perf:hotfns:{repo}"
        await self._redis.delete(key)
        if fn_scores:
            await self._redis.zadd(key, fn_scores)

    async def update_bottlenecks(self, repo: str, bottlenecks_json: list[str]) -> None:
        key = f"perf:bottlenecks:{repo}"
        await self._redis.delete(key)
        if bottlenecks_json:
            await self._redis.rpush(key, *bottlenecks_json)
            await self._redis.expire(key, 60)

    async def get_realtime(self, repo: str) -> dict[str, str]:
        raw = await self._redis.hgetall(f"perf:realtime:{repo}")
        return {
            (k.decode() if isinstance(k, bytes) else k): (v.decode() if isinstance(v, bytes) else v)
            for k, v in raw.items()
        }

    async def get_hot_functions(self, repo: str, limit: int = 20) -> list[tuple[str, float]]:
        raw = await self._redis.zrevrange(f"perf:hotfns:{repo}", 0, limit - 1, withscores=True)
        return [
            (name.decode() if isinstance(name, bytes) else name, score)
            for name, score in raw
        ]

    async def get_bottlenecks_json(self, repo: str) -> list[str]:
        raw = await self._redis.lrange(f"perf:bottlenecks:{repo}", 0, -1)
        return [v.decode() if isinstance(v, bytes) else v for v in raw]

    async def get_slow_functions(
        self, repo: str, limit: int = 20, commit_sha: str | None = None,
    ) -> list[dict]:
        pool = await self._get_pool()
        sql = """
            SELECT entity_id, value, unit, commit_sha
            FROM perf_metrics
            WHERE repo = $1 AND metric_type = 'user_code' AND metric_name = 'fn_duration'
        """
        params: list = [repo]
        if commit_sha:
            sql += " AND commit_sha = $2"
            params.append(commit_sha)
        sql += " ORDER BY value DESC LIMIT $" + str(len(params) + 1)
        params.append(limit)
        async with pool.acquire() as conn:
            rows = await conn.fetch(sql, *params)
        return [dict(r) for r in rows]

    async def get_trends(
        self, repo: str, entity_id: str, metric_name: str, window_hours: int = 24,
    ) -> list[dict]:
        pool = await self._get_pool()
        sql = """
            SELECT value, recorded_at
            FROM perf_metrics
            WHERE repo = $1 AND entity_id = $2 AND metric_name = $3
              AND recorded_at > NOW() - make_interval(hours => $4)
            ORDER BY recorded_at
        """
        async with pool.acquire() as conn:
            rows = await conn.fetch(sql, repo, entity_id, metric_name, window_hours)
        return [dict(r) for r in rows]

    async def get_regressions(
        self, repo: str, base_sha: str, head_sha: str, threshold_pct: float = 20.0,
    ) -> list[dict]:
        pool = await self._get_pool()
        sql = """
            WITH base AS (
                SELECT entity_id, AVG(value) as avg_val
                FROM perf_metrics
                WHERE repo = $1 AND commit_sha = $2
                  AND metric_type = 'user_code' AND metric_name = 'fn_duration'
                GROUP BY entity_id
            ), head AS (
                SELECT entity_id, AVG(value) as avg_val
                FROM perf_metrics
                WHERE repo = $1 AND commit_sha = $3
                  AND metric_type = 'user_code' AND metric_name = 'fn_duration'
                GROUP BY entity_id
            )
            SELECT h.entity_id, b.avg_val as base_val, h.avg_val as head_val,
                   ((h.avg_val - b.avg_val) / NULLIF(b.avg_val, 0)) * 100 as pct_change
            FROM head h
            JOIN base b ON h.entity_id = b.entity_id
            WHERE ((h.avg_val - b.avg_val) / NULLIF(b.avg_val, 0)) * 100 > $4
            ORDER BY pct_change DESC
        """
        async with pool.acquire() as conn:
            rows = await conn.fetch(sql, repo, base_sha, head_sha, threshold_pct)
        return [dict(r) for r in rows]

    async def get_summary_counts(self, repo: str) -> dict:
        pool = await self._get_pool()
        sql = """
            SELECT metric_type, COUNT(*) as cnt
            FROM perf_metrics WHERE repo = $1
            GROUP BY metric_type
        """
        async with pool.acquire() as conn:
            rows = await conn.fetch(sql, repo)
        return {r["metric_type"]: r["cnt"] for r in rows}

    async def close(self) -> None:
        if self._pool:
            await self._pool.close()
            self._pool = None
