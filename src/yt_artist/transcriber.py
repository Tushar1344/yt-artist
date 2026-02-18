"""Fetch video transcript via yt-dlp; save to DB and optional file."""

from __future__ import annotations

import json
import logging
import os
import re
import subprocess
import tempfile
import time as _time
from pathlib import Path
from typing import List, Optional, Tuple, Union

from yt_artist.storage import Storage
from yt_artist.yt_dlp_util import yt_dlp_cmd as _yt_dlp_cmd

log = logging.getLogger("yt_artist.transcriber")


def _get_available_sub_langs(video_url: str) -> List[str]:
    """Run yt-dlp -j to get exact subtitle/automatic_caption language codes offered by the video."""
    cmd = _yt_dlp_cmd() + [
        "--skip-download",
        "--no-warnings",
        "-j",
        video_url,
    ]
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=60,
        )
    except subprocess.TimeoutExpired:
        log.warning("yt-dlp timed out detecting subtitle languages for %s", video_url)
        return []
    if result.returncode != 0:
        log.debug("yt-dlp subtitle detection exited %d for %s", result.returncode, video_url)
        return []
    try:
        info = json.loads(result.stdout)
    except (json.JSONDecodeError, ValueError):
        log.debug("yt-dlp returned non-JSON for subtitle detection on %s", video_url)
        return []
    codes = []
    for key in ("subtitles", "automatic_captions"):
        raw = info.get(key) or {}
        if isinstance(raw, dict):
            codes.extend(raw.keys())

    # Prefer English-like codes first
    def rank(c: str) -> Tuple[int, str]:
        c_lower = c.lower()
        if c_lower.startswith("en") or ".en" in c_lower or c_lower == "a.en":
            return (0, c)
        return (1, c)

    codes = sorted(set(codes), key=rank)
    return codes


def extract_video_id(url_or_id: str) -> str:
    """Return video id from URL or bare id."""
    if not url_or_id:
        raise ValueError("url_or_id is required")
    # Bare id (e.g. dQw4w9WgXcQ - typically 11 chars)
    if re.match(r"^[\w-]{8,}$", url_or_id):
        return url_or_id
    # ?v=id or &v=id
    m = re.search(r"[?&]v=([\w-]{8,})", url_or_id)
    if m:
        return m.group(1)
    # youtu.be/id
    m = re.search(r"youtu\.be/([\w-]{8,})", url_or_id)
    if m:
        return m.group(1)
    raise ValueError(f"Cannot extract video id from: {url_or_id}")


def _find_subtitle_file(out_dir: Path) -> Optional[Tuple[str, str]]:
    """Return (raw_text, format) from first subtitle file, preferring English-named files."""
    exts = (".vtt", ".srt", ".ass", ".json3")
    files = []
    for f in out_dir.rglob("*") if out_dir.exists() else []:
        if f.is_file() and f.suffix.lower() in exts:
            files.append(f)
    if not files:
        return None
    # Prefer filename containing .en. or .en (e.g. id.en.vtt) so we get English when multiple exist
    files.sort(key=lambda p: (0 if ".en" in p.stem.lower() else 1, p.name))
    f = files[0]
    raw = f.read_text(encoding="utf-8", errors="replace")
    fmt = f.suffix.lstrip(".").lower()
    return (_subs_to_plain_text(raw, fmt), fmt)


def _build_sub_download_cmd(video_url: str, out_tmpl: str, sub_langs: Optional[str]) -> List[str]:
    """Build yt-dlp subtitle download command."""
    cmd = _yt_dlp_cmd() + [
        "--write-auto-sub",
        "--write-sub",
        "--skip-download",
        "--no-warnings",
        "-o",
        out_tmpl,
        "--sub-format",
        "vtt/best",
    ]
    if sub_langs is not None:
        cmd.extend(["--sub-langs", sub_langs])
    cmd.append(video_url)
    return cmd


def _is_rate_limited(stderr: str) -> bool:
    """Return True if yt-dlp stderr indicates a YouTube rate-limit (HTTP 429 or similar)."""
    lower = stderr.lower()
    return "429" in lower or "too many requests" in lower or "rate limit" in lower


# --- Patterns for classifying yt-dlp authentication / bot-detection errors ---
_AGE_PATTERNS = [
    "age-restricted",
    "age restricted",
    "sign in to confirm your age",
    "age gate",
    "age verification",
]
_AUTH_PATTERNS = [
    "sign in to confirm",
    "login required",
    "account required",
    "this video requires payment",
    "join this channel",
    "members only",
    "private video",
]
_BOT_PATTERNS = [
    "confirm you're not a bot",
    "unusual traffic",
    "automated",
    "captcha",
    "forbidden",
    "403",
]

_AUTH_GUIDANCE = (
    "  Set YT_ARTIST_COOKIES_BROWSER=chrome         (or firefox/safari)\n"
    "  Set YT_ARTIST_PO_TOKEN=web.subs+<token>      (proof of origin)\n"
    "  PO token guide: https://github.com/yt-dlp/yt-dlp/wiki/PO-Token-Guide\n"
    "  Run `yt-artist doctor` to check your configuration."
)


def _classify_yt_dlp_error(stderr: str) -> Tuple[str, str]:
    """Classify a yt-dlp error and return (error_type, user_message).

    error_type is one of: 'rate_limit', 'age_restricted', 'auth_required',
    'bot_detected', or 'generic'.  For non-generic types the user_message
    contains actionable guidance (PO token, cookies, doctor command).
    """
    lower = stderr.lower()

    if _is_rate_limited(stderr):
        return ("rate_limit", "YouTube rate-limited this request (HTTP 429).")

    for p in _AGE_PATTERNS:
        if p in lower:
            return (
                "age_restricted",
                f"This video is age-restricted. YouTube requires authentication.\n{_AUTH_GUIDANCE}",
            )

    for p in _AUTH_PATTERNS:
        if p in lower:
            return ("auth_required", f"YouTube requires authentication for this content.\n{_AUTH_GUIDANCE}")

    for p in _BOT_PATTERNS:
        if p in lower:
            return (
                "bot_detected",
                f"YouTube detected automated access and is blocking requests.\n"
                f"This usually means you need a PO (proof of origin) token.\n{_AUTH_GUIDANCE}",
            )

    return ("generic", "")


_MAX_429_RETRIES = 3
_INITIAL_BACKOFF = 5


def _run_yt_dlp_with_backoff(
    cmd: List[str],
    video_url: str,
    out_dir: Path,
    label: str = "download",
    storage: Optional[Storage] = None,
) -> Tuple[str, str, bool]:
    """Run a yt-dlp command with exponential backoff on HTTP 429.

    Returns (stdout, stderr, timed_out).  Raises FileNotFoundError on
    exhausted 429 retries or auth/bot errors.

    When *storage* is provided, logs each request to the rate-limit monitor.
    """
    backoff = _INITIAL_BACKOFF
    for attempt in range(_MAX_429_RETRIES + 1):
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=120,
                cwd=str(out_dir),
            )
        except subprocess.TimeoutExpired:
            log.warning("yt-dlp %s timed out for %s", label, video_url)
            return ("", "", True)
        stdout = result.stdout or ""
        stderr = result.stderr or ""
        if _is_rate_limited(stderr):
            if attempt < _MAX_429_RETRIES:
                log.warning("Rate limited (429) during %s for %s — backing off %ds", label, video_url, backoff)
                _time.sleep(backoff)
                backoff = min(backoff * 2, 60)
                continue
            log.error("Rate limited after %d retries for %s — aborting.", _MAX_429_RETRIES, video_url)
            raise FileNotFoundError(
                f"YouTube rate-limited (HTTP 429) after {_MAX_429_RETRIES} retries for {video_url}. "
                "Try again later, reduce --concurrency, or set YT_ARTIST_COOKIES_BROWSER=chrome for higher rate limits."
            )
        # Log the request for rate-limit monitoring
        if storage is not None:
            try:
                from yt_artist.rate_limit import log_request

                log_request(storage, "subtitle_download")
            except Exception:  # noqa: BLE001
                pass  # rate logging is best-effort; don't break transcription
        # Non-429 error classification
        err_type, err_msg = _classify_yt_dlp_error(stderr)
        if err_type not in ("rate_limit", "generic"):
            raise FileNotFoundError(f"yt-dlp cannot download subtitles for {video_url}.\n{err_msg}")
        return (stdout, stderr, False)
    # Should not reach here but just in case
    return ("", "", False)


def _run_yt_dlp_subtitles(video_url: str, out_dir: Path, storage: Optional[Storage] = None) -> Tuple[str, str]:
    """
    Run yt-dlp --write-auto-sub --write-sub --skip-download; return (raw_text, format).

    Strategy (sequential, rate-limit safe):
      1. Optimistic English download (en,a.en,en-US,en-GB,en.*) — succeeds for
         ~80% of YouTube videos with no extra metadata request.
      2. On miss: fetch subtitle language list via -j, then retry with detected languages.
      3. Final fallbacks: --sub-langs all, then omit --sub-langs.

    Each subprocess call respects --sleep-requests / --sleep-subtitles set in
    yt_dlp_cmd().  Exponential backoff is applied on HTTP 429 errors.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    out_tmpl = str(out_dir.resolve()).replace("\\", "/") + "/%(id)s.%(ext)s"
    last_stdout, last_stderr = "", ""

    # --- Step 1: Optimistic English download (single yt-dlp call) ---
    cmd = _build_sub_download_cmd(video_url, out_tmpl, "en,a.en,en-US,en-GB,en.*")
    last_stdout, last_stderr, timed_out = _run_yt_dlp_with_backoff(
        cmd, video_url, out_dir, "optimistic English", storage=storage
    )
    if not timed_out:
        found = _find_subtitle_file(out_dir)
        if found:
            log.debug("Optimistic English subtitle download succeeded for %s", video_url)
            return found

    # --- Step 2: Metadata-informed retry (only runs if optimistic English missed) ---
    json_langs = _get_available_sub_langs(video_url)

    if json_langs:
        sub_langs_list: List[Optional[str]] = [",".join(json_langs), "all", None]
    else:
        sub_langs_list = [
            "all",  # fallback: accept any available language
            None,  # final fallback: omit --sub-langs entirely, let yt-dlp pick
        ]

    for attempt, sub_langs in enumerate(sub_langs_list):
        cmd = _build_sub_download_cmd(video_url, out_tmpl, sub_langs)
        stdout, stderr, timed_out = _run_yt_dlp_with_backoff(
            cmd,
            video_url,
            out_dir,
            f"attempt {attempt + 1}",
            storage=storage,
        )
        last_stdout, last_stderr = stdout, stderr
        if timed_out:
            continue
        found = _find_subtitle_file(out_dir)
        if found:
            return found

    # Classify the error — give specific guidance for auth/bot issues.
    combined_stderr = (last_stdout + last_stderr).strip()
    err_type, err_msg = _classify_yt_dlp_error(combined_stderr)
    if err_type not in ("rate_limit", "generic"):
        raise FileNotFoundError(f"yt-dlp cannot download subtitles for {video_url}.\n{err_msg}")

    yt_out = combined_stderr
    hint = f" yt-dlp: {yt_out[:400]}" if yt_out else ""
    langs_hint = f" Detected subtitle languages: {', '.join(json_langs[:10])}" if json_langs else ""
    msg = f"No subtitle file written under {out_dir}. yt-dlp reports no subtitle tracks are available for download."
    msg += langs_hint
    msg += (
        " Note: Some videos show subtitles in the browser player but don't expose them via the API. "
        "Try another video or check if the video has region restrictions."
    )
    # Check PO token status and provider plugin — give the most relevant hint.
    has_manual_token = bool((os.environ.get("YT_ARTIST_PO_TOKEN") or "").strip())
    has_provider = False
    try:
        from importlib.metadata import distribution

        distribution("yt-dlp-get-pot-rustypipe")
        has_provider = True
    except Exception:
        pass
    if not has_manual_token and not has_provider:
        msg += (
            "\n\n  No PO token provider installed — this is the most common cause of subtitle failures.\n"
            "  Fix: pip install yt-dlp-get-pot-rustypipe   (recommended, generates tokens automatically)\n"
            "  Or:  export YT_ARTIST_PO_TOKEN=web.subs+<token>   (manual fallback)\n"
            "  Guide: https://github.com/yt-dlp/yt-dlp/wiki/PO-Token-Guide\n"
            "  Run `yt-artist doctor` to check your full configuration."
        )
    elif not has_manual_token and has_provider:
        msg += (
            "\n\n  PO token provider (rustypipe) is installed but subtitles still failed.\n"
            "  This video may genuinely have no downloadable subtitles.\n"
            "  Run `yt-artist doctor` to check your configuration."
        )
    msg += hint
    raise FileNotFoundError(msg)


def _subs_to_plain_text(content: str, format_hint: str) -> str:
    """Strip timestamps, metadata, and consecutive duplicates; return plain text."""
    lines = content.strip().splitlines()
    text_lines: list[str] = []
    prev_line = ""
    for line in lines:
        line = line.strip()
        if not line:
            continue
        # WEBVTT header
        if line.upper().startswith("WEBVTT") or line.upper().startswith("KIND:"):
            continue
        # Numbered line (SRT)
        if re.match(r"^\d+$", line):
            continue
        # Timestamp line (00:00:00.000 --> 00:00:01.000 or 00:00:00,000)
        if re.match(r"^\d{2}:\d{2}(:\d{2})?[.,]\d{3}\s*-->\s*\d{2}:\d{2}", line):
            continue
        # VTT cue settings (align:start etc.)
        if re.match(r"^\s*(?:align|position|line|size):", line):
            continue
        # Remove inline timestamps in VTT (e.g. <00:00:00.000>)
        line = re.sub(r"<\d{2}:\d{2}(:\d{2})?\.\d{3}>", "", line)
        line = re.sub(r"<[^>]+>", "", line)  # other tags
        line = line.strip()
        # Skip consecutive duplicate lines (common in auto-generated VTT)
        if line and line != prev_line:
            text_lines.append(line)
            prev_line = line
    return "\n".join(text_lines)


def transcribe(
    video_url_or_id: str,
    storage: Storage,
    *,
    artist_id: Optional[str] = None,
    write_transcript_file: bool = False,
    data_dir: Optional[Union[str, Path]] = None,
) -> str:
    """
    Fetch transcript for the video, save to Transcript table and optionally to
    data/artists/{artist_id}/transcripts/{video_id}.txt.
    Returns video_id.
    """
    video_id = extract_video_id(video_url_or_id)
    url = video_url_or_id if video_url_or_id.startswith("http") else f"https://www.youtube.com/watch?v={video_id}"

    with tempfile.TemporaryDirectory(prefix="yt_artist_") as tmp:
        out_dir = Path(tmp) / "subs"
        out_dir.mkdir(parents=True, exist_ok=True)
        raw_text, fmt = _run_yt_dlp_subtitles(url, out_dir, storage=storage)

    storage.save_transcript(video_id=video_id, raw_text=raw_text, format=fmt)

    if write_transcript_file and data_dir is not None and artist_id:
        from yt_artist.paths import transcript_file

        out_file = transcript_file(Path(data_dir), artist_id, video_id)
        out_file.parent.mkdir(parents=True, exist_ok=True)
        out_file.write_text(raw_text, encoding="utf-8")

    return video_id
