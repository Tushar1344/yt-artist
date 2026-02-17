"""Build artist 'about' text from web search or LLM fallback; used by build-artist-prompt."""

from __future__ import annotations

import logging

from yt_artist.llm import complete

log = logging.getLogger("yt_artist.artist_prompt")


def _search_about(query: str, max_results: int = 3) -> str:
    """Return concatenated snippets from web search. Uses duckduckgo-search if available."""
    try:
        from duckduckgo_search import DDGS

        with DDGS() as ddgs:
            results = list(ddgs.text(query, max_results=max_results))
        if not results:
            return ""
        return " ".join((r.get("body") or r.get("title") or "") for r in results)
    except ImportError:
        log.warning(
            "duckduckgo-search not installed; using LLM-only fallback. "
            "For better results: pip install yt-artist[search]"
        )
        return ""
    except Exception as exc:
        log.warning("Web search failed (continuing with LLM fallback): %s", exc)
        return ""


def build_artist_about(
    artist_id: str,
    artist_name: str,
    channel_url: str,
) -> str:
    """
    Build a short 'about' text for the artist: try web search first, else LLM from channel name + URL.
    Does not write to storage; caller can call storage.set_artist_about(artist_id, about).
    """
    query = f"{artist_name} YouTube channel"
    raw = _search_about(query)
    if raw and len(raw.strip()) > 50:
        # Summarize search results into a short "about" (2-3 sentences)
        system = "You are a concise editor. Summarize the following search results about a YouTube channel into 2-3 sentences. Output only the summary, no preamble."
        about = complete(system_prompt=system, user_content=raw[:3000])
        return (about or "").strip() or raw[:500]
    # Fallback: LLM from channel name + URL only
    system = "You are a concise editor. Given a YouTube channel name and URL, write 1-2 sentences describing what kind of channel it might be. Output only the description."
    user = f"Channel name: {artist_name}\nChannel URL: {channel_url}"
    about = complete(system_prompt=system, user_content=user)
    return (about or "").strip() or f"{artist_name} ({channel_url})"
