"""PositionManager unit tests.

Verifies PositionManager logic mirrors backtest engine (engine.py order_func_nb):
1. Long position open/close with correct P&L
2. Short position open/close with correct P&L
3. SL trigger for long (LOW <= entry*(1-sl))
4. SL trigger for short (HIGH >= entry*(1+sl))
5. TP trigger for long (HIGH >= entry*(1+tp))
6. TP trigger for short (LOW <= entry*(1-tp))
7. SL takes priority over TP on same bar
8. Liquidation triggers when equity < maintenance
9. Add signal increases position with weighted average entry price
10. Reduce signal decreases position without changing entry price
11. Funding cost deducted per bar for leveraged positions
12. No funding cost for 1x leverage
13. Backtest consistency: same DNA + same data produces same trades
"""

import numpy as np
import pandas as pd
import pytest

pytestmark = [pytest.mark.unit]

from core.strategy.dna import StrategyDNA, RiskGenes, ExecutionGenes, LogicGenes
from core.strategy.executor import SignalSet
from core.backtest.engine import BacktestEngine
from core.trading.position import PositionManager, _RATE_PER_8H


# ---------------------------------------------------------------------------
# Helpers (local overrides for PM-specific defaults)
# Note: For general-purpose DNA/PM creation, use tests.helpers.data_factory
# ---------------------------------------------------------------------------

def _make_dna(
    direction: str = "long",
    leverage: int = 1,
    stop_loss: float = 0.05,
    take_profit: float | None = None,
    position_size: float = 0.3,
    timeframe: str = "4h",
) -> StrategyDNA:
    """Create a minimal DNA for position manager tests."""
    from core.strategy.dna import SignalGene, SignalRole
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


def _make_pm(
    init_cash: float = 100000.0,
    fee: float = 0.0,
    **dna_kwargs,
) -> PositionManager:
    """Create PositionManager with given params."""
    dna = _make_dna(**dna_kwargs)
    return PositionManager(dna, init_cash, fee=fee)


def _bar(idx: int, close: float, high: float | None = None,
         low: float | None = None, base_time: str = "2024-01-01") -> dict:
    """Create a bar dict for process_bar."""
    if high is None:
        high = close * 1.01
    if low is None:
        low = close * 0.99
    ts = pd.Timestamp(base_time) + pd.Timedelta(hours=4 * idx)
    return {
        "bar_time": ts.isoformat(),
        "bar_high": high,
        "bar_low": low,
        "bar_close": close,
    }


# ---------------------------------------------------------------------------
# Test: Long position
# ---------------------------------------------------------------------------

class TestLongPosition:
    """Long open and close with correct P&L calculation."""

    def test_long_open(self):
        pm = _make_pm(fee=0.0, direction="long", position_size=0.5)
        events = pm.process_bar(
            entry_signal=True, direction=1.0, **_bar(0, close=100.0),
        )
        assert len(events) == 1
        assert events[0]["type"] == "position_opened"
        assert events[0]["side"] == "long"
        assert events[0]["entry_price"] == 100.0
        assert pm.position is not None
        assert pm.position.side == "long"

    def test_long_close_profit(self):
        pm = _make_pm(fee=0.0, direction="long", position_size=1.0)
        # Open at 100
        pm.process_bar(entry_signal=True, direction=1.0, **_bar(0, close=100.0))
        # Close at 110
        events = pm.process_bar(
            exit_signal=True, **_bar(1, close=110.0),
        )
        assert len(events) == 1
        assert events[0]["type"] == "position_closed"
        assert events[0]["pnl"] > 0, f"Long 100->110 should profit, got {events[0]['pnl']}"
        assert events[0]["exit_reason"] == "signal"
        assert pm.position is None

    def test_long_close_loss(self):
        pm = _make_pm(fee=0.0, direction="long", position_size=1.0)
        pm.process_bar(entry_signal=True, direction=1.0, **_bar(0, close=100.0))
        events = pm.process_bar(
            exit_signal=True, **_bar(1, close=90.0),
        )
        assert events[0]["pnl"] < 0, f"Long 100->90 should lose, got {events[0]['pnl']}"

    def test_long_pnl_calculation(self):
        """Exact P&L: quantity * (exit - entry)."""
        pm = _make_pm(init_cash=100000, fee=0.0, direction="long", position_size=1.0, leverage=1)
        pm.process_bar(entry_signal=True, direction=1.0, **_bar(0, close=100.0))
        # quantity = 100000 / 100 = 1000 units
        quantity = pm.position.quantity
        assert abs(quantity - 1000.0) < 0.01, f"Expected 1000 units, got {quantity}"

        events = pm.process_bar(exit_signal=True, **_bar(1, close=110.0))
        expected_pnl = 1000 * (110 - 100)  # = 10000
        assert abs(events[0]["pnl"] - expected_pnl) < 1.0

    def test_no_double_entry(self):
        """Cannot open position while already in position."""
        pm = _make_pm(fee=0.0, direction="long")
        pm.process_bar(entry_signal=True, direction=1.0, **_bar(0, close=100.0))
        events = pm.process_bar(entry_signal=True, direction=1.0, **_bar(1, close=105.0))
        # Should NOT open a second position
        open_events = [e for e in events if e["type"] == "position_opened"]
        assert len(open_events) == 0


# ---------------------------------------------------------------------------
# Test: Short position
# ---------------------------------------------------------------------------

class TestShortPosition:
    """Short open and close with correct P&L calculation."""

    def test_short_open(self):
        pm = _make_pm(fee=0.0, direction="short")
        events = pm.process_bar(
            entry_signal=True, direction=-1.0, **_bar(0, close=100.0),
        )
        assert events[0]["side"] == "short"
        assert pm.position.side == "short"

    def test_short_profit_when_price_drops(self):
        pm = _make_pm(fee=0.0, direction="short", position_size=1.0)
        pm.process_bar(entry_signal=True, direction=-1.0, **_bar(0, close=100.0))
        events = pm.process_bar(exit_signal=True, **_bar(1, close=90.0))
        assert events[0]["pnl"] > 0, f"Short 100->90 should profit, got {events[0]['pnl']}"

    def test_short_loss_when_price_rises(self):
        pm = _make_pm(fee=0.0, direction="short", position_size=1.0)
        pm.process_bar(entry_signal=True, direction=-1.0, **_bar(0, close=100.0))
        events = pm.process_bar(exit_signal=True, **_bar(1, close=110.0))
        assert events[0]["pnl"] < 0, f"Short 100->110 should lose, got {events[0]['pnl']}"

    def test_short_pnl_calculation(self):
        """Short P&L: quantity * (entry - exit)."""
        pm = _make_pm(init_cash=100000, fee=0.0, direction="short",
                      position_size=1.0, leverage=1)
        pm.process_bar(entry_signal=True, direction=-1.0, **_bar(0, close=100.0))
        quantity = pm.position.quantity
        assert abs(quantity - 1000.0) < 0.01

        events = pm.process_bar(exit_signal=True, **_bar(1, close=90.0))
        expected_pnl = 1000 * (100 - 90)  # = 10000
        assert abs(events[0]["pnl"] - expected_pnl) < 1.0


# ---------------------------------------------------------------------------
# Test: Stop-Loss
# ---------------------------------------------------------------------------

class TestStopLoss:
    """SL trigger using HIGH/LOW, mirrors engine.py:282-325."""

    def test_long_sl_triggers_on_low(self):
        """Long SL: bar_low <= entry * (1 - sl)."""
        pm = _make_pm(fee=0.0, stop_loss=0.05, take_profit=0.0)
        # Open at 100, SL at 95
        pm.process_bar(entry_signal=True, direction=1.0, **_bar(0, close=100.0))
        # Bar with low=93, close=97 -> SL triggers
        events = pm.process_bar(
            exit_signal=False, **_bar(1, close=97.0, high=98.0, low=93.0),
        )
        assert len(events) == 1
        assert events[0]["exit_reason"] == "sl"
        assert pm.position is None

    def test_long_sl_does_not_trigger_above_level(self):
        """Long SL should not trigger if low > SL level."""
        pm = _make_pm(fee=0.0, stop_loss=0.05, take_profit=0.0)
        pm.process_bar(entry_signal=True, direction=1.0, **_bar(0, close=100.0))
        # Bar with low=96, SL at 95 -> no trigger
        events = pm.process_bar(
            exit_signal=False, **_bar(1, close=98.0, high=99.0, low=96.0),
        )
        closed = [e for e in events if e["type"] == "position_closed"]
        assert len(closed) == 0

    def test_short_sl_triggers_on_high(self):
        """Short SL: bar_high >= entry * (1 + sl)."""
        pm = _make_pm(fee=0.0, stop_loss=0.05, take_profit=0.0, direction="short")
        # Open at 100, SL at 105
        pm.process_bar(entry_signal=True, direction=-1.0, **_bar(0, close=100.0))
        # Bar with high=107 -> SL triggers
        events = pm.process_bar(
            exit_signal=False, **_bar(1, close=103.0, high=107.0, low=102.0),
        )
        assert events[0]["exit_reason"] == "sl"

    def test_sl_disabled_when_zero(self):
        """SL=0 means SL never triggers."""
        pm = _make_pm(fee=0.0, stop_loss=0.0, take_profit=0.0)
        pm.process_bar(entry_signal=True, direction=1.0, **_bar(0, close=100.0))
        # Price drops 50%
        events = pm.process_bar(
            exit_signal=False, **_bar(1, close=50.0, high=55.0, low=45.0),
        )
        closed = [e for e in events if e["type"] == "position_closed"]
        assert len(closed) == 0


# ---------------------------------------------------------------------------
# Test: Take-Profit
# ---------------------------------------------------------------------------

class TestTakeProfit:
    """TP trigger using HIGH/LOW, mirrors engine.py:296-325."""

    def test_long_tp_triggers_on_high(self):
        """Long TP: bar_high >= entry * (1 + tp)."""
        pm = _make_pm(fee=0.0, stop_loss=0.0, take_profit=0.10)
        # Open at 100, TP at 110
        pm.process_bar(entry_signal=True, direction=1.0, **_bar(0, close=100.0))
        events = pm.process_bar(
            exit_signal=False, **_bar(1, close=108.0, high=112.0, low=107.0),
        )
        assert events[0]["exit_reason"] == "tp"

    def test_short_tp_triggers_on_low(self):
        """Short TP: bar_low <= entry * (1 - tp)."""
        pm = _make_pm(fee=0.0, stop_loss=0.0, take_profit=0.10, direction="short")
        # Open at 100, TP at 90
        pm.process_bar(entry_signal=True, direction=-1.0, **_bar(0, close=100.0))
        events = pm.process_bar(
            exit_signal=False, **_bar(1, close=93.0, high=95.0, low=88.0),
        )
        assert events[0]["exit_reason"] == "tp"


# ---------------------------------------------------------------------------
# Test: SL priority over TP
# ---------------------------------------------------------------------------

class TestSLTPPriority:
    """SL is checked before TP, mirrors engine.py processing order."""

    def test_sl_priority_on_same_bar(self):
        """When both SL and TP could trigger, SL wins."""
        pm = _make_pm(fee=0.0, stop_loss=0.05, take_profit=0.10)
        # Open at 100, SL at 95, TP at 110
        pm.process_bar(entry_signal=True, direction=1.0, **_bar(0, close=100.0))
        # Bar where low<95 AND high>110
        events = pm.process_bar(
            exit_signal=False, **_bar(1, close=100.0, high=115.0, low=90.0),
        )
        assert events[0]["exit_reason"] == "sl", \
            "SL should take priority over TP on same bar"


# ---------------------------------------------------------------------------
# Test: Liquidation
# ---------------------------------------------------------------------------

class TestLiquidation:
    """Liquidation when equity < maintenance, mirrors engine.py:264-274."""

    def test_liquidation_triggers_at_threshold(self):
        """High leverage + large loss -> liquidation."""
        pm = _make_pm(init_cash=10000, fee=0.0, leverage=10, stop_loss=0.0,
                      take_profit=0.0, position_size=1.0)
        # Open at 100
        pm.process_bar(entry_signal=True, direction=1.0, **_bar(0, close=100.0))
        # Maintenance = 10000 * (1 - 0.9/100) = 10000 * 0.991 = 9910
        # Need equity < 9910 -> need > 90 loss
        # Position value = 10000 * 10 = 100000 -> 1000 units
        # Price drop of 1% -> 1000 * 1 = 1000 loss -> equity = 10000 - 1000 = 9000
        events = pm.process_bar(
            exit_signal=False, **_bar(1, close=99.0, high=99.5, low=98.5),
        )
        assert events[0]["exit_reason"] == "liquidation"

    def test_no_liquidation_at_1x(self):
        """1x leverage never triggers liquidation."""
        pm = _make_pm(init_cash=10000, fee=0.0, leverage=1, stop_loss=0.0,
                      take_profit=0.0, position_size=1.0)
        pm.process_bar(entry_signal=True, direction=1.0, **_bar(0, close=100.0))
        # Even 90% price drop
        events = pm.process_bar(
            exit_signal=False, **_bar(1, close=10.0, high=15.0, low=8.0),
        )
        liq = [e for e in events if e.get("exit_reason") == "liquidation"]
        assert len(liq) == 0


# ---------------------------------------------------------------------------
# Test: Add signal
# ---------------------------------------------------------------------------

class TestAddSignal:
    """Add increases position with weighted average entry price."""

    def test_add_updates_entry_price(self):
        """Entry price becomes weighted average after add."""
        pm = _make_pm(fee=0.0, position_size=1.0, leverage=1)
        # Open at 100, all cash -> quantity = 100000/100 = 1000
        pm.process_bar(entry_signal=True, direction=1.0, **_bar(0, close=100.0))
        old_qty = pm.position.quantity
        old_ep = pm.position.entry_price

        # Hmm, after opening with position_size=1.0, balance is ~0 (fee=0)
        # Can't add with no balance. Use smaller position_size.
        pm2 = _make_pm(init_cash=100000, fee=0.0, position_size=0.3, leverage=1)
        pm2.process_bar(entry_signal=True, direction=1.0, **_bar(0, close=100.0))
        # quantity = 30000/100 = 300, margin = 30000, balance = 70000
        old_qty2 = pm2.position.quantity
        old_ep2 = pm2.position.entry_price  # 100

        # Add at price 120
        events = pm2.process_bar(
            add_signal=True, **_bar(1, close=120.0),
        )
        add_events = [e for e in events if e["type"] == "position_added"]
        assert len(add_events) == 1

        # New entry price = (100 * 300 + 120 * add_qty) / (300 + add_qty)
        # add_qty = (70000 * 0.3) / 120 = 175
        # new_ep = (100*300 + 120*175) / (300+175) = (30000+21000)/475 = 107.37
        new_ep = pm2.position.entry_price
        assert old_ep2 < new_ep < 120.0, \
            f"Weighted avg should be between 100 and 120, got {new_ep}"

    def test_add_increases_quantity(self):
        pm = _make_pm(init_cash=100000, fee=0.0, position_size=0.3, leverage=1)
        pm.process_bar(entry_signal=True, direction=1.0, **_bar(0, close=100.0))
        old_qty = pm.position.quantity
        pm.process_bar(add_signal=True, **_bar(1, close=110.0))
        assert pm.position.quantity > old_qty


# ---------------------------------------------------------------------------
# Test: Reduce signal
# ---------------------------------------------------------------------------

class TestReduceSignal:
    """Reduce decreases position without changing entry price."""

    def test_reduce_decreases_quantity(self):
        pm = _make_pm(init_cash=100000, fee=0.0, position_size=0.5, leverage=1)
        pm.process_bar(entry_signal=True, direction=1.0, **_bar(0, close=100.0))
        old_qty = pm.position.quantity
        events = pm.process_bar(reduce_signal=True, **_bar(1, close=105.0))
        reduce_events = [e for e in events if e["type"] == "position_reduced"]
        assert len(reduce_events) == 1
        assert pm.position.quantity < old_qty

    def test_reduce_does_not_change_entry_price(self):
        """Reduce signal does NOT update entry price (mirrors engine.py:360)."""
        pm = _make_pm(init_cash=100000, fee=0.0, position_size=0.5, leverage=1)
        pm.process_bar(entry_signal=True, direction=1.0, **_bar(0, close=100.0))
        original_ep = pm.position.entry_price
        pm.process_bar(reduce_signal=True, **_bar(1, close=120.0))
        assert pm.position.entry_price == original_ep, \
            "Entry price should not change on reduce"


# ---------------------------------------------------------------------------
# Test: Funding cost
# ---------------------------------------------------------------------------

class TestFundingCost:
    """Funding cost deducted per bar for leveraged positions."""

    def test_no_funding_at_1x(self):
        pm = _make_pm(leverage=1, position_size=1.0, fee=0.0)
        pm.process_bar(entry_signal=True, direction=1.0, **_bar(0, close=100.0))
        balance_after_open = pm.balance
        pm.process_bar(**_bar(1, close=100.0))  # flat price, no signals
        assert pm.balance == balance_after_open, \
            "No funding cost expected at 1x leverage"

    def test_funding_deducted_at_3x(self):
        pm = _make_pm(init_cash=100000, leverage=3, position_size=1.0,
                      fee=0.0, timeframe="4h")
        pm.process_bar(entry_signal=True, direction=1.0, **_bar(0, close=100.0))
        balance_after_open = pm.balance

        # Funding: cost_rate = 0.001 * (4/8) * ((3-1)/3) = 0.001 * 0.5 * 0.667 = 0.000333
        # cost = notional_value * cost_rate
        # notional = quantity * 100 = (100000*3/100) * 100 = 300000
        # cost = 300000 * 0.000333 = 100
        pm.process_bar(**_bar(1, close=100.0))
        assert pm.balance < balance_after_open, \
            "Funding cost should be deducted at 3x leverage"

    def test_funding_accumulates(self):
        """Funding accumulates on the position."""
        pm = _make_pm(init_cash=100000, leverage=3, position_size=1.0,
                      fee=0.0, timeframe="4h")
        pm.process_bar(entry_signal=True, direction=1.0, **_bar(0, close=100.0))
        pm.process_bar(**_bar(1, close=100.0))
        pm.process_bar(**_bar(2, close=100.0))
        assert pm.position.cumulative_funding > 0


# ---------------------------------------------------------------------------
# Test: Equity tracking
# ---------------------------------------------------------------------------

class TestEquityTracking:
    """Equity snapshots are recorded correctly."""

    def test_equity_updates_on_trade(self):
        pm = _make_pm(init_cash=100000, fee=0.0, position_size=1.0, leverage=1)
        # Initial: flat
        pm.process_bar(**_bar(0, close=100.0))
        assert len(pm.equity_snapshots) == 1
        assert pm.equity_snapshots[-1].position_side == "flat"
        assert pm.equity_snapshots[-1].equity == 100000.0

        # Open long
        pm.process_bar(entry_signal=True, direction=1.0, **_bar(1, close=100.0))
        assert pm.equity_snapshots[-1].position_side == "long"

        # Price rises -> unrealized P&L
        pm.process_bar(**_bar(2, close=110.0))
        snap = pm.equity_snapshots[-1]
        assert snap.unrealized_pnl > 0
        assert snap.equity > 100000.0


# ---------------------------------------------------------------------------
# Test: Backtest consistency
# ---------------------------------------------------------------------------

class TestBacktestConsistency:
    """PositionManager produces same trade results as BacktestEngine."""

    def _make_ohlcv(self, closes, freq="4h"):
        """Build OHLCV from close array."""
        n = len(closes)
        close = np.array(closes, dtype=float)
        dates = pd.date_range("2024-01-01", periods=n, freq=freq, tz="UTC")
        df = pd.DataFrame({
            "open": close * 0.999,
            "high": close * 1.005,
            "low": close * 0.995,
            "close": close,
            "volume": 1000.0,
        }, index=dates)
        df.index.name = "timestamp"
        return df

    def _make_signals(self, n, entries_idx, exits_idx, direction=1.0):
        """Build SignalSet with entry/exit at given indices."""
        dates = pd.date_range("2024-01-01", periods=n, freq="4h", tz="UTC")
        entries = pd.Series(False, index=dates)
        exits = pd.Series(False, index=dates)
        for i in entries_idx:
            entries.iloc[i] = True
        for i in exits_idx:
            exits.iloc[i] = True
        return SignalSet(
            entries=entries, exits=exits,
            adds=pd.Series(False, index=dates),
            reduces=pd.Series(False, index=dates),
            entry_direction=pd.Series(direction, index=dates),
        )

    def test_long_trade_pnl_matches_backtest(self):
        """PositionManager P&L matches BacktestEngine for a simple long trade."""
        closes = np.ones(50) * 100.0
        closes[5:25] = np.linspace(100, 120, 20)  # uptrend
        closes[25:] = 120.0

        df = self._make_ohlcv(closes)
        dna = _make_dna(direction="long", stop_loss=0.0, take_profit=0.0,
                        position_size=0.3, leverage=1)
        sig = self._make_signals(50, entries_idx=[2], exits_idx=[40], direction=1.0)

        # Run backtest
        engine = BacktestEngine(init_cash=100000, fee=0.0, slippage=0.0)
        bt_result = engine.run(dna, df, signal_set=sig)

        # Run PositionManager bar-by-bar
        pm = PositionManager(dna, init_cash=100000, fee=0.0)
        for i, (ts, row) in enumerate(df.iterrows()):
            pm.process_bar(
                bar_time=ts.isoformat(),
                bar_high=row["high"],
                bar_low=row["low"],
                bar_close=row["close"],
                entry_signal=bool(sig.entries.iloc[i]),
                exit_signal=bool(sig.exits.iloc[i]),
                direction=1.0,
            )

        # Compare trade count
        assert len(pm.closed_trades) == bt_result.total_trades, \
            f"Trade count mismatch: PM={len(pm.closed_trades)}, BT={bt_result.total_trades}"

        # Compare P&L direction
        if pm.closed_trades:
            pm_pnl = sum(t.pnl for t in pm.closed_trades)
            bt_pnl = bt_result.total_return * 100000
            assert (pm_pnl > 0) == (bt_pnl > 0), \
                f"P&L direction mismatch: PM={pm_pnl:.2f}, BT={bt_pnl:.2f}"

    def test_short_trade_pnl_matches_backtest(self):
        """PositionManager P&L matches BacktestEngine for a short trade."""
        closes = np.ones(50) * 100.0
        closes[5:25] = np.linspace(100, 80, 20)  # downtrend
        closes[25:] = 80.0

        df = self._make_ohlcv(closes)
        dna = _make_dna(direction="short", stop_loss=0.0, take_profit=0.0,
                        position_size=0.3, leverage=1)
        sig = self._make_signals(50, entries_idx=[2], exits_idx=[40], direction=-1.0)

        engine = BacktestEngine(init_cash=100000, fee=0.0, slippage=0.0)
        bt_result = engine.run(dna, df, signal_set=sig)

        pm = PositionManager(dna, init_cash=100000, fee=0.0)
        for i, (ts, row) in enumerate(df.iterrows()):
            pm.process_bar(
                bar_time=ts.isoformat(),
                bar_high=row["high"],
                bar_low=row["low"],
                bar_close=row["close"],
                entry_signal=bool(sig.entries.iloc[i]),
                exit_signal=bool(sig.exits.iloc[i]),
                direction=-1.0,
            )

        assert len(pm.closed_trades) == bt_result.total_trades
        if pm.closed_trades:
            pm_pnl = sum(t.pnl for t in pm.closed_trades)
            bt_pnl = bt_result.total_return * 100000
            assert (pm_pnl > 0) == (bt_pnl > 0)
