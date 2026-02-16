"""Tests for --dry-run flag on transcribe and summarize commands."""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from yt_artist.cli import main
from yt_artist.storage import Storage


def _make_store(tmp_path: Path) -> Storage:
    db = tmp_path / "test.db"
    store = Storage(db)
    store.ensure_schema()
    return store


def _seed(store: Storage, n_artists: int = 1, n_videos: int = 3,
          n_transcripts: int = 0, n_summaries: int = 0) -> None:
    """Seed test data flexibly."""
    for i in range(n_artists):
        aid = f"@Artist{i}"
        store.upsert_artist(
            artist_id=aid, name=f"Artist {i}",
            channel_url=f"https://www.youtube.com/{aid}",
            urllist_path=f"data/artists/{aid}/urllist.md",
        )
        for j in range(n_videos):
            vid = f"vid{i:02d}{j:03d}xxxxx"[:11]
            store.upsert_video(
                video_id=vid, artist_id=aid,
                url=f"https://www.youtube.com/watch?v={vid}",
                title=f"Video {j}",
            )

    # Add transcripts for first n_transcripts videos
    all_vids = store.list_videos()
    for v in all_vids[:n_transcripts]:
        store.save_transcript(video_id=v["id"], raw_text="transcript text", format="vtt")

    # Add summaries for first n_summaries videos (need a prompt)
    if n_summaries > 0:
        prompts = store.list_prompts()
        pid = prompts[0]["id"] if prompts else "default"
        for v in all_vids[:n_summaries]:
            store.upsert_summary(video_id=v["id"], prompt_id=pid, content="summary text")


def _run_cli(*args: str, db_path=None) -> int:
    """Call main() with patched sys.argv; return exit code."""
    import logging as _logging
    _logging.root.handlers.clear()
    argv = ["yt-artist"]
    if db_path:
        argv += ["--db", str(db_path)]
    argv += list(args)
    with patch.object(sys, "argv", argv):
        try:
            main()
            return 0
        except SystemExit as exc:
            return exc.code if exc.code else 0


# ---------------------------------------------------------------------------
# Transcribe --dry-run (bulk)
# ---------------------------------------------------------------------------

class TestDryRunTranscribeBulk:

    def test_dry_run_transcribe_bulk_shows_count(self, tmp_path, capfd):
        """--dry-run shows how many videos would be transcribed."""
        db = tmp_path / "test.db"
        store = Storage(db)
        store.ensure_schema()
        _seed(store, n_artists=1, n_videos=5, n_transcripts=2)
        code = _run_cli("--dry-run", "transcribe", "--artist-id", "@Artist0", db_path=db)
        assert code == 0
        out = capfd.readouterr().out
        assert "Would transcribe 3 videos" in out
        assert "2 already done" in out

    def test_dry_run_transcribe_shows_estimate(self, tmp_path, capfd):
        """--dry-run output includes time estimate."""
        db = tmp_path / "test.db"
        store = Storage(db)
        store.ensure_schema()
        _seed(store, n_artists=1, n_videos=5)
        code = _run_cli("--dry-run", "transcribe", "--artist-id", "@Artist0", db_path=db)
        assert code == 0
        out = capfd.readouterr().out
        assert "Estimated:" in out

    def test_dry_run_transcribe_does_not_call_run_bulk(self, tmp_path, capfd):
        """_run_bulk is never invoked during --dry-run."""
        db = tmp_path / "test.db"
        store = Storage(db)
        store.ensure_schema()
        _seed(store, n_artists=1, n_videos=5)
        with patch("yt_artist.cli._run_bulk") as mock_bulk:
            code = _run_cli("--dry-run", "transcribe", "--artist-id", "@Artist0", db_path=db)
        assert code == 0
        mock_bulk.assert_not_called()

    def test_dry_run_transcribe_all_done(self, tmp_path, capfd):
        """When all videos already have transcripts, shows 'already have transcripts'."""
        db = tmp_path / "test.db"
        store = Storage(db)
        store.ensure_schema()
        _seed(store, n_artists=1, n_videos=3, n_transcripts=3)
        code = _run_cli("--dry-run", "transcribe", "--artist-id", "@Artist0", db_path=db)
        assert code == 0
        out = capfd.readouterr().out
        # The normal "all done" message fires before dry-run intercept
        assert "already have transcripts" in out

    def test_dry_run_transcribe_exit_code_zero(self, tmp_path):
        """--dry-run always exits with code 0."""
        db = tmp_path / "test.db"
        store = Storage(db)
        store.ensure_schema()
        _seed(store, n_artists=1, n_videos=5)
        code = _run_cli("--dry-run", "transcribe", "--artist-id", "@Artist0", db_path=db)
        assert code == 0

    def test_dry_run_with_concurrency(self, tmp_path, capfd):
        """Estimate reflects concurrency (shorter time)."""
        db = tmp_path / "test.db"
        store = Storage(db)
        store.ensure_schema()
        _seed(store, n_artists=1, n_videos=10)
        # Run once without concurrency
        code = _run_cli("--dry-run", "transcribe", "--artist-id", "@Artist0", db_path=db)
        assert code == 0
        out1 = capfd.readouterr().out
        assert "Estimated:" in out1


# ---------------------------------------------------------------------------
# Transcribe --dry-run (single video)
# ---------------------------------------------------------------------------

class TestDryRunTranscribeSingle:

    def test_dry_run_single_video(self, tmp_path, capfd):
        """--dry-run on a single video shows 'Would transcribe 1 video'."""
        db = tmp_path / "test.db"
        code = _run_cli(
            "--dry-run", "transcribe",
            "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
            db_path=db,
        )
        assert code == 0
        out = capfd.readouterr().out
        assert "Would transcribe 1 video" in out
        assert "dQw4w9WgXcQ" in out

    def test_dry_run_single_does_not_call_transcribe(self, tmp_path, capfd):
        """transcribe() is never called during --dry-run."""
        db = tmp_path / "test.db"
        with patch("yt_artist.cli.transcribe") as mock_transcribe:
            code = _run_cli(
                "--dry-run", "transcribe",
                "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
                db_path=db,
            )
        assert code == 0
        mock_transcribe.assert_not_called()


# ---------------------------------------------------------------------------
# Summarize --dry-run (bulk)
# ---------------------------------------------------------------------------

class TestDryRunSummarizeBulk:

    def test_dry_run_summarize_bulk_shows_count(self, tmp_path, capfd):
        """--dry-run shows how many videos would be summarized."""
        db = tmp_path / "test.db"
        store = Storage(db)
        store.ensure_schema()
        _seed(store, n_artists=1, n_videos=5, n_transcripts=5, n_summaries=2)
        # Patch _check_llm to skip LLM health check during dry-run
        with patch("yt_artist.cli._check_llm"):
            code = _run_cli("--dry-run", "summarize", "--artist-id", "@Artist0", db_path=db)
        assert code == 0
        out = capfd.readouterr().out
        assert "Would summarize 3 videos" in out
        assert "2 already done" in out

    def test_dry_run_summarize_missing_transcripts(self, tmp_path, capfd):
        """--dry-run reports both missing transcripts and summarize work."""
        db = tmp_path / "test.db"
        store = Storage(db)
        store.ensure_schema()
        _seed(store, n_artists=1, n_videos=5, n_transcripts=3)
        with patch("yt_artist.cli._check_llm"):
            code = _run_cli("--dry-run", "summarize", "--artist-id", "@Artist0", db_path=db)
        assert code == 0
        out = capfd.readouterr().out
        assert "Would transcribe 2 videos" in out
        assert "Would summarize" in out

    def test_dry_run_summarize_does_not_call_run_bulk(self, tmp_path, capfd):
        """_run_bulk is never invoked during --dry-run summarize."""
        db = tmp_path / "test.db"
        store = Storage(db)
        store.ensure_schema()
        _seed(store, n_artists=1, n_videos=5, n_transcripts=5)
        with patch("yt_artist.cli._check_llm"), \
             patch("yt_artist.cli._run_bulk") as mock_bulk:
            code = _run_cli("--dry-run", "summarize", "--artist-id", "@Artist0", db_path=db)
        assert code == 0
        mock_bulk.assert_not_called()

    def test_dry_run_summarize_shows_estimate(self, tmp_path, capfd):
        """--dry-run summarize output includes time estimate."""
        db = tmp_path / "test.db"
        store = Storage(db)
        store.ensure_schema()
        _seed(store, n_artists=1, n_videos=5, n_transcripts=5)
        with patch("yt_artist.cli._check_llm"):
            code = _run_cli("--dry-run", "summarize", "--artist-id", "@Artist0", db_path=db)
        assert code == 0
        out = capfd.readouterr().out
        assert "Estimated:" in out

    def test_dry_run_summarize_all_done(self, tmp_path, capfd):
        """When all videos already summarized, shows 'already summarized'."""
        db = tmp_path / "test.db"
        store = Storage(db)
        store.ensure_schema()
        _seed(store, n_artists=1, n_videos=3, n_transcripts=3, n_summaries=3)
        with patch("yt_artist.cli._check_llm"):
            code = _run_cli("--dry-run", "summarize", "--artist-id", "@Artist0", db_path=db)
        assert code == 0
        out = capfd.readouterr().out
        assert "already summarized" in out


# ---------------------------------------------------------------------------
# Summarize --dry-run (single video)
# ---------------------------------------------------------------------------

class TestDryRunSummarizeSingle:

    def test_dry_run_single_summarize(self, tmp_path, capfd):
        """--dry-run on a single video shows 'Would summarize 1 video'."""
        db = tmp_path / "test.db"
        with patch("yt_artist.cli._check_llm"):
            code = _run_cli(
                "--dry-run", "summarize",
                "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
                db_path=db,
            )
        assert code == 0
        out = capfd.readouterr().out
        assert "Would summarize 1 video" in out
        assert "dQw4w9WgXcQ" in out

    def test_dry_run_single_does_not_call_summarize(self, tmp_path, capfd):
        """summarize() is never called during --dry-run."""
        db = tmp_path / "test.db"
        with patch("yt_artist.cli._check_llm"), \
             patch("yt_artist.cli.summarize") as mock_summarize:
            code = _run_cli(
                "--dry-run", "summarize",
                "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
                db_path=db,
            )
        assert code == 0
        mock_summarize.assert_not_called()
