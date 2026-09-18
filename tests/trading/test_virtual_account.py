"""VirtualAccount unit tests.

Covers:
1. Long position open/close with correct P&L (open price execution)
2. Short position open/close with correct P&L
3. SL trigger for long/short using HIGH/LOW
4. TP trigger for long/short using HIGH/LOW
5. SL priority over TP on same bar
6. Liquidation triggers when equity < maintenance
7. Add signal: weighted average entry price
8. Reduce signal: no change to entry price
9. Funding cost deduction for leveraged positions
10. Equity snapshot tracking
11. process_bar_v2 facade (SL/TP -> decision -> funding -> snapshot)
12. SL triggers then skips pending_decision
13. position_bars_held tracking
14. Backtest consistency (xfail: open vs close difference)
"""

import pytest

pytestmark = [pytest.mark.unit]

from core.trading.types import Decision
from core.trading.account import VirtualAccount


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_dna(
    direction: str = "long",
    leverage: int = 1,
    stop_loss: float = 0.0,
    take_profit: float | None = None,
    position_size: float = 0.3,
    timeframe: str = "4h",
):
    """Create a minimal DNA for account tests."""
    from core.strategy.dna import StrategyDNA, RiskGenes, ExecutionGenes, SignalGene, SignalRole
    gene = SignalGene(
        indicator="EMA",
        params={"period": 10},
        role=SignalRole.ENTRY_TRIGGER,
        condition={"type": "price_above"},
    )
    return StrategyDNA(
        signal_genes=[gene],
        risk_genes=RiskGenes(
            stop_loss=stop_loss,
            take_profit=take_profit,
            position_size=position_size,
            leverage=leverage,
            direction=direction,
        ),
        execution_genes=ExecutionGenes(timeframe=timeframe),
    )


def _make_account(
    init_cash: float = 100_000.0,
    fee: float = 0.0,
    slippage: float = 0.0,
    **dna_kwargs,
) -> VirtualAccount:
    """Create VirtualAccount with given params."""
    dna = _make_dna(**dna_kwargs)
    return VirtualAccount(dna, init_cash, fee=fee, slippage=slippage)


def _ts(idx: int, base: str = "2024-01-01") -> str:
    import pandas as pd
    ts = pd.Timestamp(base) + pd.Timedelta(hours=4 * idx)
    return ts.isoformat()


# ---------------------------------------------------------------------------
# Test: Long position
# ---------------------------------------------------------------------------

class TestLongPosition:

    def test_open_long(self):
        acc = _make_account(fee=0.0, direction="long", position_size=0.5)
        decision = Decision(action="open", direction="long", target_position_pct=0.5)
        events = acc.execute_decision(decision, open_price=100.0)
        assert len(events) == 1
        assert events[0]["type"] == "position_opened"
        assert events[0]["side"] == "long"
        assert events[0]["entry_price"] == 100.0
        assert acc.position is not None
        assert acc.position.side == "long"

    def test_close_long_profit(self):
        acc = _make_account(fee=0.0, direction="long", position_size=1.0)
        acc.execute_decision(
            Decision(action="open", direction="long", target_position_pct=1.0),
            open_price=100.0,
        )
        events = acc.execute_decision(
            Decision(action="close", reason="signal"),
            open_price=110.0,
        )
        assert len(events) == 1
        assert events[0]["type"] == "position_closed"
        assert events[0]["pnl"] > 0
        assert acc.position is None

    def test_close_long_loss(self):
        acc = _make_account(fee=0.0, direction="long", position_size=1.0)
        acc.execute_decision(
            Decision(action="open", direction="long", target_position_pct=1.0),
            open_price=100.0,
        )
        events = acc.execute_decision(
            Decision(action="close", reason="signal"),
            open_price=90.0,
        )
        assert events[0]["pnl"] < 0

    def test_long_pnl_calculation(self):
        acc = _make_account(init_cash=100_000, fee=0.0, direction="long",
                            position_size=1.0, leverage=1)
        acc.execute_decision(
            Decision(action="open", direction="long", target_position_pct=1.0),
            open_price=100.0,
        )
        # quantity = 100000 / 100 = 1000
        assert abs(acc.position.quantity - 1000.0) < 0.01

        events = acc.execute_decision(
            Decision(action="close", reason="signal"),
            open_price=110.0,
        )
        expected_pnl = 1000 * (110 - 100)  # 10000
        assert abs(events[0]["pnl"] - expected_pnl) < 1.0

    def test_no_double_open(self):
        acc = _make_account(fee=0.0, direction="long")
        acc.execute_decision(
            Decision(action="open", direction="long"),
            open_price=100.0,
        )
        events = acc.execute_decision(
            Decision(action="open", direction="long"),
            open_price=105.0,
        )
        open_events = [e for e in events if e["type"] == "position_opened"]
        assert len(open_events) == 0


# ---------------------------------------------------------------------------
# Test: Short position
# ---------------------------------------------------------------------------

class TestShortPosition:

    def test_open_short(self):
        acc = _make_account(fee=0.0, direction="short")
        events = acc.execute_decision(
            Decision(action="open", direction="short"),
            open_price=100.0,
        )
        assert events[0]["side"] == "short"
        assert acc.position.side == "short"

    def test_short_profit_when_price_drops(self):
        acc = _make_account(fee=0.0, direction="short", position_size=1.0)
        acc.execute_decision(
            Decision(action="open", direction="short", target_position_pct=1.0),
            open_price=100.0,
        )
        events = acc.execute_decision(
            Decision(action="close", reason="signal"),
            open_price=90.0,
        )
        assert events[0]["pnl"] > 0

    def test_short_loss_when_price_rises(self):
        acc = _make_account(fee=0.0, direction="short", position_size=1.0)
        acc.execute_decision(
            Decision(action="open", direction="short", target_position_pct=1.0),
            open_price=100.0,
        )
        events = acc.execute_decision(
            Decision(action="close", reason="signal"),
            open_price=110.0,
        )
        assert events[0]["pnl"] < 0

    def test_short_pnl_calculation(self):
        acc = _make_account(init_cash=100_000, fee=0.0, direction="short",
                            position_size=1.0, leverage=1)
        acc.execute_decision(
            Decision(action="open", direction="short", target_position_pct=1.0),
            open_price=100.0,
        )
        assert abs(acc.position.quantity - 1000.0) < 0.01
        events = acc.execute_decision(
            Decision(action="close", reason="signal"),
            open_price=90.0,
        )
        expected_pnl = 1000 * (100 - 90)  # 10000
        assert abs(events[0]["pnl"] - expected_pnl) < 1.0


# ---------------------------------------------------------------------------
# Test: Stop-Loss
# ---------------------------------------------------------------------------

class TestStopLoss:

    def test_long_sl_triggers_on_low(self):
        acc = _make_account(fee=0.0, stop_loss=0.05)
        acc.execute_decision(
            Decision(action="open", direction="long"),
            open_price=100.0,
        )
        events = acc.check_sl_tp(bar_high=98.0, bar_low=93.0)
        assert len(events) == 1
        assert events[0]["exit_reason"] == "sl"
        assert acc.position is None

    def test_long_sl_does_not_trigger_above_level(self):
        acc = _make_account(fee=0.0, stop_loss=0.05)
        acc.execute_decision(
            Decision(action="open", direction="long"),
            open_price=100.0,
        )
        events = acc.check_sl_tp(bar_high=99.0, bar_low=96.0)
        closed = [e for e in events if e["type"] == "position_closed"]
        assert len(closed) == 0

    def test_short_sl_triggers_on_high(self):
        acc = _make_account(fee=0.0, stop_loss=0.05, direction="short")
        acc.execute_decision(
            Decision(action="open", direction="short"),
            open_price=100.0,
        )
        events = acc.check_sl_tp(bar_high=107.0, bar_low=102.0)
        assert events[0]["exit_reason"] == "sl"

    def test_sl_disabled_when_zero(self):
        acc = _make_account(fee=0.0, stop_loss=0.0)
        acc.execute_decision(
            Decision(action="open", direction="long"),
            open_price=100.0,
        )
        events = acc.check_sl_tp(bar_high=55.0, bar_low=45.0)
        closed = [e for e in events if e["type"] == "position_closed"]
        assert len(closed) == 0


# ---------------------------------------------------------------------------
# Test: Take-Profit
# ---------------------------------------------------------------------------

class TestTakeProfit:

    def test_long_tp_triggers_on_high(self):
        acc = _make_account(fee=0.0, stop_loss=0.0, take_profit=0.10)
        acc.execute_decision(
            Decision(action="open", direction="long"),
            open_price=100.0,
        )
        events = acc.check_sl_tp(bar_high=112.0, bar_low=107.0)
        assert events[0]["exit_reason"] == "tp"

    def test_short_tp_triggers_on_low(self):
        acc = _make_account(fee=0.0, stop_loss=0.0, take_profit=0.10, direction="short")
        acc.execute_decision(
            Decision(action="open", direction="short"),
            open_price=100.0,
        )
        events = acc.check_sl_tp(bar_high=95.0, bar_low=88.0)
        assert events[0]["exit_reason"] == "tp"


# ---------------------------------------------------------------------------
# Test: SL priority over TP
# ---------------------------------------------------------------------------

class TestSLTPPriority:

    def test_sl_priority_on_same_bar(self):
        acc = _make_account(fee=0.0, stop_loss=0.05, take_profit=0.10)
        acc.execute_decision(
            Decision(action="open", direction="long"),
            open_price=100.0,
        )
        events = acc.check_sl_tp(bar_high=115.0, bar_low=90.0)
        assert events[0]["exit_reason"] == "sl"


# ---------------------------------------------------------------------------
# Test: Liquidation
# ---------------------------------------------------------------------------

class TestLiquidation:

    def test_liquidation_triggers(self):
        acc = _make_account(init_cash=10_000, fee=0.0, leverage=10,
                            stop_loss=0.0, take_profit=0.0, position_size=1.0)
        acc.execute_decision(
            Decision(action="open", direction="long", target_position_pct=1.0),
            open_price=100.0,
        )
        triggered = acc.check_liquidation(current_price=99.0)
        assert triggered is True

    def test_no_liquidation_at_1x(self):
        acc = _make_account(init_cash=10_000, fee=0.0, leverage=1,
                            stop_loss=0.0, take_profit=0.0, position_size=1.0)
        acc.execute_decision(
            Decision(action="open", direction="long", target_position_pct=1.0),
            open_price=100.0,
        )
        triggered = acc.check_liquidation(current_price=10.0)
        assert triggered is False


# ---------------------------------------------------------------------------
# Test: Add / Reduce
# ---------------------------------------------------------------------------

class TestAddReduce:

    def test_add_updates_entry_price(self):
        acc = _make_account(fee=0.0, position_size=0.3, leverage=1)
        acc.execute_decision(
            Decision(action="open", direction="long", target_position_pct=0.3),
            open_price=100.0,
        )
        old_ep = acc.position.entry_price  # 100
        # balance ~= 70000, add 30% of balance at price 120
        events = acc.execute_decision(
            Decision(action="add", target_position_pct=0.3),
            open_price=120.0,
        )
        add_events = [e for e in events if e["type"] == "position_added"]
        assert len(add_events) == 1
        new_ep = acc.position.entry_price
        assert old_ep < new_ep < 120.0

    def test_add_increases_quantity(self):
        acc = _make_account(fee=0.0, position_size=0.3, leverage=1)
        acc.execute_decision(
            Decision(action="open", direction="long", target_position_pct=0.3),
            open_price=100.0,
        )
        old_qty = acc.position.quantity
        acc.execute_decision(
            Decision(action="add", target_position_pct=0.3),
            open_price=110.0,
        )
        assert acc.position.quantity > old_qty

    def test_reduce_decreases_quantity(self):
        acc = _make_account(fee=0.0, position_size=0.5, leverage=1)
        acc.execute_decision(
            Decision(action="open", direction="long", target_position_pct=0.5),
            open_price=100.0,
        )
        old_qty = acc.position.quantity
        events = acc.execute_decision(
            Decision(action="reduce"),
            open_price=105.0,
        )
        reduce_events = [e for e in events if e["type"] == "position_reduced"]
        assert len(reduce_events) == 1
        assert acc.position.quantity < old_qty

    def test_reduce_does_not_change_entry_price(self):
        acc = _make_account(fee=0.0, position_size=0.5, leverage=1)
        acc.execute_decision(
            Decision(action="open", direction="long", target_position_pct=0.5),
            open_price=100.0,
        )
        original_ep = acc.position.entry_price
        acc.execute_decision(
            Decision(action="reduce"),
            open_price=120.0,
        )
        assert acc.position.entry_price == original_ep


# ---------------------------------------------------------------------------
# Test: Funding cost
# ---------------------------------------------------------------------------

class TestFundingCost:

    def test_no_funding_at_1x(self):
        acc = _make_account(leverage=1, position_size=1.0, fee=0.0)
        acc.execute_decision(
            Decision(action="open", direction="long", target_position_pct=1.0),
            open_price=100.0,
        )
        balance_after = acc.balance
        acc.apply_funding(current_price=100.0)
        assert acc.balance == balance_after

    def test_funding_deducted_at_3x(self):
        acc = _make_account(leverage=3, position_size=1.0, fee=0.0, timeframe="4h")
        acc.execute_decision(
            Decision(action="open", direction="long", target_position_pct=1.0),
            open_price=100.0,
        )
        balance_after = acc.balance
        acc.apply_funding(current_price=100.0)
        assert acc.balance < balance_after

    def test_funding_accumulates(self):
        acc = _make_account(leverage=3, position_size=1.0, fee=0.0, timeframe="4h")
        acc.execute_decision(
            Decision(action="open", direction="long", target_position_pct=1.0),
            open_price=100.0,
        )
        acc.apply_funding(current_price=100.0)
        acc.apply_funding(current_price=100.0)
        assert acc.position.cumulative_funding > 0


# ---------------------------------------------------------------------------
# Test: Equity tracking
# ---------------------------------------------------------------------------

class TestEquityTracking:

    def test_equity_updates_on_trade(self):
        acc = _make_account(fee=0.0, position_size=1.0, leverage=1)
        # Flat snapshot
        acc.take_snapshot(bar_time=_ts(0), current_price=100.0)
        assert len(acc.equity_snapshots) == 1
        assert acc.equity_snapshots[-1].position_side == "flat"
        assert acc.equity_snapshots[-1].equity == 100_000.0

        # Open long
        acc.execute_decision(
            Decision(action="open", direction="long", target_position_pct=1.0),
            open_price=100.0,
        )
        acc.take_snapshot(bar_time=_ts(1), current_price=110.0)
        assert acc.equity_snapshots[-1].position_side == "long"
        assert acc.equity_snapshots[-1].unrealized_pnl > 0


# ---------------------------------------------------------------------------
# Test: process_bar_v2 facade
# ---------------------------------------------------------------------------

class TestProcessBarV2:

    def test_full_pipeline(self):
        """process_bar_v2: no decision -> snapshot only."""
        acc = _make_account(fee=0.0, position_size=0.3)
        events, deferred = acc.process_bar_v2(
            bar_high=102.0, bar_low=99.0,
            bar_open=100.0, bar_close=101.0,
            bar_time=_ts(0),
            pending_decision=None,
        )
        assert events == []  # flat, no decision -> no events
        assert len(acc.equity_snapshots) == 1

    def test_sl_then_skip_decision(self):
        """SL triggers -> pending_decision should be skipped."""
        acc = _make_account(fee=0.0, stop_loss=0.05)
        acc.execute_decision(
            Decision(action="open", direction="long"),
            open_price=100.0,
        )
        # Process bar where SL triggers AND there's a pending close
        decision = Decision(action="close", reason="signal")
        events, deferred = acc.process_bar_v2(
            bar_high=98.0, bar_low=90.0,  # low=90 < 100*0.95 -> SL
            bar_open=95.0, bar_close=97.0,
            bar_time=_ts(1),
            pending_decision=decision,
        )
        # Should only have SL close, not the decision close
        closes = [e for e in events if e["type"] == "position_closed"]
        assert len(closes) == 1
        assert closes[0]["exit_reason"] == "sl"

    def test_decision_executed_at_open_price(self):
        """Decision executes at bar_open price."""
        acc = _make_account(fee=0.0, direction="long", position_size=0.5)
        decision = Decision(action="open", direction="long", target_position_pct=0.5)
        events, deferred = acc.process_bar_v2(
            bar_high=105.0, bar_low=99.0,
            bar_open=100.0, bar_close=103.0,
            bar_time=_ts(0),
            pending_decision=decision,
        )
        opens = [e for e in events if e["type"] == "position_opened"]
        assert len(opens) == 1
        assert opens[0]["entry_price"] == 100.0  # open price, not close


# ---------------------------------------------------------------------------
# Test: Bars held tracking
# ---------------------------------------------------------------------------

class TestBarsHeldTracking:

    def test_bars_held_increments(self):
        acc = _make_account(fee=0.0, direction="long")
        acc.execute_decision(
            Decision(action="open", direction="long"),
            open_price=100.0,
        )
        state = acc.get_state(current_price=100.0)
        assert state.position_bars_held == 0

        acc.take_snapshot(bar_time=_ts(0), current_price=100.0)
        acc.take_snapshot(bar_time=_ts(1), current_price=100.0)
        state = acc.get_state(current_price=100.0)
        assert state.position_bars_held == 2

    def test_bars_held_resets_after_close_and_reopen(self):
        """bars_held should not accumulate across different trades."""
        acc = _make_account(fee=0.0, direction="long", stop_loss=0.0,
                            take_profit=0.0)

        # Trade 1: open -> 3 process_bar_v2 -> close
        acc.process_bar_v2(
            101, 99, 100, 100, _ts(0),
            pending_decision=Decision(action="open", direction="long"),
        )
        acc.process_bar_v2(101, 99, 100, 100, _ts(1), None)
        acc.process_bar_v2(101, 99, 100, 100, _ts(2), None)
        acc.process_bar_v2(101, 99, 100, 100, _ts(3), None)
        state = acc.get_state(100.0)
        assert state.position_bars_held == 4  # bar 0-3

        acc.process_bar_v2(
            101, 99, 100, 100, _ts(4),
            pending_decision=Decision(action="close", reason="signal"),
        )
        assert acc.position is None

        # Trade 2: open -> 1 process_bar_v2
        acc.process_bar_v2(
            101, 99, 100, 100, _ts(5),
            pending_decision=Decision(action="open", direction="long"),
        )
        state = acc.get_state(100.0)
        assert state.position_bars_held == 1  # only current trade

    def test_bars_held_via_process_bar_v2(self):
        """process_bar_v2 increments bars_held each call when position open."""
        acc = _make_account(fee=0.0, direction="long", stop_loss=0.0)
        acc.process_bar_v2(
            101, 99, 100, 100, _ts(0),
            pending_decision=Decision(action="open", direction="long"),
        )
        assert acc.get_state(100.0).position_bars_held == 1

        acc.process_bar_v2(101, 99, 100, 100, _ts(1), None)
        assert acc.get_state(100.0).position_bars_held == 2

        acc.process_bar_v2(101, 99, 100, 100, _ts(2), None)
        assert acc.get_state(100.0).position_bars_held == 3


# ---------------------------------------------------------------------------
# Test: get_state
# ---------------------------------------------------------------------------

class TestGetState:

    def test_flat_state(self):
        acc = _make_account(fee=0.0)
        state = acc.get_state(current_price=100.0)
        assert state.balance == 100_000.0
        assert state.has_position is False
        assert state.position_side == "flat"

    def test_long_state(self):
        acc = _make_account(fee=0.0, direction="long", position_size=0.3)
        acc.execute_decision(
            Decision(action="open", direction="long", target_position_pct=0.3),
            open_price=100.0,
        )
        state = acc.get_state(current_price=110.0)
        assert state.has_position is True
        assert state.position_side == "long"
        assert state.unrealized_pnl > 0


# ---------------------------------------------------------------------------
# Test: Open price execution
# ---------------------------------------------------------------------------

class TestOpenPriceExecution:

    def test_execute_at_open_not_close(self):
        """Verify that execution uses open price, not close."""
        acc = _make_account(fee=0.0, direction="long", position_size=1.0, leverage=1)
        decision = Decision(action="open", direction="long", target_position_pct=1.0)
        # Execute with open=100, but current market is at close=110
        acc.execute_decision(decision, open_price=100.0)
        # Entry price should be 100 (open), not 110 (close)
        assert acc.position.entry_price == 100.0
        assert acc.position.quantity == 1000.0  # 100000 / 100


# ---------------------------------------------------------------------------
# Test: Backtest consistency (xfail)
# ---------------------------------------------------------------------------

class TestBacktestConsistency:
    """V2 uses open price, so P&L will differ from backtest (close price).
    This is an intentional design choice. Test marked as xfail."""

    def test_pnl_matches_backtest(self):
        import numpy as np
        import pandas as pd
        from core.strategy.executor import SignalSet
        from core.backtest.engine import BacktestEngine

        closes = np.ones(50) * 100.0
        closes[5:25] = np.linspace(100, 120, 20)
        closes[25:] = 120.0

        dates = pd.date_range("2024-01-01", periods=50, freq="4h", tz="UTC")
        df = pd.DataFrame({
            "open": closes * 0.999,
            "high": closes * 1.005,
            "low": closes * 0.995,
            "close": closes,
            "volume": 1000.0,
        }, index=dates)
        df.index.name = "timestamp"

        dna = _make_dna(direction="long", stop_loss=0.0, take_profit=0.0,
                        position_size=0.3, leverage=1)

        entries = pd.Series(False, index=dates)
        entries.iloc[2] = True
        exits = pd.Series(False, index=dates)
        exits.iloc[40] = True
        sig = SignalSet(
            entries=entries, exits=exits,
            adds=pd.Series(False, index=dates),
            reduces=pd.Series(False, index=dates),
            entry_direction=pd.Series(1.0, index=dates),
        )

        # Backtest
        engine = BacktestEngine(init_cash=100000, fee=0.0, slippage=0.0)
        bt_result = engine.run(dna, df, signal_set=sig)

        # VirtualAccount bar-by-bar (open price execution)
        acc = _make_account(fee=0.0, direction="long", position_size=0.3, leverage=1)
        for i, (ts, row) in enumerate(df.iterrows()):
            # In V2, signal at bar i -> decision -> execute at bar i+1 open
            decision = None
            if i > 0:
                if sig.entries.iloc[i - 1]:
                    decision = Decision(action="open", direction="long",
                                        target_position_pct=0.3)
                elif sig.exits.iloc[i - 1] and acc.position is not None:
                    decision = Decision(action="close", reason="signal")

            acc.process_bar_v2(
                bar_high=row["high"], bar_low=row["low"],
                bar_open=row["open"], bar_close=row["close"],
                bar_time=ts.isoformat(),
                pending_decision=decision,
            )

        if acc.closed_trades:
            pm_pnl = sum(t.pnl for t in acc.closed_trades)
            bt_pnl = bt_result.total_return * 100000
            assert abs(pm_pnl - bt_pnl) < 1.0


# ---------------------------------------------------------------------------
# Test: entry_size_pct (gradual position building)
# ---------------------------------------------------------------------------

class TestEntrySizePct:

    def test_open_uses_entry_size_pct_from_decision(self):
        """Decision.entry_size_pct should control actual position size."""
        dna = _make_dna(position_size=0.3)
        acc = VirtualAccount(dna, init_cash=100_000, fee=0.001)

        # Decision with entry_size_pct=0.1 (33% of 0.3)
        decision = Decision(
            action="open", direction="long",
            target_position_pct=0.3, entry_size_pct=0.099,
        )
        events = acc.execute_decision(decision, open_price=50000)
        assert events[0]["type"] == "position_opened"
        # Margin should be ~9900 (9.9% of 100k), not 30000 (30%)
        assert acc.position.margin == pytest.approx(9900, rel=0.01)

    def test_open_falls_back_to_position_size_when_zero(self):
        """When entry_size_pct=0, should use DNA position_size."""
        dna = _make_dna(position_size=0.3)
        acc = VirtualAccount(dna, init_cash=100_000, fee=0.001)

        decision = Decision(action="open", direction="long")
        events = acc.execute_decision(decision, open_price=50000)
        assert events[0]["type"] == "position_opened"
        assert acc.position.margin == pytest.approx(30000, rel=0.01)


# ---------------------------------------------------------------------------
# Test: Fee deduction on open/close
# ---------------------------------------------------------------------------


class TestFeeDeduction:

    def test_open_fee_deducted(self):
        acc = _make_account(fee=0.001, position_size=0.5, leverage=1)
        acc.execute_decision(
            Decision(action="open", direction="long", target_position_pct=0.5),
            open_price=100.0,
        )
        # margin = 50000, fee = 0.001 * 500 * 100 = 50
        assert acc.position.open_cost > 0
        assert acc.position.open_cost == pytest.approx(50.0, rel=0.01)
        # balance = 100000 - 50000 - 50 = 49950
        assert acc.balance == pytest.approx(49950.0, rel=0.01)

    def test_close_fee_deducted_from_pnl(self):
        acc = _make_account(fee=0.001, position_size=1.0, leverage=1)
        acc.execute_decision(
            Decision(action="open", direction="long", target_position_pct=1.0),
            open_price=100.0,
        )
        events = acc.execute_decision(
            Decision(action="close", reason="signal"),
            open_price=110.0,
        )
        # gross pnl = 1000 * (110-100) = 10000
        # open_fee = 0.001 * 1000 * 100 = 100
        # close_fee = 0.001 * 1000 * 110 = 110
        # net pnl = 10000 - 100 - 110 = 9790
        assert events[0]["pnl"] < 10000  # less than gross
        assert events[0]["pnl"] == pytest.approx(9790.0, rel=0.01)

    def test_no_fee_when_zero(self):
        acc = _make_account(fee=0.0, position_size=1.0, leverage=1)
        acc.execute_decision(
            Decision(action="open", direction="long", target_position_pct=1.0),
            open_price=100.0,
        )
        assert acc.position.open_cost == 0.0

    def test_insufficient_balance_returns_skipped(self):
        acc = _make_account(init_cash=1.0, fee=0.0, position_size=0.3)
        events = acc.execute_decision(
            Decision(action="open", direction="long", target_position_pct=0.3),
            open_price=100.0,
        )
        # margin = 1 * 0.3 = 0.3, quantity = 0.3/100 = 0.003, fee=0
        # This should succeed (margin > 0), but balance becomes tiny
        assert events[0]["type"] == "position_opened"


# ---------------------------------------------------------------------------
# Test: Slippage
# ---------------------------------------------------------------------------


class TestSlippage:

    def test_slippage_on_open(self):
        acc = _make_account(fee=0.0, slippage=0.001, position_size=1.0, leverage=1)
        acc.execute_decision(
            Decision(action="open", direction="long", target_position_pct=1.0),
            open_price=100.0,
        )
        # slippage = 0.001 * 1000 * 100 = 100
        assert acc.position.open_cost == pytest.approx(100.0, rel=0.01)
        # balance = 100000 - 100000 - 100 = -100 (slippage eats into margin)
        assert acc.balance < 0  # slippage can push balance negative

    def test_slippage_on_close(self):
        acc = _make_account(fee=0.0, slippage=0.001, position_size=1.0, leverage=1)
        acc.execute_decision(
            Decision(action="open", direction="long", target_position_pct=1.0),
            open_price=100.0,
        )
        events = acc.execute_decision(
            Decision(action="close", reason="signal"),
            open_price=110.0,
        )
        # slippage_cost = 0.001 * 1000 * 110 = 110
        assert events[0]["slippage_paid"] == pytest.approx(110.0, rel=0.01)


# ---------------------------------------------------------------------------
# Test: fill_order (Order-based execution)
# ---------------------------------------------------------------------------


class TestFillOrder:

    def _make_order(self, side="long", price=100.0, size_pct=0.3, source="entry",
                    order_type="limit"):
        from core.trading.types import Order
        return Order(
            order_id="test-fill-001",
            created_at_bar=0,
            side=side,
            price=price,
            size_pct=size_pct,
            source=source,
            order_type=order_type,
        )

    def test_entry_order_opens_position(self):
        acc = _make_account(fee=0.0, direction="long")
        order = self._make_order(side="long", price=98.0, size_pct=0.3)
        events = acc.fill_order(order)
        assert len(events) == 1
        assert events[0]["type"] == "position_opened"
        assert events[0]["entry_price"] == 98.0
        assert acc.position is not None
        assert acc.position.side == "long"

    def test_entry_order_skipped_when_has_position(self):
        acc = _make_account(fee=0.0, direction="long")
        acc._open_position("long", 100.0, 0.3)
        order = self._make_order(side="long", price=95.0)
        events = acc.fill_order(order)
        assert len(events) == 0

    def test_add_order_increases_position(self):
        acc = _make_account(fee=0.0, direction="long", position_size=0.3)
        acc._open_position("long", 100.0, 0.3)
        old_qty = acc.position.quantity
        old_margin = acc.position.margin

        order = self._make_order(side="long", price=105.0, size_pct=0.3, source="add")
        events = acc.fill_order(order)
        assert len(events) == 1
        assert events[0]["type"] == "position_added"
        assert acc.position.quantity > old_qty
        assert acc.position.margin > old_margin

    def test_add_order_skipped_when_no_position(self):
        acc = _make_account(fee=0.0)
        order = self._make_order(side="long", price=100.0, source="add")
        events = acc.fill_order(order)
        assert len(events) == 0

    def test_order_price_zero_raises(self):
        """price=0 causes ZeroDivisionError in _open_position."""
        acc = _make_account(fee=0.0, direction="long")
        order = self._make_order(side="long", price=0.0)
        with pytest.raises(ZeroDivisionError):
            acc.fill_order(order)


# ---------------------------------------------------------------------------
# Test: ATR-based SL/TP via stored prices
# ---------------------------------------------------------------------------


class TestATRStopLoss:
    """VirtualAccount with sl_price/tp_price stored in Position."""

    def test_long_sl_with_stored_price(self):
        acc = _make_account(stop_loss=0.0)
        # Open with explicit SL price (simulating ATR mode)
        acc.execute_decision(
            Decision(action="open", direction="long", target_position_pct=0.3),
            open_price=100.0,
            sl_price=96.0,  # 4 points below entry
        )
        assert acc.position.sl_price == 96.0

        # Bar low touches SL
        events = acc.check_sl_tp(bar_high=102, bar_low=95.0)
        assert len(events) == 1
        assert events[0]["exit_reason"] == "sl"
        assert abs(events[0]["exit_price"] - 96.0) < 0.01

    def test_long_tp_with_stored_price(self):
        acc = _make_account(stop_loss=0.0)
        acc.execute_decision(
            Decision(action="open", direction="long", target_position_pct=0.3),
            open_price=100.0,
            tp_price=108.0,
        )
        events = acc.check_sl_tp(bar_high=110.0, bar_low=99.0)
        assert len(events) == 1
        assert events[0]["exit_reason"] == "tp"
        assert abs(events[0]["exit_price"] - 108.0) < 0.01

    def test_short_sl_with_stored_price(self):
        acc = _make_account(stop_loss=0.0, direction="short")
        acc.execute_decision(
            Decision(action="open", direction="short", target_position_pct=0.3),
            open_price=100.0,
            sl_price=105.0,
        )
        events = acc.check_sl_tp(bar_high=106.0, bar_low=98.0)
        assert len(events) == 1
        assert events[0]["exit_reason"] == "sl"
        assert abs(events[0]["exit_price"] - 105.0) < 0.01

    def test_no_sl_trigger_when_price_above_stored(self):
        acc = _make_account(stop_loss=0.0)
        acc.execute_decision(
            Decision(action="open", direction="long", target_position_pct=0.3),
            open_price=100.0,
            sl_price=96.0,
        )
        events = acc.check_sl_tp(bar_high=102, bar_low=97.0)
        assert len(events) == 0

    def test_stored_price_overrides_percentage(self):
        """When sl_price is stored, percentage calculation is ignored."""
        acc = _make_account(stop_loss=0.05)  # 5% SL = 95.0
        acc.execute_decision(
            Decision(action="open", direction="long", target_position_pct=0.3),
            open_price=100.0,
            sl_price=98.0,  # Stored price overrides
        )
        # Bar low=96.5 touches stored SL (98.0) but not percentage SL (95.0)
        events = acc.check_sl_tp(bar_high=102, bar_low=96.5)
        assert len(events) == 1
        assert abs(events[0]["exit_price"] - 98.0) < 0.01

    def test_fill_order_with_stored_prices(self):
        acc = _make_account(fee=0.0)
        from core.trading.types import Order
        order = Order(
            order_id="test-atr-001", created_at_bar=0,
            side="long", price=100.0, size_pct=0.3,
            source="entry",
        )
        events = acc.fill_order(order, sl_price=96.0, tp_price=108.0)
        assert events[0]["type"] == "position_opened"
        assert acc.position.sl_price == 96.0
        assert acc.position.tp_price == 108.0

    def test_percentage_fallback_when_no_stored_price(self):
        """When sl_price is None, falls back to percentage calculation."""
        acc = _make_account(stop_loss=0.05)
        acc.execute_decision(
            Decision(action="open", direction="long", target_position_pct=0.3),
            open_price=100.0,
        )
        assert acc.position.sl_price is None
        events = acc.check_sl_tp(bar_high=102, bar_low=94.0)
        assert len(events) == 1
        assert abs(events[0]["exit_price"] - 95.0) < 0.01  # 100 * 0.95
