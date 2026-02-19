"""Tests for transcript_quality.py — heuristic scoring of transcript quality."""

from yt_artist.transcript_quality import (
    _avg_word_length_score,
    _line_uniqueness_score,
    _punctuation_density_score,
    _repetition_ratio_score,
    _word_count_score,
    transcript_quality_score,
)

# ---------------------------------------------------------------------------
# _word_count_score
# ---------------------------------------------------------------------------


class TestWordCountScore:
    def test_empty(self):
        assert _word_count_score("") == 0.0

    def test_whitespace_only(self):
        assert _word_count_score("   \n\n  ") == 0.0

    def test_below_minimum(self):
        assert _word_count_score("hello world") == 0.0

    def test_at_minimum(self):
        text = " ".join(["word"] * 50)
        assert _word_count_score(text) == 0.0  # 50 = MIN_WORDS, linear starts at 50

    def test_midpoint(self):
        text = " ".join(["word"] * 125)
        score = _word_count_score(text)
        assert 0.4 < score < 0.6

    def test_above_good(self):
        text = " ".join(["word"] * 300)
        assert _word_count_score(text) == 1.0


# ---------------------------------------------------------------------------
# _repetition_ratio_score
# ---------------------------------------------------------------------------


class TestRepetitionRatioScore:
    def test_empty(self):
        assert _repetition_ratio_score("") == 0.0

    def test_all_unique(self):
        text = "line one\nline two\nline three"
        assert _repetition_ratio_score(text) == 1.0

    def test_all_identical(self):
        text = "same line\n" * 10
        score = _repetition_ratio_score(text)
        assert score < 0.15  # 1/10 = 0.1

    def test_half_duplicated(self):
        text = "a\nb\na\nb\nc\nd"
        score = _repetition_ratio_score(text)
        assert 0.5 < score < 0.8


# ---------------------------------------------------------------------------
# _avg_word_length_score
# ---------------------------------------------------------------------------


class TestAvgWordLengthScore:
    def test_empty(self):
        assert _avg_word_length_score("") == 0.0

    def test_normal_english(self):
        text = "This is a normal English transcript about neuroscience and biology."
        assert _avg_word_length_score(text) > 0.8

    def test_very_short_words(self):
        text = "a b c d e f g h i j k"
        assert _avg_word_length_score(text) == 0.0

    def test_very_long_words(self):
        text = "supercalifragilisticexpialidocious " * 20
        score = _avg_word_length_score(text)
        assert score == 0.0


# ---------------------------------------------------------------------------
# _punctuation_density_score
# ---------------------------------------------------------------------------


class TestPunctuationDensityScore:
    def test_empty(self):
        assert _punctuation_density_score("") == 0.0

    def test_normal_text(self):
        text = "Hello, this is a test. It has normal punctuation! Right?"
        assert _punctuation_density_score(text) > 0.5

    def test_no_punctuation(self):
        text = "hello this is a test with no punctuation at all"
        assert _punctuation_density_score(text) == 0.2  # low but not zero

    def test_excessive_punctuation(self):
        text = "!!!...???...!!!...???...!!!"
        assert _punctuation_density_score(text) == 0.0


# ---------------------------------------------------------------------------
# _line_uniqueness_score
# ---------------------------------------------------------------------------


class TestLineUniquenessScore:
    def test_empty(self):
        assert _line_uniqueness_score("") == 0.0

    def test_all_unique(self):
        text = "Line One\nLine Two\nLine Three"
        assert _line_uniqueness_score(text) == 1.0

    def test_music_pattern(self):
        text = "la la la\nla la la\nla la la\nla la la\nchorus\nla la la\n"
        score = _line_uniqueness_score(text)
        assert score < 0.5


# ---------------------------------------------------------------------------
# transcript_quality_score (composite)
# ---------------------------------------------------------------------------


class TestTranscriptQualityScore:
    def test_empty_returns_zero(self):
        assert transcript_quality_score("") == 0.0

    def test_whitespace_returns_zero(self):
        assert transcript_quality_score("   \n\n  ") == 0.0

    def test_good_transcript(self):
        """A realistic transcript should score above 0.5."""
        text = (
            "Welcome to today's episode. We're going to talk about the neuroscience of sleep.\n"
            "Sleep is one of the most important processes for human health.\n"
            "During deep sleep, the brain consolidates memories and clears toxins.\n"
            "Research shows that adults need seven to nine hours of sleep per night.\n"
            "Let's discuss the different stages of sleep and their functions.\n"
            "Stage one is light sleep, where you drift in and out of consciousness.\n"
            "Stage two involves sleep spindles and K-complexes in the EEG.\n"
            "Stage three is deep slow-wave sleep, critical for physical recovery.\n"
            "REM sleep is when most dreaming occurs and is linked to learning.\n"
            "Poor sleep quality can lead to cognitive decline and mood disorders.\n"
        ) * 3  # repeat to get enough words
        score = transcript_quality_score(text)
        assert score > 0.5

    def test_garbage_transcript(self):
        """Repeated short gibberish should score below 0.3."""
        text = "mmm\n" * 100
        score = transcript_quality_score(text)
        assert score < 0.3

    def test_returns_float_in_range(self):
        """Score should always be between 0.0 and 1.0."""
        for text in ["", "short", "a " * 500, "hello\n" * 100]:
            score = transcript_quality_score(text)
            assert 0.0 <= score <= 1.0


# ---------------------------------------------------------------------------
# Storage integration
# ---------------------------------------------------------------------------


class TestTranscriptQualityStorage:
    def test_save_and_retrieve_quality_score(self, store):
        store.upsert_artist(artist_id="UC_test", name="T", channel_url="https://youtube.com/@t", urllist_path="x")
        store.upsert_video(video_id="v1", artist_id="UC_test", url="https://youtube.com/watch?v=v1", title="V1")
        store.save_transcript(video_id="v1", raw_text="hello world", format="vtt", quality_score=0.75)
        row = store.get_transcript("v1")
        assert row["quality_score"] == 0.75

    def test_save_without_quality_score(self, store):
        store.upsert_artist(artist_id="UC_test", name="T", channel_url="https://youtube.com/@t", urllist_path="x")
        store.upsert_video(video_id="v2", artist_id="UC_test", url="https://youtube.com/watch?v=v2", title="V2")
        store.save_transcript(video_id="v2", raw_text="text", format="vtt")
        row = store.get_transcript("v2")
        assert row["quality_score"] is None

    def test_update_quality_score(self, store):
        store.upsert_artist(artist_id="UC_test", name="T", channel_url="https://youtube.com/@t", urllist_path="x")
        store.upsert_video(video_id="v3", artist_id="UC_test", url="https://youtube.com/watch?v=v3", title="V3")
        store.save_transcript(video_id="v3", raw_text="text", format="vtt")
        store.update_transcript_quality_score("v3", 0.42)
        row = store.get_transcript("v3")
        assert row["quality_score"] == 0.42

    def test_list_transcripts_includes_quality_score(self, store):
        store.upsert_artist(artist_id="UC_test", name="T", channel_url="https://youtube.com/@t", urllist_path="x")
        store.upsert_video(video_id="v4", artist_id="UC_test", url="https://youtube.com/watch?v=v4", title="V4")
        store.save_transcript(video_id="v4", raw_text="text", format="vtt", quality_score=0.88)
        rows = store.list_transcripts(artist_id="UC_test")
        assert len(rows) == 1
        assert rows[0]["quality_score"] == 0.88
