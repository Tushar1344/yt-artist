"""Tests for guided onboarding: --quiet flag, next-step hints, quickstart, first-run detection."""

from __future__ import annotations

import logging as _logging
import sys
from pathlib import Path
from unittest.mock import patch

from yt_artist.cli import main
from yt_artist.storage import Storage

# ---------------------------------------------------------------------------
# Helpers (same pattern as test_cli.py)
# ---------------------------------------------------------------------------


def _make_store(tmp_path: Path) -> Storage:
    db = tmp_path / "test.db"
    store = Storage(db)
    store.ensure_schema()
    return store


def _run_cli(*args: str, db_path: str | Path = "") -> int:
    """Call main() with patched sys.argv; return exit code (0 on success)."""
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


def _seed_artist(store: Storage, artist_id: str = "@TestCh") -> None:
    store.upsert_artist(
        artist_id=artist_id,
        name="Test Channel",
        channel_url=f"https://www.youtube.com/{artist_id}",
        urllist_path=f"data/artists/{artist_id}/urllist.md",
    )


def _seed_video(store: Storage, video_id: str = "testvid00001", artist_id: str = "@TestCh") -> None:
    store.upsert_video(
        video_id=video_id,
        artist_id=artist_id,
        url=f"https://www.youtube.com/watch?v={video_id}",
        title="Test Video",
    )


# ---------------------------------------------------------------------------
# --quiet flag
# ---------------------------------------------------------------------------


class TestQuietFlag:
    def test_quiet_suppresses_hints(self, tmp_path, capfd):
        """With -q, no hint text should appear in stderr after fetch-channel."""
        db = tmp_path / "test.db"
        with patch("yt_artist.cli.fetch_channel", return_value=("path/urllist.md", 5)):
            code = _run_cli("-q", "fetch-channel", "https://youtube.com/@Test", db_path=db)
        assert code == 0
        captured = capfd.readouterr()
        assert "Next:" not in captured.err
        assert "\U0001f4a1" not in captured.err

    def test_no_quiet_shows_hints(self, tmp_path, capfd):
        """Without -q, hints should appear in stderr after fetch-channel."""
        db = tmp_path / "test.db"
        with patch("yt_artist.cli.fetch_channel", return_value=("path/urllist.md", 5)):
            code = _run_cli("fetch-channel", "https://youtube.com/@Test", db_path=db)
        assert code == 0
        captured = capfd.readouterr()
        assert "transcribe" in captured.err


# ---------------------------------------------------------------------------
# Hints: fetch-channel
# ---------------------------------------------------------------------------


class TestFetchChannelHints:
    def test_hint_suggests_transcribe_with_artist_id(self, tmp_path, capfd):
        """After fetch-channel, hint should suggest transcribe --artist-id."""
        db = tmp_path / "test.db"
        with patch("yt_artist.cli.fetch_channel", return_value=("path/urllist.md", 10)):
            code = _run_cli("fetch-channel", "https://youtube.com/@TestCh", db_path=db)
        assert code == 0
        captured = capfd.readouterr()
        assert "transcribe --artist-id @TestCh" in captured.err

    def test_hint_includes_sample_video(self, tmp_path, capfd):
        """Hint should include a real video URL when videos exist in DB."""
        db = tmp_path / "test.db"
        store = Storage(db)
        store.ensure_schema()
        _seed_artist(store, "@TestCh")
        _seed_video(store, "sampleVID123", "@TestCh")

        with patch("yt_artist.cli.fetch_channel", return_value=("path/urllist.md", 3)):
            code = _run_cli("fetch-channel", "https://youtube.com/@TestCh", db_path=db)
        assert code == 0
        captured = capfd.readouterr()
        assert "sampleVID123" in captured.err


# ---------------------------------------------------------------------------
# Hints: transcribe
# ---------------------------------------------------------------------------


class TestTranscribeHints:
    def test_single_video_hint_suggests_summarize(self, tmp_path, capfd):
        """After single-video transcribe, hint should suggest summarize."""
        db = tmp_path / "test.db"
        with patch("yt_artist.cli.transcribe", return_value="testvid00001"):
            code = _run_cli("transcribe", "https://youtube.com/watch?v=testvid00001", db_path=db)
        assert code == 0
        captured = capfd.readouterr()
        assert "summarize testvid00001" in captured.err

    def test_bulk_transcribe_hint_suggests_summarize(self, tmp_path, capfd):
        """After bulk transcribe, hint should suggest summarize --artist-id."""
        db = tmp_path / "test.db"
        store = Storage(db)
        store.ensure_schema()
        _seed_artist(store)
        _seed_video(store)

        def fake_transcribe(url, storage, **kw):
            vid = url.split("=")[-1]
            storage.save_transcript(video_id=vid, raw_text="Text", format="vtt")
            return vid

        with (
            patch("yt_artist.cli.transcribe", side_effect=fake_transcribe),
            patch("yt_artist.cli.get_inter_video_delay", return_value=0),
        ):
            code = _run_cli("transcribe", "--artist-id", "@TestCh", db_path=db)
        assert code == 0
        captured = capfd.readouterr()
        assert "summarize --artist-id @TestCh" in captured.err


# ---------------------------------------------------------------------------
# Hints: summarize
# ---------------------------------------------------------------------------


class TestSummarizeHints:
    def test_single_video_hint_suggests_bulk(self, tmp_path, capfd):
        """After single-video summarize, hint should suggest bulk summarize."""
        db = tmp_path / "test.db"
        store = Storage(db)
        store.ensure_schema()
        _seed_artist(store)
        _seed_video(store)
        store.save_transcript(video_id="testvid00001", raw_text="Hello", format="vtt")

        with (
            patch("yt_artist.cli.summarize", return_value="testvid00001:default") as mock_sum,
            patch("yt_artist.cli.ensure_artist_and_video_for_video_url", return_value=("@TestCh", "testvid00001")),
            patch("yt_artist.cli._check_llm"),
        ):
            # Mock get_summaries_for_video
            original_get = store.get_summaries_for_video

            def fake_get(vid):
                return [{"prompt_id": "default", "content": "A summary.", "video_id": vid, "created_at": "now"}]

            with patch.object(store, "get_summaries_for_video", side_effect=fake_get):
                code = _run_cli("summarize", "testvid00001", db_path=db)
        assert code == 0
        captured = capfd.readouterr()
        assert "summarize --artist-id @TestCh" in captured.err

    def test_bulk_summarize_hint_suggests_search(self, tmp_path, capfd):
        """After bulk summarize completes, hint should suggest search-transcripts."""
        db = tmp_path / "test.db"
        store = Storage(db)
        store.ensure_schema()
        _seed_artist(store)
        _seed_video(store)
        store.save_transcript(video_id="testvid00001", raw_text="Hello", format="vtt")

        with patch("yt_artist.cli.summarize", return_value="testvid00001:default"), patch("yt_artist.cli._check_llm"):
            code = _run_cli("summarize", "--artist-id", "@TestCh", db_path=db)
        assert code == 0
        captured = capfd.readouterr()
        assert "search-transcripts" in captured.err


# ---------------------------------------------------------------------------
# Hints: other commands
# ---------------------------------------------------------------------------


class TestOtherCommandHints:
    def test_add_prompt_hint(self, tmp_path, capfd):
        """After add-prompt, hint should suggest set-default-prompt."""
        db = tmp_path / "test.db"
        code = _run_cli(
            "add-prompt",
            "--id",
            "my-p",
            "--name",
            "My Prompt",
            "--template",
            "Summarize: {video}",
            db_path=db,
        )
        assert code == 0
        captured = capfd.readouterr()
        assert "set-default-prompt" in captured.err
        assert "my-p" in captured.err

    def test_list_prompts_hint(self, tmp_path, capfd):
        """After list-prompts (with data), hint should suggest using a prompt."""
        db = tmp_path / "test.db"
        _run_cli("add-prompt", "--id", "p1", "--name", "P", "--template", "T", db_path=db)
        code = _run_cli("list-prompts", db_path=db)
        assert code == 0
        captured = capfd.readouterr()
        assert "summarize" in captured.err.lower()

    def test_set_default_prompt_hint(self, tmp_path, capfd):
        """After set-default-prompt, hint should suggest summarize."""
        db = tmp_path / "test.db"
        store = Storage(db)
        store.ensure_schema()
        _seed_artist(store)
        store.upsert_prompt(prompt_id="p1", name="P", template="T")

        code = _run_cli("set-default-prompt", "--artist-id", "@TestCh", "--prompt", "p1", db_path=db)
        assert code == 0
        captured = capfd.readouterr()
        assert "summarize --artist-id @TestCh" in captured.err

    def test_search_transcripts_empty_hint(self, tmp_path, capfd):
        """With no transcripts, hint should suggest fetch-channel."""
        db = tmp_path / "test.db"
        code = _run_cli("search-transcripts", db_path=db)
        assert code == 0
        captured = capfd.readouterr()
        assert "fetch-channel" in captured.err

    def test_search_transcripts_with_data_hint(self, tmp_path, capfd):
        """With transcript results, hint should suggest summarize."""
        db = tmp_path / "test.db"
        store = Storage(db)
        store.ensure_schema()
        _seed_artist(store)
        _seed_video(store)
        store.save_transcript(video_id="testvid00001", raw_text="Hello", format="vtt")

        code = _run_cli("search-transcripts", "--artist-id", "@TestCh", db_path=db)
        assert code == 0
        captured = capfd.readouterr()
        assert "summarize testvid00001" in captured.err


# ---------------------------------------------------------------------------
# quickstart subcommand
# ---------------------------------------------------------------------------


class TestQuickstart:
    def test_prints_all_steps(self, tmp_path, capfd):
        """quickstart should print STEP 1/2/3 with @TED commands."""
        db = tmp_path / "test.db"
        code = _run_cli("quickstart", db_path=db)
        assert code == 0
        captured = capfd.readouterr()
        assert "STEP 1" in captured.out
        assert "STEP 2" in captured.out
        assert "STEP 3" in captured.out
        assert "@TED" in captured.out
        assert "fetch-channel" in captured.out
        assert "transcribe" in captured.out
        assert "summarize" in captured.out

    def test_includes_db_flag_when_passed(self, tmp_path, capfd):
        """When --db is passed, quickstart commands should include it."""
        db = tmp_path / "custom.db"
        code = _run_cli("quickstart", db_path=db)
        assert code == 0
        captured = capfd.readouterr()
        assert str(db) in captured.out

    def test_mentions_shortcut(self, tmp_path, capfd):
        """quickstart should mention the summarize-does-everything shortcut."""
        db = tmp_path / "test.db"
        code = _run_cli("quickstart", db_path=db)
        assert code == 0
        captured = capfd.readouterr()
        assert "SHORTCUT" in captured.out
        assert "automatically" in captured.out


# ---------------------------------------------------------------------------
# First-run detection
# ---------------------------------------------------------------------------


class TestFirstRunDetection:
    def test_empty_db_shows_quickstart_tip(self, tmp_path, capfd):
        """On first use with empty DB, stderr should mention quickstart."""
        db = tmp_path / "test.db"
        code = _run_cli("list-prompts", db_path=db)
        assert code == 0
        captured = capfd.readouterr()
        assert "quickstart" in captured.err

    def test_populated_db_no_quickstart_tip(self, tmp_path, capfd):
        """When DB has artists, quickstart tip should NOT appear."""
        db = tmp_path / "test.db"
        store = Storage(db)
        store.ensure_schema()
        _seed_artist(store)

        code = _run_cli("list-prompts", db_path=db)
        assert code == 0
        captured = capfd.readouterr()
        assert "quickstart" not in captured.err.split("Next:")[0] if "Next:" in captured.err else True
        # More precisely: the "First time?" tip should not appear
        assert "First time?" not in captured.err

    def test_quiet_suppresses_first_run_tip(self, tmp_path, capfd):
        """--quiet should suppress the first-run quickstart tip."""
        db = tmp_path / "test.db"
        code = _run_cli("-q", "list-prompts", db_path=db)
        assert code == 0
        captured = capfd.readouterr()
        assert "quickstart" not in captured.err

    def test_quickstart_itself_no_first_run_tip(self, tmp_path, capfd):
        """Running quickstart should not show the 'First time?' tip."""
        db = tmp_path / "test.db"
        code = _run_cli("quickstart", db_path=db)
        assert code == 0
        captured = capfd.readouterr()
        assert "First time?" not in captured.err


# ---------------------------------------------------------------------------
# Hint output goes to stderr (not stdout)
# ---------------------------------------------------------------------------


class TestQuickstartAuth:
    def test_quickstart_mentions_auth(self, tmp_path, capfd):
        """quickstart should mention PO token / YouTube authentication."""
        db = tmp_path / "test.db"
        code = _run_cli("quickstart", db_path=db)
        assert code == 0
        captured = capfd.readouterr()
        assert "YT_ARTIST_PO_TOKEN" in captured.out
        assert "PO-Token" in captured.out or "PO token" in captured.out

    def test_quickstart_mentions_doctor(self, tmp_path, capfd):
        """quickstart should mention yt-artist doctor."""
        db = tmp_path / "test.db"
        code = _run_cli("quickstart", db_path=db)
        assert code == 0
        captured = capfd.readouterr()
        assert "doctor" in captured.out


class TestFirstRunMentionsDoctor:
    def test_first_run_mentions_doctor(self, tmp_path, capfd):
        """On first use with empty DB, stderr should mention doctor."""
        db = tmp_path / "test.db"
        code = _run_cli("list-prompts", db_path=db)
        assert code == 0
        captured = capfd.readouterr()
        assert "doctor" in captured.err


class TestHintOutputChannel:
    def test_hints_go_to_stderr_not_stdout(self, tmp_path, capfd):
        """Hints should appear in stderr, not pollute stdout."""
        db = tmp_path / "test.db"
        with patch("yt_artist.cli.fetch_channel", return_value=("path/urllist.md", 5)):
            code = _run_cli("fetch-channel", "https://youtube.com/@Test", db_path=db)
        assert code == 0
        captured = capfd.readouterr()
        # stdout should have only the primary output (Urllist + Videos)
        assert "Next:" not in captured.out
        assert "\U0001f4a1" not in captured.out
        # stderr should have the hint
        assert "transcribe" in captured.err
