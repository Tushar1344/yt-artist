"""Export/backup: write DB contents to portable JSON or CSV files.

Produces chunked JSON (per-artist, N videos per file) or flat CSV tables.
Each JSON chunk is self-contained (includes artist metadata + prompts).
Uses stdlib only — zero external dependencies.
"""

from __future__ import annotations

import csv
import json
import logging
import re
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from yt_artist import __version__
from yt_artist.storage import Storage

log = logging.getLogger("yt_artist.exporter")

EXPORT_VERSION = 1
DEFAULT_CHUNK_SIZE = 50


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _sanitize_dirname(name: str) -> str:
    """Replace filesystem-unsafe chars in *name*."""
    return re.sub(r"[^\w@\-]", "_", name).strip("_") or "unknown"


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _make_export_dir(base_dir: Path, timestamp: str | None = None) -> Path:
    """Create and return timestamped export directory under *base_dir*."""
    ts = timestamp or datetime.now().strftime("%Y%m%d_%H%M%S")
    out = base_dir / f"export_{ts}"
    out.mkdir(parents=True, exist_ok=True)
    return out


def _zip_file(path: Path) -> Path:
    """Compress *path* into a ``.zip``, delete original, return zip path."""
    zip_path = path.with_suffix(path.suffix + ".zip")
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.write(path, arcname=path.name)
    path.unlink()
    return zip_path


def _write_json(path: Path, data: Any) -> None:
    """Write *data* as pretty-printed JSON to *path*."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, default=str, indent=2, ensure_ascii=False)
        f.write("\n")


def _file_size(path: Path) -> int:
    """Return file size in bytes, 0 if missing."""
    try:
        return path.stat().st_size
    except OSError:
        return 0


# ---------------------------------------------------------------------------
# Build a single video entry (transcript + summaries nested)
# ---------------------------------------------------------------------------


def _build_video_entry(
    video: dict[str, Any],
    transcript: dict[str, Any] | None,
    summaries: list[dict[str, Any]],
    include_vtt: bool = False,
) -> dict[str, Any]:
    """Build a video dict with nested transcript and summaries.

    Accepts pre-fetched *transcript* and *summaries* to avoid per-video
    database queries (batch-fetch at the call site instead).
    """
    entry: dict[str, Any] = {
        "id": video["id"],
        "url": video["url"],
        "title": video.get("title", ""),
        "fetched_at": video.get("fetched_at", ""),
        "transcript": None,
        "summaries": [],
    }

    if transcript:
        t_entry: dict[str, Any] = {
            "raw_text": transcript["raw_text"],
            "format": transcript.get("format", ""),
            "quality_score": transcript.get("quality_score"),
            "created_at": transcript.get("created_at", ""),
        }
        if include_vtt and transcript.get("raw_vtt"):
            t_entry["raw_vtt"] = transcript["raw_vtt"]
        entry["transcript"] = t_entry

    for s in summaries:
        entry["summaries"].append(
            {
                "prompt_id": s["prompt_id"],
                "content": s["content"],
                "created_at": s.get("created_at", ""),
                "quality_score": s.get("quality_score"),
                "heuristic_score": s.get("heuristic_score"),
                "llm_score": s.get("llm_score"),
                "faithfulness_score": s.get("faithfulness_score"),
                "verification_score": s.get("verification_score"),
                "model": s.get("model"),
                "strategy": s.get("strategy"),
            }
        )

    return entry


# ---------------------------------------------------------------------------
# JSON export (chunked, per-artist)
# ---------------------------------------------------------------------------


def export_json(
    storage: Storage,
    output_dir: Path,
    *,
    artist_id: str | None = None,
    chunk_size: int = DEFAULT_CHUNK_SIZE,
    include_vtt: bool = False,
    compress: bool = False,
) -> dict[str, Any]:
    """Export data as chunked JSON files.  Returns manifest dict."""
    export_dir = _make_export_dir(output_dir)
    exported_at = _now_iso()
    all_prompts = {p["id"]: dict(p) for p in storage.list_prompts()}
    file_sizes: dict[str, int] = {}
    artist_stats: list[dict[str, Any]] = []

    artists = [storage.get_artist(artist_id)] if artist_id else storage.list_artists()
    artists = [a for a in artists if a]  # filter None

    for artist in artists:
        aid = artist["id"]
        videos = storage.list_videos(artist_id=aid)
        safe_name = _sanitize_dirname(aid)
        artist_dir = export_dir / safe_name
        artist_dir.mkdir(parents=True, exist_ok=True)

        n_transcripts = 0
        n_summaries = 0
        total_chunks = max(1, (len(videos) + chunk_size - 1) // chunk_size)

        for chunk_num in range(total_chunks):
            start = chunk_num * chunk_size
            chunk_videos = videos[start : start + chunk_size]
            if not chunk_videos:
                continue

            chunk_ids = [v["id"] for v in chunk_videos]
            transcripts_map = storage.get_transcripts_for_videos(chunk_ids)
            summaries_map = storage.get_summaries_for_videos(chunk_ids)

            video_entries = []
            chunk_prompt_ids: set[str] = set()
            for v in chunk_videos:
                vid = v["id"]
                entry = _build_video_entry(
                    v,
                    transcripts_map.get(vid),
                    summaries_map.get(vid, []),
                    include_vtt=include_vtt,
                )
                video_entries.append(entry)
                if entry["transcript"]:
                    n_transcripts += 1
                for s in entry["summaries"]:
                    n_summaries += 1
                    chunk_prompt_ids.add(s["prompt_id"])

            chunk_prompts = [all_prompts[pid] for pid in sorted(chunk_prompt_ids) if pid in all_prompts]

            chunk_data = {
                "export_version": EXPORT_VERSION,
                "exported_at": exported_at,
                "yt_artist_version": __version__,
                "artist": {
                    "id": artist["id"],
                    "name": artist["name"],
                    "channel_url": artist["channel_url"],
                    "created_at": artist.get("created_at", ""),
                    "about": artist.get("about"),
                },
                "prompts": chunk_prompts,
                "chunk": {
                    "number": chunk_num + 1,
                    "total_chunks": total_chunks,
                    "video_count": len(video_entries),
                },
                "videos": video_entries,
            }

            filename = f"{safe_name}_{chunk_num + 1:03d}.json"
            filepath = artist_dir / filename
            _write_json(filepath, chunk_data)

            if compress:
                filepath = _zip_file(filepath)

            rel = str(filepath.relative_to(export_dir))
            file_sizes[rel] = _file_size(filepath)

        artist_stats.append(
            {
                "id": aid,
                "videos": len(videos),
                "transcripts": n_transcripts,
                "summaries": n_summaries,
                "chunks": total_chunks if videos else 0,
            }
        )

    manifest = {
        "export_version": EXPORT_VERSION,
        "yt_artist_version": __version__,
        "exported_at": exported_at,
        "format": "json",
        "output_dir": str(export_dir),
        "file_count": len(file_sizes) + 1,  # +1 for manifest itself
        "file_sizes": file_sizes,
        "artists": artist_stats,
        "options": {
            "include_vtt": include_vtt,
            "chunk_size": chunk_size,
            "compress": compress,
        },
    }
    _write_json(export_dir / "manifest.json", manifest)
    return manifest


# ---------------------------------------------------------------------------
# CSV export (flat tables)
# ---------------------------------------------------------------------------

_ARTIST_FIELDS = ["id", "name", "channel_url", "created_at", "about"]
_VIDEO_FIELDS = ["id", "artist_id", "url", "title", "fetched_at"]
_TRANSCRIPT_FIELDS = ["video_id", "raw_text", "format", "quality_score", "created_at"]
_TRANSCRIPT_FIELDS_VTT = _TRANSCRIPT_FIELDS + ["raw_vtt"]
_SUMMARY_FIELDS = [
    "video_id",
    "prompt_id",
    "content",
    "created_at",
    "quality_score",
    "heuristic_score",
    "llm_score",
    "faithfulness_score",
    "verification_score",
    "model",
    "strategy",
]
_PROMPT_FIELDS = ["id", "name", "template"]


def _write_csv(
    path: Path,
    fieldnames: list[str],
    rows: list[dict[str, Any]],
) -> None:
    """Write rows as CSV with header."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def export_csv(
    storage: Storage,
    output_dir: Path,
    *,
    artist_id: str | None = None,
    include_vtt: bool = False,
    compress: bool = False,
) -> dict[str, Any]:
    """Export data as flat CSV tables.  Returns manifest dict."""
    export_dir = _make_export_dir(output_dir)
    exported_at = _now_iso()
    file_sizes: dict[str, int] = {}

    # Artists
    if artist_id:
        a = storage.get_artist(artist_id)
        artists = [a] if a else []
    else:
        artists = storage.list_artists()

    artists_path = export_dir / "artists.csv"
    _write_csv(artists_path, _ARTIST_FIELDS, [dict(a) for a in artists])
    if compress:
        artists_path = _zip_file(artists_path)
    file_sizes[artists_path.name] = _file_size(artists_path)

    # Videos
    all_videos: list[dict[str, Any]] = []
    for a in artists:
        all_videos.extend(dict(v) for v in storage.list_videos(artist_id=a["id"]))
    videos_path = export_dir / "videos.csv"
    _write_csv(videos_path, _VIDEO_FIELDS, all_videos)
    if compress:
        videos_path = _zip_file(videos_path)
    file_sizes[videos_path.name] = _file_size(videos_path)

    # Batch-fetch transcripts and summaries (eliminates N+1 per-video queries)
    all_video_ids = [v["id"] for v in all_videos]
    transcripts_map = storage.get_transcripts_for_videos(all_video_ids)
    summaries_map = storage.get_summaries_for_videos(all_video_ids)

    # Transcripts
    t_fields = _TRANSCRIPT_FIELDS_VTT if include_vtt else _TRANSCRIPT_FIELDS
    transcripts_path = export_dir / "transcripts.csv"
    transcripts_path.parent.mkdir(parents=True, exist_ok=True)
    n_transcripts = 0
    with open(transcripts_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=t_fields, extrasaction="ignore")
        writer.writeheader()
        for v in all_videos:
            t = transcripts_map.get(v["id"])
            if t:
                writer.writerow(dict(t))
                n_transcripts += 1
    if compress:
        transcripts_path = _zip_file(transcripts_path)
    file_sizes[transcripts_path.name] = _file_size(transcripts_path)

    # Summaries
    summaries_path = export_dir / "summaries.csv"
    n_summaries = 0
    with open(summaries_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=_SUMMARY_FIELDS, extrasaction="ignore")
        writer.writeheader()
        for v in all_videos:
            for s in summaries_map.get(v["id"], []):
                writer.writerow(dict(s))
                n_summaries += 1
    if compress:
        summaries_path = _zip_file(summaries_path)
    file_sizes[summaries_path.name] = _file_size(summaries_path)

    # Prompts
    prompts_path = export_dir / "prompts.csv"
    _write_csv(prompts_path, _PROMPT_FIELDS, [dict(p) for p in storage.list_prompts()])
    if compress:
        prompts_path = _zip_file(prompts_path)
    file_sizes[prompts_path.name] = _file_size(prompts_path)

    # Artist stats (reuse batch-fetched data — no extra queries)
    artist_stats = []
    for a in artists:
        aid = a["id"]
        vids = [v for v in all_videos if v["artist_id"] == aid]
        artist_stats.append(
            {
                "id": aid,
                "videos": len(vids),
                "transcripts": sum(1 for v in vids if v["id"] in transcripts_map),
                "summaries": sum(len(summaries_map.get(v["id"], [])) for v in vids),
            }
        )

    manifest = {
        "export_version": EXPORT_VERSION,
        "yt_artist_version": __version__,
        "exported_at": exported_at,
        "format": "csv",
        "output_dir": str(export_dir),
        "file_count": len(file_sizes) + 1,
        "file_sizes": file_sizes,
        "artists": artist_stats,
        "options": {
            "include_vtt": include_vtt,
            "compress": compress,
        },
    }
    _write_json(export_dir / "manifest.json", manifest)
    return manifest
