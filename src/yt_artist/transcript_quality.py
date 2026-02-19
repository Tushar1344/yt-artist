"""Transcript quality scoring: heuristic checks before summarization.

Complements scorer.py (post-summarize scoring).  These heuristics detect
garbage transcripts (music videos, non-English, auto-generated gibberish)
before wasting LLM calls on summarization.
"""

from __future__ import annotations

import logging

log = logging.getLogger("yt_artist.transcript_quality")

# ---------------------------------------------------------------------------
# Sub-score functions (each returns 0.0–1.0)
# ---------------------------------------------------------------------------

_MIN_WORDS = 50
_GOOD_WORDS = 200


def _word_count_score(text: str) -> float:
    """Score based on word count.  Too few words = bad transcript.

    < 50 words:  0.0  (gibberish / music video)
    50–200 words: linear ramp 0.0 → 1.0
    200+ words:  1.0
    """
    words = text.split()
    n = len(words)
    if n < _MIN_WORDS:
        return 0.0
    if n >= _GOOD_WORDS:
        return 1.0
    return (n - _MIN_WORDS) / (_GOOD_WORDS - _MIN_WORDS)


def _repetition_ratio_score(text: str) -> float:
    """Score based on line-level repetition.

    Auto-generated VTT often repeats identical lines many times.
    Returns ratio of unique lines to total lines (1.0 = all unique).
    """
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    if not lines:
        return 0.0
    return len(set(lines)) / len(lines)


def _avg_word_length_score(text: str) -> float:
    """Score based on average word length.  Extreme values = garbled.

    Normal English: ~4.5 chars/word.  Music lyrics or garbled text may
    have very short (< 2 char) or very long (> 12 char) averages.
    """
    words = text.split()
    if not words:
        return 0.0
    avg = sum(len(w) for w in words) / len(words)
    if avg < 1.5 or avg > 15.0:
        return 0.0
    if avg < 2.5:
        return (avg - 1.5) / 1.0  # ramp 1.5→2.5
    if avg > 12.0:
        return (15.0 - avg) / 3.0  # ramp 12→15
    return 1.0


def _punctuation_density_score(text: str) -> float:
    """Score based on punctuation density.

    Real speech transcripts have some punctuation (auto-captions add periods).
    Zero punctuation or excessive punctuation both indicate problems.
    Target range: 1%–15% of characters are punctuation.
    """
    if not text:
        return 0.0
    punct_count = sum(1 for ch in text if ch in ".,;:!?\"'()-")
    density = punct_count / len(text)
    if density < 0.001:
        return 0.2  # no punctuation at all (common in raw auto-captions, not fatal)
    if density > 0.20:
        return 0.0  # excessive punctuation
    if density > 0.15:
        return (0.20 - density) / 0.05  # ramp down
    return 1.0


def _line_uniqueness_score(text: str) -> float:
    """Score based on unique line ratio after normalization.

    Strips whitespace, lowercases, then computes unique/total.
    Music videos and looping content have very low uniqueness.
    """
    lines = [ln.strip().lower() for ln in text.splitlines() if ln.strip()]
    if not lines:
        return 0.0
    return len(set(lines)) / len(lines)


# ---------------------------------------------------------------------------
# Composite score
# ---------------------------------------------------------------------------

# Weights for sub-scores
_WEIGHTS = {
    "word_count": 0.30,
    "repetition": 0.25,
    "line_uniqueness": 0.20,
    "avg_word_length": 0.15,
    "punctuation": 0.10,
}


def transcript_quality_score(raw_text: str) -> float:
    """Compute transcript quality score (0.0–1.0).  Heuristic only, no LLM.

    Weighted average of word count, repetition ratio, line uniqueness,
    average word length, and punctuation density sub-scores.
    """
    if not raw_text or not raw_text.strip():
        return 0.0

    scores = {
        "word_count": _word_count_score(raw_text),
        "repetition": _repetition_ratio_score(raw_text),
        "line_uniqueness": _line_uniqueness_score(raw_text),
        "avg_word_length": _avg_word_length_score(raw_text),
        "punctuation": _punctuation_density_score(raw_text),
    }

    total = sum(scores[k] * _WEIGHTS[k] for k in _WEIGHTS)
    return round(total, 4)
