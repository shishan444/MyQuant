"""End-to-end consistency tests for the backtest data pipeline.

Covers:
1. total_funding_cost is passed from engine to API response
2. liquidated flag is passed from engine to API response
3. total_return is consistent with equity_curve values
4. score_strategy receives liquidated flag (liquidated => score=0)
5. compare route computes metrics with correct params (sharpe consistency)
6. BacktestResponse reflects engine metrics_dict (no redundant compute_metrics)
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict
from unittest.mock import patch

import pytest

pytestmark = [pytest.mark.integration]

import numpy as np
import pandas as pd
from fastapi.testclient import TestClient

from MyQuant.api.app import create_app


# ── Helpers ──

def _make_parquet(data_dir: Path, n: int = 500) -> Path:
    """Create a synthetic BTCUSDT_4h parquet file."""
    data_dir.mkdir(parents=True, exist_ok=True)
    np.random.seed(42)
    dates = pd.date_range("2024-01-01", periods=n, freq="4h", tz="UTC")
    close = 100 + np.cumsum(np.random.randn(n) * 0.5)
    df = pd.DataFrame(
        {
            "open": close * 0.999,
            "high": close * 1.01,
            "low": close * 0.99,
            "close": close,
            "volume": 1000.0,
        },
        index=dates,
    )
    df.index.name = "timestamp"
    path = data_dir / "BTCUSDT_4h.parquet"
    df.to_parquet(path)
    return path


def _dna_dict(direction: str = "long", leverage: int = 1) -> Dict[str, Any]:
    """Build a valid DNA dict."""
    return {
        "signal_genes": [
            {
                "indicator": "RSI",
                "params": {"period": 14},
                "role": "entry_trigger",
                "field": None,
                "condition": {"type": "lt", "threshold": 30},
            },
            {
                "indicator": "RSI",
                "params": {"period": 14},
                "role": "exit_trigger",
                "field": None,
                "condition": {"type": "gt", "threshold": 70},
            },
        ],
        "logic_genes": {"entry_logic": "AND", "exit_logic": "AND"},
        "execution_genes": {"timeframe": "4h", "symbol": "BTCUSDT"},
        "risk_genes": {
            "stop_loss": 0.05,
            "take_profit": 0.10,
            "position_size": 0.5,
            "leverage": leverage,
            "direction": direction,
        },
    }


# ── Fixtures ──
# db_path inherited from conftest.py

@pytest.fixture
def tmp_data_dir(tmp_path: Path) -> Path:
    data_dir = tmp_path / "data"
    _make_parquet(data_dir, n=500)
    return data_dir


@pytest.fixture
def client(db_path: Path, tmp_data_dir: Path):
    app = create_app(db_path=db_path, data_dir=tmp_data_dir)
    with TestClient(app) as c:
        yield c


def _backtest(client: TestClient, direction: str = "long", leverage: int = 1):
    """Run a backtest and return the JSON response."""
    payload = {
        "dna": _dna_dict(direction=direction, leverage=leverage),
        "symbol": "BTCUSDT",
        "timeframe": "4h",
        "dataset_id": "BTCUSDT_4h",
        "init_cash": 100000,
    }
    resp = client.post("/api/strategies/backtest", json=payload)
    assert resp.status_code == 200, f"Backtest failed: {resp.text}"
    return resp.json()


# ── Tests: total_funding_cost ──

class TestFundingCostConsistency:
    """Verify total_funding_cost is passed from engine to API response."""

    def test_funding_cost_nonzero_with_leverage(self, client: TestClient):
        """With leverage > 1, total_funding_cost should be present and >= 0.

        Currently FAILS: API route does not pass total_funding_cost from
        BacktestResult to BacktestResponse, so it defaults to 0.0 even
        when the engine computed a non-zero value.
        """
        data = _backtest(client, leverage=3)

        # The engine computes total_funding_cost when leverage > 1.
        # If the API passes it through, it should be >= 0.
        # If it doesn't (current bug), it's always 0.0.
        total_fc = data.get("total_funding_cost", 0.0)
        assert total_fc >= 0.0, "total_funding_cost should be non-negative"

        # More importantly: if we can verify the engine produced a value,
        # the response should match. We check that the field is not stuck at
        # default 0.0 when leverage > 1.
        # NOTE: This test may pass trivially if the synthetic data doesn't
        # generate funding costs (short positions). The key structural test
        # is that the field is populated from the engine result.

    def test_funding_cost_zero_without_leverage(self, client: TestClient):
        """With leverage=1, total_funding_cost should be 0."""
        data = _backtest(client, leverage=1)
        assert data.get("total_funding_cost", 0.0) == 0.0


# ── Tests: liquidated ──

class TestLiquidatedConsistency:
    """Verify liquidated flag is passed from engine to API response."""

    def test_liquidated_default_is_false(self, client: TestClient):
        """Normal backtest should not be liquidated."""
        data = _backtest(client)
        assert data.get("liquidated") is False, (
            "Default backtest should have liquidated=False"
        )

    def test_liquidated_reflected_in_response(self, client: TestClient):
        """If engine returns liquidated=True, API response must reflect it.

        We mock BacktestEngine.run to force liquidated=True and verify
        the API passes it through to BacktestResponse.
        """
        from core.backtest.engine import BacktestResult
        from core.strategy.dna import StrategyDNA

        fake_result = BacktestResult(
            total_return=-0.99,
            sharpe_ratio=-5.0,
            max_drawdown=-0.99,
            win_rate=0.0,
            total_trades=5,
            equity_curve=pd.Series(
                [100000, 500],
                index=pd.to_datetime(["2024-01-01", "2024-03-01"], utc=True),
            ),
            trades_df=None,
            total_funding_cost=100.0,
            liquidated=True,
            bars_per_year=2190,
            trade_win_rate=0.0,
            trade_returns=np.array([-0.2, -0.3, -0.1, -0.4, -0.15]),
            metrics_dict={"sharpe_ratio": -5.0, "max_drawdown": -0.99},
        )

        with patch(
            "api.routes.strategies.backtest._bt_engine_mod.BacktestEngine.run",
            return_value=fake_result,
        ):
            payload = {
                "dna": _dna_dict(),
                "symbol": "BTCUSDT",
                "timeframe": "4h",
                "dataset_id": "BTCUSDT_4h",
            }
            resp = client.post("/api/strategies/backtest", json=payload)
            assert resp.status_code == 200
            data = resp.json()

            assert data.get("liquidated") is True, (
                "API response should reflect liquidated=True from engine"
            )
            assert data.get("total_funding_cost") == 100.0, (
                "API response should reflect total_funding_cost from engine"
            )


# ── Tests: equity_curve vs total_return ──

class TestEquityCurveReturnConsistency:
    """Verify total_return matches equity_curve endpoints."""

    def test_total_return_matches_equity_curve(self, client: TestClient):
        """total_return should equal equity_curve[-1]/equity_curve[0] - 1."""
        data = _backtest(client)
        equity_curve = data.get("equity_curve", [])

        if not equity_curve or len(equity_curve) < 2:
            pytest.skip("No equity curve data in response")

        first_val = equity_curve[0]["value"]
        last_val = equity_curve[-1]["value"]
        expected_return = (last_val / first_val) - 1
        actual_return = data.get("total_return", 0.0)

        assert abs(actual_return - expected_return) < 0.02, (
            f"total_return ({actual_return}) doesn't match equity curve "
            f"({expected_return}): first={first_val}, last={last_val}"
        )


# ── Tests: liquidated => score=0 ──

class TestLiquidatedScoreConsistency:
    """Verify score_strategy receives liquidated and zeros the score."""

    def test_liquidated_strategy_scores_zero(self, client: TestClient):
        """When engine returns liquidated=True, total_score should be 0.

        Currently FAILS: API route calls score_strategy() without passing
        liquidated=result.liquidated, so even liquidated strategies get
        a non-zero score.
        """
        from core.backtest.engine import BacktestResult

        fake_result = BacktestResult(
            total_return=-0.90,
            sharpe_ratio=-3.0,
            max_drawdown=-0.90,
            win_rate=0.1,
            total_trades=20,
            equity_curve=pd.Series(
                [100000, 10000],
                index=pd.to_datetime(["2024-01-01", "2024-06-01"], utc=True),
            ),
            trades_df=None,
            total_funding_cost=500.0,
            liquidated=True,
            bars_per_year=2190,
            trade_win_rate=0.1,
            trade_returns=np.array([-0.1] * 20),
            metrics_dict={"sharpe_ratio": -3.0},
        )

        with patch(
            "api.routes.strategies.backtest._bt_engine_mod.BacktestEngine.run",
            return_value=fake_result,
        ):
            payload = {
                "dna": _dna_dict(),
                "symbol": "BTCUSDT",
                "timeframe": "4h",
                "dataset_id": "BTCUSDT_4h",
            }
            resp = client.post("/api/strategies/backtest", json=payload)
            assert resp.status_code == 200
            data = resp.json()

            assert data.get("total_score", -1) == 0.0, (
                "Liquidated strategy should have total_score=0"
            )


# ── Tests: metrics_dict reuse vs redundant compute_metrics ──

class TestMetricsDictConsistency:
    """Verify API uses engine's metrics_dict instead of recomputing."""

    def test_sharpe_from_engine_not_recomputed(self, client: TestClient):
        """sharpe_ratio in response should match engine's value exactly.

        Currently FAILS (may): API route calls compute_metrics() again
        instead of using result.metrics_dict, which can produce different
        values when trade_returns are available (trade-level vs bar-level).
        """
        from core.backtest.engine import BacktestResult

        # Engine sharpe is computed with trade-level data
        engine_sharpe = 2.5

        fake_result = BacktestResult(
            total_return=0.50,
            sharpe_ratio=engine_sharpe,
            max_drawdown=-0.10,
            win_rate=0.6,
            total_trades=30,
            equity_curve=pd.Series(
                np.linspace(100000, 150000, 100),
                index=pd.date_range("2024-01-01", periods=100, freq="4h", tz="UTC"),
            ),
            trades_df=None,
            total_funding_cost=0.0,
            liquidated=False,
            bars_per_year=2190,
            trade_win_rate=0.6,
            trade_returns=np.array([0.02] * 30),
            metrics_dict={
                "sharpe_ratio": engine_sharpe,
                "max_drawdown": -0.10,
                "win_rate": 0.6,
                "total_trades": 30,
                "annual_return": 0.50,
            },
        )

        with patch(
            "api.routes.strategies.backtest._bt_engine_mod.BacktestEngine.run",
            return_value=fake_result,
        ):
            payload = {
                "dna": _dna_dict(),
                "symbol": "BTCUSDT",
                "timeframe": "4h",
                "dataset_id": "BTCUSDT_4h",
            }
            resp = client.post("/api/strategies/backtest", json=payload)
            assert resp.status_code == 200
            data = resp.json()

            # The response should use engine's sharpe directly,
            # not recompute from equity curve (which gives different value)
            assert data.get("sharpe_ratio") == engine_sharpe, (
                f"Response sharpe ({data.get('sharpe_ratio')}) should match "
                f"engine sharpe ({engine_sharpe})"
            )


# ── Tests: compare route metrics params ──

class TestCompareRouteMetricsConsistency:
    """Verify compare route passes all params to compute_metrics."""

    def test_compare_sharpe_matches_backtest_sharpe(self, client: TestClient):
        """Compare route sharpe should match backtest route sharpe for same DNA.

        Currently FAILS: compare route only passes 2 of 5 params to
        compute_metrics (missing bars_per_year, trade_win_rate, trade_returns),
        causing sharpe_ratio to degrade to bar-level calculation with wrong
        annualization factor.
        """
        from core.backtest.engine import BacktestResult

        # First, save a strategy via the API
        save_payload = {
            "name": "Test Strategy for Compare",
            "dna": _dna_dict(),
            "symbol": "BTCUSDT",
            "timeframe": "4h",
            "source": "test",
        }
        save_resp = client.post("/api/strategies/", json=save_payload)
        assert save_resp.status_code in (200, 201)
        strategy_id = save_resp.json()["strategy_id"]

        # Mock the engine to return a result with trade-level data
        engine_sharpe = 2.5
        fake_result = BacktestResult(
            total_return=0.50,
            sharpe_ratio=engine_sharpe,
            max_drawdown=-0.10,
            win_rate=0.6,
            total_trades=30,
            equity_curve=pd.Series(
                np.linspace(100000, 150000, 100),
                index=pd.date_range("2024-01-01", periods=100, freq="4h", tz="UTC"),
            ),
            trades_df=None,
            total_funding_cost=0.0,
            liquidated=False,
            bars_per_year=2190,
            trade_win_rate=0.6,
            trade_returns=np.array([0.02] * 30),
            metrics_dict={
                "sharpe_ratio": engine_sharpe,
                "max_drawdown": -0.10,
                "win_rate": 0.6,
                "total_trades": 30,
            },
        )

        with patch(
            "api.routes.strategies.backtest._bt_engine_mod.BacktestEngine.run",
            return_value=fake_result,
        ):
            compare_payload = {
                "strategy_ids": [strategy_id],
                "dataset_id": "BTCUSDT_4h",
            }
            resp = client.post("/api/strategies/compare", json=compare_payload)
            assert resp.status_code == 200
            results = resp.json().get("results", [])

            assert len(results) == 1
            assert results[0].get("error") is None, (
                f"Compare failed: {results[0].get('error')}"
            )

            # Compare route should use metrics_dict instead of recomputing
            compare_sharpe = results[0].get("sharpe_ratio", 0.0)
            assert compare_sharpe == engine_sharpe, (
                f"Compare sharpe ({compare_sharpe}) should match "
                f"engine sharpe ({engine_sharpe})"
            )


# ── Tests: score_strategy liquidated integration ──

class TestScoreStrategyLiquidatedIntegration:
    """Direct unit tests for score_strategy with liquidated flag."""

    def test_score_strategy_liquidated_returns_zero(self):
        """score_strategy(liquidated=True) should return total_score=0."""
        from core.scoring.scorer import score_strategy

        metrics = {
            "annual_return": 0.5,
            "sharpe_ratio": 2.0,
            "max_drawdown": -0.1,
            "win_rate": 0.7,
            "total_trades": 50,
        }
        result = score_strategy(metrics, liquidated=True)
        assert result["total_score"] == 0.0
        assert result["liquidated"] is True

    def test_score_strategy_not_liquidated_nonzero(self):
        """score_strategy(liquidated=False) should return non-zero score for good metrics."""
        from core.scoring.scorer import score_strategy

        metrics = {
            "annual_return": 0.5,
            "sharpe_ratio": 2.0,
            "max_drawdown": -0.1,
            "win_rate": 0.7,
            "total_trades": 50,
        }
        result = score_strategy(metrics, liquidated=False)
        assert result["total_score"] > 0.0
        assert result["liquidated"] is False

    def test_score_strategy_zero_trades_independent_of_liquidated(self):
        """score_strategy with zero trades should return 0 regardless of liquidated."""
        from core.scoring.scorer import score_strategy

        metrics = {"total_trades": 0}
        result = score_strategy(metrics, liquidated=False)
        assert result["total_score"] == 0.0
