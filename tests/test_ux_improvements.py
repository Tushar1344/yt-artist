"""Tests for UX / performance improvements batch."""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import patch

import pytest

from yt_artist.storage import Storage


def _make_store(tmp_path: Path) -> Storage:
    db = tmp_path / "test.db"
    store = Storage(db)
    store.ensure_schema()
    return store


# ---------------------------------------------------------------------------
# UX-3: DB-first fast path in ensure_artist_and_video — skip yt-dlp
# ---------------------------------------------------------------------------


class TestEnsureArtistVideoFastPath:
    def test_db_hit_skips_yt_dlp(self, tmp_path):
        """When video + artist are already in DB, no yt-dlp subprocess should run."""
        store = _make_store(tmp_path)
        store.upsert_artist(
            artist_id="@TestCh",
            name="Test Channel",
            channel_url="https://www.youtube.com/@TestCh",
            urllist_path="data/artists/@TestCh/urllist.md",
        )
        store.upsert_video(
            video_id="abc12345678",
            artist_id="@TestCh",
            url="https://www.youtube.com/watch?v=abc12345678",
            title="Existing",
        )
        from yt_artist.fetcher import ensure_artist_and_video_for_video_url

        with patch("yt_artist.fetcher._video_metadata") as mock_meta:
            aid, vid = ensure_artist_and_video_for_video_url(
                "https://www.youtube.com/watch?v=abc12345678",
                store,
                tmp_path,
            )
            # _video_metadata (yt-dlp) should NOT be called
            mock_meta.assert_not_called()
        assert aid == "@TestCh"
        assert vid == "abc12345678"

    def test_db_miss_falls_through_to_yt_dlp(self, tmp_path):
        """When video is NOT in DB, yt-dlp metadata fetch should run."""
        store = _make_store(tmp_path)
        from yt_artist.fetcher import ensure_artist_and_video_for_video_url

        with patch("yt_artist.fetcher._video_metadata") as mock_meta:
            mock_meta.return_value = {
                "id": "newvid456789",
                "title": "New",
                "channel_id": "UCnew",
                "uploader_id": "@NewCh",
                "channel": "New Channel",
            }
            with patch("yt_artist.fetcher.fetch_channel") as mock_fetch:
                aid, vid = ensure_artist_and_video_for_video_url(
                    "https://www.youtube.com/watch?v=newvid456789",
                    store,
                    tmp_path,
                )
            mock_meta.assert_called_once()
            mock_fetch.assert_not_called()
        assert aid == "@NewCh"
        assert vid == "newvid456789"


# ---------------------------------------------------------------------------
# UX-4: Cached OpenAI client
# ---------------------------------------------------------------------------


class TestLLMClientCaching:
    def test_client_is_reused(self):
        """get_client() should return the same object on consecutive calls with same config."""
        import yt_artist.llm as llm_mod
        from yt_artist.llm import get_client

        # Reset cache
        llm_mod._cached_client = None
        llm_mod._cached_client_key = None

        with (
            patch("yt_artist.llm._resolve_config", return_value=("http://localhost:11434/v1", "ollama", "mistral")),
            patch("yt_artist.llm.OpenAI") as MockOpenAI,
        ):
            MockOpenAI.return_value = "mock_client"
            c1 = get_client()
            c2 = get_client()
        assert c1 is c2
        # OpenAI constructor should be called only once
        assert MockOpenAI.call_count == 1

    def test_client_refreshes_on_config_change(self):
        """get_client() should create a new client when config changes."""
        import yt_artist.llm as llm_mod
        from yt_artist.llm import get_client

        llm_mod._cached_client = None
        llm_mod._cached_client_key = None

        configs = iter(
            [
                ("http://localhost:11434/v1", "ollama", "mistral"),
                ("https://api.openai.com/v1", "sk-xxx", "gpt-4o-mini"),
            ]
        )
        with (
            patch("yt_artist.llm._resolve_config", side_effect=lambda: next(configs)),
            patch("yt_artist.llm.OpenAI") as MockOpenAI,
        ):
            MockOpenAI.side_effect = lambda **kw: f"client_{kw['base_url']}"
            c1 = get_client()
            c2 = get_client()
        assert c1 != c2
        assert MockOpenAI.call_count == 2


# ---------------------------------------------------------------------------
# UX-5: Transcript truncation
# ---------------------------------------------------------------------------


class TestTranscriptTruncation:
    def test_long_transcript_is_truncated(self, tmp_path):
        """Transcript longer than MAX_TRANSCRIPT_CHARS is truncated before LLM call."""
        store = _make_store(tmp_path)
        store.upsert_artist(
            artist_id="@A",
            name="A",
            channel_url="https://youtube.com/@A",
            urllist_path="data/artists/@A/urllist.md",
        )
        store.upsert_video(
            video_id="vid001234567", artist_id="@A", url="https://youtube.com/watch?v=vid001234567", title="Vid"
        )
        store.save_transcript(video_id="vid001234567", raw_text="x" * 50_000, format="vtt")
        store.upsert_prompt(prompt_id="p1", name="P", template="Summarize: {video}")

        from yt_artist.summarizer import summarize

        with (
            patch("yt_artist.summarizer.complete", return_value="Summary text") as mock_complete,
            patch.dict(os.environ, {"YT_ARTIST_MAX_TRANSCRIPT_CHARS": "1000"}),
        ):
            summarize("vid001234567", "p1", store, strategy="truncate")
        # The user_content passed to complete should be truncated
        call_args = mock_complete.call_args
        user_content = (
            call_args.kwargs.get("user_content") or call_args[1]
            if len(call_args[0]) > 1
            else call_args.kwargs.get("user_content")
        )
        # Positional or keyword — get user_content either way
        if user_content is None:
            user_content = call_args[0][1]
        # "Transcript:\n\n" prefix = 13 chars, then 1000 chars of transcript
        prefix_len = len("Transcript:\n\n")  # 13
        assert len(user_content) == prefix_len + 1000

    def test_short_transcript_not_truncated(self, tmp_path):
        """Transcript shorter than limit is passed verbatim."""
        store = _make_store(tmp_path)
        store.upsert_artist(
            artist_id="@A",
            name="A",
            channel_url="https://youtube.com/@A",
            urllist_path="data/artists/@A/urllist.md",
        )
        store.upsert_video(
            video_id="vid001234567", artist_id="@A", url="https://youtube.com/watch?v=vid001234567", title="Vid"
        )
        store.save_transcript(video_id="vid001234567", raw_text="Short text", format="vtt")
        store.upsert_prompt(prompt_id="p1", name="P", template="Summarize: {video}")

        from yt_artist.summarizer import summarize

        with patch("yt_artist.summarizer.complete", return_value="Summary text") as mock_complete:
            summarize("vid001234567", "p1", store)
        call_args = mock_complete.call_args
        user_content = call_args[0][1] if len(call_args[0]) > 1 else call_args.kwargs.get("user_content")
        assert user_content == "Transcript:\n\nShort text"


# ---------------------------------------------------------------------------
# UX-7: Built-in default prompt
# ---------------------------------------------------------------------------


class TestDefaultPrompt:
    def test_default_prompt_created_on_fresh_db(self, tmp_path):
        """ensure_schema on a new DB should auto-create 'default' prompt."""
        store = _make_store(tmp_path)
        prompt = store.get_prompt("default")
        assert prompt is not None
        assert prompt["name"] == "Default Summary"
        assert "summarize" in prompt["template"].lower()

    def test_default_prompt_not_overwritten_when_prompts_exist(self, tmp_path):
        """If user already has prompts, ensure_schema should NOT add 'default'."""
        store = _make_store(tmp_path)
        # Remove default and add a custom prompt
        conn = store._conn()
        try:
            conn.execute("DELETE FROM prompts")
            conn.execute(
                "INSERT INTO prompts (id, name, template, artist_component, video_component, intent_component, audience_component) "
                "VALUES ('custom', 'Custom', 'My template', '', '', '', '')"
            )
            conn.commit()
        finally:
            conn.close()
        # Re-run ensure_schema (simulates app restart)
        store.ensure_schema()
        # 'default' should NOT exist since user already has prompts
        assert store.get_prompt("default") is None
        assert store.get_prompt("custom") is not None


# ---------------------------------------------------------------------------
# UX-12: Header row in search-transcripts
# ---------------------------------------------------------------------------


class TestSearchTranscriptsHeader:
    def test_output_includes_header(self, tmp_path, capfd):
        """search-transcripts should print a header row before data."""
        import logging as _logging
        import sys

        from yt_artist.cli import main

        db = tmp_path / "test.db"
        store = Storage(db)
        store.ensure_schema()
        store.upsert_artist(
            artist_id="@A",
            name="A",
            channel_url="https://youtube.com/@A",
            urllist_path="data/artists/@A/urllist.md",
        )
        store.upsert_video(
            video_id="vid001234567", artist_id="@A", url="https://youtube.com/watch?v=vid001234567", title="Test"
        )
        store.save_transcript(video_id="vid001234567", raw_text="Hello", format="vtt")

        _logging.root.handlers.clear()
        argv = ["yt-artist", "--db", str(db), "search-transcripts"]
        with patch.object(sys, "argv", argv):
            main()
        captured = capfd.readouterr()
        assert "VIDEO_ID" in captured.out
        assert "ARTIST" in captured.out
        assert "CHARS" in captured.out


# ---------------------------------------------------------------------------
# UX-16: --version flag
# ---------------------------------------------------------------------------


class TestVersionFlag:
    def test_version_prints_and_exits(self, capfd):
        """--version should print version and exit."""
        import logging as _logging
        import sys

        from yt_artist import __version__
        from yt_artist.cli import main

        _logging.root.handlers.clear()
        with patch.object(sys, "argv", ["yt-artist", "--version"]), pytest.raises(SystemExit) as exc_info:
            main()
        assert exc_info.value.code == 0
        captured = capfd.readouterr()
        assert __version__ in captured.out
