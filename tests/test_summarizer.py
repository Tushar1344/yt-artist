"""Tests for summarizer: DB prompt template rendering, LLM calls, strategy selection."""

from unittest.mock import patch

import pytest

from yt_artist.summarizer import _fill_template, summarize

# ---------------------------------------------------------------------------
# _fill_template tests
# ---------------------------------------------------------------------------


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


def test_fill_template_unknown_placeholders_preserved():
    """Placeholders not in {artist,video,intent,audience} should remain as-is."""
    out = _fill_template("{artist} likes {unknown}.", artist="A", video="", intent="", audience="")
    assert out == "A likes {unknown}."


# ---------------------------------------------------------------------------
# Helper: set up test data in store
# ---------------------------------------------------------------------------


def _setup_video(store, video_id="sv1", artist_id="UC_s", title="Video Title", transcript="Transcript."):
    store.upsert_artist(
        artist_id=artist_id,
        name="Artist Name",
        channel_url=f"https://www.youtube.com/@{artist_id}",
        urllist_path=f"data/artists/{artist_id}/urllist.md",
    )
    store.upsert_video(
        video_id=video_id,
        artist_id=artist_id,
        url=f"https://www.youtube.com/watch?v={video_id}",
        title=title,
    )
    store.save_transcript(video_id=video_id, raw_text=transcript, format="vtt")


# ---------------------------------------------------------------------------
# summarize() — single-pass (default for short transcripts)
# ---------------------------------------------------------------------------


class TestSummarizeSinglePass:
    @patch("yt_artist.summarizer.llm_complete", return_value="This is the summary.")
    def test_saves_to_db(self, mock_llm, store):
        _setup_video(store)
        store.upsert_prompt(
            prompt_id="p1",
            name="Short summary",
            template="Summarize for {audience}. Video: {video}. Artist: {artist}.",
        )
        out = summarize("sv1", "p1", store)
        assert out == "sv1:p1"
        rows = store.get_summaries_for_video("sv1")
        assert len(rows) == 1
        assert rows[0]["content"] == "This is the summary."
        assert rows[0]["prompt_id"] == "p1"

    @patch("yt_artist.summarizer.llm_complete", return_value="This is the summary.")
    def test_uses_rendered_template_as_system_prompt(self, mock_llm, store):
        """The DB template should be rendered and passed as system_prompt to llm_complete."""
        _setup_video(store, title="My Video")
        store.upsert_prompt(prompt_id="p1", name="P1", template="Summarize {video} by {artist}.")
        summarize("sv1", "p1", store)
        # Verify llm_complete was called with rendered template
        args, kwargs = mock_llm.call_args
        assert "My Video" in kwargs["system_prompt"]
        assert "Artist Name" in kwargs["system_prompt"]

    @patch("yt_artist.summarizer.llm_complete", return_value="Summary with intent.")
    def test_intent_and_audience_rendered(self, mock_llm, store):
        """intent_override and audience_override should appear in the system prompt."""
        _setup_video(store)
        store.upsert_prompt(
            prompt_id="p1",
            name="P1",
            template="Intent: {intent}. Audience: {audience}. Summarize {video}.",
        )
        summarize("sv1", "p1", store, intent_override="key takeaways", audience_override="developers")
        args, kwargs = mock_llm.call_args
        assert "key takeaways" in kwargs["system_prompt"]
        assert "developers" in kwargs["system_prompt"]

    @patch("yt_artist.summarizer.llm_complete", return_value="Custom summary.")
    def test_custom_template_changes_prompt(self, mock_llm, store):
        """A custom template should produce a different system prompt than default."""
        _setup_video(store)
        store.upsert_prompt(
            prompt_id="custom",
            name="Custom",
            template="You are a tech reviewer. Rate {video} by {artist}.",
        )
        summarize("sv1", "custom", store)
        args, kwargs = mock_llm.call_args
        assert "tech reviewer" in kwargs["system_prompt"]

    @patch("yt_artist.summarizer.llm_complete", return_value="Second summary.")
    def test_overwrites_same_video_prompt(self, mock_llm, store):
        _setup_video(store, video_id="sv2", transcript="Transcript.")
        store.upsert_prompt(prompt_id="p2", name="P2", template="Sum: {video}")
        store.upsert_summary(video_id="sv2", prompt_id="p2", content="First.")
        summarize("sv2", "p2", store)
        rows = store.get_summaries_for_video("sv2")
        assert len(rows) == 1
        assert rows[0]["content"] == "Second summary."

    @patch("yt_artist.summarizer.llm_complete", return_value="Summary.")
    def test_transcript_sent_as_user_content(self, mock_llm, store):
        """The transcript text should be sent as user_content, not embedded in the system prompt."""
        _setup_video(store, transcript="Full transcript text here.")
        store.upsert_prompt(prompt_id="p1", name="P1", template="Summarize.")
        summarize("sv1", "p1", store)
        args, kwargs = mock_llm.call_args
        assert kwargs["user_content"] == "Full transcript text here."


# ---------------------------------------------------------------------------
# summarize() — error cases
# ---------------------------------------------------------------------------


class TestSummarizeErrors:
    def test_no_transcript_raises(self, store):
        store.upsert_artist(
            artist_id="UC_s",
            name="S",
            channel_url="https://www.youtube.com/@s",
            urllist_path="data/artists/UC_s/urllist.md",
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

    def test_no_prompt_raises(self, store):
        _setup_video(store, video_id="sv4", transcript="Text.")
        with pytest.raises(ValueError, match="No prompt"):
            summarize("sv4", "nonexistent", store)

    @patch("yt_artist.summarizer.llm_complete", return_value="   ")
    def test_empty_summary_raises(self, mock_llm, store):
        _setup_video(store, video_id="sv5", transcript="Some text.")
        store.upsert_prompt(prompt_id="p1", name="P1", template="Summarize.")
        with pytest.raises(ValueError, match="empty summary"):
            summarize("sv5", "p1", store)


# ---------------------------------------------------------------------------
# summarize() — map-reduce strategy
# ---------------------------------------------------------------------------


class TestSummarizeMapReduce:
    @patch("yt_artist.summarizer.llm_complete")
    def test_map_reduce_calls_llm_multiple_times(self, mock_llm, store):
        """Map-reduce should call LLM once per chunk + once for reduce."""
        # Create a long transcript that exceeds max_chars
        long_text = "Word " * 10000  # ~50k chars
        _setup_video(store, video_id="sv_mr", transcript=long_text)
        store.upsert_prompt(prompt_id="p1", name="P1", template="Summarize {video}.")

        mock_llm.return_value = "Chunk summary."
        summarize("sv_mr", "p1", store, strategy="map-reduce")
        # Should have multiple calls: chunks + final reduce
        assert mock_llm.call_count >= 3  # at least 2 chunks + 1 reduce

    @patch("yt_artist.summarizer.llm_complete")
    def test_map_reduce_reduce_uses_user_template(self, mock_llm, store):
        """The reduce phase should use the user's DB template (not internal chunk prompt)."""
        long_text = "Word " * 10000
        _setup_video(store, video_id="sv_mr2", transcript=long_text)
        store.upsert_prompt(prompt_id="custom_mr", name="MR", template="You are a tech analyst for {video}.")

        mock_llm.return_value = "Summary."
        summarize("sv_mr2", "custom_mr", store, strategy="map-reduce")

        # The last call is the reduce — should contain the user's template text
        last_call = mock_llm.call_args_list[-1]
        assert "tech analyst" in last_call.kwargs["system_prompt"]


# ---------------------------------------------------------------------------
# summarize() — refine strategy
# ---------------------------------------------------------------------------


class TestSummarizeRefine:
    @patch("yt_artist.summarizer.llm_complete")
    def test_refine_calls_llm_iteratively(self, mock_llm, store):
        """Refine should call LLM once for initial + once per subsequent chunk."""
        long_text = "Word " * 10000
        _setup_video(store, video_id="sv_ref", transcript=long_text)
        store.upsert_prompt(prompt_id="p1", name="P1", template="Summarize {video}.")

        mock_llm.return_value = "Refined summary."
        summarize("sv_ref", "p1", store, strategy="refine")
        assert mock_llm.call_count >= 2  # initial + at least 1 refine

    @patch("yt_artist.summarizer.llm_complete")
    def test_refine_first_chunk_uses_user_template(self, mock_llm, store):
        """The first chunk in refine should use the user's DB template."""
        long_text = "Word " * 10000
        _setup_video(store, video_id="sv_ref2", transcript=long_text)
        store.upsert_prompt(prompt_id="custom_ref", name="Ref", template="You review talks for {video}.")

        mock_llm.return_value = "Summary."
        summarize("sv_ref2", "custom_ref", store, strategy="refine")

        # First call should use user's template
        first_call = mock_llm.call_args_list[0]
        assert "review talks" in first_call.kwargs["system_prompt"]
