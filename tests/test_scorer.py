"""Tests for scorer.py — heuristic + LLM quality scoring, parallel scoring CLI."""

from pathlib import Path
from unittest.mock import MagicMock, patch

from yt_artist.scorer import (
    _key_term_coverage,
    _length_ratio_score,
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
# Helper: mock ScoreRating
# ---------------------------------------------------------------------------


def _mock_score_rating(completeness: int = 4, coherence: int = 4, faithfulness: int = 5) -> MagicMock:
    """Create a mock ScoreRating with given field values."""
    rating = MagicMock()
    rating.completeness = completeness
    rating.coherence = coherence
    rating.faithfulness = faithfulness
    return rating


# ---------------------------------------------------------------------------
# llm_score (now uses BAML ScoreSummary via prompts adapter)
# ---------------------------------------------------------------------------


class TestLlmScore:
    @patch("yt_artist.scorer.prompts.score_summary")
    def test_valid_response(self, mock_score):
        mock_score.return_value = _mock_score_rating(4, 4, 5)
        result = llm_score("summary text", "transcript text")
        assert result is not None
        # (4+4+5)/3 = 4.333, normalized: (4.333 - 1) / 4 = 0.8333
        assert 0.8 <= result["llm_score"] <= 0.85
        # faithfulness: (5-1)/4 = 1.0
        assert result["faithfulness"] == 1.0

    @patch("yt_artist.scorer.prompts.score_summary")
    def test_llm_failure(self, mock_score):
        mock_score.side_effect = RuntimeError("LLM down")
        result = llm_score("summary text", "transcript text")
        assert result is None

    @patch("yt_artist.scorer.prompts.score_summary")
    def test_out_of_range_values(self, mock_score):
        """ScoreRating with values outside 1-5 returns None."""
        mock_score.return_value = _mock_score_rating(6, 3, 5)
        result = llm_score("summary text", "transcript text")
        assert result is None

    @patch("yt_artist.scorer.prompts.score_summary")
    def test_bad_attribute(self, mock_score):
        """ScoreRating missing attributes returns None."""
        rating = MagicMock()
        del rating.completeness  # simulate missing attribute
        mock_score.return_value = rating
        result = llm_score("summary text", "transcript text")
        assert result is None

    @patch("yt_artist.scorer.prompts.score_summary")
    def test_low_faithfulness(self, mock_score):
        """Low faithfulness (1) → faithfulness_score = 0.0."""
        mock_score.return_value = _mock_score_rating(4, 4, 1)
        result = llm_score("summary text", "transcript text")
        assert result is not None
        assert result["faithfulness"] == 0.0


# ---------------------------------------------------------------------------
# score_summary
# ---------------------------------------------------------------------------


class TestScoreSummary:
    @patch("yt_artist.scorer.prompts.score_summary")
    def test_full_scoring(self, mock_score):
        mock_score.return_value = _mock_score_rating(4, 4, 4)
        result = score_summary("A summary. With points. And more.", "transcript " * 500)
        assert "heuristic_score" in result
        assert "llm_score" in result
        assert "quality_score" in result
        assert "faithfulness_score" in result
        assert result["llm_score"] is not None
        assert result["faithfulness_score"] is not None
        assert 0.0 <= result["quality_score"] <= 1.0

    def test_skip_llm(self):
        """With skip_llm=True, llm_score is None and quality_score = heuristic."""
        result = score_summary("A summary. With points.", "transcript " * 500, skip_llm=True)
        assert result["llm_score"] is None
        assert result["faithfulness_score"] is None
        assert result["quality_score"] == result["heuristic_score"]

    @patch("yt_artist.scorer.prompts.score_summary")
    def test_llm_failure_falls_back(self, mock_score):
        """If LLM call fails, quality_score = heuristic_score."""
        mock_score.side_effect = RuntimeError("down")
        result = score_summary("A summary.", "transcript " * 500)
        assert result["llm_score"] is None
        assert result["faithfulness_score"] is None
        assert result["quality_score"] == result["heuristic_score"]

    @patch("yt_artist.scorer.prompts.score_summary")
    def test_low_faithfulness_warning(self, mock_score, caplog):
        """Low faithfulness triggers log.warning."""
        import logging

        mock_score.return_value = _mock_score_rating(4, 4, 1)  # faithfulness=1 → 0.0
        with caplog.at_level(logging.WARNING, logger="yt_artist.scorer"):
            result = score_summary("A summary.", "transcript " * 500)
        assert result["faithfulness_score"] == 0.0
        assert any("Low faithfulness" in r.message for r in caplog.records)


# ---------------------------------------------------------------------------
# score_video_summary (DB integration)
# ---------------------------------------------------------------------------


class TestScoreVideoSummary:
    @patch("yt_artist.scorer.prompts.score_summary")
    def test_scores_and_saves(self, mock_score, store):
        """Score a summary and verify it's saved to DB."""
        mock_score.return_value = _mock_score_rating(4, 4, 4)
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
            assert "faithfulness_score" in names
        finally:
            conn.close()

    def test_faithfulness_score_stored(self, store):
        """faithfulness_score is persisted via update_summary_scores."""
        store.upsert_artist(artist_id="@f", name="F", channel_url="https://youtube.com/@f", urllist_path="data/f")
        store.upsert_video(artist_id="@f", video_id="vf1", url="https://youtube.com/watch?v=vf1", title="VF1")
        store.upsert_summary(video_id="vf1", prompt_id="default", content="Summary")

        store.update_summary_scores(
            video_id="vf1",
            prompt_id="default",
            quality_score=0.8,
            heuristic_score=0.7,
            llm_score=0.9,
            faithfulness_score=0.25,
        )

        rows = store.get_summaries_for_video("vf1")
        assert rows[0]["faithfulness_score"] == 0.25


# ---------------------------------------------------------------------------
# _named_entity_score
# ---------------------------------------------------------------------------


class TestNamedEntityScore:
    def test_all_verified(self):
        """All proper nouns in summary found in transcript → 1.0."""
        from yt_artist.scorer import _named_entity_score

        transcript = "Andrew Huberman discussed dopamine pathways with David Sinclair at Stanford University."
        summary = "In this episode, Andrew Huberman talks about dopamine with David Sinclair from Stanford University."
        score = _named_entity_score(summary, transcript)
        assert score == 1.0

    def test_hallucinated_entity(self):
        """Proper noun NOT in transcript → low score."""
        from yt_artist.scorer import _named_entity_score

        transcript = "Andrew Huberman discussed dopamine pathways and neuroplasticity mechanisms."
        summary = "In this episode, Andrew Huberman and Elijah Wood discuss dopamine pathways."
        score = _named_entity_score(summary, transcript)
        assert score < 1.0  # "Elijah Wood" not in transcript

    def test_no_entities(self):
        """Summary with no proper nouns → 1.0 (neutral)."""
        from yt_artist.scorer import _named_entity_score

        summary = "This video discusses various topics related to health and wellness."
        transcript = "Some transcript text about health topics."
        score = _named_entity_score(summary, transcript)
        assert score == 1.0

    def test_sentence_start_filtered(self):
        """Capitalized words at sentence start are not treated as entities."""
        from yt_artist.scorer import _named_entity_score

        summary = "However, the results were clear. Therefore, we can conclude this works."
        transcript = "Some unrelated transcript."
        score = _named_entity_score(summary, transcript)
        assert score == 1.0  # "However" and "Therefore" filtered as stopwords

    def test_mixed_verified_unverified(self):
        """Mix of verified and unverified entities → partial score."""
        from yt_artist.scorer import _named_entity_score

        transcript = "Stanford University published the study on neural networks."
        summary = "The study from Stanford University was led by James Patterson."
        score = _named_entity_score(summary, transcript)
        # "Stanford University" verified, "James Patterson" not
        assert 0.0 < score < 1.0

    def test_single_mid_sentence_entity(self):
        """Single capitalized word mid-sentence detected as entity."""
        from yt_artist.scorer import _named_entity_score

        transcript = "The researcher spoke about cortisol."
        summary = "The discussion with Huberman about cortisol was enlightening."
        score = _named_entity_score(summary, transcript)
        # "Huberman" not in transcript
        assert score < 1.0


# ---------------------------------------------------------------------------
# _sample_transcript
# ---------------------------------------------------------------------------


class TestSampleTranscript:
    def test_short_transcript_returned_whole(self):
        """Transcript shorter than max_excerpt returned unchanged."""
        from yt_artist.scorer import _sample_transcript

        text = "Short transcript text."
        assert _sample_transcript(text, max_excerpt=3000) == text

    def test_long_transcript_sampled(self):
        """Long transcript gets stratified samples from start, middle, end."""
        from yt_artist.scorer import _sample_transcript

        # Create 10K char transcript with distinct sections
        text = "START " * 500 + "MIDDLE " * 500 + "END " * 500
        result = _sample_transcript(text, max_excerpt=3000)
        assert len(result) < len(text)
        assert "[...]" in result
        assert "START" in result
        assert "MIDDLE" in result
        assert "END" in result

    def test_exact_boundary(self):
        """Transcript exactly at max_excerpt returned whole."""
        from yt_artist.scorer import _sample_transcript

        text = "x" * 3000
        assert _sample_transcript(text, max_excerpt=3000) == text

    def test_just_over_boundary(self):
        """Transcript just over max_excerpt gets sampled (contains markers)."""
        from yt_artist.scorer import _sample_transcript

        text = "x" * 3001
        result = _sample_transcript(text, max_excerpt=3000)
        assert "[...]" in result

    def test_very_long_transcript_shorter_than_original(self):
        """Very long transcript gets significantly compressed."""
        from yt_artist.scorer import _sample_transcript

        text = "word " * 10000  # ~50K chars
        result = _sample_transcript(text, max_excerpt=3000)
        assert len(result) < len(text)
        assert "[...]" in result


# ---------------------------------------------------------------------------
# verify_claims
# ---------------------------------------------------------------------------


class TestVerifyClaims:
    @patch("yt_artist.scorer.prompts.verify_claims")
    def test_all_verified(self, mock_verify):
        """All claims verified → verification_score = 1.0."""
        from yt_artist.scorer import verify_claims

        mock_claim1 = MagicMock()
        mock_claim1.claim = "Dopamine modulates motivation"
        mock_claim1.verified = True
        mock_claim2 = MagicMock()
        mock_claim2.claim = "Exercise boosts BDNF"
        mock_claim2.verified = True
        mock_verify.return_value = [mock_claim1, mock_claim2]

        result = verify_claims("summary", "transcript")
        assert result is not None
        assert result["verification_score"] == 1.0
        assert len(result["claims"]) == 2

    @patch("yt_artist.scorer.prompts.verify_claims")
    def test_mixed_claims(self, mock_verify):
        """Mix of verified/unverified → partial score."""
        from yt_artist.scorer import verify_claims

        claims = []
        for text, verified in [("Claim A", True), ("Claim B", False), ("Claim C", True)]:
            c = MagicMock()
            c.claim = text
            c.verified = verified
            claims.append(c)
        mock_verify.return_value = claims

        result = verify_claims("summary", "transcript")
        assert result is not None
        assert abs(result["verification_score"] - 2 / 3) < 0.01

    @patch("yt_artist.scorer.prompts.verify_claims")
    def test_llm_failure(self, mock_verify):
        """LLM failure → None."""
        from yt_artist.scorer import verify_claims

        mock_verify.side_effect = RuntimeError("LLM down")
        result = verify_claims("summary", "transcript")
        assert result is None

    @patch("yt_artist.scorer.prompts.verify_claims")
    def test_empty_claims(self, mock_verify):
        """Empty claims list → None."""
        from yt_artist.scorer import verify_claims

        mock_verify.return_value = []
        result = verify_claims("summary", "transcript")
        assert result is None


# ---------------------------------------------------------------------------
# score_summary with verify=True
# ---------------------------------------------------------------------------


class TestScoreSummaryWithVerification:
    @patch("yt_artist.scorer.prompts.verify_claims")
    @patch("yt_artist.scorer.prompts.score_summary")
    def test_verify_triggers_verification(self, mock_score, mock_verify):
        """verify=True triggers exactly 2 LLM calls (score + verify)."""
        mock_score.return_value = _mock_score_rating(4, 4, 4)
        claim = MagicMock()
        claim.claim = "Test claim"
        claim.verified = True
        mock_verify.return_value = [claim]

        result = score_summary("A summary. With points.", "transcript " * 500, verify=True)
        mock_score.assert_called_once()
        mock_verify.assert_called_once()
        assert result["verification_score"] == 1.0

    @patch("yt_artist.scorer.prompts.score_summary")
    def test_no_verify_by_default(self, mock_score):
        """verify=False (default) does not call verify_claims."""
        mock_score.return_value = _mock_score_rating(4, 4, 4)
        result = score_summary("A summary. With points.", "transcript " * 500)
        assert result["verification_score"] is None

    @patch("yt_artist.scorer.prompts.verify_claims")
    @patch("yt_artist.scorer.prompts.score_summary")
    def test_verify_failure_still_returns_scores(self, mock_score, mock_verify):
        """If verification fails, other scores still returned."""
        mock_score.return_value = _mock_score_rating(4, 4, 4)
        mock_verify.side_effect = RuntimeError("fail")
        result = score_summary("A summary.", "transcript " * 500, verify=True)
        assert result["llm_score"] is not None
        assert result["verification_score"] is None


class TestVerificationScoreStored:
    @patch("yt_artist.scorer.prompts.verify_claims")
    @patch("yt_artist.scorer.prompts.score_summary")
    def test_verification_score_in_db(self, mock_score, mock_verify, store):
        """verification_score is persisted to DB via score_video_summary."""
        mock_score.return_value = _mock_score_rating(4, 4, 4)
        claim = MagicMock()
        claim.claim = "Test"
        claim.verified = True
        mock_verify.return_value = [claim]

        store.upsert_artist(artist_id="@v", name="V", channel_url="https://youtube.com/@v", urllist_path="data/v")
        store.upsert_video(artist_id="@v", video_id="vv1", url="https://youtube.com/watch?v=vv1", title="VV1")
        store.save_transcript(video_id="vv1", raw_text="Transcript text about topics. " * 200)
        store.upsert_summary(video_id="vv1", prompt_id="default", content="A summary. With points. And details.")

        result = score_video_summary("vv1", "default", store, verify=True)
        assert result is not None
        assert result["verification_score"] == 1.0

        rows = store.get_summaries_for_video("vv1")
        assert rows[0]["verification_score"] == 1.0

    def test_verification_score_column_exists(self, store):
        """verification_score column exists in schema."""
        conn = store._conn()
        try:
            cur = conn.execute("PRAGMA table_info(summaries)")
            rows = cur.fetchall()
            names = {r["name"] for r in rows}
            assert "verification_score" in names
        finally:
            conn.close()


# ---------------------------------------------------------------------------
# Parallel scoring via _cmd_score (CLI integration)
# ---------------------------------------------------------------------------


def _setup_score_data(store, n_videos: int = 3) -> str:
    """Create artist + videos + transcripts + summaries for scoring tests."""
    store.upsert_artist(
        artist_id="@score",
        name="Score",
        channel_url="https://youtube.com/@score",
        urllist_path="data/score",
    )
    for i in range(1, n_videos + 1):
        vid = f"sv{i}"
        store.upsert_video(artist_id="@score", video_id=vid, url=f"https://youtube.com/watch?v={vid}", title=f"V{i}")
        store.save_transcript(video_id=vid, raw_text=f"Transcript for video {i}. " * 200)
        store.upsert_summary(
            video_id=vid,
            prompt_id="default",
            content=f"Summary for video {i}. With multiple sentences. And details.",
        )
    return "@score"


class TestParallelScoring:
    @patch("yt_artist.scorer.prompts.score_summary")
    def test_parallel_scoring_produces_correct_results(self, mock_score, store, capsys):
        """Concurrent scoring with concurrency=2 scores all summaries."""
        import argparse

        from yt_artist.cli import _cmd_score

        mock_score.return_value = _mock_score_rating(4, 4, 4)
        artist_id = _setup_score_data(store, 3)

        args = argparse.Namespace(
            artist_id=artist_id,
            prompt_id="default",
            skip_llm=False,
            verify=False,
            dry_run=False,
            concurrency=2,
        )
        from yt_artist.cli import AppContext

        ctx = AppContext(args=args, storage=store, data_dir=Path("."))
        _cmd_score(ctx)

        captured = capsys.readouterr()
        assert "Scored 3 summaries" in captured.out
        # All 3 videos scored in DB
        for i in range(1, 4):
            rows = store.get_summaries_for_video(f"sv{i}")
            assert rows[0]["quality_score"] is not None

    @patch("yt_artist.scorer.score_video_summary")
    def test_score_error_does_not_block_others(self, mock_svs, store, capsys):
        """Error on one video doesn't prevent others from scoring."""
        import argparse

        from yt_artist.cli import _cmd_score

        def _selective_svs(video_id, prompt_id, storage, **kwargs):
            """Raise only for sv2."""
            if video_id == "sv2":
                raise RuntimeError("LLM exploded")
            return {
                "quality_score": 0.75,
                "heuristic_score": 0.8,
                "llm_score": 0.7,
                "faithfulness_score": None,
                "verification_score": None,
            }

        mock_svs.side_effect = _selective_svs
        artist_id = _setup_score_data(store, 3)

        args = argparse.Namespace(
            artist_id=artist_id,
            prompt_id="default",
            skip_llm=False,
            verify=False,
            dry_run=False,
            concurrency=2,
        )
        from yt_artist.cli import AppContext

        ctx = AppContext(args=args, storage=store, data_dir=Path("."))
        _cmd_score(ctx)

        captured = capsys.readouterr()
        # 2 scored, 1 error
        assert "Scored 2 summaries" in captured.out
        assert "1 errors" in captured.out

    def test_score_dry_run(self, store, capsys):
        """--dry-run prints count without scoring."""
        import argparse

        from yt_artist.cli import _cmd_score

        artist_id = _setup_score_data(store, 2)

        args = argparse.Namespace(
            artist_id=artist_id,
            prompt_id="default",
            skip_llm=False,
            verify=False,
            dry_run=True,
            concurrency=1,
        )
        from yt_artist.cli import AppContext

        ctx = AppContext(args=args, storage=store, data_dir=Path("."))
        _cmd_score(ctx)

        captured = capsys.readouterr()
        assert "Would score 2 summaries" in captured.out
        # No DB writes
        unscored = store.get_unscored_summaries("default")
        artist_videos = {v["id"] for v in store.list_videos(artist_id)}
        still_unscored = [r for r in unscored if r["video_id"] in artist_videos]
        assert len(still_unscored) == 2

    @patch("yt_artist.scorer.prompts.score_summary")
    def test_score_single_concurrency_regression(self, mock_score, store, capsys):
        """concurrency=1 still works correctly (sequential within pool)."""
        import argparse

        from yt_artist.cli import _cmd_score

        mock_score.return_value = _mock_score_rating(4, 4, 4)
        artist_id = _setup_score_data(store, 2)

        args = argparse.Namespace(
            artist_id=artist_id,
            prompt_id="default",
            skip_llm=False,
            verify=False,
            dry_run=False,
            concurrency=1,
        )
        from yt_artist.cli import AppContext

        ctx = AppContext(args=args, storage=store, data_dir=Path("."))
        _cmd_score(ctx)

        captured = capsys.readouterr()
        assert "Scored 2 summaries" in captured.out
