"""Tests for the work ledger helper module and domain function integration."""

from __future__ import annotations

import time
from unittest.mock import patch

import pytest

from yt_artist.ledger import WorkTimer, record_operation
from yt_artist.storage import Storage

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _setup_video(store: Storage, video_id: str = "ledgervid01") -> None:
    """Seed minimal artist+video for ledger tests."""
    store.upsert_artist(
        artist_id="@LA",
        name="Ledger Artist",
        channel_url="https://www.youtube.com/@LA",
        urllist_path="data/artists/@LA/urllist.md",
    )
    store.upsert_video(
        video_id=video_id,
        artist_id="@LA",
        url=f"https://www.youtube.com/watch?v={video_id}",
        title="Ledger Video",
    )


# ---------------------------------------------------------------------------
# WorkTimer
# ---------------------------------------------------------------------------


class TestWorkTimer:
    def test_elapsed_ms_positive(self):
        """WorkTimer.elapsed_ms returns positive integer after small delay."""
        timer = WorkTimer()
        time.sleep(0.01)
        ms = timer.elapsed_ms()
        assert ms >= 5  # allow some slack
        assert isinstance(ms, int)

    def test_started_at_is_iso_format(self):
        """started_at is ISO-8601 with T separator and Z suffix."""
        timer = WorkTimer()
        assert "T" in timer.started_at
        assert timer.started_at.endswith("Z")


# ---------------------------------------------------------------------------
# record_operation
# ---------------------------------------------------------------------------


class TestRecordOperation:
    def test_record_success(self, store):
        """record_operation writes a ledger entry retrievable via get_work_history."""
        _setup_video(store)
        record_operation(
            store,
            video_id="ledgervid01",
            operation="transcribe",
            status="success",
            started_at="2026-02-19T10:00:00Z",
            duration_ms=100,
        )
        rows = store.get_work_history(video_id="ledgervid01")
        assert len(rows) == 1
        assert rows[0]["operation"] == "transcribe"
        assert rows[0]["status"] == "success"

    def test_record_never_raises(self, store):
        """record_operation swallows exceptions (best-effort) — FK violation doesn't propagate."""
        # video_id 'nonexistent' doesn't exist → FK violation
        record_operation(
            store,
            video_id="nonexistent",
            operation="test",
            status="success",
            started_at="2026-01-01T00:00:00Z",
            duration_ms=0,
        )
        # No assertion needed — just verify it didn't raise


# ---------------------------------------------------------------------------
# Domain function integration: summarize logs to ledger
# ---------------------------------------------------------------------------


class TestSummarizeLogsToLedger:
    def _seed_for_summarize(self, store):
        _setup_video(store)
        store.save_transcript(video_id="ledgervid01", raw_text="Hello world transcript.", format="vtt")
        store.upsert_prompt(prompt_id="lp1", name="LP", template="Summarize: {video}")

    def test_summarize_success_logged(self, store):
        """After successful summarize(), a 'summarize'/'success' entry exists."""
        self._seed_for_summarize(store)
        from yt_artist.summarizer import summarize

        with patch("yt_artist.summarizer.llm_complete", return_value="Great summary."):
            summarize("ledgervid01", "lp1", store)

        rows = store.get_work_history(video_id="ledgervid01", operation="summarize")
        assert len(rows) == 1
        assert rows[0]["status"] == "success"
        assert rows[0]["duration_ms"] >= 0
        assert rows[0]["prompt_id"] == "lp1"

    def test_summarize_failure_logged(self, store):
        """When summarize() raises, a 'summarize'/'failed' entry exists."""
        self._seed_for_summarize(store)
        from yt_artist.summarizer import summarize

        with patch("yt_artist.summarizer.llm_complete", side_effect=RuntimeError("LLM down")):
            with pytest.raises(RuntimeError, match="LLM down"):
                summarize("ledgervid01", "lp1", store)

        rows = store.get_work_history(video_id="ledgervid01", operation="summarize")
        assert len(rows) == 1
        assert rows[0]["status"] == "failed"
        assert "LLM down" in rows[0]["error_message"]


# ---------------------------------------------------------------------------
# Domain function integration: transcribe logs to ledger
# ---------------------------------------------------------------------------


class TestTranscribeLogsToLedger:
    def _seed_for_transcribe(self, store):
        _setup_video(store)

    def test_transcribe_success_logged(self, store, tmp_path):
        """After successful transcribe(), a 'transcribe'/'success' entry exists."""
        self._seed_for_transcribe(store)
        from yt_artist.transcriber import transcribe

        with (
            patch(
                "yt_artist.transcriber._run_yt_dlp_subtitles",
                return_value=("Hello world transcript.", "vtt", "WEBVTT\n\n00:00.000 --> 00:01.000\nHello"),
            ),
            patch("yt_artist.transcript_quality.transcript_quality_score", return_value=0.9),
        ):
            transcribe("https://www.youtube.com/watch?v=ledgervid01", store)

        rows = store.get_work_history(video_id="ledgervid01", operation="transcribe")
        assert len(rows) == 1
        assert rows[0]["status"] == "success"
        assert rows[0]["duration_ms"] >= 0

    def test_transcribe_failure_logged(self, store, tmp_path):
        """When transcribe() raises, a 'transcribe'/'failed' entry exists."""
        self._seed_for_transcribe(store)
        from yt_artist.transcriber import transcribe

        with (
            patch(
                "yt_artist.transcriber._run_yt_dlp_subtitles",
                side_effect=FileNotFoundError("No subtitles found"),
            ),
            pytest.raises(FileNotFoundError, match="No subtitles"),
        ):
            transcribe("https://www.youtube.com/watch?v=ledgervid01", store)

        rows = store.get_work_history(video_id="ledgervid01", operation="transcribe")
        assert len(rows) == 1
        assert rows[0]["status"] == "failed"
        assert "No subtitles" in rows[0]["error_message"]
