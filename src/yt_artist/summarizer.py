"""Generate AI summary from transcript using prompt template; save to DB.

Supports three strategies for long transcripts:
- truncate: cut text to max_chars (legacy default)
- map-reduce: chunk → summarize each → combine summaries
- refine: iteratively refine a rolling summary with each chunk
- auto: single-pass if fits, map-reduce if too long (new default)

Prompts are managed via BAML (.baml files in baml_src/) through the prompts module.
"""

from __future__ import annotations

import logging
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import List, Optional

from yt_artist import prompts
from yt_artist.storage import Storage

log = logging.getLogger("yt_artist.summarizer")

# Approximate character limit for transcripts sent to LLM.
# ~4 chars/token → 30 000 chars ≈ 7 500 tokens, leaving headroom for system prompt + response.
# Override with YT_ARTIST_MAX_TRANSCRIPT_CHARS env var.
_DEFAULT_MAX_TRANSCRIPT_CHARS = 30_000

# Overlap between chunks to preserve cross-boundary context.
_CHUNK_OVERLAP = 500

# Max concurrent workers for map-reduce chunk summaries.
# Effective against OpenAI API; local Ollama processes sequentially (set to 1 to skip pool overhead).
_MAP_CONCURRENCY = int(os.environ.get("YT_ARTIST_MAP_CONCURRENCY", "3"))

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


def _summarize_single(raw_text: str, artist: str, video_title: str) -> str:
    """Summarize text in a single LLM call (fits within context window)."""
    return prompts.summarize_single_pass(transcript=raw_text, artist=artist, video_title=video_title)


# ---------------------------------------------------------------------------
# Strategy: map-reduce
# ---------------------------------------------------------------------------


def _summarize_map_reduce(
    raw_text: str,
    max_chars: int,
    artist: str,
    video_title: str,
) -> str:
    """Chunk the transcript → summarize each chunk → combine summaries.

    If the combined chunk summaries still exceed *max_chars*, recursively reduce.
    """
    chunks = _chunk_text(raw_text, max_chars)
    n = len(chunks)
    log.info("Map-reduce: splitting %d chars into %d chunks of ~%d chars each.", len(raw_text), n, max_chars)

    # Map: summarize each chunk (parallel when multiple chunks + concurrency > 1)
    max_workers = min(n, _MAP_CONCURRENCY)

    if max_workers <= 1:
        # Single chunk or concurrency disabled — direct call, no pool overhead
        chunk_summaries: List[str] = []
        for i, chunk in enumerate(chunks, 1):
            summary = prompts.summarize_chunk(chunk=chunk, chunk_index=i, total_chunks=n)
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
                    prompts.summarize_chunk,
                    chunk=chunk,
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
        return _summarize_map_reduce(combined, max_chars, artist, video_title)

    # Final reduce with artist/video context
    final = prompts.reduce_chunk_summaries(section_summaries=combined, artist=artist, video_title=video_title)
    log.info("Map-reduce: final summary produced (%d chars).", len(final))
    return final


# ---------------------------------------------------------------------------
# Strategy: refine (iterative/rolling)
# ---------------------------------------------------------------------------


def _summarize_refine(
    raw_text: str,
    max_chars: int,
    artist: str,
    video_title: str,
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

    # First chunk: generate initial summary via single-pass
    summary = prompts.summarize_single_pass(transcript=chunks[0], artist=artist, video_title=video_title)
    log.info("Refine: initial summary from chunk 1/%d (%d chars).", n, len(summary))

    # Subsequent chunks: refine
    for i, chunk in enumerate(chunks[1:], 2):
        summary = prompts.refine_summary(prev_summary=summary, chunk=chunk, chunk_index=i, total_chunks=n)
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
    """Load transcript and prompt, call LLM via BAML, save Summary.

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
        summary_text = _summarize_single(raw_text, artist, video_title)

    elif strat == "map-reduce":
        if fits_in_context:
            summary_text = _summarize_single(raw_text, artist, video_title)
        else:
            summary_text = _summarize_map_reduce(raw_text, max_chars, artist, video_title)

    elif strat == "refine":
        if fits_in_context:
            summary_text = _summarize_single(raw_text, artist, video_title)
        else:
            summary_text = _summarize_refine(raw_text, max_chars, artist, video_title)

    else:  # "auto"
        if fits_in_context:
            summary_text = _summarize_single(raw_text, artist, video_title)
        else:
            log.info(
                "Auto strategy: transcript (%d chars) exceeds limit (%d), using map-reduce.", len(raw_text), max_chars
            )
            summary_text = _summarize_map_reduce(raw_text, max_chars, artist, video_title)

    if not summary_text.strip():
        raise ValueError(f"LLM returned empty summary for video_id={video_id}")

    storage.upsert_summary(
        video_id=video_id,
        prompt_id=prompt_id,
        content=summary_text,
    )
    return f"{video_id}:{prompt_id}"
