"""Tests for content hashing utility."""

import hashlib

from yt_artist.hashing import content_hash


class TestContentHash:
    def test_deterministic(self):
        """Same input always produces same hash."""
        assert content_hash("hello") == content_hash("hello")

    def test_different_inputs(self):
        """Different inputs produce different hashes."""
        assert content_hash("hello") != content_hash("world")

    def test_empty_string(self):
        """Empty string hashes without error."""
        result = content_hash("")
        assert isinstance(result, str)
        assert len(result) == 64

    def test_unicode(self):
        """Unicode text hashes correctly."""
        result = content_hash("日本語テスト 🎵")
        assert isinstance(result, str)
        assert len(result) == 64

    def test_sha256_hex_format(self):
        """Output is 64-char lowercase hex string matching stdlib."""
        text = "test content"
        expected = hashlib.sha256(text.encode("utf-8")).hexdigest()
        assert content_hash(text) == expected

    def test_whitespace_sensitivity(self):
        """Trailing whitespace produces a different hash."""
        assert content_hash("text") != content_hash("text ")
