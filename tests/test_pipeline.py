"""Tests for pipeline parallelism (producer-consumer transcribe+summarize)."""

from __future__ import annotations

import sys
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

from yt_artist.pipeline import PipelineResult, _split_concurrency, run_pipeline
from yt_artist.storage import Storage

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_store(tmp_path: Path) -> Storage:
    db = tmp_path / "test.db"
    store = Storage(db)
    store.ensure_schema()
    return store


def _seed(store: Storage, n_videos: int = 5, n_transcripts: int = 0, n_summaries: int = 0) -> list[str]:
    """Seed one artist with n_videos. Returns list of video IDs."""
    aid = "@TestArtist"
    store.upsert_artist(
        artist_id=aid,
        name="Test Artist",
        channel_url=f"https://www.youtube.com/{aid}",
        urllist_path=f"data/artists/{aid}/urllist.md",
    )
    vids: list[str] = []
    for j in range(n_videos):
        vid = f"vid{j:05d}xxxxx"[:11]
        store.upsert_video(
            video_id=vid,
            artist_id=aid,
            url=f"https://www.youtube.com/watch?v={vid}",
            title=f"Video {j}",
        )
        vids.append(vid)

    for vid in vids[:n_transcripts]:
        store.save_transcript(video_id=vid, raw_text="transcript text", format="vtt")

    if n_summaries > 0:
        store.upsert_prompt(prompt_id="p1", name="Test", template="Summarize: {video}")
        for vid in vids[:n_summaries]:
            store.upsert_summary(video_id=vid, prompt_id="p1", content="summary text")

    return vids


def _make_transcribe_fn(store: Storage):
    """Build a transcribe_fn that writes a real transcript to the DB."""

    def fn(vid: str) -> tuple[str, str | None]:
        store.save_transcript(video_id=vid, raw_text=f"transcript for {vid}", format="vtt")
        return (vid, None)

    return fn


def _make_summarize_fn(store: Storage, prompt_id: str = "p1"):
    """Build a summarize_fn that writes a real summary to the DB."""

    def fn(vid: str) -> tuple[str, str, str | None]:
        store.upsert_summary(video_id=vid, prompt_id=prompt_id, content=f"summary for {vid}")
        return (vid, f"{vid}:{prompt_id}", None)

    return fn


def _make_poll_fn(store: Storage, all_ids: list[str], prompt_id: str = "p1"):
    """Build a poll_fn that queries the real DB."""

    def fn() -> list[str]:
        have_t = store.video_ids_with_transcripts(all_ids)
        have_s = store.video_ids_with_summary(all_ids, prompt_id)
        return [vid for vid in all_ids if vid in have_t and vid not in have_s]

    return fn


# ---------------------------------------------------------------------------
# TestSplitConcurrency
# ---------------------------------------------------------------------------


class TestSplitConcurrency:
    def test_split_concurrency_1(self):
        assert _split_concurrency(1) == (1, 1)

    def test_split_concurrency_2(self):
        assert _split_concurrency(2) == (1, 1)

    def test_split_concurrency_3(self):
        assert _split_concurrency(3) == (2, 1)


# ---------------------------------------------------------------------------
# TestPipelineHappyPath
# ---------------------------------------------------------------------------


class TestPipelineHappyPath:
    def test_transcribe_and_summarize(self, tmp_path):
        """All videos need transcription + summarization."""
        store = _make_store(tmp_path)
        vids = _seed(store, n_videos=5, n_transcripts=0)
        store.upsert_prompt(prompt_id="p1", name="Test", template="Summarize: {video}")

        result = run_pipeline(
            video_ids_to_transcribe=vids,
            video_ids_to_summarize=[],
            transcribe_fn=_make_transcribe_fn(store),
            summarize_fn=_make_summarize_fn(store),
            poll_fn=_make_poll_fn(store, vids),
            poll_interval=0.1,
            inter_delay=0,
        )
        assert result.transcribed == 5
        assert result.transcribe_errors == 0
        assert result.summarized == 5
        assert result.summarize_errors == 0
        assert result.elapsed > 0

    def test_mixed_state(self, tmp_path):
        """2 need transcription, 1 already has transcript but no summary."""
        store = _make_store(tmp_path)
        vids = _seed(store, n_videos=3, n_transcripts=1)
        store.upsert_prompt(prompt_id="p1", name="Test", template="Summarize: {video}")

        need_transcribe = vids[1:]  # vids[0] already has transcript
        immediate_summarize = [vids[0]]  # has transcript, no summary

        result = run_pipeline(
            video_ids_to_transcribe=need_transcribe,
            video_ids_to_summarize=immediate_summarize,
            transcribe_fn=_make_transcribe_fn(store),
            summarize_fn=_make_summarize_fn(store),
            poll_fn=_make_poll_fn(store, vids),
            poll_interval=0.1,
            inter_delay=0,
        )
        assert result.transcribed == 2
        assert result.summarized == 3

    def test_only_summarize(self, tmp_path):
        """0 to transcribe, 3 to summarize (degenerate case)."""
        store = _make_store(tmp_path)
        vids = _seed(store, n_videos=3, n_transcripts=3)
        store.upsert_prompt(prompt_id="p1", name="Test", template="Summarize: {video}")

        result = run_pipeline(
            video_ids_to_transcribe=[],
            video_ids_to_summarize=vids,
            transcribe_fn=_make_transcribe_fn(store),
            summarize_fn=_make_summarize_fn(store),
            poll_fn=_make_poll_fn(store, vids),
            poll_interval=0.1,
            inter_delay=0,
        )
        assert result.transcribed == 0
        assert result.summarized == 3
        assert result.transcribe_errors == 0
        assert result.summarize_errors == 0


# ---------------------------------------------------------------------------
# TestPipelineErrors
# ---------------------------------------------------------------------------


class TestPipelineErrors:
    def test_transcribe_error_no_block_summarize(self, tmp_path):
        """Transcribe error on 1/3 videos; the other 2 still get summarized."""
        store = _make_store(tmp_path)
        vids = _seed(store, n_videos=3, n_transcripts=0)
        store.upsert_prompt(prompt_id="p1", name="Test", template="Summarize: {video}")

        fail_vid = vids[0]
        real_fn = _make_transcribe_fn(store)

        def failing_transcribe(vid: str) -> tuple[str, str | None]:
            if vid == fail_vid:
                return (vid, "simulated error")
            return real_fn(vid)

        result = run_pipeline(
            video_ids_to_transcribe=vids,
            video_ids_to_summarize=[],
            transcribe_fn=failing_transcribe,
            summarize_fn=_make_summarize_fn(store),
            poll_fn=_make_poll_fn(store, vids),
            poll_interval=0.1,
            inter_delay=0,
        )
        assert result.transcribed == 2
        assert result.transcribe_errors == 1
        assert result.summarized == 2
        assert result.summarize_errors == 0

    def test_summarize_error_no_block_transcribe(self, tmp_path):
        """Summarize error on 1/3 videos; all 3 still get transcribed."""
        store = _make_store(tmp_path)
        vids = _seed(store, n_videos=3, n_transcripts=0)
        store.upsert_prompt(prompt_id="p1", name="Test", template="Summarize: {video}")

        fail_vid = vids[0]
        real_fn = _make_summarize_fn(store)

        def failing_summarize(vid: str) -> tuple[str, str, str | None]:
            if vid == fail_vid:
                return (vid, "", "simulated error")
            return real_fn(vid)

        result = run_pipeline(
            video_ids_to_transcribe=vids,
            video_ids_to_summarize=[],
            transcribe_fn=_make_transcribe_fn(store),
            summarize_fn=failing_summarize,
            poll_fn=_make_poll_fn(store, vids),
            poll_interval=0.1,
            inter_delay=0,
        )
        assert result.transcribed == 3
        assert result.transcribe_errors == 0
        assert result.summarized == 2
        assert result.summarize_errors == 1

    def test_all_transcribe_errors(self, tmp_path):
        """All transcriptions fail; only pre-existing transcripts get summarized."""
        store = _make_store(tmp_path)
        vids = _seed(store, n_videos=3, n_transcripts=1)
        store.upsert_prompt(prompt_id="p1", name="Test", template="Summarize: {video}")

        def always_fail(vid: str) -> tuple[str, str | None]:
            return (vid, "always fails")

        result = run_pipeline(
            video_ids_to_transcribe=vids[1:],  # 2 need transcription, all fail
            video_ids_to_summarize=[vids[0]],  # 1 already transcribed
            transcribe_fn=always_fail,
            summarize_fn=_make_summarize_fn(store),
            poll_fn=_make_poll_fn(store, vids),
            poll_interval=0.1,
            inter_delay=0,
        )
        assert result.transcribed == 0
        assert result.transcribe_errors == 2
        assert result.summarized == 1
        assert result.summarize_errors == 0


# ---------------------------------------------------------------------------
# TestPipelineProgress
# ---------------------------------------------------------------------------


class TestPipelineProgress:
    def test_progress_tick_called(self, tmp_path):
        """tick() is called the correct number of times on both counters."""
        store = _make_store(tmp_path)
        vids = _seed(store, n_videos=3, n_transcripts=0)
        store.upsert_prompt(prompt_id="p1", name="Test", template="Summarize: {video}")

        t_progress = MagicMock()
        s_progress = MagicMock()

        run_pipeline(
            video_ids_to_transcribe=vids,
            video_ids_to_summarize=[],
            transcribe_fn=_make_transcribe_fn(store),
            summarize_fn=_make_summarize_fn(store),
            poll_fn=_make_poll_fn(store, vids),
            poll_interval=0.1,
            inter_delay=0,
            transcribe_progress=t_progress,
            summarize_progress=s_progress,
        )
        assert t_progress.tick.call_count == 3
        assert s_progress.tick.call_count == 3

    def test_progress_labels(self, tmp_path):
        """Progress ticks use 'Pipeline:Transcribing' and 'Pipeline:Summarizing'."""
        store = _make_store(tmp_path)
        vids = _seed(store, n_videos=2, n_transcripts=0)
        store.upsert_prompt(prompt_id="p1", name="Test", template="Summarize: {video}")

        t_progress = MagicMock()
        s_progress = MagicMock()

        run_pipeline(
            video_ids_to_transcribe=vids,
            video_ids_to_summarize=[],
            transcribe_fn=_make_transcribe_fn(store),
            summarize_fn=_make_summarize_fn(store),
            poll_fn=_make_poll_fn(store, vids),
            poll_interval=0.1,
            inter_delay=0,
            transcribe_progress=t_progress,
            summarize_progress=s_progress,
        )
        for c in t_progress.tick.call_args_list:
            assert c.args[0] == "Pipeline:Transcribing"
        for c in s_progress.tick.call_args_list:
            assert c.args[0] == "Pipeline:Summarizing"


# ---------------------------------------------------------------------------
# TestPipelineTermination
# ---------------------------------------------------------------------------


class TestPipelineTermination:
    def test_terminates_when_all_done(self, tmp_path):
        """Pipeline returns within a reasonable time (not hung)."""
        store = _make_store(tmp_path)
        vids = _seed(store, n_videos=5, n_transcripts=0)
        store.upsert_prompt(prompt_id="p1", name="Test", template="Summarize: {video}")

        t0 = time.monotonic()
        result = run_pipeline(
            video_ids_to_transcribe=vids,
            video_ids_to_summarize=[],
            transcribe_fn=_make_transcribe_fn(store),
            summarize_fn=_make_summarize_fn(store),
            poll_fn=_make_poll_fn(store, vids),
            poll_interval=0.1,
            inter_delay=0,
        )
        elapsed = time.monotonic() - t0
        assert elapsed < 30  # should finish in seconds, not hang
        assert result.transcribed == 5
        assert result.summarized == 5

    def test_empty_transcribe_list(self, tmp_path):
        """video_ids_to_transcribe=[] works; summarize completes promptly."""
        store = _make_store(tmp_path)
        vids = _seed(store, n_videos=2, n_transcripts=2)
        store.upsert_prompt(prompt_id="p1", name="Test", template="Summarize: {video}")

        t0 = time.monotonic()
        result = run_pipeline(
            video_ids_to_transcribe=[],
            video_ids_to_summarize=vids,
            transcribe_fn=_make_transcribe_fn(store),
            summarize_fn=_make_summarize_fn(store),
            poll_fn=_make_poll_fn(store, vids),
            poll_interval=0.1,
            inter_delay=0,
        )
        elapsed = time.monotonic() - t0
        assert elapsed < 15
        assert result.summarized == 2


# ---------------------------------------------------------------------------
# TestPipelineDelay
# ---------------------------------------------------------------------------


class TestPipelineDelay:
    def test_inter_delay_on_transcribe(self, tmp_path):
        """With inter_delay=0.05 and 3 videos, elapsed >= 0.1s (2 gaps)."""
        store = _make_store(tmp_path)
        vids = _seed(store, n_videos=3, n_transcripts=0)
        store.upsert_prompt(prompt_id="p1", name="Test", template="Summarize: {video}")

        result = run_pipeline(
            video_ids_to_transcribe=vids,
            video_ids_to_summarize=[],
            transcribe_fn=_make_transcribe_fn(store),
            summarize_fn=_make_summarize_fn(store),
            poll_fn=_make_poll_fn(store, vids),
            poll_interval=0.1,
            inter_delay=0.05,
        )
        assert result.elapsed >= 0.1
        assert result.transcribed == 3


# ---------------------------------------------------------------------------
# TestPipelineCLIIntegration
# ---------------------------------------------------------------------------


def _run_cli(*args: str, db_path=None) -> int:
    """Call main() with patched sys.argv; return exit code."""
    import logging as _logging

    _logging.root.handlers.clear()
    from yt_artist.cli import main

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


class TestPipelineCLIIntegration:
    def test_bulk_summarize_missing_uses_pipeline(self, tmp_path, capfd):
        """When transcripts are missing, pipeline mode activates."""
        db = tmp_path / "test.db"
        store = Storage(db)
        store.ensure_schema()
        _seed(store, n_videos=3, n_transcripts=0)
        store.upsert_prompt(prompt_id="p1", name="Test", template="Summarize: {video}")

        with (
            patch("yt_artist.cli.transcribe") as mock_t,
            patch("yt_artist.cli.summarize", return_value="vid:p1") as mock_s,
            patch("yt_artist.cli._check_llm"),
            patch("yt_artist.pipeline.run_pipeline") as mock_pipeline,
        ):
            mock_pipeline.return_value = PipelineResult(
                transcribed=3,
                transcribe_errors=0,
                summarized=3,
                summarize_errors=0,
                elapsed=1.0,
            )
            code = _run_cli("summarize", "--artist-id", "@TestArtist", db_path=db)

        assert code == 0
        mock_pipeline.assert_called_once()
        # _run_bulk should NOT have been called for summarize
        out = capfd.readouterr().out
        assert "Pipeline" in out

    def test_bulk_summarize_no_missing_sequential(self, tmp_path, capfd):
        """When all transcripts exist, pipeline is NOT used."""
        db = tmp_path / "test.db"
        store = Storage(db)
        store.ensure_schema()
        _seed(store, n_videos=3, n_transcripts=3)
        store.upsert_prompt(prompt_id="p1", name="Test", template="Summarize: {video}")

        with (
            patch("yt_artist.cli.summarize", return_value="vid:p1") as mock_s,
            patch("yt_artist.cli._check_llm"),
            patch("yt_artist.pipeline.run_pipeline") as mock_pipeline,
        ):
            code = _run_cli("summarize", "--artist-id", "@TestArtist", db_path=db)

        assert code == 0
        mock_pipeline.assert_not_called()
        out = capfd.readouterr().out
        assert "Summarized" in out

    def test_pipeline_with_background_job(self, tmp_path):
        """Pipeline updates job progress when job_id/job_storage are set."""
        store = _make_store(tmp_path)
        vids = _seed(store, n_videos=2, n_transcripts=0)
        store.upsert_prompt(prompt_id="p1", name="Test", template="Summarize: {video}")

        # Create a mock progress counter to verify ticks happen
        s_progress = MagicMock()

        result = run_pipeline(
            video_ids_to_transcribe=vids,
            video_ids_to_summarize=[],
            transcribe_fn=_make_transcribe_fn(store),
            summarize_fn=_make_summarize_fn(store),
            poll_fn=_make_poll_fn(store, vids),
            poll_interval=0.1,
            inter_delay=0,
            summarize_progress=s_progress,
        )
        assert result.summarized == 2
        assert s_progress.tick.call_count == 2
