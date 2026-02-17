"""Tests for transcriber: mock yt-dlp subtitle output; assert DB transcript."""

import os
from unittest.mock import patch

import pytest

from yt_artist.transcriber import (
    _classify_yt_dlp_error,
    _run_yt_dlp_subtitles,
    _run_yt_dlp_with_backoff,
    _subs_to_plain_text,
    extract_video_id,
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


# ---------------------------------------------------------------------------
# _run_yt_dlp_subtitles provider-aware error message tests
# ---------------------------------------------------------------------------


class TestRunYtDlpWithBackoff:
    """Tests for the extracted _run_yt_dlp_with_backoff helper."""

    def test_success_returns_stdout_stderr(self, tmp_path):
        """Successful run returns (stdout, stderr, False)."""
        mock_result = type("R", (), {"returncode": 0, "stdout": "ok", "stderr": ""})()
        with patch("subprocess.run", return_value=mock_result):
            stdout, stderr, timed_out = _run_yt_dlp_with_backoff(
                ["echo"],
                "https://youtube.com/watch?v=x",
                tmp_path,
                "test",
            )
        assert stdout == "ok"
        assert not timed_out

    def test_timeout_returns_timed_out_flag(self, tmp_path):
        """Timeout sets timed_out=True instead of raising."""
        with patch("subprocess.run", side_effect=TimeoutError("timed out")):
            # subprocess.TimeoutExpired inherits from SubprocessError, let's use it properly
            import subprocess as _sp

            with patch("subprocess.run", side_effect=_sp.TimeoutExpired(["cmd"], 120)):
                stdout, stderr, timed_out = _run_yt_dlp_with_backoff(
                    ["cmd"],
                    "https://youtube.com/watch?v=x",
                    tmp_path,
                    "test",
                )
        assert timed_out is True
        assert stdout == ""

    def test_429_retries_then_raises(self, tmp_path):
        """429 exhausts retries and raises FileNotFoundError."""
        mock_result = type(
            "R",
            (),
            {
                "returncode": 1,
                "stdout": "",
                "stderr": "HTTP Error 429: Too Many Requests",
            },
        )()
        with patch("subprocess.run", return_value=mock_result), patch("yt_artist.transcriber._time.sleep"):
            with pytest.raises(FileNotFoundError, match="429"):
                _run_yt_dlp_with_backoff(
                    ["cmd"],
                    "https://youtube.com/watch?v=x",
                    tmp_path,
                    "test",
                )

    def test_auth_error_raises_immediately(self, tmp_path):
        """Auth/bot error raises on first attempt without retrying."""
        mock_result = type(
            "R",
            (),
            {
                "returncode": 1,
                "stdout": "",
                "stderr": "Sign in to confirm your age",
            },
        )()
        with patch("subprocess.run", return_value=mock_result) as mock_run:
            with pytest.raises(FileNotFoundError, match="age-restricted"):
                _run_yt_dlp_with_backoff(
                    ["cmd"],
                    "https://youtube.com/watch?v=x",
                    tmp_path,
                    "test",
                )
        # Should only call subprocess once (no retries for auth errors)
        assert mock_run.call_count == 1

    def test_429_backoff_increases(self, tmp_path):
        """Backoff doubles between 429 retries."""
        call_count = 0

        def _mock_run(*a, **kw):
            nonlocal call_count
            call_count += 1
            if call_count <= 3:
                return type("R", (), {"returncode": 1, "stdout": "", "stderr": "429"})()
            return type("R", (), {"returncode": 0, "stdout": "ok", "stderr": ""})()

        sleep_times = []
        with (
            patch("subprocess.run", side_effect=_mock_run),
            patch("yt_artist.transcriber._time.sleep", side_effect=lambda s: sleep_times.append(s)),
        ):
            stdout, stderr, timed_out = _run_yt_dlp_with_backoff(
                ["cmd"],
                "https://youtube.com/watch?v=x",
                tmp_path,
                "test",
            )
        assert stdout == "ok"
        assert not timed_out
        # Should have slept 3 times with increasing backoff: 5, 10, 20
        assert len(sleep_times) == 3
        assert sleep_times[0] == 5
        assert sleep_times[1] == 10
        assert sleep_times[2] == 20


class TestRunYtDlpSubtitlesProviderHints:
    """Test that no-subtitle errors include provider-aware hints."""

    def test_error_hints_install_provider_when_missing(self, tmp_path):
        """When no provider and no manual token, error should suggest installing rustypipe."""
        from importlib.metadata import PackageNotFoundError

        original_distribution = __import__("importlib.metadata", fromlist=["distribution"]).distribution

        def _mock_distribution(name):
            if name == "yt-dlp-get-pot-rustypipe":
                raise PackageNotFoundError(name)
            return original_distribution(name)

        # Mock subprocess.run to simulate yt-dlp returning no subtitles (exit 0, no files)
        mock_result = type(
            "Result",
            (),
            {
                "returncode": 0,
                "stdout": "",
                "stderr": "",
                "check_returncode": lambda self: None,
            },
        )()

        with (
            patch("subprocess.run", return_value=mock_result),
            patch("yt_artist.transcriber._get_available_sub_langs", return_value=[]),
            patch("importlib.metadata.distribution", side_effect=_mock_distribution),
            patch.dict(os.environ, {}, clear=True),
        ):
            with pytest.raises(FileNotFoundError, match="pip install yt-dlp-get-pot-rustypipe"):
                _run_yt_dlp_subtitles("https://www.youtube.com/watch?v=test12345", tmp_path / "subs")

    def test_error_hints_provider_installed_but_failed(self, tmp_path):
        """When provider IS installed but subtitles still fail, error should note that."""
        # Mock subprocess.run to simulate yt-dlp returning no subtitles (exit 0, no files)
        mock_result = type(
            "Result",
            (),
            {
                "returncode": 0,
                "stdout": "",
                "stderr": "",
                "check_returncode": lambda self: None,
            },
        )()

        with (
            patch("subprocess.run", return_value=mock_result),
            patch("yt_artist.transcriber._get_available_sub_langs", return_value=[]),
            patch.dict(os.environ, {}, clear=True),
        ):
            # yt-dlp-get-pot-rustypipe is a real dependency, so it IS importable
            with pytest.raises(FileNotFoundError, match="provider.*installed but subtitles still failed"):
                _run_yt_dlp_subtitles("https://www.youtube.com/watch?v=test12345", tmp_path / "subs")
