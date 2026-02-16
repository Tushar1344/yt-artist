"""Tests for scorer.py — heuristic + LLM quality scoring."""

from unittest.mock import patch

from yt_artist.scorer import (
    _key_term_coverage,
    _length_ratio_score,
    _parse_llm_rating,
    _repetition_score,
    _structure_score,
    heuristic_score,
    llm_score,
    score_summary,
    score_video_summary,
)

# ---------------------------------------------------------------------------
# _length_ratio_score
# ---------------------------------------------------------------------------


class TestLengthRatioScore:
    def test_ideal_ratio(self):
        """Summary/transcript ratio in 0.02–0.10 → 1.0."""
        # 500 chars summary / 10000 chars transcript = 0.05 ratio
        assert _length_ratio_score(500, 10000) == 1.0

    def test_slightly_short(self):
        """Ratio 0.01–0.02 → 0.7."""
        # 100 / 10000 = 0.01
        assert _length_ratio_score(100, 10000) == 0.7

    def test_way_too_short(self):
        """Ratio < 0.01 → 0.3."""
        assert _length_ratio_score(5, 10000) == 0.3

    def test_too_long(self):
        """Ratio > 0.20 → 0.4."""
        assert _length_ratio_score(3000, 10000) == 0.4

    def test_zero_transcript(self):
        """Zero-length transcript → 0.5 (neutral)."""
        assert _length_ratio_score(100, 0) == 0.5


# ---------------------------------------------------------------------------
# _repetition_score
# ---------------------------------------------------------------------------


class TestRepetitionScore:
    def test_no_repetition(self):
        """All unique sentences → 1.0."""
        summary = "First point. Second point. Third point."
        assert _repetition_score(summary) == 1.0

    def test_full_repetition(self):
        """All identical sentences → low score."""
        summary = "Same thing. Same thing. Same thing. Same thing. Same thing. "
        score = _repetition_score(summary)
        assert score <= 0.5

    def test_single_sentence(self):
        """Single sentence → 0.5 (can't measure)."""
        assert _repetition_score("Only one sentence here") == 0.5


# ---------------------------------------------------------------------------
# _key_term_coverage
# ---------------------------------------------------------------------------


class TestKeyTermCoverage:
    def test_full_coverage(self):
        """Summary containing all top terms → high score."""
        transcript = "machine learning deep neural networks training data model performance accuracy"
        # Repeat terms to make them top-N
        transcript = " ".join([transcript] * 10)
        summary = "This covers machine learning, deep neural networks, training data, and model performance accuracy."
        score = _key_term_coverage(summary, transcript)
        assert score >= 0.5

    def test_zero_coverage(self):
        """Summary with no matching terms → 0.0."""
        transcript = "quantum physics entanglement particles electrons"
        transcript = " ".join([transcript] * 10)
        summary = "This is about cooking recipes and gardening tips."
        score = _key_term_coverage(summary, transcript)
        assert score < 0.3

    def test_empty_transcript(self):
        """Empty transcript → 0.5 (neutral)."""
        assert _key_term_coverage("Some summary", "") == 0.5


# ---------------------------------------------------------------------------
# _structure_score
# ---------------------------------------------------------------------------


class TestStructureScore:
    def test_multi_sentence(self):
        """10+ sentences → 1.0."""
        summary = ". ".join(f"Point {i}" for i in range(12)) + "."
        assert _structure_score(summary) == 1.0

    def test_few_sentences(self):
        """2-3 sentences → 0.6."""
        summary = "First point. Second point."
        assert _structure_score(summary) == 0.6

    def test_single_sentence(self):
        """Single sentence → 0.3."""
        assert _structure_score("Just one sentence") == 0.3

    def test_bullets_bonus(self):
        """Bullet points add 0.1 bonus."""
        summary = "- Point one.\n- Point two.\n- Point three.\n- Point four."
        score = _structure_score(summary)
        assert score >= 0.7  # 0.6 base (2-3 sentences) + bullet bonus

    def test_sections_bonus(self):
        """Section headers add 0.1 bonus."""
        summary = "# Section One\nContent. More content. Even more. And more. Point five. Point six. Point seven. Point eight. Point nine. Point ten."
        score = _structure_score(summary)
        assert score == 1.0  # 1.0 base (10+ sentences) capped at 1.0


# ---------------------------------------------------------------------------
# heuristic_score (composite)
# ---------------------------------------------------------------------------


class TestHeuristicScore:
    def test_returns_float(self):
        summary = "This is a good summary with multiple points. It covers key topics. And more details."
        transcript = "Long transcript " * 500
        score = heuristic_score(summary, transcript)
        assert isinstance(score, float)
        assert 0.0 <= score <= 1.0

    def test_ideal_summary(self):
        """Well-structured summary with good coverage → high score."""
        transcript = "machine learning neural networks training data model accuracy performance optimization results"
        transcript = " ".join([transcript] * 100)
        summary = (
            "This video discusses machine learning and neural networks. "
            "The training data preparation process is explained in detail. "
            "Model accuracy and performance metrics are analyzed. "
            "Optimization techniques for better results are covered. "
            "Key findings include improvements in training approaches."
        )
        score = heuristic_score(summary, transcript)
        assert score >= 0.5


# ---------------------------------------------------------------------------
# _parse_llm_rating
# ---------------------------------------------------------------------------


class TestParseLlmRating:
    def test_valid_output(self):
        assert _parse_llm_rating("4 3 5") == (4, 3, 5)

    def test_with_extra_text(self):
        """Ignores surrounding text, extracts first 3 numbers."""
        assert _parse_llm_rating("Rating: 3 4 5 - good") == (3, 4, 5)

    def test_too_few_numbers(self):
        assert _parse_llm_rating("4 3") is None

    def test_out_of_range(self):
        assert _parse_llm_rating("6 3 5") is None
        assert _parse_llm_rating("0 3 5") is None

    def test_empty_string(self):
        assert _parse_llm_rating("") is None

    def test_no_numbers(self):
        assert _parse_llm_rating("no numbers here") is None


# ---------------------------------------------------------------------------
# llm_score
# ---------------------------------------------------------------------------


class TestLlmScore:
    @patch("yt_artist.scorer.complete")
    def test_valid_response(self, mock_complete):
        mock_complete.return_value = "4 4 5"
        score = llm_score("summary text", "transcript text")
        assert score is not None
        # (4+4+5)/3 = 4.333, normalized: (4.333 - 1) / 4 = 0.8333
        assert 0.8 <= score <= 0.85

    @patch("yt_artist.scorer.complete")
    def test_unparseable_response(self, mock_complete):
        mock_complete.return_value = "I think this summary is pretty good"
        score = llm_score("summary text", "transcript text")
        assert score is None

    @patch("yt_artist.scorer.complete")
    def test_llm_failure(self, mock_complete):
        mock_complete.side_effect = RuntimeError("LLM down")
        score = llm_score("summary text", "transcript text")
        assert score is None


# ---------------------------------------------------------------------------
# score_summary
# ---------------------------------------------------------------------------


class TestScoreSummary:
    @patch("yt_artist.scorer.complete")
    def test_full_scoring(self, mock_complete):
        mock_complete.return_value = "4 4 4"
        result = score_summary("A summary. With points. And more.", "transcript " * 500)
        assert "heuristic_score" in result
        assert "llm_score" in result
        assert "quality_score" in result
        assert result["llm_score"] is not None
        assert 0.0 <= result["quality_score"] <= 1.0

    def test_skip_llm(self):
        """With skip_llm=True, llm_score is None and quality_score = heuristic."""
        result = score_summary("A summary. With points.", "transcript " * 500, skip_llm=True)
        assert result["llm_score"] is None
        assert result["quality_score"] == result["heuristic_score"]

    @patch("yt_artist.scorer.complete")
    def test_llm_failure_falls_back(self, mock_complete):
        """If LLM call fails, quality_score = heuristic_score."""
        mock_complete.side_effect = RuntimeError("down")
        result = score_summary("A summary.", "transcript " * 500)
        assert result["llm_score"] is None
        assert result["quality_score"] == result["heuristic_score"]


# ---------------------------------------------------------------------------
# score_video_summary (DB integration)
# ---------------------------------------------------------------------------


class TestScoreVideoSummary:
    @patch("yt_artist.scorer.complete")
    def test_scores_and_saves(self, mock_complete, store):
        """Score a summary and verify it's saved to DB."""
        mock_complete.return_value = "4 4 4"
        # Set up data in DB
        store.upsert_artist(
            artist_id="@test", name="Test", channel_url="https://youtube.com/@test", urllist_path="data/test"
        )
        store.upsert_video(
            artist_id="@test", video_id="vid1", url="https://youtube.com/watch?v=vid1", title="Test Video"
        )
        store.save_transcript(video_id="vid1", raw_text="This is a long transcript about various topics. " * 200)
        store.upsert_summary(
            video_id="vid1",
            prompt_id="default",
            content="A good summary covering key points. With multiple sentences. And more details. Enough to score well.",
        )

        result = score_video_summary("vid1", "default", store)
        assert result is not None
        assert result["quality_score"] > 0

        # Verify scores are in DB
        rows = store.get_summaries_for_video("vid1")
        row = rows[0]
        assert row["quality_score"] is not None
        assert row["heuristic_score"] is not None

    def test_missing_summary(self, store):
        """Returns None when no summary exists."""
        result = score_video_summary("nonexistent", "default", store)
        assert result is None


# ---------------------------------------------------------------------------
# Storage scoring methods
# ---------------------------------------------------------------------------


class TestStorageScoring:
    def test_update_and_count(self, store):
        """update_summary_scores + count_scored_summaries + avg_quality_score."""
        store.upsert_artist(artist_id="@a", name="A", channel_url="https://youtube.com/@a", urllist_path="data/a")
        store.upsert_video(artist_id="@a", video_id="v1", url="https://youtube.com/watch?v=v1", title="V1")
        store.upsert_summary(video_id="v1", prompt_id="default", content="Summary text")

        assert store.count_scored_summaries() == 0
        assert store.avg_quality_score() is None

        store.update_summary_scores(
            video_id="v1",
            prompt_id="default",
            quality_score=0.75,
            heuristic_score=0.8,
            llm_score=0.7,
        )

        assert store.count_scored_summaries() == 1
        assert store.avg_quality_score() == 0.75

    def test_get_unscored_summaries(self, store):
        """get_unscored_summaries returns summaries without quality_score."""
        store.upsert_artist(artist_id="@b", name="B", channel_url="https://youtube.com/@b", urllist_path="data/b")
        store.upsert_video(artist_id="@b", video_id="v2", url="https://youtube.com/watch?v=v2", title="V2")
        store.upsert_video(artist_id="@b", video_id="v3", url="https://youtube.com/watch?v=v3", title="V3")
        store.upsert_summary(video_id="v2", prompt_id="default", content="Summary 2")
        store.upsert_summary(video_id="v3", prompt_id="default", content="Summary 3")

        unscored = store.get_unscored_summaries("default")
        assert len(unscored) == 2

        # Score one
        store.update_summary_scores(
            video_id="v2",
            prompt_id="default",
            quality_score=0.8,
            heuristic_score=0.8,
            llm_score=None,
        )
        unscored = store.get_unscored_summaries("default")
        assert len(unscored) == 1
        assert unscored[0]["video_id"] == "v3"

    def test_schema_migration(self, store):
        """Score columns exist after schema migration."""
        conn = store._conn()
        try:
            cur = conn.execute("PRAGMA table_info(summaries)")
            rows = cur.fetchall()
            names = {r["name"] for r in rows}
            assert "quality_score" in names
            assert "heuristic_score" in names
            assert "llm_score" in names
        finally:
            conn.close()
