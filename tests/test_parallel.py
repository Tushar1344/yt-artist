"""Tests for parallel execution, batch DB queries, and rate-limit safety."""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from yt_artist.storage import Storage


def _make_store(tmp_path: Path) -> Storage:
    db = tmp_path / "test.db"
    store = Storage(db)
    store.ensure_schema()
    return store


def _seed_full(store: Storage, n_videos: int = 5) -> list[str]:
    """Seed artist + N videos, return video IDs."""
    store.upsert_artist(
        artist_id="@Bulk", name="Bulk Artist",
        channel_url="https://www.youtube.com/@Bulk",
        urllist_path="data/artists/@Bulk/urllist.md",
    )
    ids = []
    for i in range(n_videos):
        vid = f"bulkvid{i:05d}"
        store.upsert_video(video_id=vid, artist_id="@Bulk",
                           url=f"https://www.youtube.com/watch?v={vid}", title=f"Video {i}")
        ids.append(vid)
    return ids


# ---------------------------------------------------------------------------
# Batch DB queries
# ---------------------------------------------------------------------------

class TestBatchDBQueries:

    def test_video_ids_with_transcripts(self, tmp_path):
        store = _make_store(tmp_path)
        ids = _seed_full(store, 5)
        # Add transcripts for first 3
        for vid in ids[:3]:
            store.save_transcript(video_id=vid, raw_text="Text", format="vtt")
        have = store.video_ids_with_transcripts(ids)
        assert have == set(ids[:3])

    def test_video_ids_with_transcripts_empty(self, tmp_path):
        store = _make_store(tmp_path)
        assert store.video_ids_with_transcripts([]) == set()

    def test_video_ids_with_transcripts_none_found(self, tmp_path):
        store = _make_store(tmp_path)
        ids = _seed_full(store, 3)
        assert store.video_ids_with_transcripts(ids) == set()

    def test_video_ids_with_summary(self, tmp_path):
        store = _make_store(tmp_path)
        ids = _seed_full(store, 5)
        store.upsert_prompt(prompt_id="p1", name="P", template="Summarize")
        for vid in ids[:3]:
            store.save_transcript(video_id=vid, raw_text="Text", format="vtt")
        # Add summaries for first 2
        for vid in ids[:2]:
            store.upsert_summary(video_id=vid, prompt_id="p1", content="Summary")
        have = store.video_ids_with_summary(ids, "p1")
        assert have == set(ids[:2])

    def test_video_ids_with_summary_different_prompts(self, tmp_path):
        store = _make_store(tmp_path)
        ids = _seed_full(store, 3)
        store.upsert_prompt(prompt_id="p1", name="P1", template="Summarize")
        store.upsert_prompt(prompt_id="p2", name="P2", template="Summarize v2")
        for vid in ids:
            store.save_transcript(video_id=vid, raw_text="Text", format="vtt")
        store.upsert_summary(video_id=ids[0], prompt_id="p1", content="S")
        store.upsert_summary(video_id=ids[1], prompt_id="p2", content="S")
        assert store.video_ids_with_summary(ids, "p1") == {ids[0]}
        assert store.video_ids_with_summary(ids, "p2") == {ids[1]}


# ---------------------------------------------------------------------------
# _ProgressCounter
# ---------------------------------------------------------------------------

class TestProgressCounter:

    def test_basic_counting(self):
        from yt_artist.cli import _ProgressCounter
        pc = _ProgressCounter(5)
        pc.tick("Test", "vid1")
        pc.tick("Test", "vid2", error="fail")
        assert pc.done == 2
        assert pc.errors == 1


# ---------------------------------------------------------------------------
# Parallel transcribe — verifies concurrency > 1 processes all videos
# ---------------------------------------------------------------------------

class TestBulkTranscribeConcurrency:

    def test_bulk_transcribe_parallel_all_succeed(self, tmp_path, capfd):
        """With --concurrency 2, all videos should be transcribed."""
        import logging as _logging
        from yt_artist.cli import main

        db = tmp_path / "test.db"
        store = Storage(db)
        store.ensure_schema()
        store.upsert_artist(
            artist_id="@Test", name="Test",
            channel_url="https://www.youtube.com/@Test",
            urllist_path="data/artists/@Test/urllist.md",
        )
        for i in range(3):
            vid = f"vid{i:09d}"
            store.upsert_video(video_id=vid, artist_id="@Test",
                               url=f"https://www.youtube.com/watch?v={vid}", title=f"V{i}")

        call_count = {"n": 0}

        def fake_transcribe(url, storage, **kw):
            call_count["n"] += 1
            vid = url.split("=")[-1]
            storage.save_transcript(video_id=vid, raw_text="Transcript text", format="vtt")
            return vid

        _logging.root.handlers.clear()
        argv = ["yt-artist", "--db", str(db), "--concurrency", "2", "transcribe", "--artist-id", "@Test"]
        with patch.object(sys, "argv", argv), \
             patch("yt_artist.cli.transcribe", side_effect=fake_transcribe), \
             patch("yt_artist.cli.get_inter_video_delay", return_value=0):
            main()

        captured = capfd.readouterr()
        assert "3 videos" in captured.out
        assert call_count["n"] == 3

    def test_bulk_transcribe_handles_errors(self, tmp_path, capfd):
        """Errors in individual videos should not abort the batch."""
        import logging as _logging
        from yt_artist.cli import main

        db = tmp_path / "test.db"
        store = Storage(db)
        store.ensure_schema()
        store.upsert_artist(
            artist_id="@Test", name="Test",
            channel_url="https://www.youtube.com/@Test",
            urllist_path="data/artists/@Test/urllist.md",
        )
        for i in range(2):
            vid = f"vid{i:09d}"
            store.upsert_video(video_id=vid, artist_id="@Test",
                               url=f"https://www.youtube.com/watch?v={vid}", title=f"V{i}")

        def fail_first(url, storage, **kw):
            vid = url.split("=")[-1]
            if "000000000" in vid:
                raise RuntimeError("Simulated error")
            storage.save_transcript(video_id=vid, raw_text="OK", format="vtt")
            return vid

        _logging.root.handlers.clear()
        argv = ["yt-artist", "--db", str(db), "transcribe", "--artist-id", "@Test"]
        with patch.object(sys, "argv", argv), \
             patch("yt_artist.cli.transcribe", side_effect=fail_first), \
             patch("yt_artist.cli.get_inter_video_delay", return_value=0):
            main()

        captured = capfd.readouterr()
        # Should report 1 error
        assert "1 error" in captured.out.lower() or "error" in captured.err.lower()


# ---------------------------------------------------------------------------
# Optimistic English download + metadata-informed fallback
# ---------------------------------------------------------------------------

class TestOptimisticSubtitleDownload:

    def test_optimistic_english_success_skips_lang_detection(self, tmp_path):
        """When optimistic English download succeeds, _get_available_sub_langs is not called."""
        from yt_artist.transcriber import _run_yt_dlp_subtitles

        out_dir = tmp_path / "subs"

        def fake_run(cmd, **kwargs):
            """Simulate yt-dlp writing an English subtitle file."""
            out_dir.mkdir(parents=True, exist_ok=True)
            f = out_dir / "test123.en.vtt"
            f.write_text("WEBVTT\n\n00:00:00.000 --> 00:00:01.000\nHello world\n", encoding="utf-8")
            return MagicMock(returncode=0, stdout="", stderr="")

        with patch("yt_artist.transcriber.subprocess.run", side_effect=fake_run), \
             patch("yt_artist.transcriber._get_available_sub_langs") as mock_langs:
            text, fmt = _run_yt_dlp_subtitles("https://youtube.com/watch?v=test123", out_dir)

        assert text == "Hello world"
        assert fmt == "vtt"
        # Lang detection should NOT have been called since optimistic download succeeded.
        mock_langs.assert_not_called()

    def test_optimistic_miss_falls_back_to_lang_detection(self, tmp_path):
        """When optimistic English download produces no file, falls through to metadata-informed retry."""
        from yt_artist.transcriber import _run_yt_dlp_subtitles

        out_dir = tmp_path / "subs"
        call_count = {"n": 0}

        def fake_run(cmd, **kwargs):
            """First call (optimistic) produces nothing; second call writes Spanish subs."""
            call_count["n"] += 1
            out_dir.mkdir(parents=True, exist_ok=True)
            if call_count["n"] >= 2:
                f = out_dir / "test123.es.vtt"
                f.write_text("WEBVTT\n\n00:00:00.000 --> 00:00:01.000\nHola mundo\n", encoding="utf-8")
            return MagicMock(returncode=0, stdout="", stderr="")

        with patch("yt_artist.transcriber.subprocess.run", side_effect=fake_run), \
             patch("yt_artist.transcriber._get_available_sub_langs", return_value=["es"]):
            text, fmt = _run_yt_dlp_subtitles("https://youtube.com/watch?v=test123", out_dir)

        assert "Hola mundo" in text
        assert call_count["n"] >= 2  # At least optimistic + one retry

    def test_429_triggers_backoff(self, tmp_path):
        """HTTP 429 in yt-dlp stderr triggers retry with backoff."""
        from yt_artist.transcriber import _run_yt_dlp_subtitles

        out_dir = tmp_path / "subs"
        call_count = {"n": 0}

        def fake_run(cmd, **kwargs):
            call_count["n"] += 1
            out_dir.mkdir(parents=True, exist_ok=True)
            if call_count["n"] == 1:
                # First call returns 429
                return MagicMock(returncode=1, stdout="", stderr="HTTP Error 429: Too Many Requests")
            # Second call succeeds
            f = out_dir / "test123.en.vtt"
            f.write_text("WEBVTT\n\n00:00:00.000 --> 00:00:01.000\nHello\n", encoding="utf-8")
            return MagicMock(returncode=0, stdout="", stderr="")

        import yt_artist.transcriber as _mod
        with patch("yt_artist.transcriber.subprocess.run", side_effect=fake_run), \
             patch.object(_mod._time, "sleep") as mock_sleep:
            text, fmt = _run_yt_dlp_subtitles("https://youtube.com/watch?v=test123", out_dir)

        assert text == "Hello"
        # Backoff sleep should have been called at least once.
        assert mock_sleep.call_count >= 1

    def test_rate_limit_detection(self):
        """_is_rate_limited detects 429 and rate limit patterns."""
        from yt_artist.transcriber import _is_rate_limited
        assert _is_rate_limited("HTTP Error 429: Too Many Requests")
        assert _is_rate_limited("ERROR: rate limit exceeded")
        assert _is_rate_limited("too many requests from your IP")
        assert not _is_rate_limited("Download complete")
        assert not _is_rate_limited("")
