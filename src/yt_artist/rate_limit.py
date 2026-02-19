"""Rate-limit monitoring: log YouTube requests, warn when rate is high."""

from __future__ import annotations

import logging
import sys
from typing import Any, Dict, Optional

from yt_artist.storage import Storage

log = logging.getLogger("yt_artist.rate_limit")

# Thresholds for rate-limit warnings (requests per hour)
WARN_THRESHOLD_1H = 200
HIGH_THRESHOLD_1H = 400

# Cleanup: delete request_log rows older than this many hours
_CLEANUP_AGE_HOURS = 24


def log_request(storage: Storage, request_type: str) -> None:
    """Log a yt-dlp request and clean up old entries (>24h).

    Called after each successful yt-dlp subprocess call.
    """
    storage.log_rate_request(request_type, cleanup_age_hours=_CLEANUP_AGE_HOURS)


def count_requests(storage: Storage, hours: int = 1) -> int:
    """Count yt-dlp requests in the last *hours* hours."""
    return storage.count_rate_requests(hours)


def get_rate_status(storage: Storage) -> Dict[str, Any]:
    """Return rate info for status display.

    Returns dict with keys: count_1h, count_24h, warning (str or None).
    """
    count_1h = count_requests(storage, 1)
    count_24h = count_requests(storage, 24)
    warning: Optional[str] = None
    if count_1h >= HIGH_THRESHOLD_1H:
        warning = (
            f"High request rate: {count_1h} YouTube requests in last hour "
            f"(threshold: {HIGH_THRESHOLD_1H}). Consider reducing --concurrency or waiting."
        )
    elif count_1h >= WARN_THRESHOLD_1H:
        warning = (
            f"Elevated request rate: {count_1h} YouTube requests in last hour "
            f"(threshold: {WARN_THRESHOLD_1H}). Monitor for 429 errors."
        )
    return {"count_1h": count_1h, "count_24h": count_24h, "warning": warning}


def check_rate_warning(storage: Storage, quiet: bool = False) -> None:
    """Print a warning to stderr if YouTube request rate is high.

    Called before bulk operations (transcribe, summarize) to alert users proactively.
    """
    if quiet:
        return
    status = get_rate_status(storage)
    if status["warning"]:
        sys.stderr.write(f"\n  \u26a0\ufe0f  {status['warning']}\n\n")
