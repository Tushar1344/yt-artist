"""Tests for summarizer: mock LLM response; assert Summary row and placeholder substitution."""
from unittest.mock import patch

import pytest

from yt_artist import storage
from yt_artist.summarizer import _fill_template, summarize


def test_fill_template():
    t = "For {audience}: summarize {video} by {artist}. Intent: {intent}."
    out = _fill_template(
        t,
        artist="Band",
        video="Song Session",
        intent="highlights",
        audience="fans",
    )
    assert "fans" in out
    assert "Song Session" in out
    assert "Band" in out
    assert "highlights" in out


def test_fill_template_missing_placeholders_replaced_empty():
    out = _fill_template("Only {artist} here.", artist="X", video="", intent="", audience="")
    assert out == "Only X here."


def test_summarize_saves_to_db(store):
    store.upsert_artist(
        artist_id="UC_s",
        name="Artist Name",
        channel_url="https://www.youtube.com/@s",
        urllist_path="data/artists/UC_s/artistUC_sArtist_Name-urllist.md",
    )
    store.upsert_video(
        video_id="sv1",
        artist_id="UC_s",
        url="https://www.youtube.com/watch?v=sv1",
        title="Video Title",
    )
    store.save_transcript(video_id="sv1", raw_text="Transcript line one. Line two.", format="vtt")
    store.upsert_prompt(
        prompt_id="p1",
        name="Short summary",
        template="Summarize for {audience}. Video: {video}. Artist: {artist}.",
    )

    with patch("yt_artist.summarizer.complete", return_value="This is the summary."):
        out = summarize("sv1", "p1", store)

    assert out == "sv1:p1"
    rows = store.get_summaries_for_video("sv1")
    assert len(rows) == 1
    assert rows[0]["content"] == "This is the summary."
    assert rows[0]["prompt_id"] == "p1"


def test_summarize_overwrites_same_video_prompt(store):
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
        title="V2",
    )
    store.save_transcript(video_id="sv2", raw_text="Transcript.", format="vtt")
    store.upsert_prompt(prompt_id="p2", name="P2", template="Sum: {video}")
    store.upsert_summary(video_id="sv2", prompt_id="p2", content="First.")

    with patch("yt_artist.summarizer.complete", return_value="Second summary."):
        summarize("sv2", "p2", store)

    rows = store.get_summaries_for_video("sv2")
    assert len(rows) == 1
    assert rows[0]["content"] == "Second summary."


def test_summarize_no_transcript_raises(store):
    store.upsert_artist(
        artist_id="UC_s",
        name="S",
        channel_url="https://www.youtube.com/@s",
        urllist_path="data/artists/UC_s/artistUC_sS-urllist.md",
    )
    store.upsert_video(
        video_id="sv3",
        artist_id="UC_s",
        url="https://www.youtube.com/watch?v=sv3",
        title="V3",
    )
    store.upsert_prompt(prompt_id="p3", name="P3", template="Sum: {video}")

    with pytest.raises(ValueError, match="No transcript"):
        summarize("sv3", "p3", store)


def test_summarize_no_prompt_raises(store):
    store.upsert_artist(
        artist_id="UC_s",
        name="S",
        channel_url="https://www.youtube.com/@s",
        urllist_path="data/artists/UC_s/artistUC_sS-urllist.md",
    )
    store.upsert_video(
        video_id="sv4",
        artist_id="UC_s",
        url="https://www.youtube.com/watch?v=sv4",
        title="V4",
    )
    store.save_transcript(video_id="sv4", raw_text="Text.", format="vtt")

    with pytest.raises(ValueError, match="No prompt"):
        summarize("sv4", "nonexistent", store)
