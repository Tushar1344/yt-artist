"""Tests for prompts.py — BAML adapter layer and prompt content assertions."""

import os
from unittest.mock import MagicMock, patch

# ---------------------------------------------------------------------------
# BAML adapter function tests (mock baml_client.b)
# ---------------------------------------------------------------------------


class TestSummarizeAdapters:
    @patch("yt_artist.prompts.b")
    def test_summarize_single_pass(self, mock_b):
        from yt_artist.prompts import summarize_single_pass

        mock_b.SummarizeSinglePass.return_value = "A summary"
        result = summarize_single_pass(transcript="text", artist="Art", video_title="Vid")
        assert result == "A summary"
        mock_b.SummarizeSinglePass.assert_called_once_with(transcript="text", artist="Art", video_title="Vid")

    @patch("yt_artist.prompts.b")
    def test_summarize_chunk(self, mock_b):
        from yt_artist.prompts import summarize_chunk

        mock_b.SummarizeChunk.return_value = "Chunk summary"
        result = summarize_chunk(chunk="chunk text", chunk_index=1, total_chunks=3)
        assert result == "Chunk summary"
        mock_b.SummarizeChunk.assert_called_once_with(chunk="chunk text", chunk_index=1, total_chunks=3)

    @patch("yt_artist.prompts.b")
    def test_reduce_chunk_summaries(self, mock_b):
        from yt_artist.prompts import reduce_chunk_summaries

        mock_b.ReduceChunkSummaries.return_value = "Combined"
        result = reduce_chunk_summaries(section_summaries="sec1\nsec2", artist="A", video_title="V")
        assert result == "Combined"
        mock_b.ReduceChunkSummaries.assert_called_once_with(section_summaries="sec1\nsec2", artist="A", video_title="V")

    @patch("yt_artist.prompts.b")
    def test_refine_summary(self, mock_b):
        from yt_artist.prompts import refine_summary

        mock_b.RefineSummary.return_value = "Refined"
        result = refine_summary(prev_summary="prev", chunk="new", chunk_index=2, total_chunks=5)
        assert result == "Refined"
        mock_b.RefineSummary.assert_called_once_with(prev_summary="prev", chunk="new", chunk_index=2, total_chunks=5)


class TestScoringAdapters:
    @patch("yt_artist.prompts.b")
    def test_score_summary(self, mock_b):
        from yt_artist.prompts import score_summary

        mock_rating = MagicMock()
        mock_rating.completeness = 4
        mock_rating.coherence = 3
        mock_rating.faithfulness = 5
        mock_b.ScoreSummary.return_value = mock_rating
        result = score_summary(transcript_excerpt="excerpt", summary="sum")
        assert result.completeness == 4
        assert result.coherence == 3
        assert result.faithfulness == 5
        mock_b.ScoreSummary.assert_called_once_with(transcript_excerpt="excerpt", summary="sum")

    @patch("yt_artist.prompts.b")
    def test_verify_claims(self, mock_b):
        from yt_artist.prompts import verify_claims

        mock_claim = MagicMock()
        mock_claim.claim = "Test claim"
        mock_claim.verified = True
        mock_b.VerifyClaims.return_value = [mock_claim]
        result = verify_claims(summary="sum", transcript_excerpt="excerpt")
        assert len(result) == 1
        assert result[0].claim == "Test claim"
        assert result[0].verified is True
        mock_b.VerifyClaims.assert_called_once_with(summary="sum", transcript_excerpt="excerpt")


# ---------------------------------------------------------------------------
# Prompt content assertions: verify faithfulness instructions in .baml files
# ---------------------------------------------------------------------------

_BAML_SRC = os.path.join(os.path.dirname(__file__), "..", "baml_src")


class TestPromptFaithfulness:
    """Assert that all BAML prompt files contain anti-hallucination instructions."""

    def _read_baml(self, filename: str) -> str:
        path = os.path.join(_BAML_SRC, filename)
        with open(path) as f:
            return f.read()

    def test_summarize_baml_has_faithfulness(self):
        """All summarize prompts must include 'do not invent' instruction."""
        content = self._read_baml("summarize.baml")
        # Each of the 4 functions should have anti-hallucination language
        assert content.count("Do not invent") >= 3, "summarize.baml must have 'Do not invent' in chunk/reduce/refine"
        assert "Only state facts" in content or "Only include" in content, (
            "summarize.baml must have factual-only instruction"
        )

    def test_score_baml_has_faithfulness_criterion(self):
        """ScoreSummary prompt must evaluate faithfulness as a criterion."""
        content = self._read_baml("score.baml")
        assert "faithfulness" in content.lower()
        assert "hallucination" in content.lower() or "not in the transcript" in content.lower()

    def test_single_pass_has_faithfulness(self):
        """SummarizeSinglePass must instruct model to stay faithful."""
        content = self._read_baml("summarize.baml")
        # Find the SummarizeSinglePass function block
        start = content.find("function SummarizeSinglePass")
        end = content.find("function ", start + 1) if content.find("function ", start + 1) != -1 else len(content)
        single_pass = content[start:end]
        assert "do not invent" in single_pass.lower() or "only state facts" in single_pass.lower()
