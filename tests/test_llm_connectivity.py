"""Tests for LLM connectivity check and retry logic."""

from unittest.mock import MagicMock, patch

import pytest

from yt_artist.llm import _is_transient, check_connectivity, complete


class TestCheckConnectivity:
    def test_reachable_endpoint_passes(self):
        """check_connectivity should not raise when the endpoint is reachable."""
        with patch("yt_artist.llm.socket.create_connection") as mock_conn:
            mock_conn.return_value.close = lambda: None
            check_connectivity()  # should not raise

    def test_unreachable_ollama_gives_actionable_error(self):
        """When Ollama is configured but unreachable, error should mention 'ollama serve'."""
        with (
            patch("yt_artist.llm._resolve_config", return_value=("http://localhost:11434/v1", "ollama", "mistral")),
            patch("yt_artist.llm.socket.create_connection", side_effect=OSError("Connection refused")),
        ):
            with pytest.raises(RuntimeError, match="ollama serve"):
                check_connectivity()

    def test_unreachable_remote_gives_generic_error(self):
        """When a non-Ollama endpoint is unreachable, error should mention the URL."""
        with (
            patch("yt_artist.llm._resolve_config", return_value=("https://api.openai.com/v1", "sk-xxx", "gpt-4o-mini")),
            patch("yt_artist.llm.socket.create_connection", side_effect=OSError("Connection refused")),
        ):
            with pytest.raises(RuntimeError, match="api.openai.com"):
                check_connectivity()


# ---------------------------------------------------------------------------
# _is_transient classification
# ---------------------------------------------------------------------------


class TestIsTransient:
    def test_connection_error_is_transient(self):
        assert _is_transient(ConnectionError("refused")) is True

    def test_timeout_error_is_transient(self):
        assert _is_transient(TimeoutError("timed out")) is True

    def test_429_in_message_is_transient(self):
        assert _is_transient(Exception("HTTP 429 Too Many Requests")) is True

    def test_500_in_message_is_transient(self):
        assert _is_transient(Exception("Internal Server Error 500")) is True

    def test_502_in_message_is_transient(self):
        assert _is_transient(Exception("Bad Gateway 502")) is True

    def test_rate_limit_phrase_is_transient(self):
        assert _is_transient(Exception("Rate limit exceeded")) is True

    def test_value_error_not_transient(self):
        assert _is_transient(ValueError("invalid input")) is False

    def test_auth_error_not_transient(self):
        assert _is_transient(Exception("Authentication failed: invalid API key")) is False


# ---------------------------------------------------------------------------
# complete() retry behavior
# ---------------------------------------------------------------------------


class TestCompleteRetry:
    def _mock_response(self, content: str = "summary text"):
        """Build a mock OpenAI chat completion response."""
        msg = MagicMock()
        msg.content = content
        choice = MagicMock()
        choice.message = msg
        resp = MagicMock()
        resp.choices = [choice]
        return resp

    def test_success_no_retry(self):
        """Successful call returns immediately without retrying."""
        resp = self._mock_response("ok")
        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = resp
        with (
            patch("yt_artist.llm.get_client", return_value=mock_client),
            patch("yt_artist.llm._resolve_config", return_value=("http://localhost:11434/v1", "ollama", "mistral")),
        ):
            result = complete("sys", "user", max_retries=2)
        assert result == "ok"
        assert mock_client.chat.completions.create.call_count == 1

    def test_transient_failure_retries_then_succeeds(self):
        """Transient error retries and eventually succeeds."""
        resp = self._mock_response("recovered")
        mock_client = MagicMock()
        mock_client.chat.completions.create.side_effect = [
            ConnectionError("refused"),
            ConnectionError("refused"),
            resp,
        ]
        with (
            patch("yt_artist.llm.get_client", return_value=mock_client),
            patch("yt_artist.llm._resolve_config", return_value=("http://localhost:11434/v1", "ollama", "mistral")),
            patch("yt_artist.llm._time.sleep"),
        ):
            result = complete("sys", "user", max_retries=3)
        assert result == "recovered"
        assert mock_client.chat.completions.create.call_count == 3

    def test_transient_failure_exhausts_retries(self):
        """Transient error exhausts retries and raises RuntimeError."""
        mock_client = MagicMock()
        mock_client.chat.completions.create.side_effect = ConnectionError("refused")
        with (
            patch("yt_artist.llm.get_client", return_value=mock_client),
            patch("yt_artist.llm._resolve_config", return_value=("http://localhost:11434/v1", "ollama", "mistral")),
            patch("yt_artist.llm._time.sleep"),
            pytest.raises(RuntimeError, match="Ollama"),
        ):
            complete("sys", "user", max_retries=2)
        # 1 original + 2 retries = 3 calls
        assert mock_client.chat.completions.create.call_count == 3

    def test_non_transient_failure_no_retry(self):
        """Non-transient error raises immediately without retrying."""
        mock_client = MagicMock()
        mock_client.chat.completions.create.side_effect = ValueError("bad input")
        with (
            patch("yt_artist.llm.get_client", return_value=mock_client),
            patch("yt_artist.llm._resolve_config", return_value=("https://api.openai.com/v1", "sk-x", "gpt-4")),
        ):
            with pytest.raises(RuntimeError, match="api.openai.com"):
                complete("sys", "user", max_retries=3)
        assert mock_client.chat.completions.create.call_count == 1

    def test_backoff_increases_between_retries(self):
        """Sleep time increases with each retry."""
        resp = self._mock_response("ok")
        mock_client = MagicMock()
        mock_client.chat.completions.create.side_effect = [
            ConnectionError("refused"),
            ConnectionError("refused"),
            resp,
        ]
        sleep_times = []
        with (
            patch("yt_artist.llm.get_client", return_value=mock_client),
            patch("yt_artist.llm._resolve_config", return_value=("http://localhost:11434/v1", "ollama", "mistral")),
            patch("yt_artist.llm._time.sleep", side_effect=lambda s: sleep_times.append(s)),
        ):
            complete("sys", "user", max_retries=3)
        assert len(sleep_times) == 2
        assert sleep_times[0] == 2  # initial backoff
        assert sleep_times[1] == 4  # doubled


# ---------------------------------------------------------------------------
# get_model_name() resolution
# ---------------------------------------------------------------------------


class TestGetModelName:
    def test_default_returns_nonempty(self):
        """get_model_name() with no args returns the config default."""
        from yt_artist.llm import get_model_name

        with patch.dict("os.environ", {}, clear=False):
            # Remove OPENAI_MODEL if set to test pure default
            import os

            env = {k: v for k, v in os.environ.items() if k != "OPENAI_MODEL"}
            with patch.dict("os.environ", env, clear=True):
                name = get_model_name()
                assert isinstance(name, str)
                assert len(name) > 0

    def test_explicit_arg_returned(self):
        """Explicit model arg is returned as-is, overriding env and default."""
        from yt_artist.llm import get_model_name

        result = get_model_name("llama3")
        assert result == "llama3"

    def test_env_var_overrides_default(self):
        """OPENAI_MODEL env var overrides the config default."""
        from yt_artist.llm import get_model_name

        with patch.dict("os.environ", {"OPENAI_MODEL": "gemma2"}, clear=False):
            result = get_model_name()
            assert result == "gemma2"

    def test_explicit_arg_overrides_env(self):
        """Explicit arg beats OPENAI_MODEL env var."""
        from yt_artist.llm import get_model_name

        with patch.dict("os.environ", {"OPENAI_MODEL": "gemma2"}, clear=False):
            result = get_model_name("llama3")
            assert result == "llama3"
