"""Tests for prompts.py — BAML adapter layer for scoring/verification."""

import os
from unittest.mock import MagicMock, patch


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
    """Assert that BAML prompt files contain anti-hallucination instructions."""

    def _read_baml(self, filename: str) -> str:
        path = os.path.join(_BAML_SRC, filename)
        with open(path) as f:
            return f.read()

    def test_summarize_baml_has_faithfulness(self):
        """Summarize prompts (chunk/reduce/refine) must include 'do not invent' instruction."""
        content = self._read_baml("summarize.baml")
        assert content.count("Do not invent") >= 3, "summarize.baml must have 'Do not invent' in chunk/reduce/refine"
        assert "Only state facts" in content or "Only include" in content, (
            "summarize.baml must have factual-only instruction"
        )

    def test_score_baml_has_faithfulness_criterion(self):
        """ScoreSummary prompt must evaluate faithfulness as a criterion."""
        content = self._read_baml("score.baml")
        assert "faithfulness" in content.lower()
        assert "hallucination" in content.lower() or "not in the transcript" in content.lower()
