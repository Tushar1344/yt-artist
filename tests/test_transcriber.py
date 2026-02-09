"""Tests for transcriber: mock yt-dlp subtitle output; assert DB transcript."""
from pathlib import Path
from unittest.mock import patch

import pytest

from yt_artist import storage
from yt_artist.transcriber import (
    extract_video_id,
    _subs_to_plain_text,
    _classify_yt_dlp_error,
    transcribe,
)


def testextract_video_id_from_url():
    assert extract_video_id("https://www.youtube.com/watch?v=abc123xyz01") == "abc123xyz01"
    assert extract_video_id("https://youtu.be/abc123xyz01") == "abc123xyz01"
    assert extract_video_id("https://www.youtube.com/watch?foo=1&v=dQw4w9WgXcQ&bar=2") == "dQw4w9WgXcQ"


def testextract_video_id_bare_id():
    assert extract_video_id("dQw4w9WgXcQ") == "dQw4w9WgXcQ"


def testextract_video_id_invalid_raises():
    with pytest.raises(ValueError, match="Cannot extract"):
        extract_video_id("https://example.com/not-a-video")
    with pytest.raises(ValueError, match="required"):
        extract_video_id("")


def test_subs_to_plain_text_vtt():
    vtt = """WEBVTT

00:00:00.000 --> 00:00:02.500
Hello world.

00:00:02.500 --> 00:00:05.000
This is a transcript.
"""
    out = _subs_to_plain_text(vtt, "vtt")
    assert "Hello world." in out
    assert "This is a transcript." in out
    assert "00:00:00" not in out


def test_subs_to_plain_text_srt():
    srt = """1
00:00:00,000 --> 00:00:02,500
First line.

2
00:00:02,500 --> 00:00:05,000
Second line.
"""
    out = _subs_to_plain_text(srt, "srt")
    assert "First line." in out
    assert "Second line." in out


def test_transcribe_saves_to_db(store):
    store.upsert_artist(
        artist_id="UC_a",
        name="A",
        channel_url="https://www.youtube.com/@a",
        urllist_path="data/artists/UC_a/artistUC_aA-urllist.md",
    )
    store.upsert_video(
        video_id="vid1test01",
        artist_id="UC_a",
        url="https://www.youtube.com/watch?v=vid1test01",
        title="V1",
    )

    with patch("yt_artist.transcriber._run_yt_dlp_subtitles", return_value=("Mocked transcript text.", "vtt")):
        video_id = transcribe(
            "https://www.youtube.com/watch?v=vid1test01",
            store,
        )

    assert video_id == "vid1test01"
    row = store.get_transcript("vid1test01")
    assert row is not None
    assert "Mocked transcript text" in row["raw_text"]
    assert row["format"] == "vtt"


def test_transcribe_writes_optional_file(store, tmp_path):
    store.upsert_artist(
        artist_id="UC_a",
        name="A",
        channel_url="https://www.youtube.com/@a",
        urllist_path="data/artists/UC_a/artistUC_aA-urllist.md",
    )
    store.upsert_video(
        video_id="vid2test02",
        artist_id="UC_a",
        url="https://www.youtube.com/watch?v=vid2test02",
        title="V2",
    )

    with patch("yt_artist.transcriber._run_yt_dlp_subtitles", return_value=("Optional file text.", "vtt")):
        transcribe(
            "vid2test02",
            store,
            artist_id="UC_a",
            write_transcript_file=True,
            data_dir=tmp_path,
        )

    transcript_file = tmp_path / "artists" / "UC_a" / "transcripts" / "vid2test02.txt"
    assert transcript_file.exists()
    assert "Optional file text" in transcript_file.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# _classify_yt_dlp_error tests
# ---------------------------------------------------------------------------

class TestClassifyYtDlpError:

    def test_rate_limit_detected(self):
        """HTTP 429 / rate limit is classified correctly."""
        err_type, msg = _classify_yt_dlp_error("ERROR: HTTP Error 429: Too Many Requests")
        assert err_type == "rate_limit"
        assert "429" in msg

    def test_age_restricted_detected(self):
        """Age-restricted error is classified with auth guidance."""
        err_type, msg = _classify_yt_dlp_error("ERROR: Sign in to confirm your age")
        assert err_type == "age_restricted"
        assert "YT_ARTIST_PO_TOKEN" in msg
        assert "doctor" in msg

    def test_auth_required_detected(self):
        """Login required error is classified."""
        err_type, msg = _classify_yt_dlp_error("ERROR: This video requires login required")
        assert err_type == "auth_required"
        assert "authentication" in msg.lower()

    def test_members_only_detected(self):
        """Members-only content detected as auth_required."""
        err_type, msg = _classify_yt_dlp_error("ERROR: Join this channel to get access to members only content")
        assert err_type == "auth_required"

    def test_bot_detected_403(self):
        """403 Forbidden classified as bot_detected."""
        err_type, msg = _classify_yt_dlp_error("ERROR: HTTP Error 403: Forbidden")
        assert err_type == "bot_detected"
        assert "PO" in msg or "po_token" in msg.lower() or "proof of origin" in msg.lower()

    def test_bot_detected_captcha(self):
        """CAPTCHA / bot detection classified."""
        err_type, msg = _classify_yt_dlp_error("ERROR: confirm you're not a bot")
        assert err_type == "bot_detected"

    def test_generic_unknown_error(self):
        """Unknown errors return generic type with empty message."""
        err_type, msg = _classify_yt_dlp_error("ERROR: Something completely unrelated went wrong")
        assert err_type == "generic"
        assert msg == ""

    def test_messages_mention_doctor(self):
        """All non-generic messages mention yt-artist doctor."""
        for stderr in [
            "Sign in to confirm your age",
            "login required",
            "confirm you're not a bot",
        ]:
            _, msg = _classify_yt_dlp_error(stderr)
            assert "doctor" in msg, f"Message for '{stderr}' should mention doctor"
