"""Generate AI summary from transcript using prompt template; save to DB."""
from __future__ import annotations

import logging
from typing import Optional

from yt_artist.llm import complete
from yt_artist.storage import Storage

log = logging.getLogger("yt_artist.summarizer")

# Approximate character limit for transcripts sent to LLM.
# ~4 chars/token → 30 000 chars ≈ 7 500 tokens, leaving headroom for system prompt + response.
# Override with YT_ARTIST_MAX_TRANSCRIPT_CHARS env var.
_DEFAULT_MAX_TRANSCRIPT_CHARS = 30_000


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
) -> str:
    """
    Load transcript and prompt, fill template, call LLM, save Summary.
    Returns summary id (we use video_id+prompt_id; the row is upserted).
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

    artist = artist_override or (
        (artist_row.get("about") or artist_row.get("name") or "") if artist_row else ""
    )
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

    # Truncate very long transcripts to control LLM token usage / cost.
    import os
    max_chars = int(os.environ.get("YT_ARTIST_MAX_TRANSCRIPT_CHARS", _DEFAULT_MAX_TRANSCRIPT_CHARS))
    if max_chars > 0 and len(raw_text) > max_chars:
        original_len = len(raw_text)
        raw_text = raw_text[:max_chars]
        est_tokens_saved = (original_len - max_chars) // 4
        log.warning(
            "Transcript for %s truncated from %d to %d chars (~%d tokens saved). "
            "Set YT_ARTIST_MAX_TRANSCRIPT_CHARS to adjust.",
            video_id, original_len, max_chars, est_tokens_saved,
        )

    user_content = "Transcript:\n\n" + raw_text

    summary_text = complete(
        system_prompt=system_prompt,
        user_content=user_content,
        model=model,
    )

    if not summary_text.strip():
        raise ValueError(f"LLM returned empty summary for video_id={video_id}")

    storage.upsert_summary(
        video_id=video_id,
        prompt_id=prompt_id,
        content=summary_text,
    )
    return f"{video_id}:{prompt_id}"
