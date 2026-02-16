"""Tests for rate-limit monitoring: request logging, counting, and warnings."""
from __future__ import annotations

import sys
from io import StringIO

import pytest

from yt_artist.rate_limit import (
    WARN_THRESHOLD_1H,
    HIGH_THRESHOLD_1H,
    log_request,
    count_requests,
    get_rate_status,
    check_rate_warning,
)
from yt_artist.storage import Storage


def _make_store(tmp_path):
    store = Storage(tmp_path / "test.db")
    store.ensure_schema()
    return store


# ---------------------------------------------------------------------------
# log_request + count_requests
# ---------------------------------------------------------------------------

class TestLogRequest:

    def test_log_request_inserts_row(self, tmp_path):
        """log_request inserts a row with the correct request_type."""
        store = _make_store(tmp_path)
        log_request(store, "subtitle_download")
        conn = store._conn()
        try:
            cur = conn.execute("SELECT request_type FROM request_log")
            rows = cur.fetchall()
        finally:
            conn.close()
        assert len(rows) == 1
        assert rows[0]["request_type"] == "subtitle_download"

    def test_log_request_multiple_types(self, tmp_path):
        """Different request types are stored correctly."""
        store = _make_store(tmp_path)
        log_request(store, "subtitle_download")
        log_request(store, "playlist")
        log_request(store, "metadata")
        conn = store._conn()
        try:
            cur = conn.execute("SELECT request_type FROM request_log ORDER BY id")
            types = [r["request_type"] for r in cur.fetchall()]
        finally:
            conn.close()
        assert types == ["subtitle_download", "playlist", "metadata"]

    def test_count_requests_empty(self, tmp_path):
        """count_requests returns 0 on empty table."""
        store = _make_store(tmp_path)
        assert count_requests(store, 1) == 0

    def test_count_requests_counts_recent_only(self, tmp_path):
        """count_requests with hours=1 only counts recent rows."""
        store = _make_store(tmp_path)
        conn = store._conn()
        try:
            # Insert a row from 2 hours ago
            conn.execute(
                "INSERT INTO request_log (timestamp, request_type) "
                "VALUES (datetime('now', '-2 hours'), 'old')"
            )
            # Insert a row from 30 minutes ago (within 1 hour)
            conn.execute(
                "INSERT INTO request_log (timestamp, request_type) "
                "VALUES (datetime('now', '-30 minutes'), 'recent')"
            )
            # Insert a current row
            conn.execute(
                "INSERT INTO request_log (timestamp, request_type) "
                "VALUES (datetime('now'), 'now')"
            )
            conn.commit()
        finally:
            conn.close()
        # 1-hour window should see 2 rows (30min ago + now)
        assert count_requests(store, 1) == 2
        # 24-hour window should see all 3
        assert count_requests(store, 24) == 3

    def test_log_request_cleans_old_entries(self, tmp_path):
        """Rows older than 24h are deleted on next log_request call."""
        store = _make_store(tmp_path)
        conn = store._conn()
        try:
            # Insert rows from 25 hours ago
            for _ in range(5):
                conn.execute(
                    "INSERT INTO request_log (timestamp, request_type) "
                    "VALUES (datetime('now', '-25 hours'), 'old')"
                )
            conn.commit()
        finally:
            conn.close()
        # Verify they exist
        assert count_requests(store, 48) == 5
        # New log_request triggers cleanup
        log_request(store, "new")
        # Old rows should be gone, only the new one remains
        conn = store._conn()
        try:
            cur = conn.execute("SELECT COUNT(*) AS cnt FROM request_log")
            total = cur.fetchone()["cnt"]
        finally:
            conn.close()
        assert total == 1


# ---------------------------------------------------------------------------
# get_rate_status
# ---------------------------------------------------------------------------

class TestGetRateStatus:

    def test_no_requests(self, tmp_path):
        """Returns zeros and no warning when table is empty."""
        store = _make_store(tmp_path)
        status = get_rate_status(store)
        assert status["count_1h"] == 0
        assert status["count_24h"] == 0
        assert status["warning"] is None

    def test_below_threshold_no_warning(self, tmp_path):
        """No warning when request count is below WARN_THRESHOLD_1H."""
        store = _make_store(tmp_path)
        conn = store._conn()
        try:
            for _ in range(50):
                conn.execute("INSERT INTO request_log (request_type) VALUES ('test')")
            conn.commit()
        finally:
            conn.close()
        status = get_rate_status(store)
        assert status["count_1h"] == 50
        assert status["warning"] is None

    def test_warn_threshold_triggers_warning(self, tmp_path):
        """Warning when count_1h >= WARN_THRESHOLD_1H."""
        store = _make_store(tmp_path)
        conn = store._conn()
        try:
            for _ in range(WARN_THRESHOLD_1H):
                conn.execute("INSERT INTO request_log (request_type) VALUES ('test')")
            conn.commit()
        finally:
            conn.close()
        status = get_rate_status(store)
        assert status["warning"] is not None
        assert "Elevated" in status["warning"]

    def test_high_threshold_triggers_stronger_warning(self, tmp_path):
        """Stronger warning when count_1h >= HIGH_THRESHOLD_1H."""
        store = _make_store(tmp_path)
        conn = store._conn()
        try:
            for _ in range(HIGH_THRESHOLD_1H):
                conn.execute("INSERT INTO request_log (request_type) VALUES ('test')")
            conn.commit()
        finally:
            conn.close()
        status = get_rate_status(store)
        assert status["warning"] is not None
        assert "High" in status["warning"]


# ---------------------------------------------------------------------------
# check_rate_warning
# ---------------------------------------------------------------------------

class TestCheckRateWarning:

    def test_prints_warning_to_stderr(self, tmp_path, capfd):
        """Warning printed to stderr when rate is high."""
        store = _make_store(tmp_path)
        conn = store._conn()
        try:
            for _ in range(WARN_THRESHOLD_1H + 10):
                conn.execute("INSERT INTO request_log (request_type) VALUES ('test')")
            conn.commit()
        finally:
            conn.close()
        check_rate_warning(store, quiet=False)
        captured = capfd.readouterr()
        assert "request" in captured.err.lower() or "rate" in captured.err.lower()

    def test_quiet_suppresses_warning(self, tmp_path, capfd):
        """No output when quiet=True."""
        store = _make_store(tmp_path)
        conn = store._conn()
        try:
            for _ in range(WARN_THRESHOLD_1H + 10):
                conn.execute("INSERT INTO request_log (request_type) VALUES ('test')")
            conn.commit()
        finally:
            conn.close()
        check_rate_warning(store, quiet=True)
        captured = capfd.readouterr()
        assert captured.err == ""

    def test_no_warning_when_rate_low(self, tmp_path, capfd):
        """No output when rate is below threshold."""
        store = _make_store(tmp_path)
        log_request(store, "test")
        check_rate_warning(store, quiet=False)
        captured = capfd.readouterr()
        assert captured.err == ""


# ---------------------------------------------------------------------------
# Migration
# ---------------------------------------------------------------------------

class TestMigration:

    def test_migration_creates_request_log_table(self, tmp_path):
        """ensure_schema() creates the request_log table."""
        store = _make_store(tmp_path)
        conn = store._conn()
        try:
            cur = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='request_log'"
            )
            assert cur.fetchone() is not None
        finally:
            conn.close()

    def test_migration_idempotent(self, tmp_path):
        """ensure_schema() can be called twice without error."""
        store = _make_store(tmp_path)
        store.ensure_schema()  # second call
        # Should not raise
        conn = store._conn()
        try:
            cur = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='request_log'"
            )
            assert cur.fetchone() is not None
        finally:
            conn.close()
