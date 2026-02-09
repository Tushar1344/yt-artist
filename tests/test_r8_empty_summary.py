"""Tests for R8: Empty LLM responses must not be persisted as summaries."""
from unittest.mock import patch

import pytest

from yt_artist.summarizer import summarize


def _setup_video_with_transcript(store):
    """Helper: create artist + video + transcript + prompt for summary tests."""
    store.upsert_artist(
        artist_id="UC_empty",
        name="Empty Test",
        channel_url="https://www.youtube.com/@empty",
        urllist_path="data/artists/UC_empty/artistUC_emptyEmpty_Test-urllist.md",
    )
    store.upsert_video(
        video_id="ev1",
        artist_id="UC_empty",
        url="https://www.youtube.com/watch?v=ev1",
        title="Empty Summary Video",
    )
    store.save_transcript(video_id="ev1", raw_text="Some transcript text.", format="vtt")
    store.upsert_prompt(
        prompt_id="p_empty",
        name="Empty test prompt",
        template="Summarize: {video}",
    )


def test_empty_llm_response_raises(store):
    """LLM returning empty string must raise ValueError, not persist."""
    _setup_video_with_transcript(store)

    with patch("yt_artist.summarizer.complete", return_value=""):
        with pytest.raises(ValueError, match="empty summary"):
            summarize("ev1", "p_empty", store)


def test_whitespace_llm_response_raises(store):
    """LLM returning only whitespace must raise ValueError."""
    _setup_video_with_transcript(store)

    with patch("yt_artist.summarizer.complete", return_value="   \n  \t  "):
        with pytest.raises(ValueError, match="empty summary"):
            summarize("ev1", "p_empty", store)


def test_no_summary_persisted_on_empty(store):
    """After empty-response ValueError, no summary row should exist in DB."""
    _setup_video_with_transcript(store)

    with patch("yt_artist.summarizer.complete", return_value=""):
        with pytest.raises(ValueError):
            summarize("ev1", "p_empty", store)

    rows = store.get_summaries_for_video("ev1")
    assert len(rows) == 0
