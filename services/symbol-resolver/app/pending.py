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
      2. DEL — clear the list
      3. For each ref: attempt resolution; push back if unresolved and not expired.

    attempt_count is incremented before writing back so the next cycle sees the
    updated count.
    """
    key = f"pending_refs:{repo}"
    raw_entries = r.lrange(key, 0, -1)
    if not raw_entries:
        return

    r.delete(key)

    for raw in raw_entries:
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

        resolved = resolve_import(
            driver,
            ref.source_stable_id,
            ref.target_name,
            ref.path_hint,
            ref.repo,
        )

        if resolved:
            logger.debug("Resolved pending ref: target=%s", ref.target_name)
        else:
            ref = ref.model_copy(update={
                "attempt_count": ref.attempt_count + 1,
                "attempted_at": datetime.now(timezone.utc),
            })
            push_pending(r, ref)
