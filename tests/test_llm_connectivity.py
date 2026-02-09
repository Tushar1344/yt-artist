"""Tests for LLM pre-flight connectivity check."""
from unittest.mock import patch

import pytest

from yt_artist.llm import check_connectivity


class TestCheckConnectivity:

    def test_reachable_endpoint_passes(self):
        """check_connectivity should not raise when the endpoint is reachable."""
        with patch("yt_artist.llm.socket.create_connection") as mock_conn:
            mock_conn.return_value.close = lambda: None
            check_connectivity()  # should not raise

    def test_unreachable_ollama_gives_actionable_error(self):
        """When Ollama is configured but unreachable, error should mention 'ollama serve'."""
        with patch("yt_artist.llm._resolve_config", return_value=("http://localhost:11434/v1", "ollama", "mistral")), \
             patch("yt_artist.llm.socket.create_connection", side_effect=OSError("Connection refused")):
            with pytest.raises(RuntimeError, match="ollama serve"):
                check_connectivity()

    def test_unreachable_remote_gives_generic_error(self):
        """When a non-Ollama endpoint is unreachable, error should mention the URL."""
        with patch("yt_artist.llm._resolve_config", return_value=("https://api.openai.com/v1", "sk-xxx", "gpt-4o-mini")), \
             patch("yt_artist.llm.socket.create_connection", side_effect=OSError("Connection refused")):
            with pytest.raises(RuntimeError, match="api.openai.com"):
                check_connectivity()
