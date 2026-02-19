"""MCP server: tools fetch_channel, transcribe_video, summarize_video, list_artists, list_videos."""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Optional

from yt_artist.config import get_app_config
from yt_artist.fetcher import fetch_channel as do_fetch_channel
from yt_artist.storage import Storage
from yt_artist.summarizer import summarize
from yt_artist.transcriber import transcribe

log = logging.getLogger("yt_artist.mcp_server")


_storage_instance: Optional[Storage] = None


def _get_storage() -> Storage:
    global _storage_instance
    if _storage_instance is None:
        from yt_artist.paths import db_path as _db_path

        cfg = get_app_config()
        data_dir = Path(cfg.data_dir_env or os.getcwd())
        resolved = Path(cfg.db_env or str(_db_path(data_dir)))
        _storage_instance = Storage(resolved)
        _storage_instance.ensure_schema()
    return _storage_instance


def _get_data_dir() -> Path:
    cfg = get_app_config()
    return Path(cfg.data_dir_env or os.getcwd())


def run_mcp_server() -> None:
    try:
        from mcp.server.fastmcp import FastMCP
    except ImportError:
        raise SystemExit("MCP server requires the mcp package. Install with: pip install yt-artist[mcp]")

    mcp = FastMCP("yt-artist", json_response=True)
    storage_factory = _get_storage
    data_dir_factory = _get_data_dir

    @mcp.tool()
    def fetch_channel(channel_url: str) -> dict:
        """Fetch all video URLs for a YouTube channel. Writes urllist markdown and upserts artists/videos."""
        storage = storage_factory()
        data_dir = data_dir_factory()
        path, count = do_fetch_channel(channel_url, storage, data_dir=data_dir)
        return {"urllist_path": path, "video_count": count}

    @mcp.tool()
    def transcribe_video(video_url_or_id: str, write_file: bool = False) -> dict:
        """Transcribe a video by URL or video ID; optionally write transcript to file."""
        storage = storage_factory()
        data_dir = data_dir_factory()
        artist_id = None
        if write_file:
            from yt_artist.transcriber import extract_video_id

            vid = extract_video_id(video_url_or_id)
            v = storage.get_video(vid)
            artist_id = v["artist_id"] if v else None
        video_id = transcribe(
            video_url_or_id,
            storage,
            artist_id=artist_id,
            write_transcript_file=write_file,
            data_dir=data_dir,
        )
        return {"video_id": video_id}

    @mcp.tool()
    def summarize_video(
        video_id: str,
        prompt_id: str,
        intent: Optional[str] = None,
        audience: Optional[str] = None,
    ) -> dict:
        """Generate AI summary for a video using a stored prompt. Returns summary id and content."""
        storage = storage_factory()
        summary_id = summarize(
            video_id,
            prompt_id,
            storage,
            intent_override=intent,
            audience_override=audience,
        )
        rows = storage.get_summaries_for_video(video_id)
        content = next((r["content"] for r in rows if r["prompt_id"] == prompt_id), "")
        return {"summary_id": summary_id, "content": content}

    @mcp.tool()
    def list_artists() -> dict:
        """List all artists (channels) in the database."""
        storage = storage_factory()
        artists = storage.list_artists()
        return {"artists": [{"id": a["id"], "name": a["name"], "channel_url": a["channel_url"]} for a in artists]}

    @mcp.tool()
    def list_videos(artist_id: Optional[str] = None) -> dict:
        """List videos, optionally filtered by artist_id."""
        storage = storage_factory()
        videos = storage.list_videos(artist_id=artist_id)
        return {
            "videos": [
                {"id": v["id"], "artist_id": v["artist_id"], "title": v["title"], "url": v["url"]} for v in videos
            ]
        }

    @mcp.tool()
    def search_transcripts(
        query: Optional[str] = None,
        artist_id: Optional[str] = None,
        video_id: Optional[str] = None,
        limit: int = 20,
    ) -> dict:
        """Search or list transcripts. With query, full-text search with BM25 ranking and snippets."""
        storage = storage_factory()
        if query:
            try:
                rows = storage.search_transcripts(query, artist_id=artist_id, limit=limit)
            except ValueError as exc:
                return {"error": str(exc), "results": []}
            return {
                "results": [
                    {
                        "video_id": r["video_id"],
                        "artist_id": r.get("artist_id", ""),
                        "title": r.get("title", ""),
                        "snippet": r.get("snippet", ""),
                        "rank": r.get("rank"),
                    }
                    for r in rows
                ]
            }
        rows = storage.list_transcripts(artist_id=artist_id, video_id=video_id)
        return {
            "transcripts": [
                {
                    "video_id": r["video_id"],
                    "artist_id": r.get("artist_id", ""),
                    "title": r.get("title", ""),
                    "transcript_len": r.get("transcript_len", 0),
                }
                for r in rows[:limit]
            ]
        }

    mcp.run(transport="stdio")


if __name__ == "__main__":
    run_mcp_server()
