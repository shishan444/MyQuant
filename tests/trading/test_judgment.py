"""Judgment rule unit tests.

Covers all 6 rules + edge cases:
1. min_hold_bars blocks exit
2. min_profit_ratio blocks exit
3. initial_entry_pct for new positions
4. profit_add_only blocks add when losing
5. reverse direction closes first
6. max_hold_bars safety valve
7. No-signal continue-fill behavior
8. Edge cases: zero balance, mixed direction
"""

import pytest

pytestmark = [pytest.mark.unit]

from core.trading.types import AccountState, BarSignals, Decision, JudgmentConfig
from core.trading.judgment import evaluate


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _flat_state(**overrides) -> AccountState:
    """Create a flat (no position) AccountState."""
    defaults = dict(
        balance=100_000,
        has_position=False,
        position_side="flat",
        position_entry=0.0,
        position_quantity=0.0,
        position_margin=0.0,
        unrealized_pnl=0.0,
        position_bars_held=0,
        target_position_pct=0.0,
        actual_position_pct=0.0,
        equity=100_000,
    )
    defaults.update(overrides)
    return AccountState(**defaults)


def _long_state(bars_held=5, unrealized_pnl=500.0, **overrides) -> AccountState:
    """Create an AccountState with a long position."""
    defaults = dict(
        balance=70_000,
        has_position=True,
        position_side="long",
        position_entry=100.0,
        position_quantity=300.0,
        position_margin=30_000,
        unrealized_pnl=unrealized_pnl,
        position_bars_held=bars_held,
        target_position_pct=0.30,
        actual_position_pct=0.30,
        equity=100_500,
    )
    defaults.update(overrides)
    return AccountState(**defaults)


def _short_state(bars_held=5, unrealized_pnl=500.0, **overrides) -> AccountState:
    """Create an AccountState with a short position."""
    defaults = dict(
        balance=70_000,
        has_position=True,
        position_side="short",
        position_entry=100.0,
        position_quantity=300.0,
        position_margin=30_000,
        unrealized_pnl=unrealized_pnl,
        position_bars_held=bars_held,
        target_position_pct=0.30,
        actual_position_pct=0.30,
        equity=100_500,
    )
    defaults.update(overrides)
    return AccountState(**defaults)


def _signals(
    entry=False, exit=False, add=False, reduce=False, direction=1.0,
) -> BarSignals:
    return BarSignals(
        entry=entry, exit=exit, add=add, reduce=reduce, direction=direction,
    )


# ---------------------------------------------------------------------------
# Test: min_hold_bars
# ---------------------------------------------------------------------------

class TestMinHoldBars:

    def test_exit_blocked_when_held_too_few_bars(self):
        state = _long_state(bars_held=1, unrealized_pnl=1000.0)
        config = JudgmentConfig(min_hold_bars=3)
        d = evaluate(_signals(exit=True), state, config)
        assert d.action == "hold"
        assert "hold_bars" in d.reason

    def test_exit_allowed_when_held_enough_bars(self):
        state = _long_state(bars_held=5, unrealized_pnl=1000.0)
        config = JudgmentConfig(min_hold_bars=3)
        d = evaluate(_signals(exit=True), state, config)
        assert d.action == "close"


# ---------------------------------------------------------------------------
# Test: min_profit_ratio
# ---------------------------------------------------------------------------

class TestMinProfitRatio:

    def test_exit_blocked_when_profit_below_threshold(self):
        state = _long_state(bars_held=10, unrealized_pnl=5.0)
        config = JudgmentConfig(min_profit_ratio=2.0, fee_rate=0.001)
        d = evaluate(_signals(exit=True), state, config)
        assert d.action == "hold"
        assert "pnl" in d.reason

    def test_exit_allowed_when_profit_above_threshold(self):
        state = _long_state(bars_held=10, unrealized_pnl=5000.0)
        config = JudgmentConfig(min_profit_ratio=2.0, fee_rate=0.001)
        d = evaluate(_signals(exit=True), state, config)
        assert d.action == "close"

    def test_exit_allowed_when_losing_regardless_of_fee(self):
        """Losing positions should always be allowed to exit."""
        state = _long_state(bars_held=10, unrealized_pnl=-50.0)
        config = JudgmentConfig(min_profit_ratio=2.0, fee_rate=0.001)
        d = evaluate(_signals(exit=True), state, config)
        assert d.action == "close"

    def test_exit_blocked_when_tiny_profit_below_fee(self):
        """Tiny positive profit below fee threshold should be blocked."""
        state = _long_state(bars_held=10, unrealized_pnl=0.5)
        config = JudgmentConfig(min_profit_ratio=2.0, fee_rate=0.001)
        d = evaluate(_signals(exit=True), state, config)
        assert d.action == "hold"


# ---------------------------------------------------------------------------
# Test: initial entry
# ---------------------------------------------------------------------------

class TestInitialEntry:

    def test_entry_opens_with_initial_pct(self):
        state = _flat_state()
        config = JudgmentConfig(initial_entry_pct=0.33)
        d = evaluate(_signals(entry=True, direction=1.0), state, config)
        assert d.action == "open"
        assert d.direction == "long"
        assert d.target_position_pct > 0
        assert d.reason.startswith("initial_entry")

    def test_entry_short_direction(self):
        state = _flat_state()
        d = evaluate(_signals(entry=True, direction=-1.0), state)
        assert d.action == "open"
        assert d.direction == "short"


# ---------------------------------------------------------------------------
# Test: profit_add_only
# ---------------------------------------------------------------------------

class TestProfitAddOnly:

    def test_add_blocked_when_losing(self):
        state = _long_state(unrealized_pnl=-500.0, actual_position_pct=0.10)
        config = JudgmentConfig(profit_add_only=True)
        d = evaluate(_signals(entry=True, direction=1.0), state, config)
        assert d.action == "hold"
        assert "losing" in d.reason

    def test_add_allowed_when_profitable(self):
        state = _long_state(unrealized_pnl=500.0, actual_position_pct=0.10,
                             target_position_pct=0.30)
        config = JudgmentConfig(profit_add_only=True)
        d = evaluate(_signals(entry=True, direction=1.0), state, config)
        assert d.action == "add"

    def test_add_blocked_when_at_target(self):
        state = _long_state(unrealized_pnl=500.0, actual_position_pct=0.30,
                             target_position_pct=0.30)
        config = JudgmentConfig(profit_add_only=True)
        d = evaluate(_signals(entry=True, direction=1.0), state, config)
        assert d.action == "hold"
        assert "target" in d.reason


# ---------------------------------------------------------------------------
# Test: reverse direction
# ---------------------------------------------------------------------------

class TestReverseClose:

    def test_opposite_direction_closes_first(self):
        state = _long_state()
        d = evaluate(_signals(entry=True, direction=-1.0), state)
        assert d.action == "close"
        assert "reverse" in d.reason

    def test_same_direction_does_not_reverse(self):
        state = _long_state(actual_position_pct=0.10, target_position_pct=0.30)
        d = evaluate(_signals(entry=True, direction=1.0), state)
        assert d.action != "close"


# ---------------------------------------------------------------------------
# Test: no signal behavior
# ---------------------------------------------------------------------------

class TestNoSignal:

    def test_hold_when_profitable_and_under_target_no_signal(self):
        """After Rule 5 removal, no-signal state returns hold even when profitable and under target."""
        state = _long_state(bars_held=2, unrealized_pnl=200.0, actual_position_pct=0.10,
                             target_position_pct=0.30)
        d = evaluate(_signals(), state)
        assert d.action == "hold"

    def test_hold_when_no_need_to_fill(self):
        state = _long_state(unrealized_pnl=200.0, actual_position_pct=0.30,
                             target_position_pct=0.30)
        d = evaluate(_signals(), state)
        assert d.action == "hold"

    def test_hold_when_flat_no_signals(self):
        state = _flat_state()
        d = evaluate(_signals(), state)
        assert d.action == "hold"


# ---------------------------------------------------------------------------
# Test: max_hold_bars safety valve
# ---------------------------------------------------------------------------

class TestMaxHoldBars:

    def test_force_close_when_exceeded_and_losing(self):
        state = _long_state(bars_held=50, unrealized_pnl=-200.0)
        config = JudgmentConfig(max_hold_bars=30)
        d = evaluate(_signals(), state, config)
        assert d.action == "close"
        assert "max_hold_bars" in d.reason

    def test_no_force_close_when_profitable(self):
        state = _long_state(bars_held=50, unrealized_pnl=500.0)
        config = JudgmentConfig(max_hold_bars=30)
        d = evaluate(_signals(), state, config)
        assert d.action != "close" or "max_hold_bars" not in d.reason

    def test_no_force_close_when_none(self):
        state = _long_state(bars_held=50, unrealized_pnl=-200.0)
        config = JudgmentConfig(max_hold_bars=None)
        d = evaluate(_signals(), state, config)
        assert d.action != "close" or "max_hold_bars" not in d.reason


# ---------------------------------------------------------------------------
# Test: edge cases
# ---------------------------------------------------------------------------

class TestEdgeCases:

    def test_reduce_signal_with_position(self):
        state = _long_state(bars_held=10, unrealized_pnl=500.0)
        d = evaluate(_signals(reduce=True), state)
        assert d.action == "reduce"

    def test_exit_and_reduce_both_true_exits(self):
        state = _long_state(bars_held=10, unrealized_pnl=500.0)
        d = evaluate(_signals(exit=True, reduce=True), state)
        assert d.action == "close"

    def test_zero_balance_no_crash(self):
        state = _flat_state(balance=0, equity=0)
        d = evaluate(_signals(entry=True), state)
        # Should not crash; may return open or hold depending on impl
        assert d.action in ("open", "hold")

    def test_mixed_direction_long(self):
        state = _flat_state()
        d = evaluate(_signals(entry=True, direction=0.5), state)
        assert d.direction == "long"

    def test_mixed_direction_short(self):
        state = _flat_state()
        d = evaluate(_signals(entry=True, direction=-0.5), state)
        assert d.direction == "short"
