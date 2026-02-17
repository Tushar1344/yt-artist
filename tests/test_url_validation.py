"""Tests for YouTube URL validation: channel and video URLs."""

import pytest

from yt_artist.yt_dlp_util import (
    validate_youtube_channel_url,
    validate_youtube_video_url,
)

# ---------------------------------------------------------------------------
# validate_youtube_channel_url — happy paths
# ---------------------------------------------------------------------------


class TestChannelUrlHappy:
    def test_standard_handle(self):
        assert (
            validate_youtube_channel_url("https://www.youtube.com/@hubermanlab")
            == "https://www.youtube.com/@hubermanlab"
        )

    def test_bare_handle_expanded(self):
        """Bare @handle is expanded to full URL."""
        assert validate_youtube_channel_url("@hubermanlab") == "https://www.youtube.com/@hubermanlab"

    def test_channel_id_url(self):
        url = "https://www.youtube.com/channel/UC123abc"
        assert validate_youtube_channel_url(url) == url

    def test_c_style_url(self):
        url = "https://www.youtube.com/c/SomeChannel"
        assert validate_youtube_channel_url(url) == url

    def test_user_style_url(self):
        url = "https://www.youtube.com/user/SomeUser"
        assert validate_youtube_channel_url(url) == url

    def test_trailing_slash_stripped(self):
        url = "https://www.youtube.com/@hubermanlab/"
        assert validate_youtube_channel_url(url) == url

    def test_http_accepted(self):
        url = "http://www.youtube.com/@hubermanlab"
        assert validate_youtube_channel_url(url) == url

    def test_mobile_youtube(self):
        url = "https://m.youtube.com/@hubermanlab"
        assert validate_youtube_channel_url(url) == url

    def test_whitespace_trimmed(self):
        assert validate_youtube_channel_url("  @hubermanlab  ") == "https://www.youtube.com/@hubermanlab"


# ---------------------------------------------------------------------------
# validate_youtube_channel_url — error cases
# ---------------------------------------------------------------------------


class TestChannelUrlErrors:
    def test_empty_string(self):
        with pytest.raises(SystemExit, match="empty"):
            validate_youtube_channel_url("")

    def test_not_a_url(self):
        with pytest.raises(SystemExit, match="Not a valid URL"):
            validate_youtube_channel_url("hubermanlab")

    def test_non_youtube_host(self):
        with pytest.raises(SystemExit, match="Not a YouTube URL"):
            validate_youtube_channel_url("https://www.example.com/@test")

    def test_video_url_rejected(self):
        """Video URL passed to channel command gives helpful redirect."""
        with pytest.raises(SystemExit, match="looks like a video URL"):
            validate_youtube_channel_url("https://www.youtube.com/watch?v=dQw4w9WgXcQ")

    def test_shorts_url_rejected(self):
        with pytest.raises(SystemExit, match="looks like a video URL"):
            validate_youtube_channel_url("https://www.youtube.com/shorts/abc123xyz")

    def test_unrecognized_path(self):
        with pytest.raises(SystemExit, match="Unrecognized"):
            validate_youtube_channel_url("https://www.youtube.com/feed/trending")


# ---------------------------------------------------------------------------
# validate_youtube_video_url — happy paths
# ---------------------------------------------------------------------------


class TestVideoUrlHappy:
    def test_standard_watch_url(self):
        url = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
        assert validate_youtube_video_url(url) == url

    def test_bare_video_id(self):
        assert validate_youtube_video_url("dQw4w9WgXcQ") == "dQw4w9WgXcQ"

    def test_youtu_be_shortlink(self):
        url = "https://youtu.be/dQw4w9WgXcQ"
        assert validate_youtube_video_url(url) == url

    def test_shorts_url(self):
        url = "https://www.youtube.com/shorts/abc123xyz01"
        assert validate_youtube_video_url(url) == url

    def test_embed_url(self):
        url = "https://www.youtube.com/embed/dQw4w9WgXcQ"
        assert validate_youtube_video_url(url) == url

    def test_watch_with_extra_params(self):
        url = "https://www.youtube.com/watch?v=dQw4w9WgXcQ&t=42"
        assert validate_youtube_video_url(url) == url

    def test_whitespace_trimmed(self):
        assert validate_youtube_video_url("  dQw4w9WgXcQ  ") == "dQw4w9WgXcQ"


# ---------------------------------------------------------------------------
# validate_youtube_video_url — error cases
# ---------------------------------------------------------------------------


class TestVideoUrlErrors:
    def test_empty_string(self):
        with pytest.raises(SystemExit, match="empty"):
            validate_youtube_video_url("")

    def test_not_a_url_or_id(self):
        with pytest.raises(SystemExit, match="Not a valid video URL or ID"):
            validate_youtube_video_url("foo")

    def test_non_youtube_host(self):
        with pytest.raises(SystemExit, match="Not a YouTube URL"):
            validate_youtube_video_url("https://www.example.com/watch?v=abc123xyz01")

    def test_channel_url_rejected(self):
        """Channel URL passed to video command gives helpful redirect."""
        with pytest.raises(SystemExit, match="looks like a channel URL"):
            validate_youtube_video_url("https://www.youtube.com/@hubermanlab")

    def test_no_video_id_in_url(self):
        with pytest.raises(SystemExit, match="Cannot find a video ID"):
            validate_youtube_video_url("https://www.youtube.com/feed/trending")

    def test_short_bare_id_rejected(self):
        """Bare strings shorter than 8 chars are not valid video IDs."""
        with pytest.raises(SystemExit, match="Not a valid video URL or ID"):
            validate_youtube_video_url("short")
