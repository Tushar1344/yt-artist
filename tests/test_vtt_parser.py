"""Tests for vtt_parser.py — VTT/SRT timestamp parsing."""

from yt_artist.vtt_parser import _parse_timestamp, parse_timestamped_segments

# ---------------------------------------------------------------------------
# _parse_timestamp
# ---------------------------------------------------------------------------


class TestParseTimestamp:
    def test_full_vtt(self):
        assert _parse_timestamp("00:01:30.500") == 90.5

    def test_short_vtt(self):
        assert _parse_timestamp("01:30.500") == 90.5

    def test_srt_comma(self):
        assert _parse_timestamp("00:01:30,500") == 90.5

    def test_zero(self):
        assert _parse_timestamp("00:00:00.000") == 0.0

    def test_hours(self):
        assert _parse_timestamp("02:00:00.000") == 7200.0


# ---------------------------------------------------------------------------
# parse_timestamped_segments — VTT
# ---------------------------------------------------------------------------


class TestParseVttSegments:
    def test_basic_vtt(self):
        raw = (
            "WEBVTT\n"
            "\n"
            "00:00:00.000 --> 00:00:02.500\n"
            "Welcome to the channel\n"
            "\n"
            "00:00:02.500 --> 00:00:05.000\n"
            "Today we discuss sleep\n"
        )
        segs = parse_timestamped_segments(raw, "vtt")
        assert len(segs) == 2
        assert segs[0]["start_sec"] == 0.0
        assert segs[0]["end_sec"] == 2.5
        assert segs[0]["text"] == "Welcome to the channel"
        assert segs[1]["text"] == "Today we discuss sleep"

    def test_deduplicates_consecutive_identical(self):
        raw = (
            "WEBVTT\n"
            "\n"
            "00:00:00.000 --> 00:00:01.000\n"
            "Hello world\n"
            "\n"
            "00:00:01.000 --> 00:00:02.000\n"
            "Hello world\n"
            "\n"
            "00:00:02.000 --> 00:00:03.000\n"
            "Something new\n"
        )
        segs = parse_timestamped_segments(raw, "vtt")
        assert len(segs) == 2
        assert segs[0]["text"] == "Hello world"
        assert segs[0]["end_sec"] == 2.0  # extended
        assert segs[1]["text"] == "Something new"

    def test_strips_inline_tags(self):
        raw = "WEBVTT\n\n00:00:00.000 --> 00:00:02.000\n<c>Hello</c> <00:00:01.000>world\n"
        segs = parse_timestamped_segments(raw, "vtt")
        assert len(segs) == 1
        assert segs[0]["text"] == "Hello world"

    def test_skips_cue_settings(self):
        raw = "WEBVTT\n\n00:00:00.000 --> 00:00:02.000 align:start position:0%\nalign:start\nHello world\n"
        segs = parse_timestamped_segments(raw, "vtt")
        assert len(segs) == 1
        assert segs[0]["text"] == "Hello world"

    def test_empty_input(self):
        assert parse_timestamped_segments("", "vtt") == []

    def test_none_input(self):
        assert parse_timestamped_segments("", "vtt") == []

    def test_unsupported_format(self):
        assert parse_timestamped_segments("some content", "ass") == []


# ---------------------------------------------------------------------------
# parse_timestamped_segments — SRT
# ---------------------------------------------------------------------------


class TestParseSrtSegments:
    def test_basic_srt(self):
        raw = (
            "1\n"
            "00:00:00,000 --> 00:00:02,500\n"
            "Welcome to the channel\n"
            "\n"
            "2\n"
            "00:00:02,500 --> 00:00:05,000\n"
            "Today we discuss sleep\n"
        )
        segs = parse_timestamped_segments(raw, "srt")
        assert len(segs) == 2
        assert segs[0]["start_sec"] == 0.0
        assert segs[0]["end_sec"] == 2.5
        assert segs[0]["text"] == "Welcome to the channel"


# ---------------------------------------------------------------------------
# Storage integration for raw_vtt
# ---------------------------------------------------------------------------


class TestRawVttStorage:
    def test_save_and_retrieve_raw_vtt(self, store):
        store.upsert_artist(artist_id="UC_test", name="T", channel_url="https://youtube.com/@t", urllist_path="x")
        store.upsert_video(video_id="v1", artist_id="UC_test", url="https://youtube.com/watch?v=v1", title="V1")
        raw_vtt = "WEBVTT\n\n00:00:00.000 --> 00:00:05.000\nHello\n"
        store.save_transcript(video_id="v1", raw_text="Hello", format="vtt", raw_vtt=raw_vtt)
        row = store.get_transcript("v1")
        assert row["raw_vtt"] == raw_vtt

    def test_save_without_raw_vtt(self, store):
        store.upsert_artist(artist_id="UC_test", name="T", channel_url="https://youtube.com/@t", urllist_path="x")
        store.upsert_video(video_id="v2", artist_id="UC_test", url="https://youtube.com/watch?v=v2", title="V2")
        store.save_transcript(video_id="v2", raw_text="text", format="vtt")
        row = store.get_transcript("v2")
        assert row["raw_vtt"] is None
