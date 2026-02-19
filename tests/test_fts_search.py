"""Tests for FTS5 full-text transcript search (storage, migration, CLI)."""

from __future__ import annotations

import io
import json
import logging
import sqlite3
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

from yt_artist.cli import main
from yt_artist.storage import Storage

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _seed(store: Storage, artist_id: str = "@Test", n_videos: int = 3) -> list[str]:
    """Seed artist + videos + transcripts; return video IDs."""
    store.upsert_artist(
        artist_id=artist_id,
        name="Test Channel",
        channel_url=f"https://www.youtube.com/{artist_id}",
        urllist_path=f"data/artists/{artist_id}/urllist.md",
    )
    texts = [
        "Dopamine is a neurotransmitter that plays a role in motivation and reward.",
        "The mitochondria is the powerhouse of the cell. ATP synthesis occurs here.",
        "Dopamine pathways connect the ventral tegmental area to the prefrontal cortex.",
    ]
    vids: list[str] = []
    for i in range(n_videos):
        vid = f"vid{i:03d}"
        store.upsert_video(
            video_id=vid,
            artist_id=artist_id,
            url=f"https://youtube.com/watch?v={vid}",
            title=f"Video {i}",
        )
        store.save_transcript(video_id=vid, raw_text=texts[i % len(texts)])
        vids.append(vid)
    return vids


def _run_cli(*args: str, db_path: str | Path = "", json_output: bool = False) -> tuple[int, str, str]:
    """Run CLI and capture stdout/stderr."""
    logging.root.handlers.clear()
    argv = ["yt-artist"]
    if db_path:
        argv += ["--db", str(db_path)]
    if json_output:
        argv += ["--json"]
    argv += list(args)
    old_out, old_err = sys.stdout, sys.stderr
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
        sys.stdout = old_out
        sys.stderr = old_err


# ---------------------------------------------------------------------------
# Storage: search_transcripts
# ---------------------------------------------------------------------------


class TestSearchTranscripts:
    def test_basic_search(self, store):
        """Single-word search returns matching transcripts."""
        _seed(store)
        results = store.search_transcripts("dopamine")
        assert len(results) == 2
        for r in results:
            assert r["video_id"] in ("vid000", "vid002")

    def test_no_results(self, store):
        """Query with no matches returns empty list."""
        _seed(store)
        results = store.search_transcripts("quantum")
        assert results == []

    def test_phrase_search(self, store):
        """Quoted phrase search."""
        _seed(store)
        results = store.search_transcripts('"powerhouse of the cell"')
        assert len(results) == 1
        assert results[0]["video_id"] == "vid001"

    def test_prefix_search(self, store):
        """Prefix search with wildcard."""
        _seed(store)
        results = store.search_transcripts("mito*")
        assert len(results) >= 1
        assert results[0]["video_id"] == "vid001"

    def test_artist_filter(self, store):
        """--artist-id narrows search to one artist."""
        _seed(store, artist_id="@A", n_videos=2)
        store.upsert_artist(
            artist_id="@B",
            name="B",
            channel_url="https://www.youtube.com/@B",
            urllist_path="data/artists/@B/urllist.md",
        )
        store.upsert_video(
            video_id="bvid",
            artist_id="@B",
            url="https://youtube.com/watch?v=bvid",
            title="B Video",
        )
        store.save_transcript(video_id="bvid", raw_text="Dopamine levels in the brain.")
        # Without filter: @A has vid000 (dopamine), @B has bvid (dopamine).
        all_results = store.search_transcripts("dopamine")
        a_results = store.search_transcripts("dopamine", artist_id="@A")
        assert len(a_results) < len(all_results)
        assert all(r["artist_id"] == "@A" for r in a_results)

    def test_limit(self, store):
        """Limit caps results."""
        _seed(store)
        results = store.search_transcripts("dopamine", limit=1)
        assert len(results) == 1

    def test_snippet_has_markers(self, store):
        """Snippets contain [ ] match markers."""
        _seed(store)
        results = store.search_transcripts("dopamine")
        assert any("[" in r.get("snippet", "") and "]" in r.get("snippet", "") for r in results)

    def test_ranked_by_relevance(self, store):
        """Results are ordered by rank (lower = better match in FTS5 BM25)."""
        _seed(store)
        results = store.search_transcripts("dopamine")
        if len(results) > 1:
            ranks = [r["rank"] for r in results]
            assert ranks == sorted(ranks)

    def test_invalid_query_raises(self, store):
        """Malformed FTS5 query raises ValueError with helpful message."""
        _seed(store)
        with pytest.raises(ValueError, match="Invalid search query"):
            store.search_transcripts("AND OR NOT")

    def test_fts5_not_available_raises(self, tmp_path):
        """search_transcripts raises ValueError if FTS5 table missing."""
        db = tmp_path / "nofts.db"
        conn = sqlite3.connect(str(db))
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute(
            "CREATE TABLE artists (id TEXT PRIMARY KEY, name TEXT NOT NULL, channel_url TEXT NOT NULL, urllist_path TEXT NOT NULL, created_at TEXT DEFAULT (datetime('now')))"
        )
        conn.execute(
            "CREATE TABLE videos (id TEXT PRIMARY KEY, artist_id TEXT NOT NULL REFERENCES artists(id), url TEXT NOT NULL, title TEXT, fetched_at TEXT DEFAULT (datetime('now')))"
        )
        conn.execute(
            "CREATE TABLE transcripts (video_id TEXT PRIMARY KEY REFERENCES videos(id), raw_text TEXT NOT NULL, format TEXT, quality_score REAL, raw_vtt TEXT, created_at TEXT DEFAULT (datetime('now')))"
        )
        conn.commit()
        conn.close()
        store = Storage(db)
        with pytest.raises(ValueError, match="not available"):
            store.search_transcripts("test")

    def test_returns_transcript_len(self, store):
        """Search results include transcript_len."""
        _seed(store)
        results = store.search_transcripts("dopamine")
        assert results[0]["transcript_len"] > 0

    def test_returns_title_and_artist(self, store):
        """Search results include title and artist_id from videos join."""
        _seed(store)
        results = store.search_transcripts("dopamine")
        assert results[0]["artist_id"] == "@Test"
        assert results[0]["title"].startswith("Video ")


# ---------------------------------------------------------------------------
# Storage: has_fts5
# ---------------------------------------------------------------------------


class TestHasFts5:
    def test_true_after_ensure_schema(self, store):
        assert store.has_fts5() is True

    def test_false_without_migration(self, tmp_path):
        db = tmp_path / "bare.db"
        conn = sqlite3.connect(str(db))
        conn.execute("CREATE TABLE artists (id TEXT PRIMARY KEY)")
        conn.commit()
        conn.close()
        store = Storage(db)
        assert store.has_fts5() is False


# ---------------------------------------------------------------------------
# Migration: idempotent, rebuild, trigger sync
# ---------------------------------------------------------------------------


class TestFtsMigration:
    def test_migration_idempotent(self, store):
        """Running migration twice does not error."""
        with store.transaction() as conn:
            store._migrate_fts5_transcripts(conn)
            store._migrate_fts5_transcripts(conn)

    def test_rebuild_indexes_existing_transcripts(self, tmp_path):
        """Migration rebuilds FTS index from pre-existing transcripts."""
        db = tmp_path / "legacy.db"
        store = Storage(db)
        store.ensure_schema()
        # Seed data while triggers are active.
        store.upsert_artist(
            artist_id="@L",
            name="L",
            channel_url="https://www.youtube.com/@L",
            urllist_path="x.md",
        )
        store.upsert_video(video_id="lv1", artist_id="@L", url="u", title="Legacy")
        store.save_transcript(video_id="lv1", raw_text="Legacy transcript about dopamine.")
        # Drop FTS table + triggers to simulate pre-migration DB.
        conn = sqlite3.connect(str(db))
        conn.execute("DROP TABLE IF EXISTS transcripts_fts")
        conn.execute("DROP TRIGGER IF EXISTS transcripts_ai")
        conn.execute("DROP TRIGGER IF EXISTS transcripts_ad")
        conn.execute("DROP TRIGGER IF EXISTS transcripts_au")
        conn.commit()
        conn.close()
        # Re-run ensure_schema (triggers migration + rebuild).
        store.ensure_schema()
        results = store.search_transcripts("dopamine")
        assert len(results) == 1
        assert results[0]["video_id"] == "lv1"

    def test_trigger_sync_on_insert(self, store):
        """New transcript is immediately searchable."""
        store.upsert_artist(
            artist_id="@T",
            name="T",
            channel_url="https://www.youtube.com/@T",
            urllist_path="x.md",
        )
        store.upsert_video(video_id="tv1", artist_id="@T", url="u", title="Trigger Test")
        store.save_transcript(video_id="tv1", raw_text="Serotonin regulates mood.")
        results = store.search_transcripts("serotonin")
        assert len(results) == 1

    def test_trigger_sync_on_update(self, store):
        """Updated transcript reflects in search results (old content removed)."""
        store.upsert_artist(
            artist_id="@T",
            name="T",
            channel_url="https://www.youtube.com/@T",
            urllist_path="x.md",
        )
        store.upsert_video(video_id="tv1", artist_id="@T", url="u", title="Trigger Test")
        store.save_transcript(video_id="tv1", raw_text="Old content about melatonin.")
        store.save_transcript(video_id="tv1", raw_text="New content about cortisol.")
        assert store.search_transcripts("melatonin") == []
        results = store.search_transcripts("cortisol")
        assert len(results) == 1

    def test_trigger_sync_on_delete(self, store):
        """Deleted transcript is removed from search index."""
        store.upsert_artist(
            artist_id="@T",
            name="T",
            channel_url="https://www.youtube.com/@T",
            urllist_path="x.md",
        )
        store.upsert_video(video_id="tv1", artist_id="@T", url="u", title="Del Test")
        store.save_transcript(video_id="tv1", raw_text="Ephemeral content about oxytocin.")
        assert len(store.search_transcripts("oxytocin")) == 1
        # Delete via cascade (delete the video).
        with store.transaction() as conn:
            conn.execute("DELETE FROM videos WHERE id = 'tv1'")
        assert store.search_transcripts("oxytocin") == []


# ---------------------------------------------------------------------------
# CLI: search-transcripts --query
# ---------------------------------------------------------------------------


class TestSearchTranscriptsCli:
    def test_query_human_output(self, tmp_path):
        db = tmp_path / "test.db"
        store = Storage(db)
        store.ensure_schema()
        _seed(store)
        code, out, _ = _run_cli("search-transcripts", "--query", "dopamine", db_path=db)
        assert code == 0
        assert "vid000" in out or "vid002" in out

    def test_query_json_output(self, tmp_path):
        db = tmp_path / "test.db"
        store = Storage(db)
        store.ensure_schema()
        _seed(store)
        code, out, _ = _run_cli(
            "search-transcripts",
            "--query",
            "dopamine",
            db_path=db,
            json_output=True,
        )
        assert code == 0
        data = json.loads(out)
        assert isinstance(data, list)
        assert len(data) >= 1
        assert "snippet" in data[0]
        assert "rank" in data[0]
        assert "video_id" in data[0]

    def test_query_with_artist_filter(self, tmp_path):
        db = tmp_path / "test.db"
        store = Storage(db)
        store.ensure_schema()
        _seed(store)
        code, out, _ = _run_cli(
            "search-transcripts",
            "--query",
            "dopamine",
            "--artist-id",
            "@Test",
            db_path=db,
            json_output=True,
        )
        assert code == 0
        data = json.loads(out)
        assert all(r["artist_id"] == "@Test" for r in data)

    def test_query_no_results(self, tmp_path):
        db = tmp_path / "test.db"
        store = Storage(db)
        store.ensure_schema()
        _seed(store)
        code, out, _ = _run_cli("search-transcripts", "--query", "quantumxyz", db_path=db)
        assert code == 0
        assert "No transcripts matching" in out

    def test_query_with_limit(self, tmp_path):
        db = tmp_path / "test.db"
        store = Storage(db)
        store.ensure_schema()
        _seed(store)
        code, out, _ = _run_cli(
            "search-transcripts",
            "--query",
            "dopamine",
            "--limit",
            "1",
            db_path=db,
            json_output=True,
        )
        assert code == 0
        data = json.loads(out)
        assert len(data) <= 1

    def test_no_query_still_lists(self, tmp_path):
        """Without --query, command still lists transcripts (backward compat)."""
        db = tmp_path / "test.db"
        store = Storage(db)
        store.ensure_schema()
        _seed(store)
        code, out, _ = _run_cli("search-transcripts", db_path=db)
        assert code == 0
        assert "vid000" in out

    def test_invalid_query_exits_with_error(self, tmp_path):
        db = tmp_path / "test.db"
        store = Storage(db)
        store.ensure_schema()
        _seed(store)
        code, out, err = _run_cli("search-transcripts", "--query", "AND OR NOT", db_path=db)
        assert code != 0
