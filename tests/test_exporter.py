"""Tests for exporter.py — JSON and CSV export/backup."""

from __future__ import annotations

import csv
import json
import zipfile
from pathlib import Path

from yt_artist.exporter import (
    EXPORT_VERSION,
    _build_video_entry,
    _make_export_dir,
    _sanitize_dirname,
    _zip_file,
    export_csv,
    export_json,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _seed_artist(store, artist_id: str = "UC_test", name: str = "Test"):
    store.upsert_artist(
        artist_id=artist_id,
        name=name,
        channel_url=f"https://youtube.com/@{name}",
        urllist_path="x",
    )


def _seed_video(store, video_id: str, artist_id: str = "UC_test", title: str = "V"):
    store.upsert_video(
        video_id=video_id,
        artist_id=artist_id,
        url=f"https://youtube.com/watch?v={video_id}",
        title=title,
    )


def _seed_transcript(store, video_id: str, text: str = "hello world", **kwargs):
    store.save_transcript(video_id=video_id, raw_text=text, format="vtt", **kwargs)


def _seed_summary(store, video_id: str, prompt_id: str = "default", content: str = "sum"):
    store.upsert_summary(video_id=video_id, prompt_id=prompt_id, content=content, model="test")


def _seed_prompt(store, prompt_id: str = "default", name: str = "Default", template: str = "T"):
    store.upsert_prompt(prompt_id=prompt_id, name=name, template=template)


def _seed_full(store, n_videos: int = 3, artist_id: str = "UC_test", name: str = "Test"):
    """Seed artist + N videos with transcripts and summaries."""
    _seed_artist(store, artist_id=artist_id, name=name)
    _seed_prompt(store)
    for i in range(n_videos):
        vid = f"{artist_id}_v{i:03d}"
        _seed_video(store, vid, artist_id=artist_id, title=f"Video {i}")
        _seed_transcript(store, vid, text=f"transcript text {i}")
        _seed_summary(store, vid)


# ---------------------------------------------------------------------------
# _sanitize_dirname
# ---------------------------------------------------------------------------


class TestSanitizeDirname:
    def test_at_prefix_kept(self):
        assert _sanitize_dirname("@TED") == "@TED"

    def test_slashes_replaced(self):
        assert "/" not in _sanitize_dirname("foo/bar")

    def test_spaces_replaced(self):
        assert " " not in _sanitize_dirname("foo bar baz")

    def test_empty_fallback(self):
        assert _sanitize_dirname("") == "unknown"

    def test_special_chars(self):
        result = _sanitize_dirname("@hub!er#man")
        assert "@hub" in result  # @ kept, others replaced


# ---------------------------------------------------------------------------
# _make_export_dir
# ---------------------------------------------------------------------------


class TestMakeExportDir:
    def test_creates_dir(self, tmp_path):
        d = _make_export_dir(tmp_path, timestamp="20260218_120000")
        assert d.exists()
        assert d.name == "export_20260218_120000"

    def test_auto_timestamp(self, tmp_path):
        d = _make_export_dir(tmp_path)
        assert d.exists()
        assert d.name.startswith("export_")


# ---------------------------------------------------------------------------
# _zip_file
# ---------------------------------------------------------------------------


class TestZipFile:
    def test_creates_zip_removes_original(self, tmp_path):
        f = tmp_path / "test.json"
        f.write_text('{"key":"value"}')
        zp = _zip_file(f)
        assert zp.suffix == ".zip"
        assert zp.exists()
        assert not f.exists()  # original removed
        with zipfile.ZipFile(zp) as z:
            assert z.namelist() == ["test.json"]
            assert json.loads(z.read("test.json")) == {"key": "value"}


# ---------------------------------------------------------------------------
# _build_video_entry
# ---------------------------------------------------------------------------


class TestBuildVideoEntry:
    def test_with_transcript_and_summary(self, store):
        _seed_artist(store)
        _seed_video(store, "v1")
        _seed_transcript(store, "v1", text="hello")
        _seed_prompt(store)
        _seed_summary(store, "v1")

        video = store.list_videos(artist_id="UC_test")[0]
        entry = _build_video_entry(store, video)
        assert entry["id"] == "v1"
        assert entry["transcript"]["raw_text"] == "hello"
        assert len(entry["summaries"]) == 1
        assert entry["summaries"][0]["prompt_id"] == "default"

    def test_without_transcript(self, store):
        _seed_artist(store)
        _seed_video(store, "v1")

        video = store.list_videos(artist_id="UC_test")[0]
        entry = _build_video_entry(store, video)
        assert entry["transcript"] is None
        assert entry["summaries"] == []

    def test_include_vtt(self, store):
        _seed_artist(store)
        _seed_video(store, "v1")
        raw_vtt = "WEBVTT\n\n00:00:00.000 --> 00:00:05.000\nHello\n"
        _seed_transcript(store, "v1", text="Hello", raw_vtt=raw_vtt)

        video = store.list_videos(artist_id="UC_test")[0]
        entry_no_vtt = _build_video_entry(store, video, include_vtt=False)
        assert "raw_vtt" not in entry_no_vtt["transcript"]

        entry_vtt = _build_video_entry(store, video, include_vtt=True)
        assert entry_vtt["transcript"]["raw_vtt"] == raw_vtt


# ---------------------------------------------------------------------------
# export_json — single artist, single chunk
# ---------------------------------------------------------------------------


class TestExportJsonSingle:
    def test_structure(self, store, tmp_path):
        _seed_full(store, n_videos=3)
        manifest = export_json(store, tmp_path, artist_id="UC_test")

        assert manifest["export_version"] == EXPORT_VERSION
        assert manifest["format"] == "json"
        assert manifest["file_count"] >= 2  # manifest + 1 chunk
        assert len(manifest["artists"]) == 1
        assert manifest["artists"][0]["id"] == "UC_test"
        assert manifest["artists"][0]["videos"] == 3
        assert manifest["artists"][0]["transcripts"] == 3
        assert manifest["artists"][0]["summaries"] == 3

    def test_chunk_file_valid_json(self, store, tmp_path):
        _seed_full(store, n_videos=2)
        manifest = export_json(store, tmp_path, artist_id="UC_test")

        out_dir = Path(manifest["output_dir"])
        chunks = list(out_dir.rglob("*.json"))
        # Should have manifest + 1 chunk
        assert len(chunks) == 2
        chunk_files = [c for c in chunks if c.name != "manifest.json"]
        assert len(chunk_files) == 1

        data = json.loads(chunk_files[0].read_text())
        assert data["export_version"] == EXPORT_VERSION
        assert data["artist"]["id"] == "UC_test"
        assert data["chunk"]["number"] == 1
        assert data["chunk"]["total_chunks"] == 1
        assert len(data["videos"]) == 2
        assert data["videos"][0]["transcript"]["raw_text"].startswith("transcript text")


# ---------------------------------------------------------------------------
# export_json — multiple chunks
# ---------------------------------------------------------------------------


class TestExportJsonMultiChunk:
    def test_two_chunks(self, store, tmp_path):
        _seed_full(store, n_videos=75)
        manifest = export_json(store, tmp_path, artist_id="UC_test", chunk_size=50)

        out_dir = Path(manifest["output_dir"])
        chunk_files = sorted(out_dir.rglob("UC_test*.json"))
        assert len(chunk_files) == 2

        c1 = json.loads(chunk_files[0].read_text())
        c2 = json.loads(chunk_files[1].read_text())
        assert c1["chunk"]["number"] == 1
        assert c1["chunk"]["total_chunks"] == 2
        assert c1["chunk"]["video_count"] == 50
        assert c2["chunk"]["number"] == 2
        assert c2["chunk"]["video_count"] == 25

    def test_manifest_chunk_count(self, store, tmp_path):
        _seed_full(store, n_videos=75)
        manifest = export_json(store, tmp_path, artist_id="UC_test", chunk_size=50)
        assert manifest["artists"][0]["chunks"] == 2


# ---------------------------------------------------------------------------
# export_json — all artists
# ---------------------------------------------------------------------------


class TestExportJsonAllArtists:
    def test_two_artists(self, store, tmp_path):
        _seed_full(store, n_videos=2, artist_id="UC_a", name="ArtistA")
        _seed_full(store, n_videos=3, artist_id="UC_b", name="ArtistB")
        manifest = export_json(store, tmp_path)

        assert len(manifest["artists"]) == 2
        ids = {a["id"] for a in manifest["artists"]}
        assert ids == {"UC_a", "UC_b"}

        out_dir = Path(manifest["output_dir"])
        # Each artist gets a subdirectory
        artist_dirs = [d for d in out_dir.iterdir() if d.is_dir()]
        assert len(artist_dirs) == 2


# ---------------------------------------------------------------------------
# export_json — --include-vtt
# ---------------------------------------------------------------------------


class TestExportJsonIncludeVtt:
    def test_vtt_present(self, store, tmp_path):
        _seed_artist(store)
        _seed_prompt(store)
        _seed_video(store, "v1")
        raw_vtt = "WEBVTT\n\n00:00:00.000 --> 00:00:02.000\nHi\n"
        _seed_transcript(store, "v1", text="Hi", raw_vtt=raw_vtt)

        manifest = export_json(store, tmp_path, artist_id="UC_test", include_vtt=True)
        out_dir = Path(manifest["output_dir"])
        chunk_file = next(out_dir.rglob("UC_test*.json"))
        data = json.loads(chunk_file.read_text())
        assert data["videos"][0]["transcript"]["raw_vtt"] == raw_vtt

    def test_vtt_absent_by_default(self, store, tmp_path):
        _seed_artist(store)
        _seed_prompt(store)
        _seed_video(store, "v1")
        raw_vtt = "WEBVTT\n\n00:00:00.000 --> 00:00:02.000\nHi\n"
        _seed_transcript(store, "v1", text="Hi", raw_vtt=raw_vtt)

        manifest = export_json(store, tmp_path, artist_id="UC_test", include_vtt=False)
        out_dir = Path(manifest["output_dir"])
        chunk_file = next(out_dir.rglob("UC_test*.json"))
        data = json.loads(chunk_file.read_text())
        assert "raw_vtt" not in data["videos"][0]["transcript"]


# ---------------------------------------------------------------------------
# export_json — --zip
# ---------------------------------------------------------------------------


class TestExportJsonZip:
    def test_zip_files_created(self, store, tmp_path):
        _seed_full(store, n_videos=2)
        manifest = export_json(store, tmp_path, artist_id="UC_test", compress=True)

        out_dir = Path(manifest["output_dir"])
        zips = list(out_dir.rglob("*.zip"))
        assert len(zips) == 1  # one chunk, one zip

        # Original json should not exist
        jsons_in_artist = list(out_dir.rglob("UC_test*.json"))
        assert len(jsons_in_artist) == 0  # removed by _zip_file

        # Zip contents valid
        with zipfile.ZipFile(zips[0]) as z:
            names = z.namelist()
            assert len(names) == 1
            data = json.loads(z.read(names[0]))
            assert data["artist"]["id"] == "UC_test"

    def test_manifest_reports_zip_sizes(self, store, tmp_path):
        _seed_full(store, n_videos=2)
        manifest = export_json(store, tmp_path, artist_id="UC_test", compress=True)
        for key in manifest["file_sizes"]:
            assert key.endswith(".zip")
        assert manifest["options"]["compress"] is True


# ---------------------------------------------------------------------------
# export_csv — basic
# ---------------------------------------------------------------------------


class TestExportCsvBasic:
    def test_five_csv_files(self, store, tmp_path):
        _seed_full(store, n_videos=3)
        manifest = export_csv(store, tmp_path)

        out_dir = Path(manifest["output_dir"])
        csv_files = {f.name for f in out_dir.glob("*.csv")}
        assert csv_files == {"artists.csv", "videos.csv", "transcripts.csv", "summaries.csv", "prompts.csv"}

    def test_manifest_format(self, store, tmp_path):
        _seed_full(store, n_videos=2)
        manifest = export_csv(store, tmp_path)
        assert manifest["format"] == "csv"
        assert manifest["export_version"] == EXPORT_VERSION
        assert manifest["file_count"] == 6  # 5 csv + manifest

    def test_csv_headers(self, store, tmp_path):
        _seed_full(store, n_videos=1)
        manifest = export_csv(store, tmp_path)
        out_dir = Path(manifest["output_dir"])

        with open(out_dir / "artists.csv") as f:
            reader = csv.DictReader(f)
            assert "id" in reader.fieldnames
            assert "name" in reader.fieldnames

        with open(out_dir / "videos.csv") as f:
            reader = csv.DictReader(f)
            assert "id" in reader.fieldnames
            assert "artist_id" in reader.fieldnames

        with open(out_dir / "transcripts.csv") as f:
            reader = csv.DictReader(f)
            assert "video_id" in reader.fieldnames
            assert "raw_text" in reader.fieldnames

    def test_csv_row_counts(self, store, tmp_path):
        _seed_full(store, n_videos=3)
        manifest = export_csv(store, tmp_path)
        out_dir = Path(manifest["output_dir"])

        with open(out_dir / "artists.csv") as f:
            assert len(list(csv.DictReader(f))) == 1

        with open(out_dir / "videos.csv") as f:
            assert len(list(csv.DictReader(f))) == 3

        with open(out_dir / "transcripts.csv") as f:
            assert len(list(csv.DictReader(f))) == 3

        with open(out_dir / "summaries.csv") as f:
            assert len(list(csv.DictReader(f))) == 3


# ---------------------------------------------------------------------------
# export_csv — filtered by artist
# ---------------------------------------------------------------------------


class TestExportCsvFiltered:
    def test_only_one_artist(self, store, tmp_path):
        _seed_full(store, n_videos=2, artist_id="UC_a", name="A")
        _seed_full(store, n_videos=3, artist_id="UC_b", name="B")
        manifest = export_csv(store, tmp_path, artist_id="UC_a")

        out_dir = Path(manifest["output_dir"])
        with open(out_dir / "artists.csv") as f:
            rows = list(csv.DictReader(f))
            assert len(rows) == 1
            assert rows[0]["id"] == "UC_a"

        with open(out_dir / "videos.csv") as f:
            rows = list(csv.DictReader(f))
            assert len(rows) == 2
            assert all(r["artist_id"] == "UC_a" for r in rows)

        assert manifest["artists"][0]["videos"] == 2


# ---------------------------------------------------------------------------
# export_csv — --include-vtt
# ---------------------------------------------------------------------------


class TestExportCsvIncludeVtt:
    def test_vtt_column_present(self, store, tmp_path):
        _seed_artist(store)
        _seed_prompt(store)
        _seed_video(store, "v1")
        _seed_transcript(store, "v1", raw_vtt="WEBVTT\n\nHello\n")

        manifest = export_csv(store, tmp_path, include_vtt=True)
        out_dir = Path(manifest["output_dir"])
        with open(out_dir / "transcripts.csv") as f:
            reader = csv.DictReader(f)
            assert "raw_vtt" in reader.fieldnames
            rows = list(reader)
            assert rows[0]["raw_vtt"] == "WEBVTT\n\nHello\n"

    def test_vtt_column_absent_by_default(self, store, tmp_path):
        _seed_artist(store)
        _seed_prompt(store)
        _seed_video(store, "v1")
        _seed_transcript(store, "v1", raw_vtt="WEBVTT\n\nHello\n")

        manifest = export_csv(store, tmp_path, include_vtt=False)
        out_dir = Path(manifest["output_dir"])
        with open(out_dir / "transcripts.csv") as f:
            reader = csv.DictReader(f)
            assert "raw_vtt" not in reader.fieldnames


# ---------------------------------------------------------------------------
# export_csv — --zip
# ---------------------------------------------------------------------------


class TestExportCsvZip:
    def test_csv_zipped(self, store, tmp_path):
        _seed_full(store, n_videos=2)
        manifest = export_csv(store, tmp_path, compress=True)

        out_dir = Path(manifest["output_dir"])
        zips = sorted(f.name for f in out_dir.glob("*.zip"))
        assert "artists.csv.zip" in zips
        assert "videos.csv.zip" in zips
        assert "transcripts.csv.zip" in zips
        assert "summaries.csv.zip" in zips
        assert "prompts.csv.zip" in zips

        # No raw CSV files remain
        raw_csvs = list(out_dir.glob("*.csv"))
        assert len(raw_csvs) == 0

        # Zips contain valid CSV
        with zipfile.ZipFile(out_dir / "artists.csv.zip") as z:
            assert z.namelist() == ["artists.csv"]


# ---------------------------------------------------------------------------
# Empty DB
# ---------------------------------------------------------------------------


class TestExportEmptyDb:
    def test_json_empty(self, store, tmp_path):
        manifest = export_json(store, tmp_path)
        assert manifest["artists"] == []
        assert manifest["file_count"] == 1  # only manifest

    def test_csv_empty(self, store, tmp_path):
        manifest = export_csv(store, tmp_path)
        assert manifest["artists"] == []
        # Still creates 5 CSV files (with headers only) + manifest
        assert manifest["file_count"] == 6


# ---------------------------------------------------------------------------
# Manifest correctness
# ---------------------------------------------------------------------------


class TestManifestCorrectness:
    def test_json_manifest_file_on_disk(self, store, tmp_path):
        _seed_full(store, n_videos=5)
        manifest = export_json(store, tmp_path)
        out_dir = Path(manifest["output_dir"])
        m_path = out_dir / "manifest.json"
        assert m_path.exists()
        on_disk = json.loads(m_path.read_text())
        assert on_disk["format"] == "json"
        assert on_disk["artists"][0]["videos"] == 5

    def test_csv_manifest_file_on_disk(self, store, tmp_path):
        _seed_full(store, n_videos=2)
        manifest = export_csv(store, tmp_path)
        out_dir = Path(manifest["output_dir"])
        m_path = out_dir / "manifest.json"
        assert m_path.exists()
        on_disk = json.loads(m_path.read_text())
        assert on_disk["format"] == "csv"

    def test_options_recorded(self, store, tmp_path):
        _seed_full(store, n_videos=1)
        manifest = export_json(store, tmp_path, include_vtt=True, chunk_size=10, compress=True)
        assert manifest["options"]["include_vtt"] is True
        assert manifest["options"]["chunk_size"] == 10
        assert manifest["options"]["compress"] is True


# ---------------------------------------------------------------------------
# storage.list_summaries
# ---------------------------------------------------------------------------


class TestStorageListSummaries:
    def test_all_summaries(self, store):
        _seed_artist(store, artist_id="UC_a")
        _seed_video(store, "v1", artist_id="UC_a")
        _seed_prompt(store)
        _seed_summary(store, "v1")

        rows = store.list_summaries()
        assert len(rows) == 1
        assert rows[0]["video_id"] == "v1"

    def test_filtered_by_artist(self, store):
        _seed_artist(store, artist_id="UC_a", name="A")
        _seed_artist(store, artist_id="UC_b", name="B")
        _seed_video(store, "v1", artist_id="UC_a")
        _seed_video(store, "v2", artist_id="UC_b")
        _seed_prompt(store)
        _seed_summary(store, "v1")
        _seed_summary(store, "v2")

        rows_a = store.list_summaries(artist_id="UC_a")
        assert len(rows_a) == 1
        assert rows_a[0]["video_id"] == "v1"

        rows_b = store.list_summaries(artist_id="UC_b")
        assert len(rows_b) == 1
        assert rows_b[0]["video_id"] == "v2"

    def test_empty(self, store):
        assert store.list_summaries() == []


# ---------------------------------------------------------------------------
# paths.export_dir
# ---------------------------------------------------------------------------


class TestExportDir:
    def test_path(self, tmp_path):
        from yt_artist.paths import export_dir

        result = export_dir(tmp_path)
        assert result == tmp_path / "data" / "exports"
