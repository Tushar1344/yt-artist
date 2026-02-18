"""Tests for paths.py — centralized path construction."""

from pathlib import Path

from yt_artist.paths import (
    db_path,
    job_log_file,
    jobs_dir,
    transcript_dir,
    transcript_file,
    urllist_abs_path,
    urllist_rel_path,
)


class TestDbPath:
    def test_returns_path(self):
        result = db_path(Path("/data"))
        assert isinstance(result, Path)

    def test_correct_location(self):
        result = db_path(Path("/mydir"))
        assert result == Path("/mydir/data/yt_artist.db")


class TestUrllistRelPath:
    def test_basic(self):
        result = urllist_rel_path("UC_xyz", "My Channel")
        assert result.startswith("data/artists/UC_xyz/")
        assert result.endswith("-urllist.md")
        assert "UC_xyz" in result

    def test_sanitizes_special_chars(self):
        result = urllist_rel_path("UC_1", "Test! @#$ Channel")
        # Special chars should be replaced with underscores
        assert "!" not in result
        assert "@" not in result
        assert "#" not in result

    def test_empty_name_fallback(self):
        result = urllist_rel_path("UC_1", "")
        assert "channel" in result

    def test_matches_storage_urllist_path(self, store):
        """Integration: paths.urllist_rel_path == Storage.urllist_path for same args."""
        args = ("UC_test", "My Channel Name")
        assert urllist_rel_path(*args) == store.urllist_path(*args)


class TestUrllistAbsPath:
    def test_combines_data_dir_and_rel_path(self):
        result = urllist_abs_path(Path("/base"), "UC_x", "Name")
        assert result == Path("/base") / urllist_rel_path("UC_x", "Name")
        assert result.is_absolute()


class TestTranscriptDir:
    def test_correct_structure(self):
        result = transcript_dir(Path("/data"), "UC_abc")
        assert result == Path("/data/artists/UC_abc/transcripts")


class TestTranscriptFile:
    def test_correct_structure(self):
        result = transcript_file(Path("/data"), "UC_abc", "vid123")
        assert result == Path("/data/artists/UC_abc/transcripts/vid123.txt")

    def test_is_inside_transcript_dir(self):
        d = transcript_dir(Path("/d"), "UC_1")
        f = transcript_file(Path("/d"), "UC_1", "v1")
        assert f.parent == d


class TestJobsDir:
    def test_correct_structure(self):
        result = jobs_dir(Path("/data"))
        assert result == Path("/data/data/jobs")


class TestJobLogFile:
    def test_correct_structure(self):
        result = job_log_file(Path("/data"), "abc123")
        assert result == Path("/data/data/jobs/abc123.log")

    def test_is_inside_jobs_dir(self):
        d = jobs_dir(Path("/d"))
        f = job_log_file(Path("/d"), "j1")
        assert f.parent == d
