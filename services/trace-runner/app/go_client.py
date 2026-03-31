"""Client for Go instrumentation and trace execution via go-instrumenter + go-trace-runner."""
from __future__ import annotations

import asyncio
import logging

import httpx

from .models import EntrypointJob, TraceEvent, RawTrace

logger = logging.getLogger(__name__)

STREAM_OUT = "stream:raw-traces"


class GoTraceClient:
    def __init__(self, instrumenter_url: str, runner_url: str, timeout: float = 120.0) -> None:
        self._instr_url = instrumenter_url.rstrip("/")
        self._runner_url = runner_url.rstrip("/")
        self._client = httpx.AsyncClient(timeout=timeout)

    async def process_job(
        self,
        job: EntrypointJob,
        commit_sha: str,
        r,  # aioredis.Redis
        repo_dir: str = "/repos",
    ) -> str:
        """Instrument and run a single Go entrypoint. Returns 'ok' or 'error'."""
        repo_path = f"{repo_dir}/{job.repo}"

        # Step 1: Instrument
        try:
            instr_resp = await self._client.post(
                f"{self._instr_url}/instrument",
                json={
                    "repo": job.repo,
                    "repo_path": repo_path,
                    "commit_sha": commit_sha,
                    "entrypoints": [job.name],
                    "language": "go",
                    "boundary_patterns": [],
                    "include_deps": [],
                },
            )
            instr_resp.raise_for_status()
            instr_data = instr_resp.json()
            session_id = instr_data["session_id"]
        except Exception as exc:
            logger.error("Go instrumenter failed for %s: %s", job.stable_id, exc)
            return "error"

        # Step 2: Run
        try:
            ep_type = "test" if job.name.startswith("Test") else "function"
            run_resp = await self._client.post(
                f"{self._runner_url}/run",
                json={
                    "session_id": session_id,
                    "entrypoints": [
                        {"stable_id": job.stable_id, "name": job.name, "type": ep_type}
                    ],
                    "commit_sha": commit_sha,
                    "repo": job.repo,
                    "timeout_s": 30,
                },
            )
            run_resp.raise_for_status()
            run_data = run_resp.json()
            trace_id = run_data.get("trace_id", "")
        except Exception as exc:
            logger.error("Go trace-runner failed for %s: %s", job.stable_id, exc)
            return "error"

        # Step 3: Poll for completion
        try:
            for _ in range(120):
                await asyncio.sleep(1)
                status_resp = await self._client.get(
                    f"{self._runner_url}/run/{trace_id}/status"
                )
                status_resp.raise_for_status()
                status_data = status_resp.json()
                if status_data["status"] in ("completed", "failed"):
                    break
            else:
                logger.error("Go trace timed out for %s", job.stable_id)
                return "error"

            if status_data["status"] == "failed":
                logger.error("Go trace failed for %s: %s", job.stable_id, status_data.get("error"))
                return "error"

            # Emit a minimal RawTrace to the stream so downstream services can process
            trace = RawTrace(
                entrypoint_stable_id=job.stable_id,
                commit_sha=commit_sha,
                repo=job.repo,
                duration_ms=float(status_data.get("duration_ms", 0)),
                events=[],
            )
            await r.xadd(STREAM_OUT, {"event": trace.model_dump_json()})
            return "ok"
        except Exception as exc:
            logger.error("Go trace polling failed for %s: %s", job.stable_id, exc)
            return "error"

    async def aclose(self) -> None:
        await self._client.aclose()
