"""Work ledger helper: timing + best-effort recording of operations to the audit log."""

from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from typing import Optional

from yt_artist.storage import Storage

log = logging.getLogger("yt_artist.ledger")


def _now_iso() -> str:
    """Return current UTC timestamp in ISO-8601 format."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class WorkTimer:
    """Capture wall-clock timing for a work operation.

    Usage::

        timer = WorkTimer()
        # ... do work ...
        record_operation(storage, video_id=..., started_at=timer.started_at,
                        duration_ms=timer.elapsed_ms(), ...)
    """

    def __init__(self) -> None:
        self.started_at = _now_iso()
        self._t0 = time.monotonic()

    def elapsed_ms(self) -> int:
        """Return wall-clock milliseconds since timer was created."""
        return int((time.monotonic() - self._t0) * 1000)


def record_operation(
    storage: Storage,
    *,
    video_id: str,
    operation: str,
    status: str,
    started_at: str,
    duration_ms: int,
    model: Optional[str] = None,
    prompt_id: Optional[str] = None,
    strategy: Optional[str] = None,
    error_message: Optional[str] = None,
) -> None:
    """Write a single work ledger entry. Best-effort: never raises.

    Ledger failures must never break transcribe/summarize/score operations.
    """
    try:
        finished_at = _now_iso()
        storage.log_work(
            video_id=video_id,
            operation=operation,
            model=model,
            prompt_id=prompt_id,
            strategy=strategy,
            status=status,
            started_at=started_at,
            finished_at=finished_at,
            duration_ms=duration_ms,
            error_message=error_message,
        )
    except Exception:  # noqa: BLE001
        log.debug("Failed to write ledger entry for %s/%s", video_id, operation, exc_info=True)
