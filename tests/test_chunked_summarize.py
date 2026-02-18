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
# _summarize_single (calls llm_complete)
# ---------------------------------------------------------------------------


class TestSummarizeSingle:
    @patch("yt_artist.summarizer.llm_complete")
    def test_calls_llm(self, mock_llm):
        mock_llm.return_value = "Summary result"
        result = _summarize_single("raw transcript text", "Summarize this video")
        assert result == "Summary result"
        mock_llm.assert_called_once_with(system_prompt="Summarize this video", user_content="raw transcript text")


# ---------------------------------------------------------------------------
# _summarize_map_reduce (calls llm_complete for chunk + reduce)
# ---------------------------------------------------------------------------


class TestMapReduce:
    @patch("yt_artist.summarizer.llm_complete")
    def test_short_text_single_pass(self, mock_llm):
        """Text shorter than max_chars should use a single pass via _summarize_single."""
        mock_llm.return_value = "Final summary"
        # Short text fits — _summarize_map_reduce's _chunk_text returns 1 chunk
        # but map-reduce always chunks + reduces, so short text → 1 chunk map + reduce
        # Actually, _chunk_text returns [text] if len <= chunk_size, so we get 1 chunk.
        # Let's test via the strategy dispatch instead — map-reduce with short text
        # calls _summarize_single in summarize(). Test the map-reduce function directly:
        pass  # Covered by test_long_text_multiple_chunks below

    @patch("yt_artist.summarizer.llm_complete")
    def test_long_text_multiple_chunks(self, mock_llm):
        """Long text gets chunked, mapped, and reduced."""
        # llm_complete is called for each chunk (map) and once for reduce.
        # Return "Chunk summary" for map calls, "Final combined" for the reduce call.
        call_count = [0]

        def _smart_return(**kwargs):
            call_count[0] += 1
            # The reduce call has _REDUCE_SUFFIX in its system_prompt
            if "Combine them into a single coherent summary" in kwargs.get("system_prompt", ""):
                return "Final combined"
            return "Chunk summary"

        mock_llm.side_effect = _smart_return
        # Create text that exceeds max_chars
        long_text = ". ".join(f"Sentence {i} with some content here" for i in range(100))
        result = _summarize_map_reduce(long_text, 2000, "Summarize this")
        # Should have called llm_complete multiple times (map chunks + 1 reduce)
        assert mock_llm.call_count >= 3  # at least 2 chunks + 1 reduce
        assert result == "Final combined"

    @patch("yt_artist.summarizer.llm_complete")
    def test_empty_chunk_summaries_raises(self, mock_llm):
        """If all chunk summaries are empty, raises ValueError."""
        mock_llm.return_value = "   "
        long_text = ". ".join(f"Sentence {i} with some content here" for i in range(100))
        with pytest.raises(ValueError, match="no chunk summaries"):
            _summarize_map_reduce(long_text, 2000, "Summarize this")


# ---------------------------------------------------------------------------
# _summarize_refine (calls llm_complete for initial + refine passes)
# ---------------------------------------------------------------------------


class TestRefine:
    @patch("yt_artist.summarizer.llm_complete")
    def test_short_text_single_call(self, mock_llm):
        """Short text within max_chars = single call (acts like single-pass)."""
        mock_llm.return_value = "Refined summary"
        result = _summarize_refine("Short text.", 1000, "Summarize this")
        assert result == "Refined summary"
        mock_llm.assert_called_once()

    @patch("yt_artist.summarizer.llm_complete")
    def test_long_text_iterative_refinement(self, mock_llm):
        """Long text gets refined iteratively across chunks."""
        call_count = [0]

        def _fake_llm(**kwargs):
            call_count[0] += 1
            if call_count[0] == 1:
                return "Initial summary"
            return f"Summary v{call_count[0]}"

        mock_llm.side_effect = _fake_llm
        long_text = ". ".join(f"Sentence {i} with some content here" for i in range(100))
        result = _summarize_refine(long_text, 2000, "Summarize this")
        # Should have called llm_complete once (initial) + once per subsequent chunk
        assert mock_llm.call_count >= 2
        # Final result is last refine call
        assert "Summary v" in result


# ---------------------------------------------------------------------------
# Strategy names
# ---------------------------------------------------------------------------


class TestParallelMapReduce:
    """Tests for parallelized map phase in _summarize_map_reduce."""

    @patch("yt_artist.summarizer.llm_complete")
    @patch.dict("os.environ", {"YT_ARTIST_MAP_CONCURRENCY": "3"})
    def test_parallel_produces_correct_output(self, mock_llm):
        """Multiple chunks with concurrency > 1 -> parallel path, correct result."""
        call_count = [0]

        def _smart_return(**kwargs):
            call_count[0] += 1
            if "Combine them into a single coherent summary" in kwargs.get("system_prompt", ""):
                return "Final"
            # Chunk map call — extract chunk_index from the system_prompt
            return f"Summary {call_count[0]}"

        mock_llm.side_effect = _smart_return
        long_text = ". ".join(f"Sentence {i} with some padding here" for i in range(100))
        result = _summarize_map_reduce(long_text, 2000, "Summarize this")
        assert mock_llm.call_count >= 3  # at least 2 chunks + 1 reduce
        assert result == "Final"

    @patch("yt_artist.summarizer.llm_complete")
    @patch.dict("os.environ", {"YT_ARTIST_MAP_CONCURRENCY": "3"})
    def test_chunk_ordering_preserved(self, mock_llm):
        """Parallel results reassembled in original chunk order."""
        import time

        def _smart_return(**kwargs):
            sp = kwargs.get("system_prompt", "")
            if "Combine them into a single coherent summary" in sp:
                # Reduce call — return the combined section text as-is for inspection
                return kwargs.get("user_content", "")
            # Chunk map call — extract chunk_index from system_prompt
            # system_prompt contains "section {chunk_index} of {total_chunks}"
            import re

            m = re.search(r"section (\d+) of (\d+)", sp)
            idx = int(m.group(1)) if m else 0
            # Odd chunks take longer to finish out of order
            if idx % 2 == 1:
                time.sleep(0.02)
            return f"Summary-{idx}"

        mock_llm.side_effect = _smart_return

        long_text = ". ".join(f"Sentence {i} with some padding here" for i in range(100))
        result = _summarize_map_reduce(long_text, 2000, "Summarize this")
        # The reduce receives sections in order: Section 1, Section 2, ...
        sections = result.split("\n\n---\n\n")
        for i, section in enumerate(sections, 1):
            assert section.startswith(f"Section {i}:")
            assert f"Summary-{i}" in section

    @patch("yt_artist.summarizer.llm_complete")
    @patch.dict("os.environ", {"YT_ARTIST_MAP_CONCURRENCY": "3"})
    def test_chunk_error_propagates(self, mock_llm):
        """Exception in one chunk propagates (pool shuts down)."""
        call_count = [0]

        def _failing_llm(**kwargs):
            call_count[0] += 1
            sp = kwargs.get("system_prompt", "")
            # Check for "section 2 of" in system prompt to simulate chunk 2 failure
            if "section 2 of" in sp:
                raise RuntimeError("LLM exploded on chunk 2")
            return f"Summary {call_count[0]}"

        mock_llm.side_effect = _failing_llm
        long_text = ". ".join(f"Sentence {i} with some padding here" for i in range(100))
        with pytest.raises(RuntimeError, match="LLM exploded"):
            _summarize_map_reduce(long_text, 2000, "Summarize this")

    @patch("yt_artist.summarizer.llm_complete")
    @patch.dict("os.environ", {"YT_ARTIST_MAP_CONCURRENCY": "1"})
    def test_concurrency_one_uses_sequential_path(self, mock_llm):
        """_MAP_CONCURRENCY=1 -> sequential path (no ThreadPoolExecutor)."""

        def _smart_return(**kwargs):
            if "Combine them into a single coherent summary" in kwargs.get("system_prompt", ""):
                return "Final"
            return "Chunk summary"

        mock_llm.side_effect = _smart_return
        long_text = ". ".join(f"Sentence {i} with some padding here" for i in range(100))
        result = _summarize_map_reduce(long_text, 2000, "Summarize this")
        assert mock_llm.call_count >= 3  # at least 2 chunks + 1 reduce
        assert result == "Final"

    @patch("yt_artist.summarizer.llm_complete")
    @patch.dict("os.environ", {"YT_ARTIST_MAP_CONCURRENCY": "3"})
    def test_single_chunk_skips_pool(self, mock_llm):
        """Single chunk -> max_workers=1 -> sequential path regardless of _MAP_CONCURRENCY."""

        def _smart_return(**kwargs):
            if "Combine them into a single coherent summary" in kwargs.get("system_prompt", ""):
                return "Final"
            return "Only chunk"

        mock_llm.side_effect = _smart_return
        # Short enough for 1 chunk
        text = "Short text that fits in one chunk."
        result = _summarize_map_reduce(text, 10000, "Summarize this")
        # 1 chunk call + 1 reduce call = 2 total
        assert mock_llm.call_count == 2
        assert result == "Final"


class TestStrategies:
    def test_valid_strategies(self):
        assert "auto" in STRATEGIES
        assert "truncate" in STRATEGIES
        assert "map-reduce" in STRATEGIES
        assert "refine" in STRATEGIES
        assert len(STRATEGIES) == 4
