"""Tests for R2: Shared yt_dlp_util module."""
import os
import sys
from unittest.mock import patch

from yt_artist.yt_dlp_util import (
    _resolve_base,
    yt_dlp_cmd,
    MAX_CONCURRENCY,
    DEFAULT_INTER_VIDEO_DELAY,
    get_inter_video_delay,
)

# Default sleep flags that yt_dlp_cmd always appends.
_SLEEP = ["--sleep-requests", "1", "--sleep-subtitles", "3"]


def test_prefers_binary_when_available():
    """When yt-dlp is on PATH, return ['yt-dlp', ...sleep flags...]."""
    _resolve_base.cache_clear()
    with patch("yt_artist.yt_dlp_util.shutil.which", return_value="/usr/local/bin/yt-dlp"), \
         patch.dict(os.environ, {}, clear=True):
        result = yt_dlp_cmd()
    assert result == ["yt-dlp"] + _SLEEP


def test_falls_back_to_python_module():
    """When yt-dlp binary is not on PATH, return [sys.executable, '-m', 'yt_dlp', ...sleep...]."""
    _resolve_base.cache_clear()
    with patch("yt_artist.yt_dlp_util.shutil.which", return_value=None), \
         patch.dict(os.environ, {}, clear=True):
        result = yt_dlp_cmd()
    assert result == [sys.executable, "-m", "yt_dlp"] + _SLEEP


def test_cookies_browser_env_appends_flag():
    """YT_ARTIST_COOKIES_BROWSER adds --cookies-from-browser."""
    _resolve_base.cache_clear()
    with patch("yt_artist.yt_dlp_util.shutil.which", return_value="/usr/bin/yt-dlp"), \
         patch.dict(os.environ, {"YT_ARTIST_COOKIES_BROWSER": "chrome"}, clear=True):
        result = yt_dlp_cmd()
    assert result == ["yt-dlp"] + _SLEEP + ["--cookies-from-browser", "chrome"]


def test_cookies_file_env_appends_flag():
    """YT_ARTIST_COOKIES_FILE adds --cookies."""
    _resolve_base.cache_clear()
    with patch("yt_artist.yt_dlp_util.shutil.which", return_value="/usr/bin/yt-dlp"), \
         patch.dict(os.environ, {"YT_ARTIST_COOKIES_FILE": "/tmp/cookies.txt"}, clear=True):
        result = yt_dlp_cmd()
    assert result == ["yt-dlp"] + _SLEEP + ["--cookies", "/tmp/cookies.txt"]


def test_cookies_browser_takes_precedence_over_file():
    """When both env vars are set, browser wins."""
    _resolve_base.cache_clear()
    with patch("yt_artist.yt_dlp_util.shutil.which", return_value="/usr/bin/yt-dlp"), \
         patch.dict(os.environ, {
             "YT_ARTIST_COOKIES_BROWSER": "firefox",
             "YT_ARTIST_COOKIES_FILE": "/tmp/cookies.txt",
         }, clear=True):
        result = yt_dlp_cmd()
    assert result == ["yt-dlp"] + _SLEEP + ["--cookies-from-browser", "firefox"]


def test_empty_cookies_env_ignored():
    """Empty or whitespace-only cookie env vars are ignored."""
    _resolve_base.cache_clear()
    with patch("yt_artist.yt_dlp_util.shutil.which", return_value="/usr/bin/yt-dlp"), \
         patch.dict(os.environ, {"YT_ARTIST_COOKIES_BROWSER": "  ", "YT_ARTIST_COOKIES_FILE": ""}, clear=True):
        result = yt_dlp_cmd()
    assert result == ["yt-dlp"] + _SLEEP


# --- Sleep flag override tests ---

def test_sleep_flags_custom_via_env():
    """YT_ARTIST_SLEEP_REQUESTS / YT_ARTIST_SLEEP_SUBTITLES override defaults."""
    _resolve_base.cache_clear()
    with patch("yt_artist.yt_dlp_util.shutil.which", return_value="/usr/bin/yt-dlp"), \
         patch.dict(os.environ, {
             "YT_ARTIST_SLEEP_REQUESTS": "5",
             "YT_ARTIST_SLEEP_SUBTITLES": "10",
         }, clear=True):
        result = yt_dlp_cmd()
    assert result == ["yt-dlp", "--sleep-requests", "5", "--sleep-subtitles", "10"]


# --- MAX_CONCURRENCY constant ---

def test_max_concurrency_is_conservative():
    """MAX_CONCURRENCY should be 3 or less to avoid YouTube rate-limiting."""
    assert MAX_CONCURRENCY <= 3


# --- Inter-video delay ---

def test_inter_video_delay_default():
    """get_inter_video_delay returns DEFAULT_INTER_VIDEO_DELAY when env not set."""
    with patch.dict(os.environ, {}, clear=True):
        assert get_inter_video_delay() == DEFAULT_INTER_VIDEO_DELAY


def test_inter_video_delay_from_env():
    """YT_ARTIST_INTER_VIDEO_DELAY overrides default."""
    with patch.dict(os.environ, {"YT_ARTIST_INTER_VIDEO_DELAY": "5.5"}, clear=True):
        assert get_inter_video_delay() == 5.5


def test_inter_video_delay_invalid_fallback():
    """Invalid env value falls back to default."""
    with patch.dict(os.environ, {"YT_ARTIST_INTER_VIDEO_DELAY": "not-a-number"}, clear=True):
        assert get_inter_video_delay() == DEFAULT_INTER_VIDEO_DELAY


def test_inter_video_delay_negative_clamped():
    """Negative env value is clamped to 0."""
    with patch.dict(os.environ, {"YT_ARTIST_INTER_VIDEO_DELAY": "-3"}, clear=True):
        assert get_inter_video_delay() == 0.0
