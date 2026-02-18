"""Prompt management via BAML — typed, versioned, benchmarkable.

Thin adapter that bridges BAML-generated functions with the existing codebase.
Scoring and verification prompts live in baml_src/*.baml files (git-versioned).
Summarization prompts are stored in the DB and rendered at runtime (see summarizer.py).
This module wraps baml_client.b calls so the rest of the codebase doesn't
import baml_client directly — making it easy to swap or mock.
"""

from __future__ import annotations

import logging
from typing import List

from baml_client import b
from baml_client.types import ClaimVerification, ScoreRating

log = logging.getLogger("yt_artist.prompts")

# Re-export types for convenience
__all__ = [
    "ClaimVerification",
    "ScoreRating",
    "score_summary",
    "verify_claims",
]


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------


def score_summary(transcript_excerpt: str, summary: str) -> ScoreRating:
    """LLM self-check — rate a summary against a transcript excerpt.

    Returns a ScoreRating with completeness, coherence, faithfulness (1-5 each).
    """
    return b.ScoreSummary(transcript_excerpt=transcript_excerpt, summary=summary)


def verify_claims(summary: str, transcript_excerpt: str) -> List[ClaimVerification]:
    """Claim verification — fact-check specific claims against transcript.

    Returns list of ClaimVerification with claim text and verified bool.
    """
    return b.VerifyClaims(summary=summary, transcript_excerpt=transcript_excerpt)
