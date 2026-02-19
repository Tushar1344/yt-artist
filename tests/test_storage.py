"""Tests for storage layer: artists, videos, transcripts, prompts, summaries."""


def test_create_and_get_artist(store):
    store.upsert_artist(
        artist_id="UC_test123",
        name="Test Channel",
        channel_url="https://www.youtube.com/@test",
        urllist_path="data/artists/UC_test123/artistUC_test123Test_Channel-urllist.md",
    )
    artist = store.get_artist("UC_test123")
    assert artist is not None
    assert artist["id"] == "UC_test123"
    assert artist["name"] == "Test Channel"
    assert artist["channel_url"] == "https://www.youtube.com/@test"
    assert "artistUC_test123Test_Channel-urllist.md" in artist["urllist_path"]


def test_upsert_artist_idempotent(store):
    store.upsert_artist(
        artist_id="UC_abc",
        name="First",
        channel_url="https://www.youtube.com/@first",
        urllist_path="data/artists/UC_abc/artistUC_abcFirst-urllist.md",
    )
    store.upsert_artist(
        artist_id="UC_abc",
        name="First Updated",
        channel_url="https://www.youtube.com/@first",
        urllist_path="data/artists/UC_abc/artistUC_abcFirst_Updated-urllist.md",
    )
    artist = store.get_artist("UC_abc")
    assert artist["name"] == "First Updated"
    assert "First_Updated" in artist["urllist_path"]


def test_get_artist_missing_returns_none(store):
    assert store.get_artist("nonexistent") is None


def test_upsert_video_requires_artist(store):
    store.upsert_artist(
        artist_id="UC_art",
        name="Art",
        channel_url="https://www.youtube.com/@art",
        urllist_path="data/artists/UC_art/artistUC_artArt-urllist.md",
    )
    store.upsert_video(
        video_id="vid1",
        artist_id="UC_art",
        url="https://www.youtube.com/watch?v=vid1",
        title="Video One",
    )
    video = store.get_video("vid1")
    assert video is not None
    assert video["id"] == "vid1"
    assert video["artist_id"] == "UC_art"
    assert video["url"] == "https://www.youtube.com/watch?v=vid1"
    assert video["title"] == "Video One"


def test_upsert_video_idempotent(store):
    store.upsert_artist(
        artist_id="UC_art",
        name="Art",
        channel_url="https://www.youtube.com/@art",
        urllist_path="data/artists/UC_art/artistUC_artArt-urllist.md",
    )
    store.upsert_video(
        video_id="v1",
        artist_id="UC_art",
        url="https://www.youtube.com/watch?v=v1",
        title="Original",
    )
    store.upsert_video(
        video_id="v1",
        artist_id="UC_art",
        url="https://www.youtube.com/watch?v=v1",
        title="Updated Title",
    )
    video = store.get_video("v1")
    assert video["title"] == "Updated Title"


def test_get_video_missing_returns_none(store):
    assert store.get_video("nonexistent") is None


def test_save_and_get_transcript(store):
    store.upsert_artist(
        artist_id="UC_a",
        name="A",
        channel_url="https://www.youtube.com/@a",
        urllist_path="data/artists/UC_a/artistUC_aA-urllist.md",
    )
    store.upsert_video(
        video_id="tv1",
        artist_id="UC_a",
        url="https://www.youtube.com/watch?v=tv1",
        title="T Video",
    )
    store.save_transcript(video_id="tv1", raw_text="Hello world.", format="srv3")
    row = store.get_transcript("tv1")
    assert row is not None
    assert row["video_id"] == "tv1"
    assert row["raw_text"] == "Hello world."
    assert row["format"] == "srv3"


def test_save_transcript_overwrites(store):
    store.upsert_artist(
        artist_id="UC_a",
        name="A",
        channel_url="https://www.youtube.com/@a",
        urllist_path="data/artists/UC_a/artistUC_aA-urllist.md",
    )
    store.upsert_video(
        video_id="tv2",
        artist_id="UC_a",
        url="https://www.youtube.com/watch?v=tv2",
        title="T2",
    )
    store.save_transcript(video_id="tv2", raw_text="First.", format="vtt")
    store.save_transcript(video_id="tv2", raw_text="Second.", format="vtt")
    row = store.get_transcript("tv2")
    assert row["raw_text"] == "Second."


def test_get_transcript_missing_returns_none(store):
    assert store.get_transcript("nonexistent") is None


def test_save_and_get_prompt(store):
    store.upsert_prompt(
        prompt_id="default",
        name="Default summary",
        template="Summarize for {audience}. Artist: {artist}. Video: {video}. Intent: {intent}.",
        artist_component="channel name",
        video_component="video title",
        intent_component="what we want",
        audience_component="who reads it",
    )
    row = store.get_prompt("default")
    assert row is not None
    assert row["id"] == "default"
    assert row["name"] == "Default summary"
    assert "{artist}" in row["template"]
    assert row["audience_component"] == "who reads it"


def test_get_prompt_missing_returns_none(store):
    assert store.get_prompt("missing") is None


def test_save_and_get_summary(store):
    store.upsert_artist(
        artist_id="UC_s",
        name="S",
        channel_url="https://www.youtube.com/@s",
        urllist_path="data/artists/UC_s/artistUC_sS-urllist.md",
    )
    store.upsert_video(
        video_id="sv1",
        artist_id="UC_s",
        url="https://www.youtube.com/watch?v=sv1",
        title="S Video",
    )
    store.upsert_prompt(
        prompt_id="p1",
        name="P1",
        template="Summarize: {video}",
    )
    store.upsert_summary(video_id="sv1", prompt_id="p1", content="This is the summary.")
    rows = store.get_summaries_for_video("sv1")
    assert len(rows) == 1
    assert rows[0]["video_id"] == "sv1"
    assert rows[0]["prompt_id"] == "p1"
    assert rows[0]["content"] == "This is the summary."
    assert "created_at" in rows[0]


def test_upsert_summary_overwrites_same_video_prompt(store):
    store.upsert_artist(
        artist_id="UC_s",
        name="S",
        channel_url="https://www.youtube.com/@s",
        urllist_path="data/artists/UC_s/artistUC_sS-urllist.md",
    )
    store.upsert_video(
        video_id="sv2",
        artist_id="UC_s",
        url="https://www.youtube.com/watch?v=sv2",
        title="S2",
    )
    store.upsert_prompt(prompt_id="p2", name="P2", template="Sum: {video}")
    store.upsert_summary(video_id="sv2", prompt_id="p2", content="First summary.")
    store.upsert_summary(video_id="sv2", prompt_id="p2", content="Second summary.")
    rows = store.get_summaries_for_video("sv2")
    assert len(rows) == 1
    assert rows[0]["content"] == "Second summary."


def test_multiple_summaries_per_video_different_prompts(store):
    store.upsert_artist(
        artist_id="UC_m",
        name="M",
        channel_url="https://www.youtube.com/@m",
        urllist_path="data/artists/UC_m/artistUC_mM-urllist.md",
    )
    store.upsert_video(
        video_id="mv1",
        artist_id="UC_m",
        url="https://www.youtube.com/watch?v=mv1",
        title="M Video",
    )
    store.upsert_prompt(prompt_id="prompt_a", name="A", template="A: {video}")
    store.upsert_prompt(prompt_id="prompt_b", name="B", template="B: {video}")
    store.upsert_summary(video_id="mv1", prompt_id="prompt_a", content="Summary A.")
    store.upsert_summary(video_id="mv1", prompt_id="prompt_b", content="Summary B.")
    rows = store.get_summaries_for_video("mv1")
    assert len(rows) == 2
    contents = {r["prompt_id"]: r["content"] for r in rows}
    assert contents["prompt_a"] == "Summary A."
    assert contents["prompt_b"] == "Summary B."


def test_urllist_path_helper(store):
    path = store.urllist_path(artist_id="UC_xyz", artist_name="My Channel Name")
    assert "UC_xyz" in path
    assert "My_Channel_Name" in path or "MyChannelName" in path
    assert path.endswith("-urllist.md")


def test_list_artists(store):
    store.upsert_artist(
        artist_id="UC_one",
        name="One",
        channel_url="https://www.youtube.com/@one",
        urllist_path="data/artists/UC_one/artistUC_oneOne-urllist.md",
    )
    store.upsert_artist(
        artist_id="UC_two",
        name="Two",
        channel_url="https://www.youtube.com/@two",
        urllist_path="data/artists/UC_two/artistUC_twoTwo-urllist.md",
    )
    artists = store.list_artists()
    assert len(artists) == 2
    names = {a["name"] for a in artists}
    assert names == {"One", "Two"}


def test_list_videos_all(store):
    store.upsert_artist(
        artist_id="UC_a",
        name="A",
        channel_url="https://www.youtube.com/@a",
        urllist_path="data/artists/UC_a/artistUC_aA-urllist.md",
    )
    store.upsert_video(video_id="v1", artist_id="UC_a", url="https://youtube.com/watch?v=v1", title="V1")
    store.upsert_video(video_id="v2", artist_id="UC_a", url="https://youtube.com/watch?v=v2", title="V2")
    videos = store.list_videos()
    assert len(videos) == 2
    assert {v["id"] for v in videos} == {"v1", "v2"}


def test_list_videos_by_artist(store):
    store.upsert_artist(
        artist_id="UC_x",
        name="X",
        channel_url="https://www.youtube.com/@x",
        urllist_path="data/artists/UC_x/artistUC_xX-urllist.md",
    )
    store.upsert_artist(
        artist_id="UC_y",
        name="Y",
        channel_url="https://www.youtube.com/@y",
        urllist_path="data/artists/UC_y/artistUC_yY-urllist.md",
    )
    store.upsert_video(video_id="vx", artist_id="UC_x", url="https://youtube.com/watch?v=vx", title="VX")
    store.upsert_video(video_id="vy", artist_id="UC_y", url="https://youtube.com/watch?v=vy", title="VY")
    videos = store.list_videos(artist_id="UC_x")
    assert len(videos) == 1
    assert videos[0]["id"] == "vx"


# ---------------------------------------------------------------------------
# IN-query chunking tests (SQLite param limit safety)
# ---------------------------------------------------------------------------
from yt_artist.storage import _IN_BATCH_SIZE


def _seed_artist_and_videos(store, n: int):
    """Create an artist and *n* videos, returning the list of video IDs."""
    store.upsert_artist(
        artist_id="UC_bulk",
        name="Bulk",
        channel_url="https://www.youtube.com/@bulk",
        urllist_path="data/artists/UC_bulk/artistUC_bulkBulk-urllist.md",
    )
    ids = [f"vid_{i:05d}" for i in range(n)]
    for vid in ids:
        store.upsert_video(
            video_id=vid,
            artist_id="UC_bulk",
            url=f"https://youtube.com/watch?v={vid}",
            title=vid,
        )
    return ids


class TestChunkedInQueries:
    """Verify IN-query batching for all 3 affected methods."""

    # -- video_ids_with_transcripts --

    def test_transcripts_empty_list(self, store):
        assert store.video_ids_with_transcripts([]) == set()

    def test_transcripts_under_limit(self, store):
        ids = _seed_artist_and_videos(store, 50)
        # Add transcripts for first 20
        for vid in ids[:20]:
            store.save_transcript(video_id=vid, raw_text=f"text for {vid}")
        result = store.video_ids_with_transcripts(ids)
        assert result == set(ids[:20])

    def test_transcripts_over_limit(self, store):
        """1500 IDs exceeds _IN_BATCH_SIZE — must batch correctly."""
        n = _IN_BATCH_SIZE * 3  # 1500
        ids = _seed_artist_and_videos(store, n)
        # Add transcripts for every 3rd video
        transcribed = ids[::3]
        for vid in transcribed:
            store.save_transcript(video_id=vid, raw_text=f"text for {vid}")
        result = store.video_ids_with_transcripts(ids)
        assert result == set(transcribed)
        assert len(result) == 500

    def test_transcripts_exact_boundary(self, store):
        """Exactly _IN_BATCH_SIZE IDs — single batch, no off-by-one."""
        ids = _seed_artist_and_videos(store, _IN_BATCH_SIZE)
        for vid in ids[:10]:
            store.save_transcript(video_id=vid, raw_text=f"text for {vid}")
        result = store.video_ids_with_transcripts(ids)
        assert result == set(ids[:10])

    # -- video_ids_with_summary --

    def test_summaries_over_limit(self, store):
        n = _IN_BATCH_SIZE * 3
        ids = _seed_artist_and_videos(store, n)
        store.upsert_prompt(prompt_id="p1", name="p1", template="test")
        # Add transcripts + summaries for every 5th video
        summarized = ids[::5]
        for vid in summarized:
            store.save_transcript(video_id=vid, raw_text=f"text for {vid}")
            store.upsert_summary(video_id=vid, prompt_id="p1", content=f"summary {vid}")
        result = store.video_ids_with_summary(ids, "p1")
        assert result == set(summarized)
        assert len(result) == 300

    def test_summaries_empty_list(self, store):
        assert store.video_ids_with_summary([], "p1") == set()

    # -- get_unscored_summaries --

    def test_unscored_over_limit(self, store):
        n = _IN_BATCH_SIZE * 2 + 100  # 1100
        ids = _seed_artist_and_videos(store, n)
        store.upsert_prompt(prompt_id="p2", name="p2", template="test")
        # Add transcripts + summaries for first 600
        for vid in ids[:600]:
            store.save_transcript(video_id=vid, raw_text=f"text for {vid}")
            store.upsert_summary(video_id=vid, prompt_id="p2", content=f"summary {vid}")
        result = store.get_unscored_summaries("p2", ids)
        assert len(result) == 600
        returned_ids = {r["video_id"] for r in result}
        assert returned_ids == set(ids[:600])

    # -- provenance columns --

    def test_upsert_summary_with_provenance(self, store):
        """Provenance columns (model, strategy) are stored and retrievable."""
        store.upsert_artist(
            artist_id="UC_prov",
            name="Prov",
            channel_url="https://www.youtube.com/@prov",
            urllist_path="data/artists/UC_prov/artistUC_provProv-urllist.md",
        )
        store.upsert_video(
            video_id="pv1",
            artist_id="UC_prov",
            url="https://youtube.com/watch?v=pv1",
            title="PV1",
        )
        store.upsert_prompt(prompt_id="pp", name="PP", template="test")
        store.upsert_summary(
            video_id="pv1",
            prompt_id="pp",
            content="Summary.",
            model="mistral",
            strategy="map-reduce",
        )
        rows = store.get_summaries_for_video("pv1")
        assert len(rows) == 1
        assert rows[0]["model"] == "mistral"
        assert rows[0]["strategy"] == "map-reduce"

    def test_upsert_summary_provenance_defaults_to_none(self, store):
        """Backward compat: omitting model/strategy stores None."""
        store.upsert_artist(
            artist_id="UC_prov2",
            name="Prov2",
            channel_url="https://www.youtube.com/@prov2",
            urllist_path="data/artists/UC_prov2/artistUC_prov2Prov2-urllist.md",
        )
        store.upsert_video(
            video_id="pv2",
            artist_id="UC_prov2",
            url="https://youtube.com/watch?v=pv2",
            title="PV2",
        )
        store.upsert_prompt(prompt_id="pp2", name="PP2", template="test")
        store.upsert_summary(video_id="pv2", prompt_id="pp2", content="Summary.")
        rows = store.get_summaries_for_video("pv2")
        assert len(rows) == 1
        assert rows[0]["model"] is None
        assert rows[0]["strategy"] is None

    def test_upsert_summary_provenance_updated_on_overwrite(self, store):
        """Re-summarize updates provenance columns."""
        store.upsert_artist(
            artist_id="UC_prov3",
            name="Prov3",
            channel_url="https://www.youtube.com/@prov3",
            urllist_path="data/artists/UC_prov3/artistUC_prov3Prov3-urllist.md",
        )
        store.upsert_video(
            video_id="pv3",
            artist_id="UC_prov3",
            url="https://youtube.com/watch?v=pv3",
            title="PV3",
        )
        store.upsert_prompt(prompt_id="pp3", name="PP3", template="test")
        store.upsert_summary(
            video_id="pv3",
            prompt_id="pp3",
            content="V1.",
            model="mistral",
            strategy="auto",
        )
        store.upsert_summary(
            video_id="pv3",
            prompt_id="pp3",
            content="V2.",
            model="gpt-4o-mini",
            strategy="map-reduce",
        )
        rows = store.get_summaries_for_video("pv3")
        assert len(rows) == 1
        assert rows[0]["content"] == "V2."
        assert rows[0]["model"] == "gpt-4o-mini"
        assert rows[0]["strategy"] == "map-reduce"

    def test_unscored_without_video_ids(self, store):
        """No video_ids filter — should return all unscored for prompt."""
        store.upsert_artist(
            artist_id="UC_unscore",
            name="Unscore",
            channel_url="https://www.youtube.com/@unscore",
            urllist_path="data/artists/UC_unscore/artistUC_unscoreUnscore-urllist.md",
        )
        store.upsert_video(
            video_id="vu1",
            artist_id="UC_unscore",
            url="https://youtube.com/watch?v=vu1",
            title="VU1",
        )
        store.save_transcript(video_id="vu1", raw_text="text")
        store.upsert_prompt(prompt_id="p3", name="p3", template="t")
        store.upsert_summary(video_id="vu1", prompt_id="p3", content="s")
        result = store.get_unscored_summaries("p3")
        assert len(result) == 1
        assert result[0]["video_id"] == "vu1"


# ---------------------------------------------------------------------------
# Connection context manager tests
# ---------------------------------------------------------------------------


class TestConnectionContextManagers:
    def test_read_conn_returns_data(self, store):
        """_read_conn() works for read operations."""
        store.upsert_artist(
            artist_id="UC_ctx",
            name="Ctx",
            channel_url="https://www.youtube.com/@ctx",
            urllist_path="data/artists/UC_ctx/artistUC_ctxCtx-urllist.md",
        )
        # get_artist now uses _read_conn internally
        artist = store.get_artist("UC_ctx")
        assert artist is not None
        assert artist["name"] == "Ctx"

    def test_write_conn_commits(self, store):
        """_write_conn() auto-commits writes."""
        store.upsert_artist(
            artist_id="UC_wctx",
            name="WriteCtx",
            channel_url="https://www.youtube.com/@wctx",
            urllist_path="data/artists/UC_wctx/artistUC_wctxWriteCtx-urllist.md",
        )
        store.upsert_prompt(prompt_id="wctx_p", name="WP", template="t")
        # set_artist_default_prompt uses _write_conn internally
        store.set_artist_default_prompt("UC_wctx", "wctx_p")
        artist = store.get_artist("UC_wctx")
        assert artist["default_prompt_id"] == "wctx_p"


# ---------------------------------------------------------------------------
# Hash persistence and staleness detection
# ---------------------------------------------------------------------------


def _setup_hash_data(store):
    """Seed artist + video + transcript + prompt for hash tests."""
    store.upsert_artist(
        artist_id="@hash",
        name="Hash Test",
        channel_url="https://www.youtube.com/@hash",
        urllist_path="data/artists/@hash/urllist.md",
    )
    store.upsert_video(
        video_id="hv1",
        artist_id="@hash",
        url="https://www.youtube.com/watch?v=hv1",
        title="Hash Video",
    )
    store.save_transcript(video_id="hv1", raw_text="Transcript content.", format="vtt")
    store.upsert_prompt(prompt_id="hp1", name="Hash Prompt", template="Summarize: {video}")


class TestHashPersistence:
    def test_upsert_summary_with_hashes(self, store):
        """prompt_hash and transcript_hash are persisted."""
        _setup_hash_data(store)
        store.upsert_summary(
            video_id="hv1",
            prompt_id="hp1",
            content="A great summary.",
            prompt_hash="abc123",
            transcript_hash="def456",
        )
        rows = store.get_summaries_for_video("hv1")
        assert len(rows) == 1
        assert rows[0]["prompt_hash"] == "abc123"
        assert rows[0]["transcript_hash"] == "def456"

    def test_upsert_summary_hashes_default_to_none(self, store):
        """Omitting hashes stores NULL (backward compat)."""
        _setup_hash_data(store)
        store.upsert_summary(video_id="hv1", prompt_id="hp1", content="No hashes.")
        rows = store.get_summaries_for_video("hv1")
        assert rows[0]["prompt_hash"] is None
        assert rows[0]["transcript_hash"] is None

    def test_upsert_summary_hashes_updated_on_overwrite(self, store):
        """Re-summarizing updates hash columns."""
        _setup_hash_data(store)
        store.upsert_summary(
            video_id="hv1",
            prompt_id="hp1",
            content="V1.",
            prompt_hash="old_ph",
            transcript_hash="old_th",
        )
        store.upsert_summary(
            video_id="hv1",
            prompt_id="hp1",
            content="V2.",
            prompt_hash="new_ph",
            transcript_hash="new_th",
        )
        rows = store.get_summaries_for_video("hv1")
        assert rows[0]["prompt_hash"] == "new_ph"
        assert rows[0]["transcript_hash"] == "new_th"
        assert rows[0]["content"] == "V2."

    def test_migrate_hash_columns_idempotent(self, store):
        """Running migration twice does not error."""
        with store.transaction() as conn:
            store._migrate_hash_columns(conn)
            store._migrate_hash_columns(conn)
        # Still works after double-migration
        _setup_hash_data(store)
        store.upsert_summary(
            video_id="hv1",
            prompt_id="hp1",
            content="OK.",
            prompt_hash="ph",
            transcript_hash="th",
        )


class TestStalenessDetection:
    def test_count_stale_all_fresh(self, store):
        """Zero stale when all hashes match current data."""
        from yt_artist.hashing import content_hash

        _setup_hash_data(store)
        t_row = store.get_transcript("hv1")
        p_row = store.get_prompt("hp1")
        store.upsert_summary(
            video_id="hv1",
            prompt_id="hp1",
            content="Fresh.",
            prompt_hash=content_hash(p_row["template"]),
            transcript_hash=content_hash(t_row["raw_text"]),
        )
        counts = store.get_stale_summary_counts()
        assert counts["total_stale"] == 0

    def test_count_stale_prompt_changed(self, store):
        """Changing prompt template makes summary stale."""
        from yt_artist.hashing import content_hash

        _setup_hash_data(store)
        t_row = store.get_transcript("hv1")
        # Save with current hashes
        store.upsert_summary(
            video_id="hv1",
            prompt_id="hp1",
            content="Before.",
            prompt_hash=content_hash("Summarize: {video}"),
            transcript_hash=content_hash(t_row["raw_text"]),
        )
        # Now change the prompt template
        store.upsert_prompt(prompt_id="hp1", name="Hash Prompt", template="NEW template: {video}")
        counts = store.get_stale_summary_counts()
        assert counts["stale_prompt"] == 1
        assert counts["total_stale"] == 1

    def test_count_stale_transcript_changed(self, store):
        """Updating transcript makes summary stale."""
        from yt_artist.hashing import content_hash

        _setup_hash_data(store)
        p_row = store.get_prompt("hp1")
        store.upsert_summary(
            video_id="hv1",
            prompt_id="hp1",
            content="Before.",
            prompt_hash=content_hash(p_row["template"]),
            transcript_hash=content_hash("Transcript content."),
        )
        # Re-save transcript with different text
        store.save_transcript(video_id="hv1", raw_text="Updated transcript.", format="vtt")
        counts = store.get_stale_summary_counts()
        assert counts["stale_transcript"] == 1
        assert counts["total_stale"] == 1

    def test_count_stale_null_is_unknown(self, store):
        """NULL hashes count as stale_unknown."""
        _setup_hash_data(store)
        store.upsert_summary(video_id="hv1", prompt_id="hp1", content="Legacy.")
        counts = store.get_stale_summary_counts()
        assert counts["stale_unknown"] == 1
        assert counts["total_stale"] == 1

    def test_stale_video_ids_prompt_changed(self, store):
        """get_stale_video_ids returns IDs with changed prompts."""
        from yt_artist.hashing import content_hash

        _setup_hash_data(store)
        t_row = store.get_transcript("hv1")
        store.upsert_summary(
            video_id="hv1",
            prompt_id="hp1",
            content="S.",
            prompt_hash=content_hash("Summarize: {video}"),
            transcript_hash=content_hash(t_row["raw_text"]),
        )
        store.upsert_prompt(prompt_id="hp1", name="HP", template="CHANGED: {video}")
        result = store.get_stale_video_ids(["hv1"], "hp1")
        assert "hv1" in result["stale_prompt"]

    def test_stale_video_ids_empty_list(self, store):
        """Empty input returns empty result."""
        result = store.get_stale_video_ids([], "hp1")
        assert result == {"stale_prompt": [], "stale_transcript": [], "stale_unknown": []}


# ---------------------------------------------------------------------------
# Work Ledger tests
# ---------------------------------------------------------------------------


def _setup_ledger_video(store):
    """Seed a minimal artist+video for work ledger tests."""
    store.upsert_artist(
        artist_id="@WL",
        name="WL Artist",
        channel_url="https://www.youtube.com/@WL",
        urllist_path="data/artists/@WL/urllist.md",
    )
    store.upsert_video(
        video_id="wlv1",
        artist_id="@WL",
        url="https://www.youtube.com/watch?v=wlv1",
        title="Work Ledger Video",
    )


class TestWorkLedger:
    """Tests for the work_ledger table CRUD operations."""

    def test_log_work_and_query(self, store):
        """Round-trip: log_work inserts, get_work_history retrieves."""
        _setup_ledger_video(store)
        row_id = store.log_work(
            video_id="wlv1",
            operation="transcribe",
            status="success",
            started_at="2026-02-19T10:00:00Z",
            finished_at="2026-02-19T10:00:05Z",
            duration_ms=5000,
        )
        assert row_id > 0
        rows = store.get_work_history(video_id="wlv1")
        assert len(rows) == 1
        assert rows[0]["operation"] == "transcribe"
        assert rows[0]["status"] == "success"
        assert rows[0]["duration_ms"] == 5000
        assert rows[0]["video_title"] == "Work Ledger Video"

    def test_log_work_with_error(self, store):
        """Failed operations include error_message."""
        _setup_ledger_video(store)
        store.log_work(
            video_id="wlv1",
            operation="summarize",
            status="failed",
            started_at="2026-02-19T10:00:00Z",
            finished_at="2026-02-19T10:00:01Z",
            duration_ms=1000,
            error_message="LLM timeout",
            model="llama3",
            prompt_id="p1",
            strategy="auto",
        )
        rows = store.get_work_history(video_id="wlv1")
        assert len(rows) == 1
        assert rows[0]["error_message"] == "LLM timeout"
        assert rows[0]["model"] == "llama3"
        assert rows[0]["prompt_id"] == "p1"

    def test_get_work_history_by_artist(self, store):
        """artist_id filter joins through videos table."""
        _setup_ledger_video(store)
        # Second artist
        store.upsert_artist(
            artist_id="@WL2",
            name="WL2",
            channel_url="https://www.youtube.com/@WL2",
            urllist_path="data/artists/@WL2/urllist.md",
        )
        store.upsert_video(
            video_id="wlv2",
            artist_id="@WL2",
            url="https://www.youtube.com/watch?v=wlv2",
            title="Other Video",
        )
        store.log_work(
            video_id="wlv1",
            operation="transcribe",
            status="success",
            started_at="2026-02-19T10:00:00Z",
            finished_at="2026-02-19T10:00:01Z",
        )
        store.log_work(
            video_id="wlv2",
            operation="transcribe",
            status="success",
            started_at="2026-02-19T10:00:02Z",
            finished_at="2026-02-19T10:00:03Z",
        )
        rows = store.get_work_history(artist_id="@WL")
        assert len(rows) == 1
        assert rows[0]["video_id"] == "wlv1"

    def test_get_work_history_by_operation(self, store):
        """operation filter works."""
        _setup_ledger_video(store)
        store.log_work(
            video_id="wlv1",
            operation="transcribe",
            status="success",
            started_at="2026-02-19T10:00:00Z",
            finished_at="2026-02-19T10:00:01Z",
        )
        store.log_work(
            video_id="wlv1",
            operation="summarize",
            status="success",
            started_at="2026-02-19T10:00:02Z",
            finished_at="2026-02-19T10:00:03Z",
        )
        rows = store.get_work_history(video_id="wlv1", operation="summarize")
        assert len(rows) == 1
        assert rows[0]["operation"] == "summarize"

    def test_get_work_history_limit(self, store):
        """limit parameter caps results."""
        _setup_ledger_video(store)
        for i in range(10):
            store.log_work(
                video_id="wlv1",
                operation="score",
                status="success",
                started_at=f"2026-02-19T10:00:{i:02d}Z",
                finished_at=f"2026-02-19T10:00:{i + 1:02d}Z",
            )
        rows = store.get_work_history(video_id="wlv1", limit=5)
        assert len(rows) == 5

    def test_multiple_entries_per_video(self, store):
        """Append-only: same video can have multiple ledger entries."""
        _setup_ledger_video(store)
        store.log_work(
            video_id="wlv1",
            operation="transcribe",
            status="success",
            started_at="2026-02-19T10:00:00Z",
            finished_at="2026-02-19T10:00:01Z",
        )
        store.log_work(
            video_id="wlv1",
            operation="summarize",
            status="success",
            started_at="2026-02-19T10:00:02Z",
            finished_at="2026-02-19T10:00:03Z",
        )
        store.log_work(
            video_id="wlv1",
            operation="score",
            status="failed",
            started_at="2026-02-19T10:00:04Z",
            finished_at="2026-02-19T10:00:05Z",
            error_message="connection refused",
        )
        rows = store.get_work_history(video_id="wlv1")
        assert len(rows) == 3

    def test_count_work_ledger(self, store):
        """count_work_ledger returns operation/status breakdown."""
        _setup_ledger_video(store)
        store.log_work(
            video_id="wlv1",
            operation="transcribe",
            status="success",
            started_at="2026-02-19T10:00:00Z",
            finished_at="2026-02-19T10:00:01Z",
        )
        store.log_work(
            video_id="wlv1",
            operation="summarize",
            status="success",
            started_at="2026-02-19T10:00:02Z",
            finished_at="2026-02-19T10:00:03Z",
        )
        store.log_work(
            video_id="wlv1",
            operation="summarize",
            status="failed",
            started_at="2026-02-19T10:00:04Z",
            finished_at="2026-02-19T10:00:05Z",
        )
        counts = store.count_work_ledger()
        assert counts["total"] == 3
        assert counts["transcribe_success"] == 1
        assert counts["summarize_success"] == 1
        assert counts["summarize_failed"] == 1

    def test_migrate_work_ledger_idempotent(self, store):
        """Running migration twice does not error."""
        with store.transaction() as conn:
            store._migrate_work_ledger_table(conn)
            store._migrate_work_ledger_table(conn)
