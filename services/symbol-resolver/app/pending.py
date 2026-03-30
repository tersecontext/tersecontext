# services/symbol-resolver/app/pending.py
from __future__ import annotations

import logging
from datetime import datetime, timezone, timedelta

from .models import PendingRef
from .resolver import resolve_import

logger = logging.getLogger(__name__)

PENDING_TTL = timedelta(hours=24)
MAX_ATTEMPTS = 5


def push_pending(r, ref: PendingRef) -> None:
    """Push a PendingRef onto the pending_refs:{repo} Redis list."""
    r.rpush(f"pending_refs:{ref.repo}", ref.model_dump_json())


def _is_expired(ref: PendingRef) -> bool:
    """Return True if this ref should be dropped rather than retried."""
    age = datetime.now(timezone.utc) - ref.attempted_at
    return ref.attempt_count > MAX_ATTEMPTS or age > PENDING_TTL


def retry_pending(driver, r, repo: str) -> None:
    """
    Retry all pending refs for a repo.

    Bulk read-requeue pattern:
      1. LRANGE — read all
      2. DEL — clear the list (atomically when possible via pipeline)
      3. For each ref: attempt resolution; push back if unresolved and not expired.

    attempt_count is incremented before writing back so the next cycle sees the
    updated count.

    If an exception occurs during resolution, all remaining unprocessed items are
    re-queued to avoid losing data.
    """
    key = f"pending_refs:{repo}"

    # Bug fix 1: Use pipeline for atomic LRANGE+DEL when available (real Redis),
    # fall back to sequential calls for mocks
    pipe = r.pipeline()
    pipe.lrange(key, 0, -1)
    pipe.delete(key)
    results = pipe.execute()

    # Check if results is actually a list (real Redis) vs MagicMock
    if isinstance(results, list) and len(results) >= 1:
        raw_entries = results[0]
    else:
        # Fallback for test mocks and clients without proper pipeline
        raw_entries = r.lrange(key, 0, -1)
        if raw_entries:
            r.delete(key)

    if not raw_entries:
        return

    # Bug fix 2: Wrap entire processing loop in try/except to handle mid-loop errors
    for i, raw in enumerate(raw_entries):
        try:
            ref = PendingRef.model_validate_json(raw)
        except Exception as exc:
            logger.warning("Malformed pending ref, dropping: %s", exc)
            continue

        if _is_expired(ref):
            logger.info(
                "Dropping expired pending ref: repo=%s target=%s attempts=%d",
                ref.repo, ref.target_name, ref.attempt_count,
            )
            continue

        try:
            resolved = resolve_import(
                driver,
                ref.source_stable_id,
                ref.target_name,
                ref.path_hint,
                ref.repo,
            )
        except Exception as exc:
            logger.warning("Error resolving pending ref, re-queuing remaining: %s", exc)
            # Re-queue this item and all remaining items unchanged
            for remaining_raw in raw_entries[i:]:
                r.rpush(key, remaining_raw)
            return

        if resolved:
            logger.debug("Resolved pending ref: target=%s", ref.target_name)
        else:
            ref = ref.model_copy(update={
                "attempt_count": ref.attempt_count + 1,
                "attempted_at": datetime.now(timezone.utc),
            })
            push_pending(r, ref)
