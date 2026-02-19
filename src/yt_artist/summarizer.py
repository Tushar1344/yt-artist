"""Generate AI summary from transcript using DB-stored prompt template; save to DB.

Supports three strategies for long transcripts:
- truncate: cut text to max_chars (legacy default)
- map-reduce: chunk → summarize each → combine summaries
- refine: iteratively refine a rolling summary with each chunk
- auto: single-pass if fits, map-reduce if too long (new default)

Prompts are stored in the DB (prompts table) and rendered via _fill_template().
Users can customize the main summary prompt with ``yt-artist add-prompt``.
Chunk/reduce/refine phases use internal prompts (not user-customizable).
"""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import List, Optional

from yt_artist.config import get_app_config
from yt_artist.llm import complete as llm_complete
from yt_artist.storage import Storage

log = logging.getLogger("yt_artist.summarizer")

# Overlap between chunks to preserve cross-boundary context.
_CHUNK_OVERLAP = 500

# Valid strategy names.
STRATEGIES = ("auto", "truncate", "map-reduce", "refine")

# ---------------------------------------------------------------------------
# Internal prompts for chunk / reduce / refine phases.
# These are NOT user-customizable — the DB template controls only the
# single-pass and final-reduce phases (the "creative" prompts).
# ---------------------------------------------------------------------------

_CHUNK_SYSTEM_PROMPT = (
    "Summarize this section of a transcript. Preserve key facts, data points, "
    "quotes, and conclusions. Be thorough — this is section {chunk_index} of {total_chunks}.\n\n"
    "Only include information explicitly stated in the transcript. "
    "Do not invent names, quotes, statistics, or facts not present in the text."
)

_REDUCE_SUFFIX = (
    "\n\nThe following are summaries of consecutive sections of a transcript. "
    "Combine them into a single coherent summary, preserving all key points.\n\n"
    "Only include facts and claims from the section summaries below. "
    "Do not add new information, names, or details not found in these summaries."
)

_REFINE_SYSTEM_PROMPT = (
    "You have a summary so far and a new section of transcript. "
    "Update the summary to incorporate the key points from this new section. "
    "Preserve all important information from the existing summary.\n\n"
    "Only include facts, names, and quotes that appear in the existing summary "
    "or in the new transcript section below. Do not invent any details."
)


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


def _summarize_single(raw_text: str, system_prompt: str) -> str:
    """Summarize text in a single LLM call (fits within context window)."""
    return llm_complete(system_prompt=system_prompt, user_content=raw_text)


def _summarize_chunk(chunk: str, chunk_index: int, total_chunks: int) -> str:
    """Map phase helper — summarize one chunk using internal chunk prompt."""
    prompt = _CHUNK_SYSTEM_PROMPT.format(chunk_index=chunk_index, total_chunks=total_chunks)
    return llm_complete(system_prompt=prompt, user_content=chunk)


# ---------------------------------------------------------------------------
# Strategy: map-reduce
# ---------------------------------------------------------------------------


def _summarize_map_reduce(
    raw_text: str,
    max_chars: int,
    system_prompt: str,
) -> str:
    """Chunk the transcript → summarize each chunk → combine summaries.

    If the combined chunk summaries still exceed *max_chars*, recursively reduce.
    The user's DB template (*system_prompt*) is used for the final reduce phase.
    """
    chunks = _chunk_text(raw_text, max_chars)
    n = len(chunks)
    log.info("Map-reduce: splitting %d chars into %d chunks of ~%d chars each.", len(raw_text), n, max_chars)

    # Map: summarize each chunk (parallel when multiple chunks + concurrency > 1)
    from yt_artist.config import get_concurrency_config

    max_workers = min(n, get_concurrency_config().map_concurrency)

    if max_workers <= 1:
        # Single chunk or concurrency disabled — direct call, no pool overhead
        chunk_summaries: List[str] = []
        for i, chunk in enumerate(chunks, 1):
            summary = _summarize_chunk(chunk, chunk_index=i, total_chunks=n)
            if summary.strip():
                chunk_summaries.append(summary.strip())
            log.info("Map-reduce: chunk %d/%d summarized (%d chars → %d chars).", i, n, len(chunk), len(summary))
    else:
        log.info("Map-reduce: parallelizing %d chunks with %d workers.", n, max_workers)
        chunk_results: dict[int, str] = {}
        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            future_to_idx = {}
            for i, chunk in enumerate(chunks, 1):
                fut = pool.submit(
                    _summarize_chunk,
                    chunk,
                    chunk_index=i,
                    total_chunks=n,
                )
                future_to_idx[fut] = (i, chunk)
            for fut in as_completed(future_to_idx):
                idx, chunk = future_to_idx[fut]
                summary = fut.result()  # propagates exceptions
                log.info(
                    "Map-reduce: chunk %d/%d summarized (%d chars → %d chars).",
                    idx,
                    n,
                    len(chunk),
                    len(summary),
                )
                if summary.strip():
                    chunk_results[idx] = summary.strip()
        # Reassemble in original chunk order
        chunk_summaries = [chunk_results[i] for i in sorted(chunk_results.keys())]

    if not chunk_summaries:
        raise ValueError("Map-reduce produced no chunk summaries.")

    # Reduce: combine chunk summaries
    combined = "\n\n---\n\n".join(f"Section {i}:\n{s}" for i, s in enumerate(chunk_summaries, 1))

    # Recursive reduce if combined summaries still too long
    if len(combined) > max_chars:
        log.info("Map-reduce: combined summaries (%d chars) exceed limit, reducing recursively.", len(combined))
        return _summarize_map_reduce(combined, max_chars, system_prompt)

    # Final reduce: user's DB template + reduce instructions
    reduce_prompt = system_prompt + _REDUCE_SUFFIX
    final = llm_complete(system_prompt=reduce_prompt, user_content=combined)
    log.info("Map-reduce: final summary produced (%d chars).", len(final))
    return final


# ---------------------------------------------------------------------------
# Strategy: refine (iterative/rolling)
# ---------------------------------------------------------------------------


def _summarize_refine(
    raw_text: str,
    max_chars: int,
    system_prompt: str,
) -> str:
    """Iteratively refine a rolling summary with each chunk.

    Chunk 1 → initial summary via user's DB template (*system_prompt*).
    Each subsequent chunk updates the summary using the internal refine prompt.
    Uses smaller chunks to leave room for the rolling summary in the context.
    """
    # Leave ~40% of context for the rolling summary
    refine_chunk_size = int(max_chars * 0.6)
    chunks = _chunk_text(raw_text, refine_chunk_size)
    n = len(chunks)
    log.info("Refine: splitting %d chars into %d chunks of ~%d chars each.", len(raw_text), n, refine_chunk_size)

    # First chunk: generate initial summary via user's prompt
    summary = llm_complete(system_prompt=system_prompt, user_content=chunks[0])
    log.info("Refine: initial summary from chunk 1/%d (%d chars).", n, len(summary))

    # Subsequent chunks: refine with internal prompt
    for i, chunk in enumerate(chunks[1:], 2):
        user_content = f"Current summary:\n{summary}\n\nNew transcript section ({i}/{n}):\n{chunk}"
        summary = llm_complete(system_prompt=_REFINE_SYSTEM_PROMPT, user_content=user_content)
        log.info("Refine: updated summary with chunk %d/%d (%d chars).", i, n, len(summary))

    return summary


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------


def _get_strategy() -> str:
    """Return the summarization strategy from config (env var or default 'auto')."""
    return get_app_config().summarize_strategy


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
    """Load transcript and prompt template, render template, call LLM, save summary.

    The prompt template is loaded from the DB and rendered with artist/video/intent/audience
    using _fill_template(). The rendered prompt becomes the LLM system prompt.

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
    video_title = video_override or (video_row["title"] if video_row else "")

    # Render the DB template with context variables
    system_prompt = _fill_template(
        prompt_row["template"],
        artist=artist,
        video=video_title,
        intent=intent_override or "",
        audience=audience_override or "",
    )

    raw_text = transcript_row["raw_text"]
    max_chars = get_app_config().max_transcript_chars

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
        summary_text = _summarize_single(raw_text, system_prompt)

    elif strat == "map-reduce":
        if fits_in_context:
            summary_text = _summarize_single(raw_text, system_prompt)
        else:
            summary_text = _summarize_map_reduce(raw_text, max_chars, system_prompt)

    elif strat == "refine":
        if fits_in_context:
            summary_text = _summarize_single(raw_text, system_prompt)
        else:
            summary_text = _summarize_refine(raw_text, max_chars, system_prompt)

    else:  # "auto"
        if fits_in_context:
            summary_text = _summarize_single(raw_text, system_prompt)
        else:
            log.info(
                "Auto strategy: transcript (%d chars) exceeds limit (%d), using map-reduce.", len(raw_text), max_chars
            )
            summary_text = _summarize_map_reduce(raw_text, max_chars, system_prompt)

    if not summary_text.strip():
        raise ValueError(f"LLM returned empty summary for video_id={video_id}")

    from yt_artist.hashing import content_hash
    from yt_artist.llm import get_model_name

    p_hash = content_hash(prompt_row["template"])
    t_hash = content_hash(transcript_row["raw_text"])

    storage.upsert_summary(
        video_id=video_id,
        prompt_id=prompt_id,
        content=summary_text,
        model=get_model_name(model),
        strategy=strat,
        prompt_hash=p_hash,
        transcript_hash=t_hash,
    )
    return f"{video_id}:{prompt_id}"
