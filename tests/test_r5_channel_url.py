"""Tests for R5: channel_url_for() helper."""

from yt_artist.yt_dlp_util import channel_url_for


def test_channel_url_for_handle():
    assert channel_url_for("@hubermanlab") == "https://www.youtube.com/@hubermanlab"


def test_channel_url_for_channel_id():
    assert channel_url_for("UC1234abcXYZ") == "https://www.youtube.com/channel/UC1234abcXYZ"


def test_channel_url_for_at_prefix_preserved():
    """Handles with @ should keep the @ in the URL path."""
    url = channel_url_for("@NateBJones")
    assert url == "https://www.youtube.com/@NateBJones"
    assert "/channel/" not in url
