"""Tests for core.validation.engine — THEN-condition + distribution helpers.

Focuses on the pure helpers (_check_then_conditions, _build_distribution,
condition primitives) that are the statistical core of validate_hypothesis
and were previously untested (validation/engine.py was at 26.2%). These
have no I/O and no parquet/indicator dependency, so they are tested directly.
"""
import numpy as np
import pandas as pd
import pytest

pytestmark = [pytest.mark.unit]

from core.validation.engine import (  # noqa: E402
    _check_then_conditions,
    _build_distribution,
    _cross_above,
    _cross_below,
    _touch_condition,
)


def _close_df(closes):
    idx = pd.date_range("2024-01-01", periods=len(closes), freq="4h")
    return pd.DataFrame({"close": closes}, index=idx)


class TestCheckThenConditions:
    """_check_then_conditions: does the forward window meet the THEN rule?"""

    def test_drop_action_matched(self):
        # price falls 10% from trigger 100 -> drop<=-5% threshold satisfied
        window = _close_df([100, 90, 95])
        assert _check_then_conditions(window, [{"action": "drop", "target": -5}], 100.0) == True

    def test_rise_action_matched(self):
        # price rises 10% from trigger 100 -> rise>=5% satisfied
        window = _close_df([100, 110, 105])
        assert _check_then_conditions(window, [{"action": "rise", "target": 5}], 100.0) == True

    def test_drop_not_matched_returns_false(self):
        # price only dips 1% -> drop<=-5% not satisfied
        window = _close_df([100, 99, 101])
        assert _check_then_conditions(window, [{"action": "drop", "target": -5}], 100.0) == False

    def test_rise_not_matched_returns_false(self):
        window = _close_df([100, 101, 99])
        assert _check_then_conditions(window, [{"action": "rise", "target": 5}], 100.0) == False

    def test_lt_action_uses_strict_less_than(self):
        # change = -10 < -5 -> lt matched
        window = _close_df([100, 90])
        assert _check_then_conditions(window, [{"action": "lt", "target": -5}], 100.0) == True

    def test_gt_action_uses_strict_greater_than(self):
        window = _close_df([100, 110])
        assert _check_then_conditions(window, [{"action": "gt", "target": 5}], 100.0) == True

    def test_empty_conditions_returns_false(self):
        window = _close_df([100, 110])
        assert _check_then_conditions(window, [], 100.0) == False

    def test_per_condition_window_trims(self):
        """A condition with window=2 only looks at the first 2 bars of the window."""
        # bars: [100, 101] in trimmed window — no drop, condition loop misses;
        # default branch then re-checks against the *full* window and finds the drop.
        window = _close_df([100, 101, 102, 90])  # drop happens at bar 3
        cond = [{"action": "drop", "target": -5, "window": 2}]
        # default branch uses full window min (90) -> change -10 <= -5 -> True
        assert _check_then_conditions(window, cond, 100.0, max_window=8) == True

    def test_non_numeric_target_falls_back_to_zero(self):
        # target "abc" -> threshold 0; drop<=0 means any drop matches
        window = _close_df([100, 99])
        assert _check_then_conditions(window, [{"action": "drop", "target": "abc"}], 100.0) == True


class TestBuildDistribution:
    """_build_distribution: histogram of change percentages."""

    def test_empty_changes_returns_empty(self):
        assert _build_distribution([], []) == []

    def test_buckets_partition_all_changes(self):
        changes = [1.0, 2.0, 3.0, 4.0, 5.0]
        matches = [True, False, True, False, True]
        buckets = _build_distribution(changes, matches)
        assert len(buckets) > 0
        assert sum(b.total_count for b in buckets) == 5
        assert sum(b.match_count for b in buckets) == 3
        assert sum(b.mismatch_count for b in buckets) == 2

    def test_max_equals_min_uses_default_bucket_width(self):
        # all changes equal -> max==min -> bucket_width=2 branch (not division by zero)
        changes = [5.0, 5.0, 5.0]
        matches = [True, True, False]
        buckets = _build_distribution(changes, matches)
        assert sum(b.total_count for b in buckets) == 3

    def test_bucket_count_within_bounds(self):
        changes = list(range(50))  # 50 changes -> num_buckets = min(10, max(5, 10)) = 10
        matches = [True] * 50
        buckets = _build_distribution(changes, matches)
        assert 5 <= len(buckets) <= 10

    def test_last_bucket_inclusive_end(self):
        # the last bucket uses <= for its upper bound (captures the max value)
        changes = [0.0, 10.0]
        matches = [False, True]
        buckets = _build_distribution(changes, matches)
        # the max value (10.0) must land in some bucket
        assert sum(b.total_count for b in buckets) == 2


class TestConditionPrimitives:
    """_cross_above / _cross_below / _touch: series-level condition helpers."""

    def test_cross_above_detects_upward_cross(self):
        s = pd.Series([1.0, 2.0, 3.0, 2.0, 4.0])
        # cross_above(s, 3.0): prev<3 & curr>=3 -> True at index 2
        result = _cross_above(s, 3.0)
        assert result.iloc[2] == True
        assert result.iloc[0] == False

    def test_cross_below_detects_downward_cross(self):
        s = pd.Series([5.0, 4.0, 3.0, 4.0, 2.0])
        # cross_below(s, 3.0): prev>3 & curr<=3 -> True at index 2
        result = _cross_below(s, 3.0)
        assert result.iloc[2] == True

    def test_cross_above_no_cross_when_already_above(self):
        s = pd.Series([5.0, 6.0, 7.0])  # always above 3
        result = _cross_above(s, 3.0)
        assert not result.any()

    def test_touch_within_tolerance(self):
        # series hovering near target -> some bars within tolerance
        s = pd.Series([100.0, 100.3, 99.7, 105.0])
        result = _touch_condition(s, 100.0)
        assert len(result) == len(s)
        assert result.dtype == bool
