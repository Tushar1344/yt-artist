"""Shared yt-dlp helpers used by fetcher and transcriber."""
from __future__ import annotations

import functools
import os
import re
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
    """Return base yt-dlp command with rate-limit sleep flags, optional cookies, and PO token.

    Rate-limit flags (always appended):
      --sleep-requests 1    – 1s pause between HTTP requests within a single yt-dlp run
      --sleep-subtitles 3   – 3s pause between subtitle download requests

    Cookie env vars (checked in order):
      YT_ARTIST_COOKIES_BROWSER  – browser name for --cookies-from-browser (e.g. "chrome")
      YT_ARTIST_COOKIES_FILE     – path to a Netscape cookies.txt for --cookies

    PO token env var:
      YT_ARTIST_PO_TOKEN  – proof-of-origin token for YouTube bot detection bypass.
                            See https://github.com/yt-dlp/yt-dlp/wiki/PO-Token-Guide

    Cookies and PO token can be used together (they serve different purposes:
    cookies = session auth, PO token = proof of origin for bot detection).

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
        base += ["--cookies-from-browser", cookies_browser]
    else:
        cookies_file = (os.environ.get("YT_ARTIST_COOKIES_FILE") or "").strip()
        if cookies_file:
            base += ["--cookies", cookies_file]

    # --- PO token (proof of origin for YouTube bot detection) ---
    po_token = (os.environ.get("YT_ARTIST_PO_TOKEN") or "").strip()
    if po_token:
        base += ["--extractor-args", f"youtube:po_token={po_token}"]

    return base


def get_auth_config() -> dict:
    """Return current YouTube authentication configuration for diagnostics."""
    return {
        "cookies_browser": (os.environ.get("YT_ARTIST_COOKIES_BROWSER") or "").strip(),
        "cookies_file": (os.environ.get("YT_ARTIST_COOKIES_FILE") or "").strip(),
        "po_token": bool((os.environ.get("YT_ARTIST_PO_TOKEN") or "").strip()),
    }


def channel_url_for(artist_id: str) -> str:
    """Build YouTube channel URL from an artist_id (@handle or channel_id)."""
    if artist_id.startswith("@"):
        return f"https://www.youtube.com/{artist_id}"
    return f"https://www.youtube.com/channel/{artist_id}"


# ---------------------------------------------------------------------------
# URL validation
# ---------------------------------------------------------------------------

# Accepted YouTube hostnames (bare and www).
_YT_HOSTS = {
    "youtube.com",
    "www.youtube.com",
    "m.youtube.com",
    "youtu.be",
    "www.youtu.be",
    "music.youtube.com",
}

# Channel URL patterns: /@handle, /channel/UC..., /c/name, /user/name
_CHANNEL_PATH_RE = re.compile(
    r"^/(@[\w.-]+|channel/[\w-]+|c/[\w.-]+|user/[\w.-]+)/?$"
)

# Video URL patterns: /watch?v=..., /shorts/..., /embed/..., /v/...
_VIDEO_PATH_RE = re.compile(
    r"^/(watch|shorts/[\w-]+|embed/[\w-]+|v/[\w-]+|live/[\w-]+)"
)


def validate_youtube_channel_url(url: str) -> str:
    """Validate and normalize a YouTube channel URL.

    Raises SystemExit with actionable message on bad input.
    Returns the cleaned URL.
    """
    url = url.strip()
    if not url:
        raise SystemExit(
            "Channel URL is empty.\n"
            "  Expected: https://www.youtube.com/@handle\n"
            "  Example:  https://www.youtube.com/@hubermanlab"
        )

    # Bare @handle shorthand — expand to full URL.
    if url.startswith("@") and "/" not in url:
        return f"https://www.youtube.com/{url}"

    # Must look like a URL.
    if not url.startswith(("http://", "https://")):
        raise SystemExit(
            f"Not a valid URL: {url}\n"
            "  Expected: https://www.youtube.com/@handle\n"
            "  Or use a bare @handle: yt-artist fetch-channel @hubermanlab"
        )

    # Parse host.
    try:
        from urllib.parse import urlparse
        parsed = urlparse(url)
    except Exception:
        raise SystemExit(f"Cannot parse URL: {url}")

    host = (parsed.hostname or "").lower()
    if host not in _YT_HOSTS:
        raise SystemExit(
            f"Not a YouTube URL: {url}\n"
            f"  Host '{host}' is not recognized as YouTube.\n"
            "  Expected: https://www.youtube.com/@handle"
        )

    path = parsed.path or "/"
    # Reject video URLs passed to channel commands.
    if _VIDEO_PATH_RE.match(path):
        raise SystemExit(
            f"This looks like a video URL, not a channel URL: {url}\n"
            "  To transcribe a single video:  yt-artist transcribe \"{url}\"\n"
            "  For a channel URL, use:        https://www.youtube.com/@handle"
        )

    # Validate channel path pattern.
    if not _CHANNEL_PATH_RE.match(path):
        raise SystemExit(
            f"Unrecognized YouTube channel path: {path}\n"
            "  Expected formats:\n"
            "    https://www.youtube.com/@handle\n"
            "    https://www.youtube.com/channel/UC...\n"
            "    https://www.youtube.com/c/ChannelName"
        )

    return url


def validate_youtube_video_url(url_or_id: str) -> str:
    """Validate a YouTube video URL or bare video ID.

    Raises SystemExit with actionable message on bad input.
    Returns the cleaned input (URL or bare ID).
    """
    url_or_id = url_or_id.strip()
    if not url_or_id:
        raise SystemExit(
            "Video URL or ID is empty.\n"
            "  Expected: https://www.youtube.com/watch?v=VIDEO_ID\n"
            "  Or a bare video ID: dQw4w9WgXcQ"
        )

    # Bare video ID — allow through (transcriber.extract_video_id validates format).
    if re.match(r"^[\w-]{8,}$", url_or_id):
        return url_or_id

    # Must be a URL at this point.
    if not url_or_id.startswith(("http://", "https://")):
        raise SystemExit(
            f"Not a valid video URL or ID: {url_or_id}\n"
            "  Expected: https://www.youtube.com/watch?v=VIDEO_ID\n"
            "  Or a bare video ID (e.g. dQw4w9WgXcQ)"
        )

    try:
        from urllib.parse import urlparse, parse_qs
        parsed = urlparse(url_or_id)
    except Exception:
        raise SystemExit(f"Cannot parse URL: {url_or_id}")

    host = (parsed.hostname or "").lower()
    if host not in _YT_HOSTS:
        raise SystemExit(
            f"Not a YouTube URL: {url_or_id}\n"
            f"  Host '{host}' is not recognized as YouTube.\n"
            "  Expected: https://www.youtube.com/watch?v=VIDEO_ID"
        )

    # Check that a video ID is extractable.
    path = parsed.path or "/"
    qs = parse_qs(parsed.query)
    has_v_param = "v" in qs
    is_video_path = bool(_VIDEO_PATH_RE.match(path)) or path.startswith("/v/")
    is_shortlink = host in ("youtu.be", "www.youtu.be")

    if not has_v_param and not is_video_path and not is_shortlink:
        # Might be a channel URL passed to a video command.
        if _CHANNEL_PATH_RE.match(path):
            raise SystemExit(
                f"This looks like a channel URL, not a video URL: {url_or_id}\n"
                "  To fetch a channel:  yt-artist fetch-channel \"{url_or_id}\"\n"
                "  For a video, use:    https://www.youtube.com/watch?v=VIDEO_ID"
            )
        raise SystemExit(
            f"Cannot find a video ID in this URL: {url_or_id}\n"
            "  Expected: https://www.youtube.com/watch?v=VIDEO_ID\n"
            "  Or: https://youtu.be/VIDEO_ID"
        )

    return url_or_id
