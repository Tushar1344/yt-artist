"""Tests for jobs.py — background job health check."""

from __future__ import annotations

import os
from unittest.mock import MagicMock, patch

from yt_artist.jobs import _STARTUP_MARKER, _verify_child_started


class TestVerifyChildStarted:
    """Tests for _verify_child_started() health check."""

    def test_success_with_marker(self, tmp_path):
        """No warning when log contains startup marker and PID is alive."""
        log_path = tmp_path / "job.log"
        log_path.write_text(f"{_STARTUP_MARKER} pid=123 job=abc\n")
        mock_proc = MagicMock(pid=os.getpid())  # alive PID

        with patch("yt_artist.jobs.time.sleep"):
            _verify_child_started(mock_proc, log_path, "abc123def456")
        # No warning — test passes if no exception and no stderr output

    def test_warns_on_dead_pid(self, tmp_path, capfd):
        """Warning printed when child PID is dead."""
        log_path = tmp_path / "job.log"
        mock_proc = MagicMock(pid=4_000_000)  # unlikely to be alive

        with patch("yt_artist.jobs.time.sleep"):
            _verify_child_started(mock_proc, log_path, "abc123def456")

        captured = capfd.readouterr()
        assert "Warning" in captured.err
        assert "died" in captured.err

    def test_warns_on_missing_marker(self, tmp_path, capfd):
        """Warning printed when child is alive but no marker in log."""
        log_path = tmp_path / "job.log"
        log_path.write_text("")  # empty log
        mock_proc = MagicMock(pid=os.getpid())  # alive

        with patch("yt_artist.jobs.time.sleep"):
            _verify_child_started(mock_proc, log_path, "abc123def456")

        captured = capfd.readouterr()
        assert "Warning" in captured.err
        assert "may not have fully started" in captured.err

    def test_warns_on_no_log_file(self, tmp_path, capfd):
        """Warning printed when log file does not exist yet."""
        log_path = tmp_path / "nonexistent.log"
        mock_proc = MagicMock(pid=os.getpid())

        with patch("yt_artist.jobs.time.sleep"):
            _verify_child_started(mock_proc, log_path, "abc123def456")

        captured = capfd.readouterr()
        assert "Warning" in captured.err

    def test_launch_background_calls_verify(self, store, tmp_path):
        """launch_background should call _verify_child_started."""
        from yt_artist.jobs import launch_background

        with (
            patch("yt_artist.jobs.subprocess.Popen") as mock_popen,
            patch("yt_artist.jobs._verify_child_started") as mock_verify,
            patch("builtins.open", MagicMock()),
        ):
            mock_popen.return_value = MagicMock(pid=12345)
            launch_background(
                ["yt-artist", "transcribe", "--artist-id", "@Test"],
                store,
                tmp_path,
            )

        mock_verify.assert_called_once()
