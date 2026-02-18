"""Tests for --json output mode across CLI commands."""

from __future__ import annotations

import json
import os
from pathlib import Path
from unittest.mock import MagicMock, patch

from yt_artist.cli import main
from yt_artist.storage import Storage

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_store(tmp_path: Path) -> Storage:
    db = tmp_path / "test.db"
    store = Storage(db)
    store.ensure_schema()
    return store


def _run_cli(*args: str, db_path: str | Path = "", json_output: bool = False) -> tuple[int, str, str]:
    """Run CLI and capture (exit_code, stdout, stderr)."""
    import io
    import logging as _logging
    import sys

    _logging.root.handlers.clear()

    argv = ["yt-artist"]
    if db_path:
        argv += ["--db", str(db_path)]
    if json_output:
        argv += ["--json"]
    argv += list(args)

    old_stdout = sys.stdout
    old_stderr = sys.stderr
    sys.stdout = io.StringIO()
    sys.stderr = io.StringIO()

    try:
        with patch("sys.argv", argv):
            try:
                main()
                code = 0
            except SystemExit as exc:
                code = exc.code if exc.code else 0
        return code, sys.stdout.getvalue(), sys.stderr.getvalue()
    finally:
        sys.stdout = old_stdout
        sys.stderr = old_stderr


# ---------------------------------------------------------------------------
# list-prompts --json
# ---------------------------------------------------------------------------


class TestListPromptsJson:
    def test_json_output_is_valid(self, tmp_path):
        db = tmp_path / "test.db"
        store = _make_store(tmp_path)
        store.upsert_prompt(prompt_id="p1", name="Prompt One", template="Summarize {video}")
        code, out, _ = _run_cli("list-prompts", db_path=db, json_output=True)
        assert code == 0
        data = json.loads(out)
        assert isinstance(data, list)
        assert len(data) >= 1  # at least "default" + "p1"
        assert any(r["id"] == "p1" for r in data)

    def test_json_has_expected_keys(self, tmp_path):
        db = tmp_path / "test.db"
        _make_store(tmp_path)
        code, out, _ = _run_cli("list-prompts", db_path=db, json_output=True)
        assert code == 0
        data = json.loads(out)
        for row in data:
            assert "id" in row
            assert "name" in row
            assert "template" in row

    def test_json_empty_db(self, tmp_path):
        """With only the built-in default prompt, returns valid JSON array with 1 entry."""
        db = tmp_path / "test.db"
        _make_store(tmp_path)
        code, out, _ = _run_cli("list-prompts", db_path=db, json_output=True)
        assert code == 0
        data = json.loads(out)
        assert isinstance(data, list)
        assert len(data) >= 1  # built-in default prompt


# ---------------------------------------------------------------------------
# search-transcripts --json
# ---------------------------------------------------------------------------


class TestSearchTranscriptsJson:
    def test_json_output_is_valid(self, tmp_path):
        db = tmp_path / "test.db"
        store = _make_store(tmp_path)
        store.upsert_artist(
            artist_id="@Test", name="Test", channel_url="https://www.youtube.com/@Test", urllist_path="x.md"
        )
        store.upsert_video(video_id="vid001", artist_id="@Test", url="https://youtube.com/watch?v=vid001", title="V1")
        store.save_transcript(video_id="vid001", raw_text="Hello world.", format="vtt")
        code, out, _ = _run_cli("search-transcripts", db_path=db, json_output=True)
        assert code == 0
        data = json.loads(out)
        assert isinstance(data, list)
        assert len(data) == 1
        assert data[0]["video_id"] == "vid001"

    def test_json_has_expected_keys(self, tmp_path):
        db = tmp_path / "test.db"
        store = _make_store(tmp_path)
        store.upsert_artist(
            artist_id="@Test", name="Test", channel_url="https://www.youtube.com/@Test", urllist_path="x.md"
        )
        store.upsert_video(video_id="vid001", artist_id="@Test", url="https://youtube.com/watch?v=vid001", title="V1")
        store.save_transcript(video_id="vid001", raw_text="Hello world.", format="vtt")
        code, out, _ = _run_cli("search-transcripts", db_path=db, json_output=True)
        data = json.loads(out)
        for row in data:
            assert "video_id" in row
            assert "artist_id" in row
            assert "transcript_len" in row
            assert "title" in row

    def test_json_empty(self, tmp_path):
        db = tmp_path / "test.db"
        _make_store(tmp_path)
        code, out, _ = _run_cli("search-transcripts", db_path=db, json_output=True)
        assert code == 0
        data = json.loads(out)
        assert data == []


# ---------------------------------------------------------------------------
# status --json
# ---------------------------------------------------------------------------


class TestStatusJson:
    def test_json_output_is_valid(self, tmp_path):
        db = tmp_path / "test.db"
        _make_store(tmp_path)
        code, out, _ = _run_cli("status", db_path=db, json_output=True)
        assert code == 0
        data = json.loads(out)
        assert isinstance(data, dict)

    def test_json_has_expected_keys(self, tmp_path):
        db = tmp_path / "test.db"
        _make_store(tmp_path)
        code, out, _ = _run_cli("status", db_path=db, json_output=True)
        data = json.loads(out)
        expected = {
            "artists",
            "videos",
            "transcribed",
            "summarized",
            "scored",
            "avg_quality",
            "prompts",
            "running_jobs",
            "youtube_reqs_1h",
            "youtube_reqs_24h",
            "db_size_bytes",
        }
        assert expected.issubset(set(data.keys()))


# ---------------------------------------------------------------------------
# jobs list --json
# ---------------------------------------------------------------------------


class TestJobsListJson:
    def test_json_output_is_valid(self, tmp_path):
        db = tmp_path / "test.db"
        _make_store(tmp_path)
        code, out, _ = _run_cli("jobs", db_path=db, json_output=True)
        assert code == 0
        data = json.loads(out)
        assert isinstance(data, list)
        assert data == []

    def test_json_has_expected_keys(self, tmp_path):
        db = tmp_path / "test.db"
        store = _make_store(tmp_path)
        # Create a job record directly via SQL
        import uuid

        jid = str(uuid.uuid4())
        conn = store._conn()
        try:
            conn.execute(
                "INSERT INTO jobs (id, status, command, total, done, started_at, pid, log_file)"
                " VALUES (?, ?, ?, ?, ?, datetime('now'), ?, ?)",
                (jid, "running", "test-cmd", 10, 3, 99999, "/tmp/test.log"),
            )
            conn.commit()
        finally:
            conn.close()
        code, out, _ = _run_cli("jobs", db_path=db, json_output=True)
        data = json.loads(out)
        assert len(data) >= 1
        for row in data:
            assert "id" in row
            assert "status" in row
            assert "done" in row
            assert "total" in row
            assert "started_at" in row
            assert "command" in row


# ---------------------------------------------------------------------------
# doctor --json
# ---------------------------------------------------------------------------


class TestDoctorJson:
    def test_json_output_is_valid(self, tmp_path):
        db = tmp_path / "test.db"
        with (
            patch("shutil.which", return_value="/usr/bin/yt-dlp"),
            patch("subprocess.run") as mock_run,
            patch("yt_artist.llm.check_connectivity"),
            patch.dict(os.environ, {}, clear=False),
        ):
            mock_run.return_value = MagicMock(returncode=0, stdout="2024.01.01", stderr="")
            code, out, _ = _run_cli("doctor", db_path=db, json_output=True)
        assert code == 0
        data = json.loads(out)
        assert isinstance(data, dict)
        assert "checks" in data
        assert "ok" in data
        assert "warn" in data
        assert "fail" in data
        assert isinstance(data["checks"], list)

    def test_json_check_has_name_status_message(self, tmp_path):
        db = tmp_path / "test.db"
        with (
            patch("shutil.which", return_value="/usr/bin/yt-dlp"),
            patch("subprocess.run") as mock_run,
            patch("yt_artist.llm.check_connectivity"),
            patch.dict(os.environ, {}, clear=False),
        ):
            mock_run.return_value = MagicMock(returncode=0, stdout="2024.01.01", stderr="")
            code, out, _ = _run_cli("doctor", db_path=db, json_output=True)
        data = json.loads(out)
        for check in data["checks"]:
            assert "name" in check
            assert "status" in check
            assert "message" in check
            assert check["status"] in ("ok", "warn", "fail")

    def test_json_suppresses_human_output(self, tmp_path):
        """--json doctor should not include [1/5] section headers."""
        db = tmp_path / "test.db"
        with (
            patch("shutil.which", return_value="/usr/bin/yt-dlp"),
            patch("subprocess.run") as mock_run,
            patch("yt_artist.llm.check_connectivity"),
            patch.dict(os.environ, {}, clear=False),
        ):
            mock_run.return_value = MagicMock(returncode=0, stdout="2024.01.01", stderr="")
            code, out, _ = _run_cli("doctor", db_path=db, json_output=True)
        assert "[1/5]" not in out
        assert "yt-artist doctor" not in out
