"""Centralized configuration from environment variables.

Typed frozen dataclasses with lru_cache accessors. Each config is read once
from env vars and cached for the process lifetime. Call ``<getter>.cache_clear()``
in tests to force re-read.
"""

from __future__ import annotations

import functools
import os
from dataclasses import dataclass

# ---------------------------------------------------------------------------
# YouTube / yt-dlp configuration
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class YouTubeConfig:
    """YouTube and yt-dlp related settings."""

    inter_video_delay: float  # YT_ARTIST_INTER_VIDEO_DELAY
    sleep_requests: str  # YT_ARTIST_SLEEP_REQUESTS
    sleep_subtitles: str  # YT_ARTIST_SLEEP_SUBTITLES
    cookies_browser: str  # YT_ARTIST_COOKIES_BROWSER
    cookies_file: str  # YT_ARTIST_COOKIES_FILE
    po_token: str  # YT_ARTIST_PO_TOKEN


def _parse_delay(raw: str, default: float) -> float:
    """Parse a float from a raw env string, returning *default* on failure."""
    raw = raw.strip()
    if raw:
        try:
            return max(0.0, float(raw))
        except ValueError:
            pass
    return default


@functools.lru_cache(maxsize=1)
def get_youtube_config() -> YouTubeConfig:
    """Return YouTube config from environment variables (cached)."""
    return YouTubeConfig(
        inter_video_delay=_parse_delay(
            os.environ.get("YT_ARTIST_INTER_VIDEO_DELAY", ""),
            2.0,
        ),
        sleep_requests=(os.environ.get("YT_ARTIST_SLEEP_REQUESTS") or "").strip() or "1",
        sleep_subtitles=(os.environ.get("YT_ARTIST_SLEEP_SUBTITLES") or "").strip() or "3",
        cookies_browser=(os.environ.get("YT_ARTIST_COOKIES_BROWSER") or "").strip(),
        cookies_file=(os.environ.get("YT_ARTIST_COOKIES_FILE") or "").strip(),
        po_token=(os.environ.get("YT_ARTIST_PO_TOKEN") or "").strip(),
    )


# ---------------------------------------------------------------------------
# LLM configuration
# ---------------------------------------------------------------------------

OLLAMA_BASE_URL = "http://localhost:11434/v1"
OLLAMA_DEFAULT_MODEL = "mistral"


def _is_ollama(base_url: str) -> bool:
    return "11434" in base_url or "ollama" in base_url.lower()


@dataclass(frozen=True)
class LLMConfig:
    """LLM endpoint settings."""

    base_url: str  # OPENAI_BASE_URL
    api_key: str  # OPENAI_API_KEY
    model: str  # OPENAI_MODEL (or derived default)
    is_ollama: bool  # derived from base_url


@functools.lru_cache(maxsize=1)
def get_llm_config() -> LLMConfig:
    """Return LLM config from environment variables (cached)."""
    base_url = (os.environ.get("OPENAI_BASE_URL") or "").strip()
    api_key = (os.environ.get("OPENAI_API_KEY") or "").strip()
    model_env = (os.environ.get("OPENAI_MODEL") or "").strip()

    # Determine base_url, api_key, and default model
    if base_url and _is_ollama(base_url):
        resolved_url = base_url
        resolved_key = "ollama"
        default_model = OLLAMA_DEFAULT_MODEL
    elif not base_url:
        if api_key:
            resolved_url = "https://api.openai.com/v1"
            resolved_key = api_key
            default_model = "gpt-4o-mini"
        else:
            resolved_url = OLLAMA_BASE_URL
            resolved_key = "ollama"
            default_model = OLLAMA_DEFAULT_MODEL
    else:
        resolved_url = base_url
        resolved_key = api_key or "ollama"
        default_model = "gpt-4o-mini"

    return LLMConfig(
        base_url=resolved_url,
        api_key=resolved_key,
        model=model_env or default_model,
        is_ollama=_is_ollama(resolved_url),
    )


# ---------------------------------------------------------------------------
# Concurrency configuration
# ---------------------------------------------------------------------------

# Maximum concurrency kept conservative to avoid YouTube rate-limits.
_DEFAULT_MAX_CONCURRENCY = 3


@dataclass(frozen=True)
class ConcurrencyConfig:
    """Concurrency budget for bulk operations."""

    max_concurrency: int  # ceiling for --concurrency (hardcoded 3)
    map_concurrency: int  # YT_ARTIST_MAP_CONCURRENCY

    def split_budget(self, total: int) -> tuple[int, int]:
        """Split *total* workers between transcribe and summarize.

        Returns ``(transcribe_workers, summarize_workers)``.
        Transcribe gets more workers (YouTube I/O is the bottleneck).
        """
        if total <= 2:
            return (1, 1)
        return (total - 1, 1)


@functools.lru_cache(maxsize=1)
def get_concurrency_config() -> ConcurrencyConfig:
    """Return concurrency config (cached)."""
    raw = (os.environ.get("YT_ARTIST_MAP_CONCURRENCY") or "").strip()
    try:
        map_c = int(raw) if raw else _DEFAULT_MAX_CONCURRENCY
    except ValueError:
        map_c = _DEFAULT_MAX_CONCURRENCY
    return ConcurrencyConfig(
        max_concurrency=_DEFAULT_MAX_CONCURRENCY,
        map_concurrency=max(1, map_c),
    )


# ---------------------------------------------------------------------------
# Application-wide configuration
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AppConfig:
    """Application-wide settings."""

    log_level: str  # YT_ARTIST_LOG_LEVEL
    data_dir_env: str  # YT_ARTIST_DATA_DIR (raw env value)
    db_env: str  # YT_ARTIST_DB (raw env value)
    default_prompt: str  # YT_ARTIST_DEFAULT_PROMPT
    max_transcript_chars: int  # YT_ARTIST_MAX_TRANSCRIPT_CHARS
    summarize_strategy: str  # YT_ARTIST_SUMMARIZE_STRATEGY


@functools.lru_cache(maxsize=1)
def get_app_config() -> AppConfig:
    """Return application config from environment variables (cached)."""
    # Parse max_transcript_chars with validation
    raw_chars = (os.environ.get("YT_ARTIST_MAX_TRANSCRIPT_CHARS") or "").strip()
    try:
        max_chars = int(raw_chars) if raw_chars else 30_000
    except ValueError:
        max_chars = 30_000

    # Parse strategy with validation
    strategy = (os.environ.get("YT_ARTIST_SUMMARIZE_STRATEGY") or "auto").strip().lower()
    if strategy not in ("auto", "truncate", "map-reduce", "refine"):
        strategy = "auto"

    return AppConfig(
        log_level=(os.environ.get("YT_ARTIST_LOG_LEVEL") or "INFO").strip().upper(),
        data_dir_env=(os.environ.get("YT_ARTIST_DATA_DIR") or "").strip(),
        db_env=(os.environ.get("YT_ARTIST_DB") or "").strip(),
        default_prompt=(os.environ.get("YT_ARTIST_DEFAULT_PROMPT") or "").strip() or "default",
        max_transcript_chars=max_chars,
        summarize_strategy=strategy,
    )
