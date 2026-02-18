"""Pytest fixtures: temp DB for tests."""

import pytest

from yt_artist import storage
from yt_artist.config import (
    get_app_config,
    get_concurrency_config,
    get_llm_config,
    get_youtube_config,
)


@pytest.fixture(autouse=True)
def _clear_config_caches():
    """Clear all config lru_caches before each test so env var patches take effect."""
    get_youtube_config.cache_clear()
    get_llm_config.cache_clear()
    get_app_config.cache_clear()
    get_concurrency_config.cache_clear()
    yield
    get_youtube_config.cache_clear()
    get_llm_config.cache_clear()
    get_app_config.cache_clear()
    get_concurrency_config.cache_clear()


@pytest.fixture
def db_path(tmp_path):
    """Path to a temporary DB file (created and schema applied by storage)."""
    return str(tmp_path / "test.db")


@pytest.fixture
def store(db_path):
    """Storage instance with temp DB; DB is created and schema applied."""
    st = storage.Storage(db_path=db_path)
    st.ensure_schema()
    return st
