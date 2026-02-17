"""Prompt management via BAML — typed, versioned, benchmarkable.

Thin adapter that bridges BAML-generated functions with the existing codebase.
All prompts live in baml_src/*.baml files (git-versioned, diffable).
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
    "summarize_single_pass",
    "summarize_chunk",
    "reduce_chunk_summaries",
    "refine_summary",
    "score_summary",
    "verify_claims",
]


# ---------------------------------------------------------------------------
# Summarization
# ---------------------------------------------------------------------------


def summarize_single_pass(transcript: str, artist: str, video_title: str) -> str:
    """Single-pass summarization — transcript fits context window."""
    return b.SummarizeSinglePass(transcript=transcript, artist=artist, video_title=video_title)


def summarize_chunk(chunk: str, chunk_index: int, total_chunks: int) -> str:
    """Map phase — summarize one chunk of a long transcript."""
    return b.SummarizeChunk(chunk=chunk, chunk_index=chunk_index, total_chunks=total_chunks)


def reduce_chunk_summaries(section_summaries: str, artist: str, video_title: str) -> str:
    """Reduce phase — combine chunk summaries into a final summary."""
    return b.ReduceChunkSummaries(section_summaries=section_summaries, artist=artist, video_title=video_title)


def refine_summary(prev_summary: str, chunk: str, chunk_index: int, total_chunks: int) -> str:
    """Refine phase — update rolling summary with a new chunk."""
    return b.RefineSummary(prev_summary=prev_summary, chunk=chunk, chunk_index=chunk_index, total_chunks=total_chunks)


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
