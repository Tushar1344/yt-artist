"""Fetch channel video list via yt-dlp; write urllist markdown and upsert Artist + Video rows."""
from __future__ import annotations

import json
import logging
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

from yt_artist.storage import Storage
from yt_artist.yt_dlp_util import yt_dlp_cmd as _yt_dlp_cmd

log = logging.getLogger("yt_artist.fetcher")


def _run_yt_dlp_flat_playlist_json(channel_url: str, storage: Optional[Storage] = None) -> List[Dict[str, Any]]:
    """Run yt-dlp --flat-playlist -j and return list of parsed JSON entries (id, url, title)."""
    cmd = _yt_dlp_cmd() + [
        "--flat-playlist",
        "-j",
        "--no-warnings",
        channel_url,
    ]
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=120,
        )
        result.check_returncode()
    except subprocess.TimeoutExpired:
        raise RuntimeError(f"yt-dlp timed out fetching playlist for {channel_url}")
    except subprocess.CalledProcessError as e:
        stderr = (e.stderr or "").strip()
        raise RuntimeError(f"yt-dlp failed (exit {e.returncode}) for {channel_url}: {stderr}") from e
    # Log request for rate-limit monitoring
    if storage is not None:
        try:
            from yt_artist.rate_limit import log_request
            log_request(storage, "playlist")
        except Exception:  # noqa: BLE001
            pass  # best-effort
    entries: List[Dict[str, Any]] = []
    for line in result.stdout.strip().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            log.debug("Skipping unparseable yt-dlp line: %.100s", line)
            continue
        video_id = obj.get("id")
        if not video_id:
            continue
        url = obj.get("url") or f"https://www.youtube.com/watch?v={video_id}"
        title = obj.get("title") or ""
        entry = {"id": video_id, "url": url, "title": title}
        for key in ("channel_id", "channel", "uploader_id", "uploader"):
            if obj.get(key):
                entry[key] = obj[key]
        entries.append(entry)
    return entries


def _channel_id_and_name_from_entries(
    channel_url: str, entries: List[Dict[str, Any]]
) -> Tuple[str, str]:
    """Derive channel id and name from first entry or channel URL. Fallback to URL-based id/name."""
    if entries:
        first = entries[0]
        cid = first.get("channel_id") or first.get("uploader_id") or ""
        name = first.get("channel") or first.get("uploader") or "Channel"
        if cid:
            return (cid, name)
    # Fallback: use channel URL as id (e.g. @handle or /channel/UC...)
    base = channel_url.rstrip("/").split("/")[-1] or "channel"
    # Prefer title-case handle for @handle URLs (e.g. @hubermanlab -> Huberman Lab)
    if base.startswith("@"):
        name = base[1:].replace("_", " ").replace("-", " ").title()
        return (base, name)
    return (base, "Channel")


def _video_metadata(video_url: str, storage: Optional[Storage] = None) -> Dict[str, Any]:
    """Run yt-dlp -j on a single video URL; return parsed JSON (one object)."""
    cmd = _yt_dlp_cmd() + [
        "-j",
        "--no-warnings",
        "--no-playlist",
        video_url,
    ]
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=60,
        )
        result.check_returncode()
    except subprocess.TimeoutExpired:
        raise RuntimeError(f"yt-dlp timed out fetching metadata for {video_url}")
    except subprocess.CalledProcessError as e:
        stderr = (e.stderr or "").strip()
        raise RuntimeError(f"yt-dlp failed (exit {e.returncode}) for {video_url}: {stderr}") from e
    # Log request for rate-limit monitoring
    if storage is not None:
        try:
            from yt_artist.rate_limit import log_request
            log_request(storage, "metadata")
        except Exception:  # noqa: BLE001
            pass  # best-effort
    try:
        return json.loads(result.stdout.strip())
    except json.JSONDecodeError as e:
        raise RuntimeError(f"yt-dlp returned non-JSON output for {video_url}") from e


def _channel_info_from_video_metadata(meta: Dict[str, Any], video_url: str) -> Tuple[str, str, str, str, str]:
    """
    Extract (artist_id, artist_name, channel_url, video_id, title) from single-video yt-dlp JSON.
    artist_id style matches _channel_id_and_name_from_entries (handle or channel id).
    """
    video_id = meta.get("id") or ""
    title = meta.get("title") or ""
    # Prefer uploader_id (often @handle) for consistency with flat-playlist entries
    uploader_id = (meta.get("uploader_id") or "").strip()
    channel_id = (meta.get("channel_id") or meta.get("uploader_id") or "").strip()
    channel = (meta.get("channel") or meta.get("uploader") or "Channel").strip()
    # Build channel_url and artist_id to match fetcher convention
    if uploader_id.startswith("@"):
        artist_id = uploader_id
        channel_url = f"https://www.youtube.com/{uploader_id}"
    elif channel_id:
        artist_id = channel_id
        channel_url = f"https://www.youtube.com/channel/{channel_id}"
    else:
        artist_id = channel or "channel"
        channel_url = video_url  # fallback
    return (artist_id, channel, channel_url, video_id, title)


def get_channel_info_for_video(video_url: str, storage: Optional[Storage] = None) -> Tuple[str, str, str, str, str]:
    """
    Get (artist_id, artist_name, channel_url, video_id, title) for a single video URL or id.
    Uses yt-dlp -j --no-playlist. Raises on failure.
    """
    meta = _video_metadata(video_url, storage=storage)
    return _channel_info_from_video_metadata(meta, video_url)


def ensure_artist_and_video_for_video_url(
    video_url: str,
    storage: Storage,
    data_dir: Union[str, Path],
) -> Tuple[str, str]:
    """
    Ensure artist and video exist in DB. Upserts just the single artist + video
    from the video's metadata — does NOT fetch the entire channel playlist.
    Returns (artist_id, video_id).
    """
    # Fast path: extract video_id from URL locally (no subprocess) and check DB.
    from yt_artist.transcriber import extract_video_id
    try:
        vid_candidate = extract_video_id(video_url)
        video = storage.get_video(vid_candidate)
        if video:
            artist = storage.get_artist(video["artist_id"])
            if artist:
                log.debug("DB hit for video %s — skipping yt-dlp metadata fetch.", vid_candidate)
                return (video["artist_id"], vid_candidate)
    except ValueError:
        pass  # URL format not parseable client-side; fall through to yt-dlp

    artist_id, artist_name, channel_url, video_id, title = get_channel_info_for_video(video_url, storage=storage)
    artist = storage.get_artist(artist_id)
    video = storage.get_video(video_id)
    if artist and video:
        return (artist_id, video_id)
    # Upsert just this artist + video from single-video metadata (fast path).
    if not artist:
        urllist_path = storage.urllist_path(artist_id, artist_name)
        storage.upsert_artist(
            artist_id=artist_id,
            name=artist_name,
            channel_url=channel_url,
            urllist_path=urllist_path,
        )
        log.info("Auto-created artist %s (%s) from video metadata.", artist_id, artist_name)
    if not video:
        url = video_url if video_url.startswith("http") else f"https://www.youtube.com/watch?v={video_id}"
        storage.upsert_video(
            video_id=video_id,
            artist_id=artist_id,
            url=url,
            title=title or None,
        )
        log.info("Auto-created video %s for artist %s.", video_id, artist_id)
    return (artist_id, video_id)


def fetch_channel(
    channel_url: str,
    storage: Storage,
    data_dir: Union[str, Path],
) -> Tuple[str, int]:
    """
    Fetch all video URLs for a channel; write urllist markdown and upsert Artist + Video rows.
    Returns (urllist_path, video_count).
    """
    data_dir = Path(data_dir)
    entries = _run_yt_dlp_flat_playlist_json(channel_url, storage=storage)
    if not entries:
        raise ValueError(f"No video entries returned for {channel_url}")

    artist_id, artist_name = _channel_id_and_name_from_entries(channel_url, entries)
    urllist_path = storage.urllist_path(artist_id, artist_name)
    full_path = data_dir / urllist_path
    full_path.parent.mkdir(parents=True, exist_ok=True)

    lines = [f"# {artist_name}\n", f"# Channel: {channel_url}\n\n"]
    for e in entries:
        lines.append(f"- {e['url']}  ({e['title']})\n")
    full_path.write_text("".join(lines), encoding="utf-8")

    # Batch all DB writes in a single transaction (one connection, one commit).
    with storage.transaction() as conn:
        conn.execute(
            """
            INSERT INTO artists (id, name, channel_url, urllist_path)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                name = excluded.name,
                channel_url = excluded.channel_url,
                urllist_path = excluded.urllist_path
            """,
            (artist_id, artist_name, channel_url, urllist_path),
        )
        for e in entries:
            conn.execute(
                """
                INSERT INTO videos (id, artist_id, url, title)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    artist_id = excluded.artist_id,
                    url = excluded.url,
                    title = excluded.title,
                    fetched_at = datetime('now')
                """,
                (e["id"], artist_id, e["url"], e["title"] or ""),
            )

    return (urllist_path, len(entries))
