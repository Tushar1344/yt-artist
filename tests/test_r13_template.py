"""Tests for R13: safe template filling with format_map."""
from yt_artist.summarizer import _fill_template


def test_fill_template_basic():
    """Standard placeholder replacement still works."""
    result = _fill_template(
        "Artist: {artist}, Video: {video}",
        artist="Nate",
        video="Ep 1",
    )
    assert result == "Artist: Nate, Video: Ep 1"


def test_fill_template_missing_placeholders_get_empty():
    """Placeholders not provided default to empty string."""
    result = _fill_template(
        "{artist} - {video} ({intent}, {audience})",
        artist="Nate",
        video="Ep 1",
    )
    assert result == "Nate - Ep 1 (, )"


def test_fill_template_artist_contains_video_placeholder():
    """If artist value contains '{video}' literally, it should NOT corrupt the video slot."""
    result = _fill_template(
        "Artist: {artist}, Video: {video}",
        artist="contains {video} literally",
        video="Actual Title",
    )
    assert "contains {video} literally" in result
    assert "Actual Title" in result
    # The old .replace() approach would have corrupted this
    assert result == "Artist: contains {video} literally, Video: Actual Title"


def test_fill_template_unknown_placeholder_preserved():
    """Unknown placeholders like {custom_field} are left as-is."""
    result = _fill_template(
        "A: {artist}, Custom: {custom_field}",
        artist="Nate",
    )
    assert result == "A: Nate, Custom: {custom_field}"


def test_fill_template_empty_template():
    """Empty template returns empty string."""
    result = _fill_template("")
    assert result == ""


def test_fill_template_no_placeholders():
    """Template with no placeholders is returned unchanged."""
    result = _fill_template("Just plain text.", artist="ignored")
    assert result == "Just plain text."
