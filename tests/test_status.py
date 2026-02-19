"""Tests for the status command and Storage count methods."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

from yt_artist.cli import _format_size, main
from yt_artist.storage import Storage


def _make_store(tmp_path: Path) -> Storage:
    db = tmp_path / "test.db"
    store = Storage(db)
    store.ensure_schema()
    return store


def _seed(store: Storage, n_artists: int = 1, n_videos: int = 3, n_transcripts: int = 0, n_summaries: int = 0) -> None:
    """Seed test data flexibly."""
    for i in range(n_artists):
        aid = f"@Artist{i}"
        store.upsert_artist(
            artist_id=aid,
            name=f"Artist {i}",
            channel_url=f"https://www.youtube.com/{aid}",
            urllist_path=f"data/artists/{aid}/urllist.md",
        )
        for j in range(n_videos):
            vid = f"vid{i:02d}{j:03d}xxxxx"[:11]
            store.upsert_video(
                video_id=vid,
                artist_id=aid,
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
# Storage count methods
# ---------------------------------------------------------------------------


class TestCountMethods:
    def test_count_artists(self, tmp_path):
        store = _make_store(tmp_path)
        assert store.count_artists() == 0
        _seed(store, n_artists=3, n_videos=0)
        assert store.count_artists() == 3

    def test_count_videos(self, tmp_path):
        store = _make_store(tmp_path)
        assert store.count_videos() == 0
        _seed(store, n_artists=1, n_videos=5)
        assert store.count_videos() == 5

    def test_count_transcribed_videos(self, tmp_path):
        store = _make_store(tmp_path)
        _seed(store, n_artists=1, n_videos=5, n_transcripts=3)
        assert store.count_transcribed_videos() == 3

    def test_count_summarized_videos(self, tmp_path):
        """count_summarized_videos uses DISTINCT — 2 summaries for 1 video counts as 1."""
        store = _make_store(tmp_path)
        _seed(store, n_artists=1, n_videos=5, n_transcripts=2, n_summaries=2)
        assert store.count_summarized_videos() == 2
        # Add a second summary for same video with different prompt
        store.upsert_prompt(prompt_id="p2", name="Alt", template="Alt: {artist}")
        vids = store.list_videos()
        store.upsert_summary(video_id=vids[0]["id"], prompt_id="p2", content="alt summary")
        # Still 2 distinct videos (not 3)
        assert store.count_summarized_videos() == 2

    def test_count_prompts(self, tmp_path):
        store = _make_store(tmp_path)
        # ensure_schema creates a default prompt
        assert store.count_prompts() >= 1
        store.upsert_prompt(prompt_id="extra", name="Extra", template="Extra: {artist}")
        assert store.count_prompts() >= 2


# ---------------------------------------------------------------------------
# _format_size helper
# ---------------------------------------------------------------------------


class TestFormatSize:
    def test_zero_bytes(self):
        assert _format_size(0) == "0 B"

    def test_small_bytes(self):
        assert _format_size(500) == "500 B"

    def test_kilobytes(self):
        result = _format_size(1024)
        assert "KB" in result

    def test_megabytes(self):
        result = _format_size(1048576)
        assert "MB" in result

    def test_gigabytes(self):
        result = _format_size(1073741824)
        assert "GB" in result


# ---------------------------------------------------------------------------
# CLI: yt-artist status
# ---------------------------------------------------------------------------


class TestStatusCommand:
    def test_status_empty_db(self, tmp_path, capfd):
        """status on empty DB shows zeros without crashing."""
        db = tmp_path / "test.db"
        code = _run_cli("status", db_path=db)
        assert code == 0
        out = capfd.readouterr().out
        assert "Artists:" in out
        assert "Videos:" in out
        assert "0" in out

    def test_status_with_data(self, tmp_path, capfd):
        """status shows correct counts after seeding data."""
        db = tmp_path / "test.db"
        store = Storage(db)
        store.ensure_schema()
        _seed(store, n_artists=2, n_videos=3, n_transcripts=4, n_summaries=2)
        code = _run_cli("status", db_path=db)
        assert code == 0
        out = capfd.readouterr().out
        assert "Artists:" in out and "2" in out
        assert "Videos:" in out and "6" in out  # 2 artists * 3 videos
        assert "4 transcribed" in out
        assert "2 summarized" in out

    def test_status_shows_artist_names(self, tmp_path, capfd):
        """Output includes artist IDs."""
        db = tmp_path / "test.db"
        store = Storage(db)
        store.ensure_schema()
        _seed(store, n_artists=2, n_videos=1)
        code = _run_cli("status", db_path=db)
        assert code == 0
        out = capfd.readouterr().out
        assert "@Artist0" in out
        assert "@Artist1" in out

    def test_status_truncates_many_artists(self, tmp_path, capfd):
        """With many artists, names are truncated with '...'."""
        db = tmp_path / "test.db"
        store = Storage(db)
        store.ensure_schema()
        _seed(store, n_artists=8, n_videos=0)
        code = _run_cli("status", db_path=db)
        assert code == 0
        out = capfd.readouterr().out
        assert "\u2026" in out  # ellipsis character

    def test_status_shows_running_jobs(self, tmp_path, capfd):
        """Running jobs are displayed."""
        db = tmp_path / "test.db"
        store = Storage(db)
        store.ensure_schema()
        # Seed a running job
        with store.transaction() as conn:
            conn.execute(
                "INSERT INTO jobs (id, command, status, pid, log_file, total, done) "
                "VALUES ('abcd1234abcd', 'transcribe --artist-id @X', 'running', 99999, '/tmp/j.log', 100, 45)"
            )
        code = _run_cli("status", db_path=db)
        assert code == 0
        out = capfd.readouterr().out
        assert "Running jobs:" in out and "1" in out

    def test_status_no_running_jobs(self, tmp_path, capfd):
        """No running jobs shows 0."""
        db = tmp_path / "test.db"
        code = _run_cli("status", db_path=db)
        assert code == 0
        out = capfd.readouterr().out
        assert "Running jobs:" in out and "0" in out

    def test_status_shows_db_size(self, tmp_path, capfd):
        """Output includes DB size."""
        db = tmp_path / "test.db"
        store = Storage(db)
        store.ensure_schema()
        _seed(store, n_artists=1, n_videos=1)
        code = _run_cli("status", db_path=db)
        assert code == 0
        out = capfd.readouterr().out
        assert "DB size:" in out
        # Should have some size unit
        assert any(unit in out for unit in ("B", "KB", "MB"))

    def test_status_shows_rate_info(self, tmp_path, capfd):
        """Output includes YouTube request rate info."""
        db = tmp_path / "test.db"
        store = Storage(db)
        store.ensure_schema()
        from yt_artist.rate_limit import log_request

        log_request(store, "test")
        code = _run_cli("status", db_path=db)
        assert code == 0
        out = capfd.readouterr().out
        assert "YouTube reqs:" in out
        assert "1 in last hour" in out

    def test_status_exit_code_zero(self, tmp_path):
        """status always exits with code 0."""
        db = tmp_path / "test.db"
        code = _run_cli("status", db_path=db)
        assert code == 0


# ---------------------------------------------------------------------------
# Status: stale summary counts
# ---------------------------------------------------------------------------


class TestStatusStale:
    def test_status_shows_stale_count(self, tmp_path, capfd):
        """Stale line shown when summaries have NULL hashes."""
        db = tmp_path / "test.db"
        store = _make_store(tmp_path)
        _seed(store, n_artists=1, n_videos=2, n_transcripts=2, n_summaries=2)
        # Summaries have no hashes → counted as stale_unknown
        code = _run_cli("status", db_path=db)
        assert code == 0
        out = capfd.readouterr().out
        assert "Stale:" in out
        assert "unknown" in out

    def test_status_no_stale_when_fresh(self, tmp_path, capfd):
        """No Stale line when all hashes match."""
        from yt_artist.hashing import content_hash

        db = tmp_path / "test.db"
        store = _make_store(tmp_path)
        _seed(store, n_artists=1, n_videos=1, n_transcripts=1)
        vid = store.list_videos()[0]["id"]
        prompts = store.list_prompts()
        pid = prompts[0]["id"]
        t_row = store.get_transcript(vid)
        p_row = store.get_prompt(pid)
        store.upsert_summary(
            video_id=vid,
            prompt_id=pid,
            content="Fresh summary.",
            prompt_hash=content_hash(p_row["template"]),
            transcript_hash=content_hash(t_row["raw_text"]),
        )
        code = _run_cli("status", db_path=db)
        assert code == 0
        out = capfd.readouterr().out
        assert "Stale:" not in out

    def test_status_json_includes_stale_fields(self, tmp_path, capfd):
        """--json output includes stale_summaries key."""
        import io
        import json
        import logging as _logging

        db = tmp_path / "test.db"
        store = _make_store(tmp_path)
        _seed(store, n_artists=1, n_videos=1, n_transcripts=1, n_summaries=1)

        _logging.root.handlers.clear()
        argv = ["yt-artist", "--db", str(db), "--json", "status"]
        old_stdout = sys.stdout
        sys.stdout = io.StringIO()
        try:
            with patch.object(sys, "argv", argv):
                try:
                    main()
                except SystemExit:
                    pass
            out = sys.stdout.getvalue()
        finally:
            sys.stdout = old_stdout

        data = json.loads(out)
        assert "stale_summaries" in data
        assert "stale_prompt" in data
        assert "stale_transcript" in data
        assert "stale_unknown" in data


# ---------------------------------------------------------------------------
# Status: work ledger integration
# ---------------------------------------------------------------------------


class TestStatusLedger:
    def _seed_ledger(self, store: Storage) -> None:
        """Seed artist + video + ledger entries for status tests."""
        _seed(store, n_artists=1, n_videos=1)
        vid = store.list_videos()[0]["id"]
        store.log_work(
            video_id=vid,
            operation="transcribe",
            status="success",
            started_at="2026-02-19T10:00:00Z",
            finished_at="2026-02-19T10:00:01Z",
            duration_ms=1000,
        )
        store.log_work(
            video_id=vid,
            operation="summarize",
            status="failed",
            started_at="2026-02-19T10:01:00Z",
            finished_at="2026-02-19T10:01:02Z",
            duration_ms=2000,
            error_message="LLM timeout",
        )

    def test_status_shows_operations_line(self, tmp_path, capfd):
        """status shows Operations line when ledger has data."""
        db = tmp_path / "test.db"
        store = _make_store(tmp_path)
        self._seed_ledger(store)
        code = _run_cli("status", db_path=db)
        assert code == 0
        out = capfd.readouterr().out
        assert "Operations:" in out
        assert "2" in out  # total count
        assert "transcribe:" in out
        assert "summarize:" in out

    def test_status_no_operations_when_empty(self, tmp_path, capfd):
        """status hides Operations line when ledger is empty."""
        db = tmp_path / "test.db"
        code = _run_cli("status", db_path=db)
        assert code == 0
        out = capfd.readouterr().out
        assert "Operations:" not in out

    def test_status_json_includes_ledger_fields(self, tmp_path, capfd):
        """--json output includes ledger_total and per-operation fields."""
        import io
        import json
        import logging as _logging

        db = tmp_path / "test.db"
        store = _make_store(tmp_path)
        self._seed_ledger(store)

        _logging.root.handlers.clear()
        argv = ["yt-artist", "--db", str(db), "--json", "status"]
        old_stdout = sys.stdout
        sys.stdout = io.StringIO()
        try:
            with patch.object(sys, "argv", argv):
                try:
                    main()
                except SystemExit:
                    pass
            out = sys.stdout.getvalue()
        finally:
            sys.stdout = old_stdout

        data = json.loads(out)
        assert data["ledger_total"] == 2
        assert data["ledger_transcribe_success"] == 1
        assert data["ledger_summarize_failed"] == 1
