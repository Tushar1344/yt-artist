"""Tests for chunking, map-reduce, and refine summarization strategies."""

from unittest.mock import patch

import pytest

from yt_artist.summarizer import (
    STRATEGIES,
    _chunk_text,
    _fill_template,
    _get_strategy,
    _summarize_map_reduce,
    _summarize_refine,
    _summarize_single,
)

# ---------------------------------------------------------------------------
# _chunk_text
# ---------------------------------------------------------------------------


class TestChunkText:
    def test_short_text_single_chunk(self):
        """Text shorter than chunk_size returns a single chunk."""
        text = "Hello world. This is short."
        chunks = _chunk_text(text, 1000)
        assert len(chunks) == 1
        assert chunks[0] == text

    def test_splits_into_multiple_chunks(self):
        """Long text gets split into multiple chunks."""
        # 100 sentences, each ~20 chars = ~2000 chars total
        text = ". ".join(f"Sentence number {i}" for i in range(100))
        chunks = _chunk_text(text, 200, overlap=50)
        assert len(chunks) > 1
        # All text should be covered
        combined = " ".join(chunks)
        for i in range(100):
            assert f"Sentence number {i}" in combined

    def test_chunks_have_overlap(self):
        """Adjacent chunks share overlapping content."""
        text = ". ".join(f"Word{i}" for i in range(50))
        chunks = _chunk_text(text, 100, overlap=30)
        if len(chunks) >= 2:
            # End of chunk 0 should overlap with beginning of chunk 1
            end_of_first = chunks[0][-30:]
            assert any(word in chunks[1][:60] for word in end_of_first.split())

    def test_never_returns_empty(self):
        """Even empty-ish text returns at least one chunk."""
        chunks = _chunk_text("   ", 100)
        assert len(chunks) >= 1

    def test_sentence_boundary_splitting(self):
        """Chunks break at sentence boundaries ('. ' or '\\n')."""
        text = "First sentence here. Second sentence here. Third sentence here. Fourth sentence here."
        chunks = _chunk_text(text, 45, overlap=5)
        # Each chunk should end at or near a sentence boundary
        for chunk in chunks[:-1]:  # last chunk can end anywhere
            assert chunk.rstrip().endswith(".") or chunk.rstrip().endswith(".")

    def test_exact_chunk_size(self):
        """Text exactly at chunk_size returns single chunk."""
        text = "x" * 500
        chunks = _chunk_text(text, 500)
        assert len(chunks) == 1
        assert chunks[0] == text


# ---------------------------------------------------------------------------
# _fill_template
# ---------------------------------------------------------------------------


class TestFillTemplate:
    def test_replaces_all_placeholders(self):
        tpl = "Artist: {artist}, Video: {video}, Intent: {intent}, Audience: {audience}"
        result = _fill_template(tpl, artist="TestArt", video="TestVid", intent="TestInt", audience="TestAud")
        assert "TestArt" in result
        assert "TestVid" in result
        assert "TestInt" in result
        assert "TestAud" in result

    def test_unknown_placeholders_preserved(self):
        """Unknown placeholders like {foo} are left as-is."""
        tpl = "Hello {artist}, {foo} bar"
        result = _fill_template(tpl, artist="World")
        assert result == "Hello World, {foo} bar"


# ---------------------------------------------------------------------------
# _get_strategy
# ---------------------------------------------------------------------------


class TestGetStrategy:
    def test_default_is_auto(self):
        with patch.dict("os.environ", {}, clear=False):
            # Remove the env var if it exists
            import os

            os.environ.pop("YT_ARTIST_SUMMARIZE_STRATEGY", None)
            assert _get_strategy() == "auto"

    def test_env_override(self):
        with patch.dict("os.environ", {"YT_ARTIST_SUMMARIZE_STRATEGY": "refine"}):
            assert _get_strategy() == "refine"

    def test_invalid_falls_back_to_auto(self):
        with patch.dict("os.environ", {"YT_ARTIST_SUMMARIZE_STRATEGY": "invalid"}):
            assert _get_strategy() == "auto"


# ---------------------------------------------------------------------------
# _summarize_single
# ---------------------------------------------------------------------------


class TestSummarizeSingle:
    @patch("yt_artist.summarizer.complete")
    def test_calls_complete(self, mock_complete):
        mock_complete.return_value = "Summary result"
        result = _summarize_single("system prompt", "raw transcript text")
        assert result == "Summary result"
        mock_complete.assert_called_once()
        call_kwargs = mock_complete.call_args
        assert "Transcript:" in call_kwargs[1]["user_content"] or "Transcript:" in call_kwargs[0][1]


# ---------------------------------------------------------------------------
# _summarize_map_reduce
# ---------------------------------------------------------------------------


class TestMapReduce:
    @patch("yt_artist.summarizer.complete")
    def test_short_text_single_pass(self, mock_complete):
        """Text shorter than max_chars should use a single pass (reduce only)."""
        mock_complete.return_value = "Final summary"
        result = _summarize_map_reduce("system prompt", "Short text.", 1000)
        # Should have called complete once (single chunk map + reduce)
        assert result == "Final summary"

    @patch("yt_artist.summarizer.complete")
    def test_long_text_multiple_chunks(self, mock_complete):
        """Long text gets chunked, mapped, and reduced."""
        mock_complete.return_value = "OK"
        # Create text that exceeds max_chars — each sentence ~35 chars, 100 sentences ≈ 3500 chars
        long_text = ". ".join(f"Sentence {i} with some content here" for i in range(100))
        result = _summarize_map_reduce("system prompt", long_text, 2000)
        # Should have called complete multiple times (map + reduce)
        assert mock_complete.call_count >= 2
        assert result == "OK"

    @patch("yt_artist.summarizer.complete")
    def test_empty_chunk_summaries_raises(self, mock_complete):
        """If all chunk summaries are empty, raises ValueError."""
        mock_complete.return_value = "   "
        long_text = ". ".join(f"Sentence {i} with some content here" for i in range(100))
        with pytest.raises(ValueError, match="no chunk summaries"):
            _summarize_map_reduce("system prompt", long_text, 2000)


# ---------------------------------------------------------------------------
# _summarize_refine
# ---------------------------------------------------------------------------


class TestRefine:
    @patch("yt_artist.summarizer.complete")
    def test_short_text_single_call(self, mock_complete):
        """Short text within max_chars = single call (acts like single-pass)."""
        mock_complete.return_value = "Refined summary"
        result = _summarize_refine("system prompt", "Short text.", 1000)
        assert result == "Refined summary"

    @patch("yt_artist.summarizer.complete")
    def test_long_text_iterative_refinement(self, mock_complete):
        """Long text gets refined iteratively across chunks."""
        call_count = [0]

        def _fake_complete(**kwargs):
            call_count[0] += 1
            return f"Summary v{call_count[0]}"

        mock_complete.side_effect = _fake_complete
        long_text = ". ".join(f"Sentence {i} with some content here" for i in range(100))
        result = _summarize_refine("system prompt", long_text, 2000)
        # Should call complete multiple times (initial + refine per chunk)
        assert call_count[0] >= 2
        # Last call's result should be the final summary
        assert result == f"Summary v{call_count[0]}"


# ---------------------------------------------------------------------------
# Strategy names
# ---------------------------------------------------------------------------


class TestStrategies:
    def test_valid_strategies(self):
        assert "auto" in STRATEGIES
        assert "truncate" in STRATEGIES
        assert "map-reduce" in STRATEGIES
        assert "refine" in STRATEGIES
        assert len(STRATEGIES) == 4
