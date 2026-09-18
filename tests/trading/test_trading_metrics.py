"""Tests for core.scoring.trading_metrics pure functions.

These formulas were moved verbatim from api/db_ext.py; the tests pin the math
so future refactors cannot silently change equity / profit_factor / drawdown.
"""
import pytest

pytestmark = [pytest.mark.unit]
from core.scoring.trading_metrics import (
    TradingMetricsInput,
    compute_equity,
    compute_trading_metrics,
)


class TestComputeEquity:
    def test_no_position_returns_balance(self):
        assert compute_equity(1000.0, None, 0.0, 0.0) == 1000.0
        # falsy position_side → balance
        assert compute_equity(1000.0, "", 200.0, 50.0) == 1000.0

    def test_with_position_adds_margin_and_pnl(self):
        assert compute_equity(1000.0, "long", 200.0, 50.0) == 1250.0

    def test_none_margin_pnl_treated_as_zero(self):
        assert compute_equity(1000.0, "long", None, None) == 1000.0


class TestComputeTradingMetrics:
    def _data(self, **kw):
        base = dict(
            initial_cash=10000.0,
            balance=10500.0,
            total_trades=4,
            win_count=3,
            loss_count=1,
            realized_pnl=500.0,
            unrealized_pnl=0.0,
            position_side=None,
            position_margin=0.0,
            close_pnls=[200.0, 150.0, 100.0, -50.0],
            equity_snapshots=[10000.0, 10200.0, 10100.0, 10500.0],
        )
        base.update(kw)
        return TradingMetricsInput(**base)

    def test_win_rate(self):
        assert compute_trading_metrics(self._data())["win_rate"] == round(3 / 4, 4)

    def test_profit_factor(self):
        # gross_profit=450, gross_loss=-50 → 9.0
        assert compute_trading_metrics(self._data())["profit_factor"] == round(450 / 50, 4)

    def test_profit_factor_inf_when_no_loss(self):
        m = compute_trading_metrics(self._data(close_pnls=[100.0, 200.0]))
        assert m["profit_factor"] is None  # inf mapped to None

    def test_max_drawdown(self):
        # peak 10200, trough 10100 → dd 100
        assert compute_trading_metrics(self._data())["max_drawdown"] == 100.0

    def test_total_return_uses_equity(self):
        # equity=10500 (no position), return=0.05
        assert compute_trading_metrics(self._data())["total_return"] == round(0.05, 6)

    def test_total_return_with_position_uses_equity_not_balance(self):
        # balance alone understates; equity = balance + margin + pnl
        m = compute_trading_metrics(self._data(
            balance=10000.0, position_side="long",
            position_margin=300.0, unrealized_pnl=200.0,
        ))
        # equity=10000+300+200=10500 → return 0.05
        assert m["total_return"] == round(0.05, 6)

    def test_returns_all_expected_keys(self):
        keys = set(compute_trading_metrics(self._data()).keys())
        expected = {
            "total_return", "total_return_pct", "win_rate", "profit_factor",
            "max_drawdown", "max_drawdown_pct", "avg_trade_pnl", "total_trades",
            "total_pnl", "realized_pnl", "unrealized_pnl", "win_count", "loss_count",
        }
        assert expected.issubset(keys)
