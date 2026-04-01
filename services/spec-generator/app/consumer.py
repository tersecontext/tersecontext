# services/spec-generator/app/consumer.py
from __future__ import annotations

import logging

from shared.consumer import RedisConsumerBase

from app.models import ExecutionPath
from app.renderer import render_spec_text
from app.store import SpecStore

logger = logging.getLogger(__name__)

messages_processed_total: int = 0
messages_failed_total: int = 0
specs_written_total: int = 0
specs_embedded_total: int = 0


class SpecGeneratorConsumer(RedisConsumerBase):
    stream = "stream:execution-paths"
    group = "spec-generator-group"

    def __init__(self, store: SpecStore) -> None:
        self._store = store

    async def handle(self, data: dict) -> None:
        global messages_processed_total, specs_written_total, specs_embedded_total
        raw = data.get(b"event") or data.get("event")
        if raw is None:
            raise KeyError("message missing 'event' key")
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8")

        path = ExecutionPath.model_validate_json(raw)
        entrypoint_name = (
            path.call_sequence[0].name if path.call_sequence else path.entrypoint_stable_id
        )

        spec_text = render_spec_text(path, entrypoint_name)
        await self._store.upsert_spec(path, spec_text)
        specs_written_total += 1
        await self._store.upsert_qdrant(path, entrypoint_name, spec_text)
        specs_embedded_total += 1
        messages_processed_total += 1
