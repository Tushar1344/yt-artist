"""Content hashing for staleness detection (SHA-256)."""

import hashlib


def content_hash(text: str) -> str:
    """Return hex SHA-256 digest of *text* (UTF-8 encoded)."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()
