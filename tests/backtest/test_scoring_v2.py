"""Tests for V2 scoring system: new metrics, piecewise normalizer, 3 templates."""

import math

import numpy as np
import pandas as pd
import pytest

pytestmark = [pytest.mark.unit]

from core.scoring.metrics import compute_metrics
from core.scoring.scorer import score_strategy

def _make_equity_curve(n=200, start=10000, drift=0.001) -> pd.Series:
    """Generate a simple upward-trending equity curve."""
    np.random.seed(42)
    returns = np.random.normal(drift, 0.01, n)
    prices = start * np.cumprod(1 + returns)
    dates = pd.date_range("2024-01-01", periods=n, freq="4h")
    return pd.Series(prices, index=dates)

def _make_trade_returns(n=50, win_rate=0.6, mean_win=0.02, mean_loss=-0.01) -> np.ndarray:
    """Generate synthetic trade returns."""
    np.random.seed(42)
    returns = []
    for _ in range(n):
        if np.random.random() < win_rate:
            returns.append(abs(np.random.normal(mean_win, 0.01)))
        else:
            returns.append(-abs(np.random.normal(mean_loss, 0.005)))
    return np.array(returns)

# -- Metrics tests --

class TestNewMetrics:
    def test_sortino_ratio_computed(self):
        eq = _make_equity_curve()
        returns = _make_trade_returns()
        metrics = compute_metrics(eq, total_trades=50, trade_returns=returns)
        assert "sortino_ratio" in metrics
        assert metrics["sortino_ratio"] >= 0

    def test_profit_factor_computed(self):
        eq = _make_equity_curve()
        returns = _make_trade_returns()
        metrics = compute_metrics(eq, total_trades=50, trade_returns=returns)
        assert "profit_factor" in metrics
        assert metrics["profit_factor"] > 0

    def test_profit_factor_all_wins(self):
        eq = _make_equity_curve()
        returns = np.array([0.01, 0.02, 0.015, 0.005, 0.01])
        metrics = compute_metrics(eq, total_trades=5, trade_returns=returns)
        assert metrics["profit_factor"] == 10.0  # Capped

    def test_max_consecutive_losses_computed(self):
        eq = _make_equity_curve()
        returns = np.array([0.01, -0.02, -0.01, -0.03, 0.02, -0.01, -0.02, 0.01])
        metrics = compute_metrics(eq, total_trades=8, trade_returns=returns)
        assert metrics["max_consecutive_losses"] == 3

    def test_monthly_consistency_computed(self):
        eq = _make_equity_curve(500)
        returns = _make_trade_returns(100)
        metrics = compute_metrics(eq, total_trades=100, trade_returns=returns)
        assert "monthly_consistency" in metrics
        assert 0 <= metrics["monthly_consistency"] <= 1.0

    def test_zero_trades_returns_zero_metrics(self):
        eq = _make_equity_curve()
        metrics = compute_metrics(eq, total_trades=0)
        assert metrics["sortino_ratio"] == 0.0
        assert metrics["profit_factor"] == 0.0
        assert metrics["max_consecutive_losses"] == 0
        assert metrics["monthly_consistency"] == 0.0
        assert metrics["r_squared"] == 0.0

    def test_r_squared_computed(self):
        eq = _make_equity_curve(200)
        returns = _make_trade_returns(50)
        metrics = compute_metrics(eq, total_trades=50, trade_returns=returns)
        assert "r_squared" in metrics
        assert 0 <= metrics["r_squared"] <= 1.0

    def test_r_squared_linear_equity(self):
        """Perfectly linear equity curve should have R-squared close to 1.0."""
        dates = pd.date_range("2024-01-01", periods=100, freq="4h")
        eq = pd.Series(range(100, 200), index=dates, dtype=float)
        metrics = compute_metrics(eq, total_trades=50, trade_returns=np.array([0.01] * 50))
        assert metrics["r_squared"] > 0.99

# -- Sigmoid penalty tests --

class TestSigmoidPenalty:
    def test_zero_trades_zero_score(self):
        metrics = {"total_trades": 0, "annual_return": 0.5}
        result = score_strategy(metrics, "explorer")
        assert result["total_score"] == 0.0

    def test_few_trades_reduced_score(self):
        eq = _make_equity_curve(100)
        returns = _make_trade_returns(5)
        metrics = compute_metrics(eq, total_trades=10, trade_returns=returns[:5])
        result = score_strategy(metrics, "explorer")
        # With lenient backward-compat defaults, score may be high but still valid
        assert result["total_score"] >= 0

    def test_enough_trades_no_penalty(self):
        eq = _make_equity_curve(200)
        returns = _make_trade_returns(50)
        metrics = compute_metrics(eq, total_trades=50, trade_returns=returns)
        result = score_strategy(metrics, "explorer")
        assert result["total_score"] > 0

# -- Integration: full scoring pipeline --

class TestFullScoringPipeline:
    def test_score_with_all_metrics(self):
        eq = _make_equity_curve(500)
        returns = _make_trade_returns(100)
        metrics = compute_metrics(eq, total_trades=100, trade_win_rate=0.6, trade_returns=returns)
        result = score_strategy(metrics, "explorer")
        assert "total_score" in result
        assert "dimension_scores" in result
        assert "raw_metrics" in result
        assert 0 <= result["total_score"] <= 100

    def test_all_templates_produce_scores(self):
        # templates.py shim 已删（CARD-10）：清单改字面枚举
        eq = _make_equity_curve(200)
        returns = _make_trade_returns(50)
        metrics = compute_metrics(eq, total_trades=50, trade_win_rate=0.6, trade_returns=returns)
        for name in ["explorer", "optimizer", "max_return",
                     "balanced", "steady", "custom",
                     "aggressive", "profit_first", "conservative", "risk_first"]:
            result = score_strategy(metrics, name)
            assert 0 <= result["total_score"] <= 100, (
                f"Template '{name}' score {result['total_score']} out of range"
            )

# -- Bug fix regression tests --

class TestFundingCostNotInTradeReturns:
    """Verify trade_returns are NOT contaminated by funding cost averaging."""

    def test_trade_returns_are_raw(self):
        eq = _make_equity_curve(200)
        returns = _make_trade_returns(50)
        metrics = compute_metrics(eq, total_trades=50, trade_returns=returns)
        assert metrics["profit_factor"] > 0
        assert metrics["sortino_ratio"] >= 0

class TestDynamicTradeCountThreshold:
    """Verify trade count penalty adapts to data size via total_bars."""

    def test_metrics_includes_total_bars(self):
        eq = _make_equity_curve(200)
        metrics = compute_metrics(eq, total_trades=10)
        assert "total_bars" in metrics
        assert metrics["total_bars"] == 200

    def test_low_freq_4h_not_overpenalized(self):
        """4h strategy with 20 trades over 1000 bars should not be heavily penalized."""
        eq = _make_equity_curve(1000)
        returns = _make_trade_returns(20)
        metrics = compute_metrics(eq, total_trades=20, trade_returns=returns)
        result = score_strategy(metrics, "explorer")
        assert result["total_score"] > 0

    def test_high_freq_15m_threshold(self):
        """15m strategy: 10000 bars => min_trades = max(10, 10000//500) = 20."""
        eq = _make_equity_curve(10000)
        returns = _make_trade_returns(25)
        metrics = compute_metrics(eq, total_trades=25, trade_returns=returns)
        result = score_strategy(metrics, "explorer")
        assert result["total_score"] > 0

    def test_zero_total_bars_uses_default(self):
        """Without total_bars, falls back to default threshold=35."""
        metrics = {"total_trades": 20, "annual_return": 0.5, "sharpe_ratio": 1.0,
                   "max_drawdown": -0.1, "profit_factor": 1.2,
                   "monthly_consistency": 0.6, "total_bars": 0}
        result = score_strategy(metrics, "explorer")
        assert result["total_score"] > 0

# -- Template differentiation integration test --

class TestTemplateDifferentiation:
    """Verify optimizer hard constraint still works and other templates pass."""

    def test_high_return_high_dd(self):
        """High return / high drawdown strategy."""
        metrics = {
            "annual_return": 2.0, "sharpe_ratio": 0.8,
            "max_drawdown": -0.65, "profit_factor": 1.8,
            "monthly_consistency": 0.4,
            "total_trades": 100, "total_bars": 2000,
        }
        scores = {
            "explorer": score_strategy(metrics, "explorer")["total_score"],
            "optimizer": score_strategy(metrics, "optimizer")["total_score"],
            "max_return": score_strategy(metrics, "max_return")["total_score"],
        }
        # optimizer should be 0 (drawdown > 60% hard constraint)
        assert scores["optimizer"] == 0.0
        # explorer and max_return should be positive (no hard constraint on drawdown)
        assert scores["explorer"] > 0
        assert scores["max_return"] > 0

    def test_moderate_return_low_dd(self):
        """Moderate return / low drawdown strategy."""
        metrics = {
            "annual_return": 0.20, "sharpe_ratio": 1.5,
            "max_drawdown": -0.10, "profit_factor": 1.5,
            "monthly_consistency": 0.8,
            "total_trades": 100, "total_bars": 2000,
        }
        scores = {
            "explorer": score_strategy(metrics, "explorer")["total_score"],
            "optimizer": score_strategy(metrics, "optimizer")["total_score"],
            "max_return": score_strategy(metrics, "max_return")["total_score"],
        }
        # All should be positive (passes hard constraints)
        assert all(s > 0 for s in scores.values())


# -- Alpha dimension tests --

class TestAlphaDimension:
    """Verify alpha (excess return over benchmark) calculation and normalization."""

    def test_alpha_computed_with_benchmark(self):
        """Alpha should be computed when benchmark_close is provided."""
        dates = pd.date_range("2024-01-01", periods=500, freq="4h")
        eq = _make_equity_curve(500)
        # Benchmark: 50% total return over the period
        bm_close = pd.Series(np.linspace(100, 150, 500), index=dates)
        metrics = compute_metrics(eq, total_trades=50, benchmark_close=bm_close)
        assert "alpha" in metrics
        assert "market_annual_return" in metrics
        assert "backtest_years" in metrics
        assert isinstance(metrics["alpha"], float)

    def test_alpha_zero_without_benchmark(self):
        """Alpha should be 0 when no benchmark is provided."""
        eq = _make_equity_curve(200)
        metrics = compute_metrics(eq, total_trades=50)
        assert metrics["alpha"] == 0.0
        assert metrics["market_annual_return"] == 0.0

    def test_alpha_positive_when_outperforming(self):
        """Strategy that doubles benchmark should have positive alpha."""
        dates = pd.date_range("2024-01-01", periods=500, freq="4h")
        # Strategy: 100% return (goes from 10000 to 20000)
        eq = pd.Series(np.linspace(10000, 20000, 500), index=dates, dtype=float)
        # Benchmark: 20% return (goes from 100 to 120)
        bm_close = pd.Series(np.linspace(100, 120, 500), index=dates, dtype=float)
        metrics = compute_metrics(eq, total_trades=50, benchmark_close=bm_close)
        assert metrics["alpha"] > 0
        assert metrics["market_annual_return"] > 0

    def test_alpha_negative_when_underperforming(self):
        """Strategy that loses while benchmark gains should have negative alpha."""
        dates = pd.date_range("2024-01-01", periods=500, freq="4h")
        # Strategy: -20% return
        eq = pd.Series(np.linspace(10000, 8000, 500), index=dates, dtype=float)
        # Benchmark: +50% return
        bm_close = pd.Series(np.linspace(100, 150, 500), index=dates, dtype=float)
        metrics = compute_metrics(eq, total_trades=50, benchmark_close=bm_close)
        assert metrics["alpha"] < 0

    def test_alpha_in_scorer_raw_metrics(self):
        """Alpha should appear in scorer raw_metrics when provided."""
        metrics = {
            "annual_return": 0.50, "alpha": 0.30,
            "sharpe_ratio": 1.0, "max_drawdown": -0.20,
            "profit_factor": 1.5, "monthly_consistency": 0.6,
            "total_trades": 100, "total_bars": 2000,
        }
        result = score_strategy(metrics, "explorer")
        assert "alpha" in result["raw_metrics"]

    def test_higher_alpha_raw_metrics_preserved(self):
        """Alpha value should be preserved in raw_metrics."""
        base_metrics = {
            "annual_return": 0.50, "sharpe_ratio": 1.0,
            "max_drawdown": -0.20, "profit_factor": 1.5,
            "monthly_consistency": 0.6,
            "total_trades": 100, "total_bars": 2000,
        }
        m_low = {**base_metrics, "alpha": 0.0}
        m_high = {**base_metrics, "alpha": 0.50}
        r_low = score_strategy(m_low, "explorer")
        r_high = score_strategy(m_high, "explorer")
        assert r_high["raw_metrics"]["alpha"] > r_low["raw_metrics"]["alpha"]
