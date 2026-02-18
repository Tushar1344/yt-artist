"""Tests for config.py — centralized configuration from environment variables."""

from unittest.mock import patch

from yt_artist.config import (
    ConcurrencyConfig,
    get_app_config,
    get_concurrency_config,
    get_llm_config,
    get_youtube_config,
)


def _clear_all_caches():
    """Clear lru_cache on all config getters so env var changes take effect."""
    get_youtube_config.cache_clear()
    get_llm_config.cache_clear()
    get_app_config.cache_clear()
    get_concurrency_config.cache_clear()


# ---------------------------------------------------------------------------
# YouTubeConfig
# ---------------------------------------------------------------------------


class TestYouTubeConfig:
    def setup_method(self):
        _clear_all_caches()

    def teardown_method(self):
        _clear_all_caches()

    def test_defaults_when_no_env(self):
        with patch.dict("os.environ", {}, clear=True):
            cfg = get_youtube_config()
        assert cfg.inter_video_delay == 2.0
        assert cfg.sleep_requests == "1"
        assert cfg.sleep_subtitles == "3"
        assert cfg.cookies_browser == ""
        assert cfg.cookies_file == ""
        assert cfg.po_token == ""

    def test_env_overrides(self):
        env = {
            "YT_ARTIST_INTER_VIDEO_DELAY": "5.5",
            "YT_ARTIST_SLEEP_REQUESTS": "2",
            "YT_ARTIST_SLEEP_SUBTITLES": "4",
            "YT_ARTIST_COOKIES_BROWSER": "chrome",
            "YT_ARTIST_COOKIES_FILE": "/tmp/cookies.txt",
            "YT_ARTIST_PO_TOKEN": "web.subs+abc",
        }
        with patch.dict("os.environ", env, clear=True):
            cfg = get_youtube_config()
        assert cfg.inter_video_delay == 5.5
        assert cfg.sleep_requests == "2"
        assert cfg.sleep_subtitles == "4"
        assert cfg.cookies_browser == "chrome"
        assert cfg.cookies_file == "/tmp/cookies.txt"
        assert cfg.po_token == "web.subs+abc"

    def test_invalid_delay_falls_back(self):
        with patch.dict("os.environ", {"YT_ARTIST_INTER_VIDEO_DELAY": "not-a-number"}, clear=True):
            cfg = get_youtube_config()
        assert cfg.inter_video_delay == 2.0

    def test_negative_delay_clamped_to_zero(self):
        with patch.dict("os.environ", {"YT_ARTIST_INTER_VIDEO_DELAY": "-5"}, clear=True):
            cfg = get_youtube_config()
        assert cfg.inter_video_delay == 0.0


# ---------------------------------------------------------------------------
# LLMConfig
# ---------------------------------------------------------------------------


class TestLLMConfig:
    def setup_method(self):
        _clear_all_caches()

    def teardown_method(self):
        _clear_all_caches()

    def test_defaults_ollama(self):
        with patch.dict("os.environ", {}, clear=True):
            cfg = get_llm_config()
        assert "11434" in cfg.base_url
        assert cfg.api_key == "ollama"
        assert cfg.model == "mistral"
        assert cfg.is_ollama is True

    def test_openai_api_key_triggers_openai(self):
        with patch.dict("os.environ", {"OPENAI_API_KEY": "sk-test"}, clear=True):
            cfg = get_llm_config()
        assert "openai.com" in cfg.base_url
        assert cfg.api_key == "sk-test"
        assert cfg.is_ollama is False

    def test_explicit_model(self):
        with patch.dict("os.environ", {"OPENAI_MODEL": "llama3"}, clear=True):
            cfg = get_llm_config()
        assert cfg.model == "llama3"

    def test_custom_base_url(self):
        with patch.dict("os.environ", {"OPENAI_BASE_URL": "http://myserver:8080/v1"}, clear=True):
            cfg = get_llm_config()
        assert cfg.base_url == "http://myserver:8080/v1"
        assert cfg.is_ollama is False

    def test_ollama_base_url_detected(self):
        with patch.dict("os.environ", {"OPENAI_BASE_URL": "http://host:11434/v1"}, clear=True):
            cfg = get_llm_config()
        assert cfg.is_ollama is True
        assert cfg.api_key == "ollama"


# ---------------------------------------------------------------------------
# ConcurrencyConfig
# ---------------------------------------------------------------------------


class TestConcurrencyConfig:
    def setup_method(self):
        _clear_all_caches()

    def teardown_method(self):
        _clear_all_caches()

    def test_defaults(self):
        with patch.dict("os.environ", {}, clear=True):
            cfg = get_concurrency_config()
        assert cfg.max_concurrency == 3
        assert cfg.map_concurrency == 3

    def test_map_concurrency_override(self):
        with patch.dict("os.environ", {"YT_ARTIST_MAP_CONCURRENCY": "1"}, clear=True):
            cfg = get_concurrency_config()
        assert cfg.map_concurrency == 1

    def test_invalid_map_concurrency_falls_back(self):
        with patch.dict("os.environ", {"YT_ARTIST_MAP_CONCURRENCY": "abc"}, clear=True):
            cfg = get_concurrency_config()
        assert cfg.map_concurrency == 3

    def test_split_budget_one(self):
        cfg = ConcurrencyConfig(max_concurrency=3, map_concurrency=3)
        assert cfg.split_budget(1) == (1, 1)

    def test_split_budget_two(self):
        cfg = ConcurrencyConfig(max_concurrency=3, map_concurrency=3)
        assert cfg.split_budget(2) == (1, 1)

    def test_split_budget_three(self):
        cfg = ConcurrencyConfig(max_concurrency=3, map_concurrency=3)
        assert cfg.split_budget(3) == (2, 1)


# ---------------------------------------------------------------------------
# AppConfig
# ---------------------------------------------------------------------------


class TestAppConfig:
    def setup_method(self):
        _clear_all_caches()

    def teardown_method(self):
        _clear_all_caches()

    def test_defaults(self):
        with patch.dict("os.environ", {}, clear=True):
            cfg = get_app_config()
        assert cfg.log_level == "INFO"
        assert cfg.data_dir_env == ""
        assert cfg.db_env == ""
        assert cfg.default_prompt == "default"
        assert cfg.max_transcript_chars == 30_000
        assert cfg.summarize_strategy == "auto"

    def test_env_overrides(self):
        env = {
            "YT_ARTIST_LOG_LEVEL": "debug",
            "YT_ARTIST_DATA_DIR": "/data",
            "YT_ARTIST_DB": "/data/mydb.db",
            "YT_ARTIST_DEFAULT_PROMPT": "custom",
            "YT_ARTIST_MAX_TRANSCRIPT_CHARS": "50000",
            "YT_ARTIST_SUMMARIZE_STRATEGY": "refine",
        }
        with patch.dict("os.environ", env, clear=True):
            cfg = get_app_config()
        assert cfg.log_level == "DEBUG"
        assert cfg.data_dir_env == "/data"
        assert cfg.db_env == "/data/mydb.db"
        assert cfg.default_prompt == "custom"
        assert cfg.max_transcript_chars == 50_000
        assert cfg.summarize_strategy == "refine"

    def test_invalid_max_chars_falls_back(self):
        with patch.dict("os.environ", {"YT_ARTIST_MAX_TRANSCRIPT_CHARS": "not-int"}, clear=True):
            cfg = get_app_config()
        assert cfg.max_transcript_chars == 30_000

    def test_unknown_strategy_falls_back(self):
        with patch.dict("os.environ", {"YT_ARTIST_SUMMARIZE_STRATEGY": "bogus"}, clear=True):
            cfg = get_app_config()
        assert cfg.summarize_strategy == "auto"


# ---------------------------------------------------------------------------
# Cache behavior
# ---------------------------------------------------------------------------


class TestCacheBehavior:
    def setup_method(self):
        _clear_all_caches()

    def teardown_method(self):
        _clear_all_caches()

    def test_cache_returns_same_object(self):
        with patch.dict("os.environ", {}, clear=True):
            a = get_youtube_config()
            b = get_youtube_config()
        assert a is b

    def test_cache_clear_allows_reread(self):
        with patch.dict("os.environ", {}, clear=True):
            cfg1 = get_youtube_config()
        assert cfg1.inter_video_delay == 2.0

        get_youtube_config.cache_clear()
        with patch.dict("os.environ", {"YT_ARTIST_INTER_VIDEO_DELAY": "10"}, clear=True):
            cfg2 = get_youtube_config()
        assert cfg2.inter_video_delay == 10.0
