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
# Prompt content assertions: verify faithfulness instructions
# ---------------------------------------------------------------------------

_BAML_SRC = os.path.join(os.path.dirname(__file__), "..", "baml_src")


class TestPromptFaithfulness:
    """Assert that prompt sources contain anti-hallucination instructions."""

    def _read_baml(self, filename: str) -> str:
        path = os.path.join(_BAML_SRC, filename)
        with open(path) as f:
            return f.read()

    def test_summarizer_prompts_have_faithfulness(self):
        """Internal chunk/reduce/refine prompts must include 'Do not invent' instruction."""
        from yt_artist.summarizer import _CHUNK_SYSTEM_PROMPT, _REDUCE_SUFFIX, _REFINE_SYSTEM_PROMPT

        assert "Do not invent" in _CHUNK_SYSTEM_PROMPT
        assert "Only include" in _CHUNK_SYSTEM_PROMPT
        assert "Do not add" in _REDUCE_SUFFIX or "not found in" in _REDUCE_SUFFIX
        assert "Do not invent" in _REFINE_SYSTEM_PROMPT

    def test_default_template_has_faithfulness(self):
        """Default DB prompt template must include anti-hallucination instruction."""
        from yt_artist.storage import Storage

        template = Storage._DEFAULT_PROMPT_TEMPLATE
        assert "do not invent" in template.lower()
        assert "Only state facts" in template or "Only include" in template

    def test_score_baml_has_faithfulness_criterion(self):
        """ScoreSummary prompt must evaluate faithfulness as a criterion."""
        content = self._read_baml("score.baml")
        assert "faithfulness" in content.lower()
        assert "hallucination" in content.lower() or "not in the transcript" in content.lower()
