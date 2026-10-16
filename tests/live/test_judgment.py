"""实盘判定规则单元测试（rebuild-paper-trading 组 6 迁移复刻）。

来源：tests/trading/test_judgment.py（组 7 将随 core/trading 旧域删除）——实盘
判定层行为契约随 core/live_trading 自有域先行复刻，import 切自有域、
断言面零改动（迁移零回归承诺的覆盖承接）。注意：min_hold_bars/
min_profit_ratio/profit_add_only 是实盘链自有判定规则（live_trading
行为规格），与纸盘「考场无观点」禁私设门槛不冲突——两链两规格。
"""

import pytest

pytestmark = [pytest.mark.unit]

from core.live_trading.types import AccountState, BarSignals, Decision, JudgmentConfig
from core.live_trading.judgment import evaluate


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


class TestNeutralDirectionAndConfidence:
    """覆盖补全（闸门盲区消解）：direction=0 双语境 hold+置信度钳制。"""

    def test_entry_with_position_zero_direction_holds(self):
        """Rule 4：entry+有仓但 direction=0 → hold(neutral_direction)。"""
        state = _long_state()
        d = evaluate(_signals(entry=True, direction=0.0), state)
        assert d.action == "hold"
        assert d.reason == "neutral_direction"

    def test_entry_without_position_zero_direction_holds(self):
        """Rule 2：entry+无仓但 direction=0 → hold(neutral_direction)。"""
        state = _flat_state()
        d = evaluate(_signals(entry=True, direction=0.0), state)
        assert d.action == "hold"
        assert d.reason == "neutral_direction"

    def test_confidence_sizing_clamps_low_confidence(self):
        """confidence_sizing_enabled：confidence=0.05 < 0.1 下限 →
        entry_size_pct 按 0.1 钳制（0.30×0.5×0.1=0.015）。"""
        state = _flat_state()  # target 缺省 0.30（_compute_target）
        config = JudgmentConfig(
            initial_entry_pct=0.5, confidence_sizing_enabled=True)
        signals = BarSignals(
            entry=True, exit=False, add=False, reduce=False,
            direction=1.0, confidence=0.05)
        d = evaluate(signals, state, config)
        assert d.action == "open"
        assert d.entry_size_pct == pytest.approx(0.30 * 0.5 * 0.1)


class TestNaNDirectionGuardAndActionMapping:
    """覆盖补全（闸门盲区消解）：from_signal_set 的 NaN 方向压制
    +decision_to_action 的无匹配分支。"""

    def test_from_signal_set_nan_direction_suppresses_entry(self):
        """NaN direction 防御：entries=True 但方向 NaN → entry 压制
        False+方向归零（防误开仓）。"""
        import pandas as pd

        from core.live_trading.types import BarSignals

        entries = pd.Series([True])
        df = pd.DataFrame({
            "entries": entries,
            "exits": pd.Series([False]),
            "adds": pd.Series([False]),
            "reduces": pd.Series([False]),
            "entry_direction": pd.Series([float("nan")]),
        })

        class _Sig:
            pass

        sig_set = _Sig()
        # from_signal_set 读列名的最小形制（列名漂移由类型契约守护）
        sig_set.entries = df["entries"]
        sig_set.exits = df["exits"]
        sig_set.adds = df["adds"]
        sig_set.reduces = df["reduces"]
        sig_set.entry_direction = df["entry_direction"]
        sig = BarSignals.from_signal_set(sig_set, 0)
        assert sig.entry is False  # NaN 压制
        assert sig.direction == 0.0

    def test_decision_to_action_hold_returns_none(self):
        """decision_to_action：action=hold → None（无单可发）+open 无
        方向 → None。"""
        from core.live_trading.pipeline import decision_to_action
        from core.live_trading.types import Decision

        assert decision_to_action(Decision(action="hold"), "") is None
        assert decision_to_action(
            Decision(action="open", direction=None), "") is None
