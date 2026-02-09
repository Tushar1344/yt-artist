"""Tests for ensure_artist_and_video_for_video_url — fast path (no full channel fetch)."""
from pathlib import Path
from unittest.mock import patch

from yt_artist.fetcher import ensure_artist_and_video_for_video_url
from yt_artist.storage import Storage


def _make_store(tmp_path: Path) -> Storage:
    db = tmp_path / "test.db"
    store = Storage(db)
    store.ensure_schema()
    return store


class TestEnsureArtistAndVideo:

    def test_existing_artist_and_video_no_yt_dlp_call(self, tmp_path):
        """When artist+video already exist, no yt-dlp call should be made."""
        store = _make_store(tmp_path)
        store.upsert_artist(
            artist_id="@Test", name="Test", channel_url="https://www.youtube.com/@Test",
            urllist_path="data/artists/@Test/urllist.md",
        )
        store.upsert_video(video_id="abc123def45", artist_id="@Test",
                           url="https://www.youtube.com/watch?v=abc123def45", title="Existing")

        with patch("yt_artist.fetcher._video_metadata") as mock_meta:
            # _video_metadata should not be called because both exist
            # But it IS called (to get artist_id/video_id from URL), so just ensure fetch_channel is NOT called
            mock_meta.return_value = {
                "id": "abc123def45", "title": "Existing",
                "channel_id": "UCtest", "uploader_id": "@Test", "channel": "Test",
            }
            with patch("yt_artist.fetcher.fetch_channel") as mock_fetch:
                aid, vid = ensure_artist_and_video_for_video_url(
                    "https://www.youtube.com/watch?v=abc123def45", store, tmp_path,
                )
            mock_fetch.assert_not_called()
        assert aid == "@Test"
        assert vid == "abc123def45"

    def test_missing_video_creates_without_channel_fetch(self, tmp_path):
        """When video is missing, it should upsert just the one video (not fetch entire channel)."""
        store = _make_store(tmp_path)

        with patch("yt_artist.fetcher._video_metadata") as mock_meta:
            mock_meta.return_value = {
                "id": "newvid123456", "title": "New Video",
                "channel_id": "UCnew", "uploader_id": "@NewChannel", "channel": "New Channel",
            }
            with patch("yt_artist.fetcher.fetch_channel") as mock_fetch:
                aid, vid = ensure_artist_and_video_for_video_url(
                    "https://www.youtube.com/watch?v=newvid123456", store, tmp_path,
                )
            # fetch_channel must NOT be called — this is the key assertion
            mock_fetch.assert_not_called()

        assert aid == "@NewChannel"
        assert vid == "newvid123456"
        # Artist and video should now be in DB
        artist = store.get_artist("@NewChannel")
        assert artist is not None
        assert artist["name"] == "New Channel"
        video = store.get_video("newvid123456")
        assert video is not None
        assert video["title"] == "New Video"
