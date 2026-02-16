"""Quality scoring for summaries: heuristic + lightweight LLM self-check.

Two-tier scoring (each 0.0–1.0):
  - heuristic_score: length ratio, repetition, key-term coverage, structure
  - llm_score: tiny LLM call asking model to rate completeness/coherence/faithfulness
  - quality_score: weighted blend (0.4 * heuristic + 0.6 * llm)

Scoring is decoupled from summarization and runs as a separate pipeline stage.
"""

from __future__ import annotations

import logging
import re
from collections import Counter
from typing import Dict, Optional, Tuple

from yt_artist.llm import complete
from yt_artist.storage import Storage

log = logging.getLogger("yt_artist.scorer")

# ---------------------------------------------------------------------------
# LLM self-check prompt
# ---------------------------------------------------------------------------

_LLM_SCORE_PROMPT = (
    "Rate this summary of a video transcript on a scale of 1–5 for each criterion:\n"
    "- Completeness: Does it cover the main topics of the transcript?\n"
    "- Coherence: Does it read naturally and logically?\n"
    "- Faithfulness: Does it only state things from the transcript (no hallucinations)?\n\n"
    "Return ONLY three numbers separated by spaces, e.g.: 4 3 5\n"
    "Do not include any other text."
)


# ---------------------------------------------------------------------------
# Heuristic scoring helpers
# ---------------------------------------------------------------------------


def _length_ratio_score(summary_len: int, transcript_len: int) -> float:
    """Score based on summary/transcript length ratio.

    Ideal ratio ~0.02–0.10.  Too short (< 0.01) or too long (> 0.20) penalized.
    """
    if transcript_len <= 0:
        return 0.5
    ratio = summary_len / transcript_len
    if 0.02 <= ratio <= 0.10:
        return 1.0
    if 0.01 <= ratio < 0.02 or 0.10 < ratio <= 0.20:
        return 0.7
    if ratio < 0.01:
        return 0.3  # way too short
    return 0.4  # way too long (ratio > 0.20)


def _repetition_score(summary: str) -> float:
    """Score based on sentence-level repetition. High repetition = bad (model looping)."""
    sentences = [s.strip() for s in re.split(r"[.!?]\s+", summary) if s.strip()]
    if len(sentences) <= 1:
        return 0.5  # can't measure repetition on single sentence
    unique = len(set(sentences))
    ratio = unique / len(sentences)
    # 1.0 = all unique, 0.0 = all duplicates
    return min(ratio, 1.0)


def _key_term_coverage(summary: str, transcript: str, top_n: int = 20) -> float:
    """Score based on what fraction of top transcript terms appear in summary.

    Extracts top-N frequent words (>3 chars) from transcript, checks coverage in summary.
    """
    # Simple word extraction: lowercase, alpha-only, > 3 chars
    _stop = {
        "this",
        "that",
        "with",
        "from",
        "your",
        "have",
        "will",
        "been",
        "they",
        "them",
        "their",
        "what",
        "when",
        "where",
        "which",
        "there",
        "about",
        "would",
        "could",
        "should",
        "into",
        "also",
        "just",
        "than",
        "more",
        "very",
        "some",
        "like",
        "know",
        "think",
        "going",
        "really",
        "because",
        "people",
        "said",
        "were",
        "does",
        "these",
        "those",
        "then",
        "here",
        "other",
        "over",
        "being",
        "even",
        "much",
        "only",
        "well",
        "back",
        "after",
        "make",
    }
    words = re.findall(r"[a-z]{4,}", transcript.lower())
    word_counts = Counter(w for w in words if w not in _stop)
    if not word_counts:
        return 0.5
    top_terms = [w for w, _ in word_counts.most_common(top_n)]
    summary_lower = summary.lower()
    hits = sum(1 for t in top_terms if t in summary_lower)
    return hits / len(top_terms)


def _structure_score(summary: str) -> float:
    """Score based on structural quality: multiple sentences, sections, bullet points."""
    sentences = [s.strip() for s in re.split(r"[.!?]\s+", summary) if s.strip()]
    n_sentences = len(sentences)
    has_bullets = bool(re.search(r"^[\s]*[-*•]\s", summary, re.MULTILINE))
    has_sections = bool(re.search(r"^#+\s", summary, re.MULTILINE))
    # Base: 0.3 for 1 sentence, 0.6 for 2-3, 0.8 for 4-9, 1.0 for 10+
    if n_sentences >= 10:
        base = 1.0
    elif n_sentences >= 4:
        base = 0.8
    elif n_sentences >= 2:
        base = 0.6
    else:
        base = 0.3
    # Bonus for structure
    bonus = 0.0
    if has_bullets:
        bonus += 0.1
    if has_sections:
        bonus += 0.1
    return min(base + bonus, 1.0)


# ---------------------------------------------------------------------------
# Public scoring API
# ---------------------------------------------------------------------------


def heuristic_score(summary: str, transcript: str) -> float:
    """Compute heuristic quality score (0.0–1.0) without any LLM call.

    Weighted average of: length ratio (0.3), repetition (0.2),
    key-term coverage (0.3), structure (0.2).
    """
    s_len = _length_ratio_score(len(summary), len(transcript))
    s_rep = _repetition_score(summary)
    s_cov = _key_term_coverage(summary, transcript)
    s_str = _structure_score(summary)
    score = 0.3 * s_len + 0.2 * s_rep + 0.3 * s_cov + 0.2 * s_str
    return round(score, 4)


def _parse_llm_rating(text: str) -> Optional[Tuple[int, int, int]]:
    """Parse '4 3 5' style LLM output into (completeness, coherence, faithfulness).

    Returns None if the output can't be parsed into exactly 3 integers 1–5.
    """
    nums = re.findall(r"\d+", text)
    if len(nums) < 3:
        return None
    try:
        vals = [int(n) for n in nums[:3]]
    except ValueError:
        return None
    if all(1 <= v <= 5 for v in vals):
        return (vals[0], vals[1], vals[2])
    return None


def llm_score(summary: str, transcript: str, *, model: Optional[str] = None) -> Optional[float]:
    """Compute LLM self-check score (0.0–1.0) via a tiny LLM call.

    Asks the model to rate completeness, coherence, and faithfulness (1–5 each).
    Returns average normalized to 0.0–1.0, or None if LLM call fails or output unparseable.
    """
    # Build a short excerpt of the transcript for context (avoid sending full text)
    max_excerpt = 3000
    excerpt = transcript[:max_excerpt]
    if len(transcript) > max_excerpt:
        excerpt += "\n\n[...transcript truncated for scoring...]"

    user_content = f"Transcript excerpt:\n{excerpt}\n\nSummary:\n{summary}"
    try:
        raw = complete(system_prompt=_LLM_SCORE_PROMPT, user_content=user_content, model=model)
    except Exception as exc:
        log.warning("LLM scoring call failed: %s", exc)
        return None

    parsed = _parse_llm_rating(raw)
    if parsed is None:
        log.warning("Could not parse LLM score output: %r", raw[:200])
        return None

    completeness, coherence, faithfulness = parsed
    avg = (completeness + coherence + faithfulness) / 3.0
    # Normalize 1–5 to 0.0–1.0
    normalized = (avg - 1.0) / 4.0
    return round(normalized, 4)


def score_summary(
    summary: str,
    transcript: str,
    *,
    model: Optional[str] = None,
    skip_llm: bool = False,
) -> Dict[str, Optional[float]]:
    """Compute full quality scores for a summary.

    Returns dict with keys: heuristic_score, llm_score, quality_score.
    If *skip_llm* is True, llm_score=None and quality_score equals heuristic_score.
    """
    h_score = heuristic_score(summary, transcript)

    if skip_llm:
        return {
            "heuristic_score": h_score,
            "llm_score": None,
            "quality_score": h_score,
        }

    l_score = llm_score(summary, transcript, model=model)
    if l_score is None:
        # LLM failed — fall back to heuristic only
        return {
            "heuristic_score": h_score,
            "llm_score": None,
            "quality_score": h_score,
        }

    q_score = round(0.4 * h_score + 0.6 * l_score, 4)
    return {
        "heuristic_score": h_score,
        "llm_score": l_score,
        "quality_score": q_score,
    }


def score_video_summary(
    video_id: str,
    prompt_id: str,
    storage: Storage,
    *,
    model: Optional[str] = None,
    skip_llm: bool = False,
) -> Optional[Dict[str, Optional[float]]]:
    """Score an existing summary for a video+prompt pair.

    Reads summary and transcript from DB, computes scores, writes them back.
    Returns the score dict, or None if no summary/transcript found.
    """
    rows = storage.get_summaries_for_video(video_id)
    summary_row = next((r for r in rows if r["prompt_id"] == prompt_id), None)
    if not summary_row:
        log.warning("No summary for video_id=%s prompt_id=%s", video_id, prompt_id)
        return None

    transcript_row = storage.get_transcript(video_id)
    if not transcript_row:
        log.warning("No transcript for video_id=%s", video_id)
        return None

    scores = score_summary(
        summary_row["content"],
        transcript_row["raw_text"],
        model=model,
        skip_llm=skip_llm,
    )

    storage.update_summary_scores(
        video_id=video_id,
        prompt_id=prompt_id,
        quality_score=scores["quality_score"],
        heuristic_score=scores["heuristic_score"],
        llm_score=scores["llm_score"],
    )

    log.info(
        "Scored %s:%s — quality=%.2f (heuristic=%.2f, llm=%s)",
        video_id,
        prompt_id,
        scores["quality_score"],
        scores["heuristic_score"],
        f"{scores['llm_score']:.2f}" if scores["llm_score"] is not None else "N/A",
    )
    return scores
