"""Tests for CLI dispatch, prompt resolution, and dependency auto-creation."""

from __future__ import annotations

import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from yt_artist.cli import _resolve_prompt_id, main
from yt_artist.storage import Storage

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_store(tmp_path: Path) -> Storage:
    db = tmp_path / "test.db"
    store = Storage(db)
    store.ensure_schema()
    return store


def _seed_artist_and_video(store: Storage) -> None:
    """Insert a minimal artist + video for use in tests."""
    store.upsert_artist(
        artist_id="@TestArtist",
        name="Test Artist",
        channel_url="https://www.youtube.com/@TestArtist",
        urllist_path="data/artists/@TestArtist/urllist.md",
    )
    store.upsert_video(
        video_id="testvid00001",
        artist_id="@TestArtist",
        url="https://www.youtube.com/watch?v=testvid00001",
        title="Test Video",
    )


def _seed_prompt(store: Storage, prompt_id: str = "p1") -> None:
    store.upsert_prompt(
        prompt_id=prompt_id,
        name="Test Prompt",
        template="Summarize: {artist} - {video}",
    )


def _run_cli(*args: str, db_path: str | Path = "") -> int:
    """Call main() with patched sys.argv; return exit code (0 on success)."""
    # Clear root logger handlers so basicConfig() in main() reconfigures
    # with the current sys.stderr (which capfd may have redirected).
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
# _resolve_prompt_id
# ---------------------------------------------------------------------------


class TestResolvePromptId:
    """Tests for the prompt-resolution fallback chain."""

    def test_explicit_flag_found(self, tmp_path):
        store = _make_store(tmp_path)
        _seed_prompt(store, "p1")
        assert _resolve_prompt_id(store, "@TestArtist", "p1") == "p1"

    def test_explicit_flag_missing_raises(self, tmp_path):
        store = _make_store(tmp_path)
        with pytest.raises(SystemExit, match="not found"):
            _resolve_prompt_id(store, "@TestArtist", "nonexistent")

    def test_uses_artist_default(self, tmp_path):
        store = _make_store(tmp_path)
        _seed_artist_and_video(store)
        _seed_prompt(store, "p2")
        store.set_artist_default_prompt("@TestArtist", "p2")
        assert _resolve_prompt_id(store, "@TestArtist", None) == "p2"

    def test_uses_env_fallback(self, tmp_path):
        store = _make_store(tmp_path)
        _seed_prompt(store, "env-prompt")
        with patch.dict(os.environ, {"YT_ARTIST_DEFAULT_PROMPT": "env-prompt"}):
            assert _resolve_prompt_id(store, "@TestArtist", None) == "env-prompt"

    def test_uses_first_in_db(self, tmp_path):
        """When config default prompt doesn't exist in DB, falls back to first prompt."""
        store = _make_store(tmp_path)
        # Remove the built-in "default" prompt so config default ("default") doesn't match
        conn = store._conn()
        try:
            conn.execute("DELETE FROM prompts WHERE id = 'default'")
            conn.commit()
        finally:
            conn.close()
        _seed_prompt(store, "alpha")
        _seed_prompt(store, "beta")
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("YT_ARTIST_DEFAULT_PROMPT", None)
            result = _resolve_prompt_id(store, None, None)
        assert result == "alpha"

    def test_no_custom_prompts_uses_builtin_default(self, tmp_path):
        """When no custom prompts exist, the built-in 'default' prompt is returned."""
        store = _make_store(tmp_path)
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("YT_ARTIST_DEFAULT_PROMPT", None)
            # ensure_schema auto-creates 'default' prompt → should not raise
            result = _resolve_prompt_id(store, None, None)
        assert result == "default"


# ---------------------------------------------------------------------------
# CLI: add-prompt / list-prompts
# ---------------------------------------------------------------------------


class TestPromptCommands:
    def test_add_and_list_prompts(self, tmp_path, capfd):
        db = tmp_path / "test.db"
        code = _run_cli(
            "add-prompt",
            "--id",
            "my-prompt",
            "--name",
            "My Prompt",
            "--template",
            "Summarize {video} by {artist}",
            db_path=db,
        )
        assert code == 0
        captured = capfd.readouterr()
        assert "Prompt saved" in captured.out

        code = _run_cli("list-prompts", db_path=db)
        assert code == 0
        captured = capfd.readouterr()
        assert "my-prompt" in captured.out
        assert "My Prompt" in captured.out


# ---------------------------------------------------------------------------
# CLI: fetch-channel (mocked)
# ---------------------------------------------------------------------------


class TestFetchChannel:
    def test_fetch_channel_happy_path(self, tmp_path, capfd):
        db = tmp_path / "test.db"
        fake_return = ("data/artists/@Test/urllist.md", 5)
        with patch("yt_artist.cli.fetch_channel", return_value=fake_return):
            code = _run_cli(
                "fetch-channel",
                "https://www.youtube.com/@Test",
                db_path=db,
            )
        assert code == 0
        captured = capfd.readouterr()
        assert "Videos:  5" in captured.out


# ---------------------------------------------------------------------------
# CLI: transcribe (mocked)
# ---------------------------------------------------------------------------


class TestTranscribe:
    def test_single_video_transcribe(self, tmp_path, capfd):
        db = tmp_path / "test.db"
        store = _make_store(tmp_path / "sub" / "test.db")  # separate to avoid conflict
        store = Storage(db)
        store.ensure_schema()
        _seed_artist_and_video(store)

        with patch("yt_artist.cli.transcribe", return_value="testvid00001"):
            code = _run_cli(
                "transcribe",
                "https://www.youtube.com/watch?v=testvid00001",
                db_path=db,
            )
        assert code == 0
        captured = capfd.readouterr()
        assert "Transcribed: testvid00001" in captured.out

    def test_bulk_transcribe_auto_fetches(self, tmp_path, capfd):
        db = tmp_path / "test.db"
        fake_fetch = ("urllist.md", 1)

        with (
            patch("yt_artist.cli.fetch_channel", return_value=fake_fetch) as mock_fetch,
            patch("yt_artist.cli.transcribe", return_value="testvid00001"),
        ):
            # Storage is empty so artist is missing → should trigger auto-fetch
            code = _run_cli(
                "transcribe",
                "--artist-id",
                "@Unknown",
                db_path=db,
            )
        # fetch_channel should have been called
        assert mock_fetch.called

    def test_transcribe_requires_video_or_artist(self, tmp_path):
        db = tmp_path / "test.db"
        code = _run_cli("transcribe", db_path=db)
        assert code != 0


# ---------------------------------------------------------------------------
# CLI: summarize (mocked)
# ---------------------------------------------------------------------------


class TestSummarize:
    def test_single_video_summarize(self, tmp_path, capfd):
        db = tmp_path / "test.db"
        store = Storage(db)
        store.ensure_schema()
        _seed_artist_and_video(store)
        _seed_prompt(store, "p1")
        store.save_transcript(video_id="testvid00001", raw_text="Hello world transcript.", format="vtt")

        with (
            patch("yt_artist.cli.summarize", return_value="testvid00001:p1") as mock_sum,
            patch("yt_artist.cli.ensure_artist_and_video_for_video_url", return_value=("@TestArtist", "testvid00001")),
            patch("yt_artist.cli._check_llm"),
        ):
            # Mock get_summaries_for_video to return the summary content
            original_get = store.get_summaries_for_video

            def fake_get(vid):
                return [{"prompt_id": "p1", "content": "Great summary.", "video_id": vid, "created_at": "now"}]

            with patch.object(store, "get_summaries_for_video", side_effect=fake_get):
                code = _run_cli(
                    "summarize",
                    "testvid00001",
                    "--prompt",
                    "p1",
                    db_path=db,
                )
        assert code == 0

    def test_summarize_requires_video_or_artist(self, tmp_path):
        db = tmp_path / "test.db"
        code = _run_cli("summarize", db_path=db)
        assert code != 0


# ---------------------------------------------------------------------------
# CLI: set-default-prompt
# ---------------------------------------------------------------------------


class TestSetDefaultPrompt:
    def test_set_default_prompt_happy_path(self, tmp_path, capfd):
        db = tmp_path / "test.db"
        store = Storage(db)
        store.ensure_schema()
        _seed_artist_and_video(store)
        _seed_prompt(store, "p1")

        code = _run_cli(
            "set-default-prompt",
            "--artist-id",
            "@TestArtist",
            "--prompt",
            "p1",
            db_path=db,
        )
        assert code == 0
        captured = capfd.readouterr()
        assert "Default prompt" in captured.out

    def test_set_default_prompt_missing_artist(self, tmp_path):
        db = tmp_path / "test.db"
        store = Storage(db)
        store.ensure_schema()
        _seed_prompt(store, "p1")

        code = _run_cli(
            "set-default-prompt",
            "--artist-id",
            "@Nonexistent",
            "--prompt",
            "p1",
            db_path=db,
        )
        assert code != 0

    def test_set_default_prompt_missing_prompt(self, tmp_path):
        db = tmp_path / "test.db"
        store = Storage(db)
        store.ensure_schema()
        _seed_artist_and_video(store)

        code = _run_cli(
            "set-default-prompt",
            "--artist-id",
            "@TestArtist",
            "--prompt",
            "nonexistent",
            db_path=db,
        )
        assert code != 0


# ---------------------------------------------------------------------------
# CLI: search-transcripts
# ---------------------------------------------------------------------------


class TestSearchTranscripts:
    def test_search_transcripts_empty(self, tmp_path, capfd):
        db = tmp_path / "test.db"
        store = Storage(db)
        store.ensure_schema()

        code = _run_cli("search-transcripts", db_path=db)
        assert code == 0
        captured = capfd.readouterr()
        assert "No transcripts found" in captured.out

    def test_search_transcripts_with_data(self, tmp_path, capfd):
        db = tmp_path / "test.db"
        store = Storage(db)
        store.ensure_schema()
        _seed_artist_and_video(store)
        store.save_transcript(video_id="testvid00001", raw_text="Hello world.", format="vtt")

        code = _run_cli(
            "search-transcripts",
            "--artist-id",
            "@TestArtist",
            db_path=db,
        )
        assert code == 0
        captured = capfd.readouterr()
        assert "testvid00001" in captured.out


# ---------------------------------------------------------------------------
# CLI: doctor
# ---------------------------------------------------------------------------


class TestDoctor:
    def test_doctor_runs_successfully(self, tmp_path, capfd):
        """doctor command should complete without error."""
        db = tmp_path / "test.db"
        with (
            patch("shutil.which", return_value="/usr/bin/yt-dlp"),
            patch("subprocess.run") as mock_run,
            patch("yt_artist.llm.check_connectivity"),
            patch.dict(os.environ, {}, clear=False),
        ):
            mock_run.return_value = MagicMock(returncode=0, stdout="2024.01.01", stderr="")
            code = _run_cli("doctor", db_path=db)
        assert code == 0

    def test_doctor_shows_five_sections(self, tmp_path, capfd):
        """doctor should show all 5 check sections."""
        db = tmp_path / "test.db"
        with (
            patch("shutil.which", return_value="/usr/bin/yt-dlp"),
            patch("subprocess.run") as mock_run,
            patch("yt_artist.llm.check_connectivity"),
            patch.dict(os.environ, {}, clear=False),
        ):
            mock_run.return_value = MagicMock(returncode=0, stdout="2024.01.01", stderr="")
            code = _run_cli("doctor", db_path=db)
        assert code == 0
        captured = capfd.readouterr()
        assert "[1/5]" in captured.out
        assert "[2/5]" in captured.out
        assert "[3/5]" in captured.out
        assert "[4/5]" in captured.out
        assert "[5/5]" in captured.out

    def test_doctor_detects_missing_yt_dlp(self, tmp_path, capfd):
        """doctor should FAIL when yt-dlp is not installed."""
        db = tmp_path / "test.db"
        with (
            patch("shutil.which", return_value=None),
            patch("yt_artist.llm.check_connectivity"),
            patch.dict(os.environ, {}, clear=False),
        ):
            code = _run_cli("doctor", db_path=db)
        assert code == 0
        captured = capfd.readouterr()
        assert "FAIL" in captured.out
        assert "yt-dlp not found" in captured.out

    def test_doctor_shows_auth_config(self, tmp_path, capfd):
        """doctor should show cookies and PO token status."""
        db = tmp_path / "test.db"
        with (
            patch("shutil.which", return_value="/usr/bin/yt-dlp"),
            patch("subprocess.run") as mock_run,
            patch("yt_artist.llm.check_connectivity"),
            patch.dict(
                os.environ,
                {
                    "YT_ARTIST_COOKIES_BROWSER": "chrome",
                    "YT_ARTIST_PO_TOKEN": "mytoken",
                },
                clear=False,
            ),
        ):
            mock_run.return_value = MagicMock(returncode=0, stdout="2024.01.01", stderr="")
            code = _run_cli("doctor", db_path=db)
        assert code == 0
        captured = capfd.readouterr()
        assert "chrome" in captured.out
        assert "PO token is set" in captured.out

    def test_doctor_warns_no_po_token_no_provider(self, tmp_path, capfd):
        """doctor should WARN when PO token is not set and no provider is installed."""
        db = tmp_path / "test.db"
        from importlib.metadata import PackageNotFoundError

        original_distribution = __import__("importlib.metadata", fromlist=["distribution"]).distribution

        def _mock_distribution(name):
            if name == "yt-dlp-get-pot-rustypipe":
                raise PackageNotFoundError(name)
            return original_distribution(name)

        with (
            patch("shutil.which", return_value="/usr/bin/yt-dlp"),
            patch("subprocess.run") as mock_run,
            patch("yt_artist.llm.check_connectivity"),
            patch("importlib.metadata.distribution", side_effect=_mock_distribution),
            patch.dict(os.environ, {}, clear=True),
        ):
            mock_run.return_value = MagicMock(returncode=0, stdout="2024.01.01", stderr="")
            code = _run_cli("doctor", db_path=db)
        assert code == 0
        captured = capfd.readouterr()
        assert "WARN" in captured.out
        assert "No PO token and no auto-provider" in captured.out

    def test_doctor_ok_with_provider_installed(self, tmp_path, capfd):
        """doctor should show OK when PO token provider is installed (even without manual token)."""
        db = tmp_path / "test.db"
        with (
            patch("shutil.which", return_value="/usr/bin/yt-dlp"),
            patch("subprocess.run") as mock_run,
            patch("yt_artist.llm.check_connectivity"),
            patch.dict(os.environ, {}, clear=True),
        ):
            mock_run.return_value = MagicMock(returncode=0, stdout="2024.01.01", stderr="")
            code = _run_cli("doctor", db_path=db)
        assert code == 0
        captured = capfd.readouterr()
        # With yt-dlp-get-pot-rustypipe installed (it's a dependency), doctor should see the provider
        assert "PO token provider installed" in captured.out or "PO token is set" in captured.out

    def test_doctor_shows_summary(self, tmp_path, capfd):
        """doctor should show a summary line at the end."""
        db = tmp_path / "test.db"
        with (
            patch("shutil.which", return_value="/usr/bin/yt-dlp"),
            patch("subprocess.run") as mock_run,
            patch("yt_artist.llm.check_connectivity"),
            patch.dict(os.environ, {}, clear=False),
        ):
            mock_run.return_value = MagicMock(returncode=0, stdout="2024.01.01", stderr="")
            code = _run_cli("doctor", db_path=db)
        assert code == 0
        captured = capfd.readouterr()
        assert "checks passed" in captured.out


# ---------------------------------------------------------------------------
# CLI: URL validation integration (Phase 1)
# ---------------------------------------------------------------------------


class TestFetchChannelValidation:
    """fetch-channel rejects bad URLs before hitting yt-dlp."""

    def test_video_url_rejected(self, tmp_path):
        """Video URL passed to fetch-channel exits with helpful message."""
        db = tmp_path / "test.db"
        code = _run_cli(
            "fetch-channel",
            "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
            db_path=db,
        )
        # SystemExit with string message → code is the message string
        assert isinstance(code, str) and "looks like a video URL" in code

    def test_non_youtube_url_rejected(self, tmp_path):
        """Non-YouTube URL is rejected."""
        db = tmp_path / "test.db"
        code = _run_cli(
            "fetch-channel",
            "https://www.example.com/@channel",
            db_path=db,
        )
        assert isinstance(code, str) and "Not a YouTube URL" in code

    def test_bare_handle_accepted(self, tmp_path):
        """Bare @handle is expanded and accepted (mocking fetch_channel)."""
        db = tmp_path / "test.db"
        fake_return = ("data/artists/@test/urllist.md", 3)
        with patch("yt_artist.cli.fetch_channel", return_value=fake_return):
            code = _run_cli("fetch-channel", "@testchannel", db_path=db)
        assert code == 0

    def test_empty_url_rejected(self, tmp_path):
        """Empty string is rejected."""
        db = tmp_path / "test.db"
        code = _run_cli("fetch-channel", "", db_path=db)
        assert isinstance(code, str) and "empty" in code.lower()

    def test_random_string_rejected(self, tmp_path):
        """Random non-URL, non-handle string is rejected."""
        db = tmp_path / "test.db"
        code = _run_cli("fetch-channel", "not-a-url", db_path=db)
        assert isinstance(code, str) and "Not a valid URL" in code

    def test_shorts_url_rejected(self, tmp_path):
        """YouTube Shorts URL rejected from fetch-channel."""
        db = tmp_path / "test.db"
        code = _run_cli(
            "fetch-channel",
            "https://www.youtube.com/shorts/abc123xyz",
            db_path=db,
        )
        assert isinstance(code, str) and "looks like a video URL" in code


class TestTranscribeValidation:
    """transcribe rejects bad URLs before hitting yt-dlp."""

    def test_channel_url_rejected(self, tmp_path):
        """Channel URL passed to transcribe exits with helpful message."""
        db = tmp_path / "test.db"
        code = _run_cli(
            "transcribe",
            "https://www.youtube.com/@hubermanlab",
            db_path=db,
        )
        assert isinstance(code, str) and "looks like a channel URL" in code

    def test_non_youtube_url_rejected(self, tmp_path):
        """Non-YouTube URL is rejected."""
        db = tmp_path / "test.db"
        code = _run_cli(
            "transcribe",
            "https://www.example.com/watch?v=abc123xyz01",
            db_path=db,
        )
        assert isinstance(code, str) and "Not a YouTube URL" in code

    def test_empty_string_rejected(self, tmp_path):
        """Empty video URL/ID is rejected (caught by arg check before validation)."""
        db = tmp_path / "test.db"
        code = _run_cli("transcribe", "", db_path=db)
        # Empty string → "Provide video_url" check fires before validation
        assert isinstance(code, str) and "Provide" in code

    def test_short_bare_string_rejected(self, tmp_path):
        """Short garbage string is rejected as invalid video URL/ID."""
        db = tmp_path / "test.db"
        code = _run_cli("transcribe", "foo", db_path=db)
        assert isinstance(code, str) and "Not a valid video URL or ID" in code

    def test_valid_video_url_passes_validation(self, tmp_path):
        """Valid video URL passes validation (mocking transcribe)."""
        db = tmp_path / "test.db"
        store = Storage(db)
        store.ensure_schema()
        _seed_artist_and_video(store)

        with patch("yt_artist.cli.transcribe", return_value="testvid00001"):
            code = _run_cli(
                "transcribe",
                "https://www.youtube.com/watch?v=testvid00001",
                db_path=db,
            )
        assert code == 0

    def test_bare_video_id_passes_validation(self, tmp_path):
        """Bare 11-char video ID passes validation (mocking transcribe)."""
        db = tmp_path / "test.db"
        store = Storage(db)
        store.ensure_schema()
        _seed_artist_and_video(store)

        with patch("yt_artist.cli.transcribe", return_value="testvid00001"):
            code = _run_cli("transcribe", "testvid00001", db_path=db)
        assert code == 0


class TestSummarizeValidation:
    """summarize rejects bad URLs before hitting LLM."""

    def test_channel_url_rejected(self, tmp_path):
        """Channel URL passed to summarize exits with helpful message."""
        db = tmp_path / "test.db"
        with patch("yt_artist.cli._check_llm"):
            code = _run_cli(
                "summarize",
                "https://www.youtube.com/@hubermanlab",
                db_path=db,
            )
        assert isinstance(code, str) and "looks like a channel URL" in code

    def test_non_youtube_url_rejected(self, tmp_path):
        """Non-YouTube URL is rejected."""
        db = tmp_path / "test.db"
        with patch("yt_artist.cli._check_llm"):
            code = _run_cli(
                "summarize",
                "https://www.example.com/watch?v=abc123xyz01",
                db_path=db,
            )
        assert isinstance(code, str) and "Not a YouTube URL" in code

    def test_short_garbage_rejected(self, tmp_path):
        """Short garbage string is rejected."""
        db = tmp_path / "test.db"
        with patch("yt_artist.cli._check_llm"):
            code = _run_cli("summarize", "foo", db_path=db)
        assert isinstance(code, str) and "Not a valid video URL or ID" in code

    def test_empty_string_rejected(self, tmp_path):
        """Empty video spec is rejected (caught by arg check before validation)."""
        db = tmp_path / "test.db"
        with patch("yt_artist.cli._check_llm"):
            code = _run_cli("summarize", "", db_path=db)
        # Empty string → "Provide video (URL or id) or --artist-id" fires first
        assert isinstance(code, str) and "Provide" in code


# ---------------------------------------------------------------------------
# set-about
# ---------------------------------------------------------------------------


class TestSetAbout:
    def test_set_about_saves_text(self, tmp_path, capfd):
        """Round-trip: set-about stores text, get_artist returns it."""
        db = tmp_path / "test.db"
        store = _make_store(tmp_path)
        _seed_artist_and_video(store)

        about = "A neuroscientist discussing science and wellness."
        code = _run_cli("set-about", "--artist-id", "@TestArtist", about, db_path=db)
        assert code == 0
        captured = capfd.readouterr()
        assert "About saved" in captured.out
        assert str(len(about)) in captured.out

        # Verify the about text was persisted
        artist = store.get_artist("@TestArtist")
        assert artist is not None
        assert artist["about"] == about

    def test_set_about_unknown_artist(self, tmp_path):
        """set-about with nonexistent artist exits with error."""
        db = tmp_path / "test.db"
        _make_store(tmp_path)

        code = _run_cli("set-about", "--artist-id", "@Ghost", "Some text", db_path=db)
        assert isinstance(code, str) and "not in DB" in code

    def test_set_about_json_output(self, tmp_path):
        """set-about --json returns valid JSON."""
        import io
        import json
        import logging as _logging

        db = tmp_path / "test.db"
        store = _make_store(tmp_path)
        _seed_artist_and_video(store)

        _logging.root.handlers.clear()
        argv = ["yt-artist", "--db", str(db), "--json", "set-about", "--artist-id", "@TestArtist", "Test about"]
        old_stdout = sys.stdout
        sys.stdout = io.StringIO()
        try:
            with patch.object(sys, "argv", argv):
                try:
                    main()
                    code = 0
                except SystemExit as exc:
                    code = exc.code if exc.code else 0
            out = sys.stdout.getvalue()
        finally:
            sys.stdout = old_stdout

        assert code == 0
        data = json.loads(out)
        assert data["artist_id"] == "@TestArtist"
        assert data["about_len"] == len("Test about")
