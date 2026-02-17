"""Tests for VTT subtitle deduplication in _subs_to_plain_text."""

from yt_artist.transcriber import _subs_to_plain_text


def test_consecutive_duplicates_removed():
    """Auto-generated VTT often repeats the last line of each cue in the next cue."""
    vtt = """WEBVTT

00:00:01.000 --> 00:00:03.000
Hello world. This is

00:00:03.000 --> 00:00:05.000
Hello world. This is
a test of the system.

00:00:05.000 --> 00:00:07.000
a test of the system.
Thank you for watching.
"""
    result = _subs_to_plain_text(vtt, "vtt")
    lines = result.strip().splitlines()
    assert lines == [
        "Hello world. This is",
        "a test of the system.",
        "Thank you for watching.",
    ]


def test_non_consecutive_duplicates_preserved():
    """Lines that repeat but not consecutively should be kept (e.g. a refrain)."""
    vtt = """WEBVTT

00:00:01.000 --> 00:00:03.000
Chorus line

00:00:03.000 --> 00:00:05.000
Something else

00:00:05.000 --> 00:00:07.000
Chorus line
"""
    result = _subs_to_plain_text(vtt, "vtt")
    lines = result.strip().splitlines()
    assert lines == ["Chorus line", "Something else", "Chorus line"]


def test_no_duplicates_unchanged():
    """Normal VTT without duplicates should produce the same output as before."""
    vtt = """WEBVTT

00:00:01.000 --> 00:00:03.000
Line one

00:00:03.000 --> 00:00:05.000
Line two
"""
    result = _subs_to_plain_text(vtt, "vtt")
    lines = result.strip().splitlines()
    assert lines == ["Line one", "Line two"]
