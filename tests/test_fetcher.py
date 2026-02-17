"""Tests for fetcher: mock yt-dlp output, assert file content and DB state."""

import json
from unittest.mock import MagicMock, patch

import pytest

from yt_artist.fetcher import fetch_channel


def _make_yt_dlp_lines(videos: list[dict], channel_id: str = "UC_test", channel_name: str = "Test_Channel") -> str:
    """Build NDJSON lines as yt-dlp would output."""
    lines = []
    for v in videos:
        obj = {
            "id": v["id"],
            "url": v.get("url", f"https://www.youtube.com/watch?v={v['id']}"),
            "title": v.get("title", ""),
            "channel_id": channel_id,
            "channel": channel_name,
        }
        lines.append(json.dumps(obj))
    return "\n".join(lines)


def test_fetch_channel_writes_urllist_and_upserts_db(store, tmp_path):
    videos = [
        {"id": "v1", "title": "Video One"},
        {"id": "v2", "title": "Video Two"},
    ]
    yt_dlp_out = _make_yt_dlp_lines(videos)

    with patch("yt_artist.fetcher.subprocess.run") as run:
        run.return_value = MagicMock(returncode=0, stdout=yt_dlp_out, stderr="")
        path, count = fetch_channel(
            "https://www.youtube.com/@test",
            store,
            data_dir=tmp_path,
        )

    assert count == 2
    assert "data/artists/UC_test" in path
    assert path.endswith("-urllist.md")

    full = tmp_path / path
    assert full.exists()
    content = full.read_text(encoding="utf-8")
    assert "Test_Channel" in content
    assert "https://www.youtube.com/watch?v=v1" in content
    assert "Video One" in content
    assert "Video Two" in content

    artist = store.get_artist("UC_test")
    assert artist is not None
    assert artist["name"] == "Test_Channel"
    assert artist["channel_url"] == "https://www.youtube.com/@test"

    v1 = store.get_video("v1")
    v2 = store.get_video("v2")
    assert v1 is not None and v1["title"] == "Video One"
    assert v2 is not None and v2["title"] == "Video Two"


def test_fetch_channel_idempotent(store, tmp_path):
    videos = [{"id": "v1", "title": "First Title"}]
    yt_dlp_out = _make_yt_dlp_lines(videos)

    with patch("yt_artist.fetcher.subprocess.run") as run:
        run.return_value = MagicMock(returncode=0, stdout=yt_dlp_out, stderr="")
        fetch_channel("https://www.youtube.com/@test", store, data_dir=tmp_path)
        # Second run: same channel, updated title
        videos[0]["title"] = "Updated Title"
        yt_dlp_out2 = _make_yt_dlp_lines(videos)
        run.return_value = MagicMock(returncode=0, stdout=yt_dlp_out2, stderr="")
        fetch_channel("https://www.youtube.com/@test", store, data_dir=tmp_path)

    video = store.get_video("v1")
    assert video["title"] == "Updated Title"
    artist = store.get_artist("UC_test")
    assert artist is not None


def test_fetch_channel_empty_raises(store, tmp_path):
    with patch("yt_artist.fetcher.subprocess.run") as run:
        run.return_value = MagicMock(returncode=0, stdout="", stderr="")
        with pytest.raises(ValueError, match="No video entries"):
            fetch_channel("https://www.youtube.com/@empty", store, data_dir=tmp_path)
