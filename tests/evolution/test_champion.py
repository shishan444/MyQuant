"""Tests for ChampionTracker and ChampionRecord (core/evolution/champion.py)."""

import pytest

pytestmark = [pytest.mark.unit]

import time

from core.evolution.champion import ChampionRecord, ChampionTracker


# ---------------------------------------------------------------------------
# ChampionTracker initialisation
# ---------------------------------------------------------------------------

class TestChampionTrackerInit:
    """Tracker starts with no champion and a sentinel best_score."""

    def test_champion_tracker_init(self) -> None:
        tracker = ChampionTracker()
        assert tracker.get_champion() is None
        assert tracker.best_score == -1.0


# ---------------------------------------------------------------------------
# Update behaviour
# ---------------------------------------------------------------------------

class TestChampionTrackerUpdate:
    """update() returns True only when a new best is accepted."""

    def test_update_sets_first_champion(self) -> None:
        tracker = ChampionTracker()
        result = tracker.update(score=1.5, metrics={"sharpe": 1.5})
        assert result is True
        assert tracker.get_champion() is not None

    def test_update_higher_score_replaces(self) -> None:
        tracker = ChampionTracker()
        tracker.update(score=1.0)
        result = tracker.update(score=2.0)
        assert result is True
        assert tracker.best_score == 2.0

    def test_update_lower_score_keeps_existing(self) -> None:
        tracker = ChampionTracker()
        tracker.update(score=2.0)
        result = tracker.update(score=1.5)
        assert result is False
        assert tracker.best_score == 2.0

    def test_update_zero_score_rejected(self) -> None:
        tracker = ChampionTracker()
        result = tracker.update(score=0.0)
        assert result is False

    def test_update_negative_score_rejected(self) -> None:
        tracker = ChampionTracker()
        result = tracker.update(score=-1.0)
        assert result is False


# ---------------------------------------------------------------------------
# get_champion() return values
# ---------------------------------------------------------------------------

class TestGetChampion:
    """get_champion() returns None initially, then a ChampionRecord copy."""

    def test_get_champion_returns_none_initially(self) -> None:
        tracker = ChampionTracker()
        assert tracker.get_champion() is None

    def test_get_champion_returns_record_after_update(self) -> None:
        tracker = ChampionTracker()
        tracker.update(
            score=3.0,
            metrics={"sharpe": 3.0},
            dimension_scores={"profit": 10.0},
            generation=5,
        )
        champ = tracker.get_champion()
        assert isinstance(champ, ChampionRecord)
        assert champ.score == 3.0
        assert champ.generation == 5
        assert champ.timestamp > 0.0

    def test_get_champion_returns_deep_copy(self) -> None:
        """Mutating the returned record must not affect internal state."""
        tracker = ChampionTracker()
        tracker.update(score=1.0, metrics={"a": 1})
        champ = tracker.get_champion()
        champ.metrics["a"] = 999  # type: ignore[index]
        assert tracker.get_champion().metrics["a"] == 1


# ---------------------------------------------------------------------------
# best_score property
# ---------------------------------------------------------------------------

class TestBestScoreProperty:
    """best_score reflects the current champion score."""

    def test_best_score_property_no_champion(self) -> None:
        tracker = ChampionTracker()
        assert tracker.best_score == -1.0

    def test_best_score_property_after_update(self) -> None:
        tracker = ChampionTracker()
        tracker.update(score=4.2)
        assert tracker.best_score == 4.2

    def test_best_score_property_tracks_latest_best(self) -> None:
        tracker = ChampionTracker()
        tracker.update(score=1.0)
        tracker.update(score=5.0)
        tracker.update(score=3.0)  # lower -- ignored
        assert tracker.best_score == 5.0


# ---------------------------------------------------------------------------
# Metrics preservation
# ---------------------------------------------------------------------------

class TestMetricsPreservation:
    """Metrics dict is deep-copied and preserved in ChampionRecord."""

    def test_update_preserves_metrics(self) -> None:
        tracker = ChampionTracker()
        original_metrics = {"sharpe": 2.1, "max_drawdown": 0.15, "win_rate": 0.6}
        tracker.update(score=2.1, metrics=original_metrics, generation=3)

        champ = tracker.get_champion()
        assert champ is not None
        assert champ.metrics == original_metrics
        assert champ.metrics is not original_metrics  # deep copy

    def test_update_preserves_dimension_scores(self) -> None:
        tracker = ChampionTracker()
        dims = {"profit": 8.0, "stability": 5.0, "risk": 7.0}
        tracker.update(score=7.0, dimension_scores=dims)

        champ = tracker.get_champion()
        assert champ is not None
        assert champ.dimension_scores == dims
        assert champ.dimension_scores is not dims
