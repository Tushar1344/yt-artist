"""Tests for R4: Storage.transaction() context manager."""

import pytest

from yt_artist.storage import Storage


def _make_store(tmp_path):
    db = tmp_path / "test.db"
    store = Storage(db)
    store.ensure_schema()
    return store


def test_transaction_commits_on_success(tmp_path):
    """Batch inserts inside transaction() are committed atomically."""
    store = _make_store(tmp_path)
    with store.transaction() as conn:
        conn.execute(
            "INSERT INTO artists (id, name, channel_url, urllist_path) VALUES (?, ?, ?, ?)",
            ("@tx_test", "Tx Test", "https://example.com", "path.md"),
        )
        conn.execute(
            "INSERT INTO videos (id, artist_id, url, title) VALUES (?, ?, ?, ?)",
            ("txvid00001", "@tx_test", "https://example.com/v", "V1"),
        )
    # Both should be visible outside the transaction
    assert store.get_artist("@tx_test") is not None
    assert store.get_video("txvid00001") is not None


def test_transaction_rolls_back_on_error(tmp_path):
    """If an exception occurs inside transaction(), everything is rolled back."""
    store = _make_store(tmp_path)
    with pytest.raises(RuntimeError, match="boom"), store.transaction() as conn:
        conn.execute(
            "INSERT INTO artists (id, name, channel_url, urllist_path) VALUES (?, ?, ?, ?)",
            ("@rollback", "Rollback", "https://example.com", "path.md"),
        )
        raise RuntimeError("boom")
    # Artist should NOT exist (rolled back)
    assert store.get_artist("@rollback") is None


def test_transaction_does_not_break_existing_methods(tmp_path):
    """Regular per-method calls still work alongside transaction()."""
    store = _make_store(tmp_path)
    store.upsert_artist(
        artist_id="@normal",
        name="Normal",
        channel_url="https://example.com",
        urllist_path="path.md",
    )
    assert store.get_artist("@normal") is not None
