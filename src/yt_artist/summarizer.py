"""Generate AI summary from transcript using prompt template; save to DB.

Supports three strategies for long transcripts:
- truncate: cut text to max_chars (legacy default)
- map-reduce: chunk → summarize each → combine summaries
- refine: iteratively refine a rolling summary with each chunk
- auto: single-pass if fits, map-reduce if too long (new default)
"""

from __future__ import annotations

import logging
import os
from typing import List, Optional

from yt_artist.llm import complete
from yt_artist.storage import Storage

log = logging.getLogger("yt_artist.summarizer")

# Approximate character limit for transcripts sent to LLM.
# ~4 chars/token → 30 000 chars ≈ 7 500 tokens, leaving headroom for system prompt + response.
# Override with YT_ARTIST_MAX_TRANSCRIPT_CHARS env var.
_DEFAULT_MAX_TRANSCRIPT_CHARS = 30_000

# Overlap between chunks to preserve cross-boundary context.
_CHUNK_OVERLAP = 500

# Valid strategy names.
STRATEGIES = ("auto", "truncate", "map-reduce", "refine")


class _SafeTemplateMap(dict):
    """Dict subclass that returns '{key}' for missing keys, preventing KeyError in format_map."""

    def __missing__(self, key: str) -> str:
        return f"{{{key}}}"


def _fill_template(
    template: str,
    *,
    artist: str = "",
    video: str = "",
    intent: str = "",
    audience: str = "",
) -> str:
    """Replace {artist}, {video}, {intent}, {audience} in template.

    Uses format_map for atomic substitution — safe even when a value
    contains another placeholder string (e.g. artist='{video}' won't
    corrupt the video slot).  Unknown placeholders are left as-is.
    """
    mapping = _SafeTemplateMap(artist=artist, video=video, intent=intent, audience=audience)
    return template.format_map(mapping)


# ---------------------------------------------------------------------------
# Chunking
# ---------------------------------------------------------------------------


def _chunk_text(text: str, chunk_size: int, overlap: int = _CHUNK_OVERLAP) -> List[str]:
    """Split *text* into chunks of approximately *chunk_size* chars.

    Splits at sentence boundaries ('. ' or '\\n') near the target size.
    Adjacent chunks overlap by *overlap* chars to preserve cross-boundary context.
    Never returns an empty list — always at least one chunk.
    """
    if len(text) <= chunk_size:
        return [text]

    # Clamp overlap to at most half the chunk size to ensure forward progress
    overlap = min(overlap, chunk_size // 2)

    chunks: List[str] = []
    start = 0
    while start < len(text):
        end = min(start + chunk_size, len(text))
        # If not at the very end, try to break at a sentence boundary
        if end < len(text):
            # Search backwards from `end` for a sentence break
            best_break = -1
            search_start = max(start + chunk_size // 2, start)  # don't look too far back
            for sep in ("\n", ". ", "? ", "! "):
                pos = text.rfind(sep, search_start, end)
                if pos > best_break:
                    best_break = pos + len(sep)
            if best_break > start:
                end = best_break
        chunk = text[start:end]
        if chunk.strip():
            chunks.append(chunk)
        # If we reached the end of text, stop
        if end >= len(text):
            break
        # Advance: next start is (end - overlap), ensuring forward progress
        next_start = end - overlap
        if next_start <= start:
            next_start = start + max(chunk_size // 2, 1)  # force at least half-chunk forward
        start = next_start

    return chunks if chunks else [text]


# ---------------------------------------------------------------------------
# Strategy: single-pass
# ---------------------------------------------------------------------------


def _summarize_single(system_prompt: str, raw_text: str, model: Optional[str] = None) -> str:
    """Summarize text in a single LLM call (fits within context window)."""
    user_content = "Transcript:\n\n" + raw_text
    return complete(system_prompt=system_prompt, user_content=user_content, model=model)


# ---------------------------------------------------------------------------
# Strategy: map-reduce
# ---------------------------------------------------------------------------

_MAP_PROMPT = (
    "Summarize this section of a transcript. Preserve key facts, data points, "
    "quotes, and conclusions. Be thorough — this is section {i} of {n}."
)

_REDUCE_PROMPT_PREFIX = (
    "The following are summaries of consecutive sections of a transcript. "
    "Combine them into a single coherent summary, preserving all key points.\n\n"
)


def _summarize_map_reduce(
    system_prompt: str,
    raw_text: str,
    max_chars: int,
    model: Optional[str] = None,
) -> str:
    """Chunk the transcript → summarize each chunk → combine summaries.

    If the combined chunk summaries still exceed *max_chars*, recursively reduce.
    """
    chunks = _chunk_text(raw_text, max_chars)
    n = len(chunks)
    log.info("Map-reduce: splitting %d chars into %d chunks of ~%d chars each.", len(raw_text), n, max_chars)

    # Map: summarize each chunk
    chunk_summaries: List[str] = []
    for i, chunk in enumerate(chunks, 1):
        map_system = _MAP_PROMPT.format(i=i, n=n)
        user_content = f"Transcript section {i}/{n}:\n\n{chunk}"
        summary = complete(system_prompt=map_system, user_content=user_content, model=model)
        if summary.strip():
            chunk_summaries.append(summary.strip())
        log.info("Map-reduce: chunk %d/%d summarized (%d chars → %d chars).", i, n, len(chunk), len(summary))

    if not chunk_summaries:
        raise ValueError("Map-reduce produced no chunk summaries.")

    # Reduce: combine chunk summaries
    combined = "\n\n---\n\n".join(f"Section {i}:\n{s}" for i, s in enumerate(chunk_summaries, 1))

    # Recursive reduce if combined summaries still too long
    if len(combined) > max_chars:
        log.info("Map-reduce: combined summaries (%d chars) exceed limit, reducing recursively.", len(combined))
        return _summarize_map_reduce(system_prompt, combined, max_chars, model)

    # Final reduce with the user's original system prompt
    reduce_user = _REDUCE_PROMPT_PREFIX + combined
    final = complete(system_prompt=system_prompt, user_content=reduce_user, model=model)
    log.info("Map-reduce: final summary produced (%d chars).", len(final))
    return final


# ---------------------------------------------------------------------------
# Strategy: refine (iterative/rolling)
# ---------------------------------------------------------------------------

_REFINE_PROMPT = (
    "You have a summary so far and a new section of transcript. "
    "Update the summary to incorporate the key points from this new section. "
    "Preserve all important information from the existing summary.\n\n"
    "Current summary:\n{prev_summary}\n\n"
    "New transcript section ({i}/{n}):\n{chunk}"
)


def _summarize_refine(
    system_prompt: str,
    raw_text: str,
    max_chars: int,
    model: Optional[str] = None,
) -> str:
    """Iteratively refine a rolling summary with each chunk.

    Chunk 1 → initial summary. Each subsequent chunk updates the summary.
    Uses smaller chunks to leave room for the rolling summary in the context.
    """
    # Leave ~40% of context for the rolling summary
    refine_chunk_size = int(max_chars * 0.6)
    chunks = _chunk_text(raw_text, refine_chunk_size)
    n = len(chunks)
    log.info("Refine: splitting %d chars into %d chunks of ~%d chars each.", len(raw_text), n, refine_chunk_size)

    # First chunk: generate initial summary
    user_content = f"Transcript section 1/{n}:\n\n{chunks[0]}"
    summary = complete(system_prompt=system_prompt, user_content=user_content, model=model)
    log.info("Refine: initial summary from chunk 1/%d (%d chars).", n, len(summary))

    # Subsequent chunks: refine
    for i, chunk in enumerate(chunks[1:], 2):
        refine_content = _REFINE_PROMPT.format(prev_summary=summary, i=i, n=n, chunk=chunk)
        summary = complete(system_prompt=system_prompt, user_content=refine_content, model=model)
        log.info("Refine: updated summary with chunk %d/%d (%d chars).", i, n, len(summary))

    return summary


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------


def _get_strategy() -> str:
    """Return the summarization strategy from env var or default 'auto'."""
    strategy = os.environ.get("YT_ARTIST_SUMMARIZE_STRATEGY", "auto").strip().lower()
    if strategy not in STRATEGIES:
        log.warning("Unknown strategy '%s', falling back to 'auto'.", strategy)
        return "auto"
    return strategy


def summarize(
    video_id: str,
    prompt_id: str,
    storage: Storage,
    *,
    intent_override: Optional[str] = None,
    audience_override: Optional[str] = None,
    artist_override: Optional[str] = None,
    video_override: Optional[str] = None,
    model: Optional[str] = None,
    strategy: Optional[str] = None,
) -> str:
    """Load transcript and prompt, fill template, call LLM, save Summary.

    Returns summary id (we use video_id+prompt_id; the row is upserted).

    *strategy* overrides the env-var / default:
      - 'auto': single-pass if text fits, map-reduce if too long (default)
      - 'truncate': truncate to max_chars then single-pass (legacy behavior)
      - 'map-reduce': always use map-reduce chunking
      - 'refine': iterative rolling summary
    """
    transcript_row = storage.get_transcript(video_id)
    if not transcript_row:
        raise ValueError(f"No transcript for video_id={video_id}")

    prompt_row = storage.get_prompt(prompt_id)
    if not prompt_row:
        raise ValueError(f"No prompt for prompt_id={prompt_id}")

    video_row = storage.get_video(video_id)
    if not video_row:
        log.warning("Video %s not in DB; proceeding without artist/title context.", video_id)

    artist_row = storage.get_artist(video_row["artist_id"]) if video_row else None

    artist = artist_override or ((artist_row.get("about") or artist_row.get("name") or "") if artist_row else "")
    video = video_override or (video_row["title"] if video_row else "")
    intent = intent_override or (prompt_row.get("intent_component") or "")
    audience = audience_override or (prompt_row.get("audience_component") or "")

    system_prompt = _fill_template(
        prompt_row["template"],
        artist=artist,
        video=video,
        intent=intent,
        audience=audience,
    )
    raw_text = transcript_row["raw_text"]
    max_chars = int(os.environ.get("YT_ARTIST_MAX_TRANSCRIPT_CHARS", _DEFAULT_MAX_TRANSCRIPT_CHARS))

    # Resolve strategy
    strat = strategy or _get_strategy()
    fits_in_context = max_chars <= 0 or len(raw_text) <= max_chars

    if strat == "truncate":
        # Legacy behavior: truncate then single-pass
        if not fits_in_context and max_chars > 0:
            original_len = len(raw_text)
            raw_text = raw_text[:max_chars]
            est_tokens_saved = (original_len - max_chars) // 4
            log.warning(
                "Transcript for %s truncated from %d to %d chars (~%d tokens saved). "
                "Set YT_ARTIST_MAX_TRANSCRIPT_CHARS to adjust.",
                video_id,
                original_len,
                max_chars,
                est_tokens_saved,
            )
        summary_text = _summarize_single(system_prompt, raw_text, model)

    elif strat == "map-reduce":
        if fits_in_context:
            summary_text = _summarize_single(system_prompt, raw_text, model)
        else:
            summary_text = _summarize_map_reduce(system_prompt, raw_text, max_chars, model)

    elif strat == "refine":
        if fits_in_context:
            summary_text = _summarize_single(system_prompt, raw_text, model)
        else:
            summary_text = _summarize_refine(system_prompt, raw_text, max_chars, model)

    else:  # "auto"
        if fits_in_context:
            summary_text = _summarize_single(system_prompt, raw_text, model)
        else:
            log.info(
                "Auto strategy: transcript (%d chars) exceeds limit (%d), using map-reduce.", len(raw_text), max_chars
            )
            summary_text = _summarize_map_reduce(system_prompt, raw_text, max_chars, model)

    if not summary_text.strip():
        raise ValueError(f"LLM returned empty summary for video_id={video_id}")

    storage.upsert_summary(
        video_id=video_id,
        prompt_id=prompt_id,
        content=summary_text,
    )
    return f"{video_id}:{prompt_id}"
