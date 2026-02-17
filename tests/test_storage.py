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
