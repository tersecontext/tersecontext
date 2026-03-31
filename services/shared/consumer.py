# services/shared/consumer.py
from __future__ import annotations

import asyncio
import logging
import socket
from abc import ABC, abstractmethod

import redis.asyncio as aioredis
import redis.exceptions

logger = logging.getLogger(__name__)


class RedisConsumerBase(ABC):
    """XREADGROUP consumer with COUNT=50, DLQ on error, and post-batch hook.

    Subclasses set class-level ``stream`` and ``group`` and implement
    ``handle(data)``. Override ``post_batch()`` to run logic (e.g. batch
    DB writes) after each group of up to 50 events.

    Usage::

        class MyConsumer(RedisConsumerBase):
            stream = "stream:my-events"
            group  = "my-group"

            async def handle(self, data: dict) -> None:
                raw = data.get(b"event") or data.get("event")
                event = MyModel.model_validate_json(raw)
                ...

        consumer = MyConsumer(...)
        await consumer.run(redis_url)
    """

    stream: str  # set by subclass
    group: str   # set by subclass

    @abstractmethod
    async def handle(self, data: dict) -> None:
        """Process a single event. Raise KeyError/ValueError for bad messages
        (→ DLQ + ACK). Raise any other Exception to log without ACK (retry)."""

    async def post_batch(self) -> None:
        """Called after each read batch (up to 50 events). Override for batch writes."""

    async def run(self, redis_url: str) -> None:
        """Run the consumer loop until cancelled."""
        r = aioredis.from_url(redis_url)
        consumer_name = f"{self.group}-{socket.gethostname()}"
        dlq_stream = f"{self.stream}-dlq"

        try:
            try:
                await r.xgroup_create(self.stream, self.group, id="0", mkstream=True)
            except redis.exceptions.ResponseError as exc:
                if "BUSYGROUP" not in str(exc):
                    raise

            logger.info("Consumer started: stream=%s group=%s", self.stream, self.group)

            while True:
                try:
                    messages = await r.xreadgroup(
                        groupname=self.group,
                        consumername=consumer_name,
                        streams={self.stream: ">"},
                        count=50,
                        block=100,
                    )
                    had_events = False
                    for _stream, events in (messages or []):
                        for msg_id, data in events:
                            had_events = True
                            try:
                                await self.handle(data)
                                await r.xack(self.stream, self.group, msg_id)
                            except (KeyError, ValueError) as exc:
                                logger.warning("Bad message %s → DLQ: %s", msg_id, exc)
                                await r.xadd(
                                    dlq_stream,
                                    {b"msg_id": str(msg_id).encode(), b"error": str(exc).encode()},
                                )
                                await r.xack(self.stream, self.group, msg_id)
                            except asyncio.CancelledError:
                                raise
                            except Exception as exc:
                                logger.error("Consumer error msg=%s: %s", msg_id, exc)
                                # No ACK — message will be redelivered

                    if had_events:
                        await self.post_batch()

                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    logger.error("Consumer loop error: %s", exc)

        finally:
            await r.aclose()
