"""Tests for R1: Foreign key enforcement is enabled on every connection."""
import sqlite3

import pytest


def test_fk_rejects_orphan_video(store):
    """Inserting a video with a nonexistent artist_id must raise IntegrityError."""
    with pytest.raises(sqlite3.IntegrityError):
        store.upsert_video(
            video_id="orphan_v1",
            artist_id="nonexistent_artist",
            url="https://www.youtube.com/watch?v=orphan_v1",
            title="Orphan Video",
        )


def test_fk_cascade_deletes_videos(store):
    """Deleting an artist cascades to its videos."""
    store.upsert_artist(
        artist_id="UC_cascade",
        name="Cascade Test",
        channel_url="https://www.youtube.com/@cascade",
        urllist_path="data/artists/UC_cascade/artistUC_cascadeCascade_Test-urllist.md",
    )
    store.upsert_video(
        video_id="cv1",
        artist_id="UC_cascade",
        url="https://www.youtube.com/watch?v=cv1",
        title="Cascade Video 1",
    )
    store.upsert_video(
        video_id="cv2",
        artist_id="UC_cascade",
        url="https://www.youtube.com/watch?v=cv2",
        title="Cascade Video 2",
    )

    # Delete artist directly via raw SQL
    conn = store._conn()
    try:
        conn.execute("DELETE FROM artists WHERE id = ?", ("UC_cascade",))
        conn.commit()
    finally:
        conn.close()

    # Videos should be gone (cascade)
    assert store.get_video("cv1") is None
    assert store.get_video("cv2") is None


def test_fk_cascade_deletes_transcripts(store):
    """Deleting a video cascades to its transcript."""
    store.upsert_artist(
        artist_id="UC_ct",
        name="CT",
        channel_url="https://www.youtube.com/@ct",
        urllist_path="data/artists/UC_ct/artistUC_ctCT-urllist.md",
    )
    store.upsert_video(
        video_id="ctv1",
        artist_id="UC_ct",
        url="https://www.youtube.com/watch?v=ctv1",
        title="CT Video",
    )
    store.save_transcript(video_id="ctv1", raw_text="Hello world.", format="vtt")

    # Delete video directly
    conn = store._conn()
    try:
        conn.execute("DELETE FROM videos WHERE id = ?", ("ctv1",))
        conn.commit()
    finally:
        conn.close()

    assert store.get_transcript("ctv1") is None
