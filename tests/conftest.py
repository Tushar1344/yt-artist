"""Pytest fixtures: temp DB for tests."""
import pytest

from yt_artist import storage


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
