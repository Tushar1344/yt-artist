"""Shared yt-dlp helpers used by fetcher and transcriber."""
from __future__ import annotations

import functools
import os
import shutil
import sys
from typing import List, Tuple

# Maximum concurrency for bulk yt-dlp operations.  Kept conservative to avoid
# triggering YouTube's adaptive rate-limiter.  Users can override via
# --concurrency but the CLI clamps to this ceiling.
MAX_CONCURRENCY = 3

# Default inter-video delay in seconds (between consecutive yt-dlp calls in
# bulk operations).  Overridable via YT_ARTIST_INTER_VIDEO_DELAY env var.
DEFAULT_INTER_VIDEO_DELAY: float = 2.0


@functools.lru_cache(maxsize=1)
def _resolve_base() -> Tuple[str, ...]:
    """Detect yt-dlp binary once and cache the result (avoids repeated shutil.which I/O)."""
    if shutil.which("yt-dlp"):
        return ("yt-dlp",)
    return (sys.executable, "-m", "yt_dlp")


def get_inter_video_delay() -> float:
    """Return the inter-video delay (seconds) for bulk operations.

    Reads YT_ARTIST_INTER_VIDEO_DELAY env var.  Falls back to
    DEFAULT_INTER_VIDEO_DELAY (2s).
    """
    raw = (os.environ.get("YT_ARTIST_INTER_VIDEO_DELAY") or "").strip()
    if raw:
        try:
            return max(0.0, float(raw))
        except ValueError:
            pass
    return DEFAULT_INTER_VIDEO_DELAY


def yt_dlp_cmd() -> List[str]:
    """Return base yt-dlp command with rate-limit sleep flags and optional cookie args.

    Rate-limit flags (always appended):
      --sleep-requests 1    – 1s pause between HTTP requests within a single yt-dlp run
      --sleep-subtitles 3   – 3s pause between subtitle download requests

    Cookie env vars (checked in order):
      YT_ARTIST_COOKIES_BROWSER  – browser name for --cookies-from-browser (e.g. "chrome")
      YT_ARTIST_COOKIES_FILE     – path to a Netscape cookies.txt for --cookies

    ⚠️  Cookie warning: using cookies ties automated activity to your Google
    account.  YouTube can (and does) suspend accounts used with automated tools.
    Use a throwaway / secondary account — never your primary Google account.
    """
    base = list(_resolve_base())

    # --- Rate-limit sleep flags (always included) ---
    sleep_requests = (os.environ.get("YT_ARTIST_SLEEP_REQUESTS") or "").strip()
    sleep_subtitles = (os.environ.get("YT_ARTIST_SLEEP_SUBTITLES") or "").strip()
    base += ["--sleep-requests", sleep_requests or "1"]
    base += ["--sleep-subtitles", sleep_subtitles or "3"]

    # --- Cookie flags ---
    cookies_browser = (os.environ.get("YT_ARTIST_COOKIES_BROWSER") or "").strip()
    if cookies_browser:
        return base + ["--cookies-from-browser", cookies_browser]

    cookies_file = (os.environ.get("YT_ARTIST_COOKIES_FILE") or "").strip()
    if cookies_file:
        return base + ["--cookies", cookies_file]

    return base


def channel_url_for(artist_id: str) -> str:
    """Build YouTube channel URL from an artist_id (@handle or channel_id)."""
    if artist_id.startswith("@"):
        return f"https://www.youtube.com/{artist_id}"
    return f"https://www.youtube.com/channel/{artist_id}"
