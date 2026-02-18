"""OpenAI-compatible LLM client; defaults to local Ollama when no API key is set."""

from __future__ import annotations

import logging
import os
import socket
import time as _time
from typing import Any, Optional, Tuple
from urllib.parse import urlparse

try:
    from openai import OpenAI
except ImportError:
    OpenAI = None  # type: ignore[misc, assignment]

log = logging.getLogger("yt_artist.llm")

OLLAMA_BASE_URL = "http://localhost:11434/v1"
OLLAMA_DEFAULT_MODEL = "mistral"


def _is_ollama(base_url: str) -> bool:
    return "11434" in base_url or "ollama" in base_url.lower()


def _resolve_config() -> Tuple[str, str, str]:
    """Return (base_url, api_key, default_model) from environment."""
    base_url = (os.environ.get("OPENAI_BASE_URL") or "").strip()
    api_key = (os.environ.get("OPENAI_API_KEY") or "").strip()

    if base_url and _is_ollama(base_url):
        return (base_url, "ollama", OLLAMA_DEFAULT_MODEL)

    if not base_url:
        if api_key:
            return ("https://api.openai.com/v1", api_key, "gpt-4o-mini")
        return (OLLAMA_BASE_URL, "ollama", OLLAMA_DEFAULT_MODEL)

    return (base_url, api_key or "ollama", "gpt-4o-mini")


def get_model_name(model: Optional[str] = None) -> str:
    """Return effective LLM model name from argument, env, or default."""
    _, _, default_model = _resolve_config()
    return model or os.environ.get("OPENAI_MODEL") or default_model


def check_connectivity() -> None:
    """Fast pre-flight check: verify the LLM endpoint is reachable (TCP connect).

    Raises RuntimeError with actionable guidance if the endpoint is down.
    Call this before starting long batch operations to fail fast.
    """
    base_url, _, _ = _resolve_config()
    parsed = urlparse(base_url)
    host = parsed.hostname or "localhost"
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    using_ollama = _is_ollama(base_url)
    try:
        sock = socket.create_connection((host, port), timeout=5)
        sock.close()
    except OSError:
        if using_ollama:
            raise RuntimeError(
                f"Cannot connect to Ollama at {host}:{port}. "
                "Is Ollama running? Start it with: ollama serve\n"
                "Then pull a model: ollama run mistral\n"
                "Or set OPENAI_API_KEY and OPENAI_BASE_URL to use a remote LLM provider."
            )
        raise RuntimeError(
            f"Cannot connect to LLM endpoint at {host}:{port} ({base_url}). "
            "Check OPENAI_BASE_URL and ensure the server is running."
        )


def get_config_summary() -> dict:
    """Return current LLM configuration for diagnostics (used by ``yt-artist doctor``).

    Returns a dict with keys: base_url, api_key_set (bool), model, is_ollama (bool).
    """
    base_url, api_key, default_model = _resolve_config()
    model = os.environ.get("OPENAI_MODEL") or default_model
    return {
        "base_url": base_url,
        "api_key_set": bool(api_key and api_key != "ollama"),
        "model": model,
        "is_ollama": _is_ollama(base_url),
    }


_cached_client: Optional[Any] = None
_cached_client_key: Optional[Tuple[str, str]] = None


def get_client() -> Any:
    """Return OpenAI client, reusing a cached instance for connection keep-alive.

    The client is recreated only when OPENAI_BASE_URL or OPENAI_API_KEY changes.
    This avoids TCP/TLS handshake overhead on every LLM call during bulk operations.
    """
    global _cached_client, _cached_client_key
    if OpenAI is None:
        raise RuntimeError("openai package is required; install with: pip install openai")
    base_url, api_key, _ = _resolve_config()
    key = (base_url, api_key)
    if _cached_client is not None and _cached_client_key == key:
        return _cached_client
    _cached_client = OpenAI(base_url=base_url, api_key=api_key)
    _cached_client_key = key
    return _cached_client


_MAX_LLM_RETRIES = 3
_LLM_INITIAL_BACKOFF = 2  # seconds


def _is_transient(exc: Exception) -> bool:
    """Return True if the exception looks transient (worth retrying)."""
    msg = str(exc).lower()
    # Connection/network errors
    if isinstance(exc, (ConnectionError, TimeoutError, OSError)):
        return True
    # HTTP 429, 500, 502, 503, 504 from the OpenAI SDK
    for code in ("429", "500", "502", "503", "504"):
        if code in msg:
            return True
    if "rate limit" in msg or "too many requests" in msg:
        return True
    if "timeout" in msg or "timed out" in msg:
        return True
    if "connection" in msg and ("refused" in msg or "reset" in msg or "error" in msg):
        return True
    return False


def complete(
    system_prompt: str,
    user_content: str,
    *,
    model: Optional[str] = None,
    max_retries: int = _MAX_LLM_RETRIES,
) -> str:
    """
    Call chat completion with system and user messages.
    Uses OPENAI_MODEL or defaults to mistral for Ollama, gpt-4o-mini for OpenAI.
    Retries up to *max_retries* times on transient errors (429, 5xx, timeouts).
    Raises RuntimeError on persistent API errors so callers can handle gracefully.
    """
    client = get_client()
    _, _, default_model = _resolve_config()
    model = model or os.environ.get("OPENAI_MODEL") or default_model
    backoff = _LLM_INITIAL_BACKOFF
    last_exc: Optional[Exception] = None

    for attempt in range(max_retries + 1):
        try:
            resp = client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_content},
                ],
            )
            choice = resp.choices[0] if resp.choices else None
            if not choice or not getattr(choice, "message", None):
                log.warning("LLM returned no choices/message for model=%s", model)
                return ""
            return (choice.message.content or "").strip()
        except Exception as exc:
            last_exc = exc
            if attempt < max_retries and _is_transient(exc):
                log.warning(
                    "LLM call failed (attempt %d/%d), retrying in %ds: %s", attempt + 1, max_retries + 1, backoff, exc
                )
                _time.sleep(backoff)
                backoff = min(backoff * 2, 30)
                continue
            # Non-transient or retries exhausted
            base_url, _, _ = _resolve_config()
            if _is_ollama(base_url):
                log.error("LLM API call failed (Ollama at %s): %s", base_url, exc)
                raise RuntimeError(
                    f"LLM API call failed. Is Ollama running? Start with: ollama serve\nError: {exc}"
                ) from exc
            log.error("LLM API call failed (%s): %s", base_url, exc)
            raise RuntimeError(f"LLM API call failed ({base_url}): {exc}") from exc
    # Should not reach here, but just in case
    raise RuntimeError(f"LLM API call failed after {max_retries + 1} attempts: {last_exc}")
