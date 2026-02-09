"""Tests for background job management: launch, list, attach, stop, cleanup, time estimates."""
from __future__ import annotations

import logging as _logging
import os
import sys
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from yt_artist.storage import Storage


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_store(tmp_path: Path) -> Storage:
    db = tmp_path / "test.db"
    store = Storage(db)
    store.ensure_schema()
    return store


def _run_cli(*args: str, db_path: str | Path = "") -> int:
    """Call main() with patched sys.argv; return exit code (0 on success)."""
    from yt_artist.cli import main
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


def _seed_job(store: Storage, job_id: str = "abc123def456",
              command: str = "transcribe --artist-id @Test",
              status: str = "running", pid: int = 99999,
              log_file: str = "/tmp/test.log",
              total: int = 10, done: int = 3, errors: int = 0) -> None:
    """Insert a job row directly for testing."""
    conn = store._conn()
    try:
        conn.execute(
            "INSERT INTO jobs (id, command, status, pid, log_file, total, done, errors) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (job_id, command, status, pid, log_file, total, done, errors),
        )
        conn.commit()
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Time estimation
# ---------------------------------------------------------------------------

class TestTimeEstimation:

    def test_estimate_time_transcribe(self):
        from yt_artist.jobs import estimate_time
        est = estimate_time(10, "transcribe", concurrency=1)
        assert est > 0
        # 10 videos * (8s + 2s delay) / 1 worker = 100s
        assert est == pytest.approx(100.0)

    def test_estimate_time_summarize(self):
        from yt_artist.jobs import estimate_time
        est = estimate_time(10, "summarize", concurrency=1)
        # 10 * (15s + 2s) / 1 = 170s
        assert est == pytest.approx(170.0)

    def test_estimate_time_with_concurrency(self):
        from yt_artist.jobs import estimate_time
        est1 = estimate_time(10, "transcribe", concurrency=1)
        est2 = estimate_time(10, "transcribe", concurrency=2)
        assert est2 < est1
        assert est2 == pytest.approx(est1 / 2)

    def test_format_estimate_seconds(self):
        from yt_artist.jobs import format_estimate
        assert format_estimate(45) == "45s"

    def test_format_estimate_minutes(self):
        from yt_artist.jobs import format_estimate
        assert format_estimate(180) == "3m"

    def test_format_estimate_hours(self):
        from yt_artist.jobs import format_estimate
        assert format_estimate(5400) == "1.5h"


# ---------------------------------------------------------------------------
# Job ID generation
# ---------------------------------------------------------------------------

class TestJobId:

    def test_unique_ids(self):
        from yt_artist.jobs import _generate_job_id
        ids = {_generate_job_id() for _ in range(100)}
        assert len(ids) == 100  # All unique

    def test_id_length(self):
        from yt_artist.jobs import _generate_job_id
        jid = _generate_job_id()
        assert len(jid) == 12
        assert all(c in "0123456789abcdef" for c in jid)


# ---------------------------------------------------------------------------
# PID alive check
# ---------------------------------------------------------------------------

class TestPidAlive:

    def test_current_process_is_alive(self):
        from yt_artist.jobs import _is_pid_alive
        assert _is_pid_alive(os.getpid()) is True

    def test_dead_pid_not_alive(self):
        from yt_artist.jobs import _is_pid_alive
        # Use a very high PID unlikely to exist
        assert _is_pid_alive(4000000) is False

    def test_invalid_pid_not_alive(self):
        from yt_artist.jobs import _is_pid_alive
        assert _is_pid_alive(0) is False
        assert _is_pid_alive(-1) is False


# ---------------------------------------------------------------------------
# Background suggestion hint
# ---------------------------------------------------------------------------

class TestBackgroundSuggestion:

    def test_below_threshold_no_output(self, capfd):
        from yt_artist.jobs import maybe_suggest_background
        maybe_suggest_background(3, "transcribe", 1, ["yt-artist", "transcribe", "--artist-id", "@X"])
        captured = capfd.readouterr()
        assert captured.err == ""

    def test_above_threshold_shows_hint(self, capfd):
        from yt_artist.jobs import maybe_suggest_background
        maybe_suggest_background(10, "transcribe", 1, ["yt-artist", "transcribe", "--artist-id", "@X"])
        captured = capfd.readouterr()
        assert "--bg" in captured.err
        assert "10 videos" in captured.err

    def test_quiet_suppresses_hint(self, capfd):
        from yt_artist.jobs import maybe_suggest_background
        maybe_suggest_background(10, "transcribe", 1, ["yt-artist", "transcribe", "--artist-id", "@X"], quiet=True)
        captured = capfd.readouterr()
        assert captured.err == ""


# ---------------------------------------------------------------------------
# DB: job CRUD
# ---------------------------------------------------------------------------

class TestJobDB:

    def test_create_and_list_job(self, tmp_path):
        from yt_artist.jobs import list_jobs
        store = _make_store(tmp_path)
        _seed_job(store, pid=os.getpid())  # use alive PID so it's not auto-staled
        rows = list_jobs(store)
        assert len(rows) == 1
        assert rows[0]["id"] == "abc123def456"
        assert rows[0]["status"] == "running"
        assert rows[0]["total"] == 10
        assert rows[0]["done"] == 3

    def test_job_progress_update(self, tmp_path):
        from yt_artist.jobs import update_job_progress, get_job
        store = _make_store(tmp_path)
        _seed_job(store, pid=os.getpid())
        update_job_progress(store, "abc123def456", done=7, errors=1)
        job = get_job(store, "abc123def456")
        assert job["done"] == 7
        assert job["errors"] == 1

    def test_job_finalize(self, tmp_path):
        from yt_artist.jobs import finalize_job, get_job
        store = _make_store(tmp_path)
        _seed_job(store, pid=os.getpid())
        finalize_job(store, "abc123def456", status="completed")
        job = get_job(store, "abc123def456")
        assert job["status"] == "completed"
        assert job["finished_at"] is not None

    def test_stale_pid_auto_detected(self, tmp_path):
        from yt_artist.jobs import list_jobs
        store = _make_store(tmp_path)
        # Use a dead PID
        _seed_job(store, pid=4000000)
        rows = list_jobs(store)
        assert len(rows) == 1
        assert rows[0]["status"] == "failed"
        assert "died" in (rows[0].get("error_message") or "").lower()

    def test_job_prefix_match(self, tmp_path):
        from yt_artist.jobs import get_job
        store = _make_store(tmp_path)
        _seed_job(store, job_id="abc123def456", pid=os.getpid())
        # Prefix match with first 8 chars
        job = get_job(store, "abc123de")
        assert job is not None
        assert job["id"] == "abc123def456"

    def test_job_cleanup_removes_old(self, tmp_path):
        from yt_artist.jobs import cleanup_old_jobs
        store = _make_store(tmp_path)
        # Insert a finished job with finished_at in the past
        log_path = tmp_path / "old.log"
        log_path.write_text("old log content")
        conn = store._conn()
        try:
            conn.execute(
                "INSERT INTO jobs (id, command, status, pid, log_file, finished_at) "
                "VALUES ('old_job_1234', 'test cmd', 'completed', 1, ?, datetime('now', '-10 days'))",
                (str(log_path),),
            )
            conn.commit()
        finally:
            conn.close()
        removed = cleanup_old_jobs(store, max_age_days=7)
        assert removed == 1
        assert not log_path.exists()


# ---------------------------------------------------------------------------
# _ProgressCounter integration
# ---------------------------------------------------------------------------

class TestProgressCounterDB:

    def test_counter_updates_job_db(self, tmp_path):
        from yt_artist.cli import _ProgressCounter
        from yt_artist.jobs import get_job
        store = _make_store(tmp_path)
        _seed_job(store, pid=os.getpid(), total=0, done=0)
        pc = _ProgressCounter(5, job_id="abc123def456", job_storage=store)
        pc.tick("Test", "vid1")
        pc.tick("Test", "vid2", error="fail")
        job = get_job(store, "abc123def456")
        assert job["total"] == 5
        assert job["done"] == 2
        assert job["errors"] == 1

    def test_counter_finalize_sets_status(self, tmp_path):
        from yt_artist.cli import _ProgressCounter
        from yt_artist.jobs import get_job
        store = _make_store(tmp_path)
        _seed_job(store, pid=os.getpid())
        pc = _ProgressCounter(5, job_id="abc123def456", job_storage=store)
        pc.finalize(status="completed")
        job = get_job(store, "abc123def456")
        assert job["status"] == "completed"
        assert job["finished_at"] is not None

    def test_counter_no_db_without_job_id(self, tmp_path):
        """Without job_id, ProgressCounter should not write to DB."""
        from yt_artist.cli import _ProgressCounter
        store = _make_store(tmp_path)
        pc = _ProgressCounter(3)
        pc.tick("Test", "vid1")
        pc.tick("Test", "vid2")
        # Finalize should be a no-op
        pc.finalize()
        # No jobs should exist
        conn = store._conn()
        try:
            cur = conn.execute("SELECT COUNT(*) AS cnt FROM jobs")
            row = cur.fetchone()
            assert (row["cnt"] if isinstance(row, dict) else row[0]) == 0
        finally:
            conn.close()


# ---------------------------------------------------------------------------
# CLI integration: --bg flag
# ---------------------------------------------------------------------------

class TestBGFlagLaunch:

    def test_bg_flag_launches_subprocess(self, tmp_path, capfd):
        """When --bg is passed, main() should call launch_background and exit."""
        db = tmp_path / "test.db"
        with patch("yt_artist.jobs.launch_background", return_value="abc123def456") as mock_launch:
            code = _run_cli("--bg", "transcribe", "--artist-id", "@Test", db_path=db)
        assert code == 0
        captured = capfd.readouterr()
        assert "abc123de" in captured.out  # shortened ID
        assert "yt-artist jobs" in captured.out
        mock_launch.assert_called_once()

    def test_bg_worker_flag_sets_globals(self, tmp_path, capfd):
        """When --_bg-worker is passed, _bg_job_id should be set."""
        import yt_artist.cli as cli_mod
        db = tmp_path / "test.db"
        store = Storage(db)
        store.ensure_schema()
        _seed_job(store, job_id="testworker123", pid=os.getpid())

        # Mock the actual command to avoid needing real data
        def fake_func(args, storage, data_dir):
            # Verify the globals are set
            assert cli_mod._bg_job_id == "testworker123"
            assert cli_mod._bg_storage is not None

        _logging.root.handlers.clear()
        argv = ["yt-artist", "--db", str(db), "--_bg-worker", "testworker123", "quickstart"]
        with patch.object(sys, "argv", argv), \
             patch("yt_artist.cli._cmd_quickstart", side_effect=fake_func):
            try:
                from yt_artist.cli import main
                main()
            except SystemExit:
                pass

        # Clean up module globals
        cli_mod._bg_job_id = None
        cli_mod._bg_storage = None


# ---------------------------------------------------------------------------
# CLI integration: jobs subcommand
# ---------------------------------------------------------------------------

class TestJobsCommand:

    def test_jobs_list_empty(self, tmp_path, capfd):
        """'yt-artist jobs' with no jobs should show a message."""
        db = tmp_path / "test.db"
        code = _run_cli("jobs", db_path=db)
        assert code == 0
        captured = capfd.readouterr()
        assert "No background jobs" in captured.out

    def test_jobs_list_with_data(self, tmp_path, capfd):
        """'yt-artist jobs' should show tabular output."""
        db = tmp_path / "test.db"
        store = Storage(db)
        store.ensure_schema()
        _seed_job(store, pid=os.getpid(), total=10, done=5)
        code = _run_cli("jobs", db_path=db)
        assert code == 0
        captured = capfd.readouterr()
        assert "abc123de" in captured.out
        assert "running" in captured.out
        assert "5/10" in captured.out

    def test_jobs_attach_reads_log(self, tmp_path, capfd):
        """'jobs attach' should print log content for a completed job."""
        db = tmp_path / "test.db"
        store = Storage(db)
        store.ensure_schema()
        log_file = tmp_path / "job.log"
        log_file.write_text("INFO: Transcribing 1/5: vid001\nINFO: Transcribing 2/5: vid002\n")
        _seed_job(store, pid=os.getpid(), log_file=str(log_file), status="completed")
        code = _run_cli("jobs", "attach", "abc123de", db_path=db)
        assert code == 0
        captured = capfd.readouterr()
        assert "Transcribing 1/5" in captured.out
        assert "Transcribing 2/5" in captured.out

    def test_jobs_stop_sends_sigterm(self, tmp_path, capfd):
        """'jobs stop' should call os.kill with SIGTERM (after alive check with signal 0)."""
        import signal as _sig
        db = tmp_path / "test.db"
        store = Storage(db)
        store.ensure_schema()
        _seed_job(store, pid=os.getpid())
        with patch("yt_artist.jobs.os.kill") as mock_kill:
            code = _run_cli("jobs", "stop", "abc123de", db_path=db)
        assert code == 0
        # os.kill is called twice: once with signal 0 (alive check), once with SIGTERM
        calls = mock_kill.call_args_list
        assert any(c.args == (os.getpid(), 0) for c in calls), "Expected alive check with signal 0"
        assert any(c.args == (os.getpid(), _sig.SIGTERM) for c in calls), "Expected SIGTERM"

    def test_jobs_clean(self, tmp_path, capfd):
        """'jobs clean' should remove old finished jobs."""
        db = tmp_path / "test.db"
        store = Storage(db)
        store.ensure_schema()
        log_file = tmp_path / "old.log"
        log_file.write_text("old content")
        conn = store._conn()
        try:
            conn.execute(
                "INSERT INTO jobs (id, command, status, pid, log_file, finished_at) "
                "VALUES ('old_job_1234', 'test cmd', 'completed', 1, ?, datetime('now', '-10 days'))",
                (str(log_file),),
            )
            conn.commit()
        finally:
            conn.close()
        code = _run_cli("jobs", "clean", db_path=db)
        assert code == 0
        captured = capfd.readouterr()
        assert "1" in captured.out


# ---------------------------------------------------------------------------
# Schema migration
# ---------------------------------------------------------------------------

class TestJobsMigration:

    def test_jobs_table_created_on_fresh_db(self, tmp_path):
        """ensure_schema() should create the jobs table."""
        store = _make_store(tmp_path)
        conn = store._conn()
        try:
            cur = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='jobs'")
            assert cur.fetchone() is not None
        finally:
            conn.close()

    def test_jobs_table_migration_idempotent(self, tmp_path):
        """Calling ensure_schema() twice should not error."""
        store = _make_store(tmp_path)
        store.ensure_schema()  # Second call
        conn = store._conn()
        try:
            cur = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='jobs'")
            assert cur.fetchone() is not None
        finally:
            conn.close()
