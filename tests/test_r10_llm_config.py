"""Tests for R10: _resolve_config() deduplication in llm.py."""
import os
from unittest.mock import patch

from yt_artist.llm import _resolve_config, OLLAMA_BASE_URL, OLLAMA_DEFAULT_MODEL


def test_resolve_config_no_env():
    """No env vars → defaults to local Ollama."""
    with patch.dict(os.environ, {}, clear=True):
        base_url, api_key, model = _resolve_config()
    assert base_url == OLLAMA_BASE_URL
    assert api_key == "ollama"
    assert model == OLLAMA_DEFAULT_MODEL


def test_resolve_config_with_api_key():
    """OPENAI_API_KEY set → uses OpenAI endpoint."""
    with patch.dict(os.environ, {"OPENAI_API_KEY": "sk-test"}, clear=True):
        base_url, api_key, model = _resolve_config()
    assert base_url == "https://api.openai.com/v1"
    assert api_key == "sk-test"
    assert model == "gpt-4o-mini"


def test_resolve_config_custom_ollama_url():
    """OPENAI_BASE_URL pointing to Ollama → uses Ollama config."""
    with patch.dict(os.environ, {"OPENAI_BASE_URL": "http://myhost:11434/v1"}, clear=True):
        base_url, api_key, model = _resolve_config()
    assert base_url == "http://myhost:11434/v1"
    assert api_key == "ollama"
    assert model == OLLAMA_DEFAULT_MODEL


def test_resolve_config_custom_non_ollama_url():
    """OPENAI_BASE_URL pointing to custom non-Ollama endpoint."""
    with patch.dict(os.environ, {"OPENAI_BASE_URL": "https://custom.llm.api/v1", "OPENAI_API_KEY": "key123"}, clear=True):
        base_url, api_key, model = _resolve_config()
    assert base_url == "https://custom.llm.api/v1"
    assert api_key == "key123"
    assert model == "gpt-4o-mini"


def test_resolve_config_custom_url_no_key_defaults_ollama():
    """Custom non-Ollama URL without API key → falls back to 'ollama' key."""
    with patch.dict(os.environ, {"OPENAI_BASE_URL": "https://custom.llm.api/v1"}, clear=True):
        base_url, api_key, model = _resolve_config()
    assert base_url == "https://custom.llm.api/v1"
    assert api_key == "ollama"
