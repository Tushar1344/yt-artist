"""Quality scoring for summaries: heuristic + lightweight LLM self-check.

Two-tier scoring (each 0.0–1.0):
  - heuristic_score: length ratio, repetition, key-term coverage, structure, named entity verification
  - llm_score: BAML-powered LLM call returning typed ScoreRating (completeness/coherence/faithfulness)
  - quality_score: weighted blend (0.4 * heuristic + 0.6 * llm)

Scoring is decoupled from summarization and runs as a separate pipeline stage.
Prompts are managed via BAML (.baml files in baml_src/) through the prompts module.
"""

from __future__ import annotations

import logging
import re
from collections import Counter
from typing import Dict, Optional

from yt_artist import prompts
from yt_artist.storage import Storage

log = logging.getLogger("yt_artist.scorer")


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


_ENTITY_STOPWORDS = {
    # Sentence-start words often capitalized but not proper nouns
    "the",
    "this",
    "that",
    "these",
    "those",
    "however",
    "therefore",
    "furthermore",
    "moreover",
    "additionally",
    "meanwhile",
    "nevertheless",
    "consequently",
    "overall",
    "finally",
    "first",
    "second",
    "third",
    "next",
    "then",
    "also",
    "here",
    "there",
    "where",
    "when",
    "what",
    "which",
    "while",
    "after",
    "before",
    "during",
    "since",
    "until",
    "although",
    "because",
    "but",
    "and",
    "for",
    "not",
    "with",
    "from",
    "into",
    "about",
    "some",
    "many",
    "most",
    "each",
    "every",
    "both",
    "several",
    "other",
    "another",
    "such",
    "like",
    "just",
    "only",
    "even",
    # Months and days
    "january",
    "february",
    "march",
    "april",
    "may",
    "june",
    "july",
    "august",
    "september",
    "october",
    "november",
    "december",
    "monday",
    "tuesday",
    "wednesday",
    "thursday",
    "friday",
    "saturday",
    "sunday",
    # Common non-entity capitalized words
    "chapter",
    "section",
    "part",
    "point",
    "step",
    "figure",
    "table",
    "key",
    "important",
    "main",
    "summary",
    "conclusion",
    "introduction",
}


def _named_entity_score(summary: str, transcript: str) -> float:
    """Score based on whether proper nouns in summary appear in transcript.

    Extracts capitalized multi-word sequences (likely proper nouns) from the
    summary, then checks each against the transcript (case-insensitive).
    Returns verified_count / total. Returns 1.0 if no entities found (neutral).
    """
    # Match capitalized words that aren't sentence starters:
    # Look for sequences of 2+ capitalized words (e.g. "Elijah Wood", "Stanford University")
    # Also match single capitalized words that appear mid-sentence
    entities: set[str] = set()

    # Multi-word proper nouns: 2+ consecutive capitalized words
    for match in re.finditer(r"(?<![.!?]\s)(?<!\n)([A-Z][a-z]+(?:\s+[A-Z][a-z]+)+)", summary):
        entity = match.group(1)
        words = entity.lower().split()
        if not all(w in _ENTITY_STOPWORDS for w in words):
            entities.add(entity.lower())

    # Single capitalized words mid-sentence (after lowercase word or comma)
    for match in re.finditer(r"(?<=[a-z,]\s)([A-Z][a-z]{2,})", summary):
        word = match.group(1).lower()
        if word not in _ENTITY_STOPWORDS:
            entities.add(word)

    if not entities:
        return 1.0  # No entities to check — neutral

    transcript_lower = transcript.lower()
    verified = sum(1 for e in entities if e in transcript_lower)
    return verified / len(entities)


def _sample_transcript(transcript: str, max_excerpt: int = 3000) -> str:
    """Stratified sampling of transcript: start, middle, end.

    Short transcripts (≤ max_excerpt) returned whole. Longer ones get ~1000 chars
    from each of three segments separated by [...] markers.
    """
    if len(transcript) <= max_excerpt:
        return transcript

    segment = max_excerpt // 3  # ~1000 chars each
    start = transcript[:segment]
    mid_point = len(transcript) // 2
    middle = transcript[mid_point - segment // 2 : mid_point + segment // 2]
    end = transcript[-segment:]

    return f"{start}\n\n[...]\n\n{middle}\n\n[...]\n\n{end}"


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

    Weighted average of: length ratio (0.25), repetition (0.15),
    key-term coverage (0.25), structure (0.15), named entity (0.20).
    """
    s_len = _length_ratio_score(len(summary), len(transcript))
    s_rep = _repetition_score(summary)
    s_cov = _key_term_coverage(summary, transcript)
    s_str = _structure_score(summary)
    s_ent = _named_entity_score(summary, transcript)
    score = 0.25 * s_len + 0.15 * s_rep + 0.25 * s_cov + 0.15 * s_str + 0.20 * s_ent
    return round(score, 4)


def llm_score(summary: str, transcript: str, *, model: Optional[str] = None) -> Optional[Dict[str, float]]:
    """Compute LLM self-check scores via BAML ScoreSummary function.

    Calls the BAML ScoreSummary function which returns a typed ScoreRating
    with completeness, coherence, and faithfulness (1–5 each).
    Returns dict with 'llm_score' (overall normalized avg) and 'faithfulness'
    (faithfulness dimension normalized separately), or None if LLM call fails.
    """
    # Build a stratified excerpt of the transcript for context
    excerpt = _sample_transcript(transcript, max_excerpt=3000)

    try:
        rating = prompts.score_summary(transcript_excerpt=excerpt, summary=summary)
    except Exception as exc:
        log.warning("LLM scoring call failed: %s", exc)
        return None

    # Validate rating values are in expected range
    try:
        completeness = int(rating.completeness)
        coherence = int(rating.coherence)
        faithfulness = int(rating.faithfulness)
    except (TypeError, ValueError, AttributeError) as exc:
        log.warning("Could not extract scores from BAML ScoreRating: %s", exc)
        return None

    if not all(1 <= v <= 5 for v in (completeness, coherence, faithfulness)):
        log.warning(
            "ScoreRating values out of range: completeness=%s, coherence=%s, faithfulness=%s",
            completeness,
            coherence,
            faithfulness,
        )
        return None

    avg = (completeness + coherence + faithfulness) / 3.0
    # Normalize 1–5 to 0.0–1.0
    normalized = (avg - 1.0) / 4.0
    faith_normalized = (faithfulness - 1.0) / 4.0
    return {
        "llm_score": round(normalized, 4),
        "faithfulness": round(faith_normalized, 4),
    }


def verify_claims(
    summary: str,
    transcript: str,
    *,
    model: Optional[str] = None,
) -> Optional[Dict]:
    """Verify factual claims in a summary against the transcript.

    Calls the BAML VerifyClaims function which returns a list of
    ClaimVerification objects (claim text + verified boolean).
    Returns dict with 'claims' list, 'verification_score', or None on failure.
    """
    excerpt = _sample_transcript(transcript, max_excerpt=6000)

    try:
        claims = prompts.verify_claims(summary=summary, transcript_excerpt=excerpt)
    except Exception as exc:
        log.warning("Claim verification failed: %s", exc)
        return None

    if not claims:
        log.warning("VerifyClaims returned empty list")
        return None

    results = []
    for c in claims:
        try:
            results.append((str(c.claim), bool(c.verified)))
        except (AttributeError, TypeError) as exc:
            log.warning("Could not parse claim: %s", exc)
            continue

    if not results:
        return None

    verified_count = sum(1 for _, v in results if v)
    v_score = round(verified_count / len(results), 4)

    return {
        "claims": results,
        "verification_score": v_score,
    }


def score_summary(
    summary: str,
    transcript: str,
    *,
    model: Optional[str] = None,
    skip_llm: bool = False,
    verify: bool = False,
) -> Dict[str, Optional[float]]:
    """Compute full quality scores for a summary.

    Returns dict with keys: heuristic_score, llm_score, quality_score,
    faithfulness_score, verification_score.
    If *skip_llm* is True, llm_score=None and quality_score equals heuristic_score.
    If *verify* is True, runs claim verification (1 extra LLM call).
    """
    h_score = heuristic_score(summary, transcript)

    base_result: Dict[str, Optional[float]] = {
        "heuristic_score": h_score,
        "llm_score": None,
        "quality_score": h_score,
        "faithfulness_score": None,
        "verification_score": None,
    }

    if not skip_llm:
        l_result = llm_score(summary, transcript, model=model)
        if l_result is not None:
            l_score = l_result["llm_score"]
            f_score = l_result["faithfulness"]
            if f_score is not None and f_score <= 0.4:
                log.warning("Low faithfulness score (%.2f) — summary may contain hallucinated content", f_score)
            q_score = round(0.4 * h_score + 0.6 * l_score, 4)
            base_result["llm_score"] = l_score
            base_result["quality_score"] = q_score
            base_result["faithfulness_score"] = f_score

    if verify:
        v_result = verify_claims(summary, transcript, model=model)
        if v_result is not None:
            base_result["verification_score"] = v_result["verification_score"]

    return base_result


def score_video_summary(
    video_id: str,
    prompt_id: str,
    storage: Storage,
    *,
    model: Optional[str] = None,
    skip_llm: bool = False,
    verify: bool = False,
) -> Optional[Dict[str, Optional[float]]]:
    """Score an existing summary for a video+prompt pair.

    Reads summary and transcript from DB, computes scores, writes them back.
    Returns the score dict, or None if no summary/transcript found.
    If *verify* is True, runs claim verification (1 extra LLM call).
    """
    from yt_artist.ledger import WorkTimer, record_operation
    from yt_artist.llm import get_model_name

    timer = WorkTimer()
    effective_model = get_model_name(model) if not skip_llm else None

    rows = storage.get_summaries_for_video(video_id)
    summary_row = next((r for r in rows if r["prompt_id"] == prompt_id), None)
    if not summary_row:
        log.warning("No summary for video_id=%s prompt_id=%s", video_id, prompt_id)
        record_operation(
            storage,
            video_id=video_id,
            operation="score",
            prompt_id=prompt_id,
            status="skipped",
            started_at=timer.started_at,
            duration_ms=timer.elapsed_ms(),
            error_message="no summary found",
        )
        return None

    transcript_row = storage.get_transcript(video_id)
    if not transcript_row:
        log.warning("No transcript for video_id=%s", video_id)
        record_operation(
            storage,
            video_id=video_id,
            operation="score",
            prompt_id=prompt_id,
            status="skipped",
            started_at=timer.started_at,
            duration_ms=timer.elapsed_ms(),
            error_message="no transcript found",
        )
        return None

    try:
        scores = score_summary(
            summary_row["content"],
            transcript_row["raw_text"],
            model=model,
            skip_llm=skip_llm,
            verify=verify,
        )

        storage.update_summary_scores(
            video_id=video_id,
            prompt_id=prompt_id,
            quality_score=scores["quality_score"],
            heuristic_score=scores["heuristic_score"],
            llm_score=scores["llm_score"],
            faithfulness_score=scores.get("faithfulness_score"),
            verification_score=scores.get("verification_score"),
        )

        log.info(
            "Scored %s:%s — quality=%.2f (heuristic=%.2f, llm=%s, faith=%s, verified=%s)",
            video_id,
            prompt_id,
            scores["quality_score"],
            scores["heuristic_score"],
            f"{scores['llm_score']:.2f}" if scores["llm_score"] is not None else "N/A",
            f"{scores['faithfulness_score']:.2f}" if scores.get("faithfulness_score") is not None else "N/A",
            f"{scores['verification_score']:.0%}" if scores.get("verification_score") is not None else "N/A",
        )

        record_operation(
            storage,
            video_id=video_id,
            operation="score",
            model=effective_model,
            prompt_id=prompt_id,
            status="success",
            started_at=timer.started_at,
            duration_ms=timer.elapsed_ms(),
        )
        if verify and scores.get("verification_score") is not None:
            record_operation(
                storage,
                video_id=video_id,
                operation="verify",
                model=effective_model,
                prompt_id=prompt_id,
                status="success",
                started_at=timer.started_at,
                duration_ms=timer.elapsed_ms(),
            )

        return scores

    except Exception as exc:
        record_operation(
            storage,
            video_id=video_id,
            operation="score",
            model=effective_model,
            prompt_id=prompt_id,
            status="failed",
            started_at=timer.started_at,
            duration_ms=timer.elapsed_ms(),
            error_message=str(exc)[:500],
        )
        raise
