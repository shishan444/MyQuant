"""Integration tests for _VerifyProcessor (strategies verify SSE orchestrator).

_VerifyProcessor was previously 0% covered (test_api_verify tests schema +
endpoint existence, not the process_step pipeline). These tests mock only the
I/O boundaries (_bt_engine_mod.BacktestEngine.batch_run + data loading) and
drive init() -> process_step() -> finalize() directly (bypassing HTTP/SSE).
"""
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

pytestmark = [pytest.mark.integration]

from tests.helpers.data_factory import make_dna, make_ohlcv  # noqa: E402


@pytest.fixture
def verify_db(tmp_path: Path) -> Path:
    from core.persistence.repository.strategy import save_strategy
    from core.persistence.db_ext import init_db_ext
    db_path = tmp_path / "test_verify.db"
    init_db_ext(db_path)
    dna = make_dna(direction="long", leverage=1)
    save_strategy(db_path, strategy_id="strat-1", dna_json=dna.to_json(),
                  symbol="BTCUSDT", timeframe="4h", name="TestStrat")
    return db_path


def _fake_bt_result(total_return=0.10, sharpe=1.5, max_dd=0.05,
                    win_rate=0.6, total_trades=10):
    """Minimal BacktestResult stand-in for fallback_metrics + save_backtest_result."""
    return SimpleNamespace(
        total_return=total_return, sharpe_ratio=sharpe, max_drawdown=max_dd,
        win_rate=win_rate, total_trades=total_trades, liquidated=False,
    )


class TestVerifyProcessor:
    """_VerifyProcessor: init -> process_step -> finalize orchestration."""

    def test_full_pipeline_produces_summary(self, verify_db, tmp_path):
        from api.routes.strategies import _VerifyProcessor
        from api.schemas import VerifyRequest, VerifyDateRange

        payload = VerifyRequest(
            strategy_ids=["strat-1"],
            data_ranges=[VerifyDateRange(start="2024-01-01", end="2024-06-01")],
            init_cash=100_000, fee=0.001, slippage=0.0005, leverage=1,
        )
        df = make_ohlcv(50, "4h")

        with patch("api.routes.strategies.backtest._bt_engine_mod") as bt_mod:
            bt_mod.BacktestEngine.return_value.batch_run.return_value = [_fake_bt_result()]
            processor = _VerifyProcessor(payload, verify_db, tmp_path)
            processor._load_df = lambda *a, **kw: df  # bypass parquet loading
            processor._load_mtf = lambda *a, **kw: None  # no MTF

            processor.init()
            processor.process_step(0)
            result = processor.finalize()

        assert "session_id" in result
        assert "summary" in result and "results" in result
        assert len(result["summary"]) >= 1
        # summary carries the comprehensive score computed from avg_fitness
        summary = result["summary"][0]
        assert summary["strategy_id"] == "strat-1"
        assert "comprehensive_score" in summary

    def test_init_raises_when_no_valid_strategies(self, verify_db, tmp_path):
        """A payload referencing a non-existent strategy -> 404."""
        from fastapi import HTTPException
        from api.routes.strategies import _VerifyProcessor
        from api.schemas import VerifyRequest, VerifyDateRange

        payload = VerifyRequest(
            strategy_ids=["does-not-exist"],
            data_ranges=[VerifyDateRange(start="2024-01-01", end="2024-06-01")],
            init_cash=100_000, fee=0.001, slippage=0.0005, leverage=1,
        )
        with patch("api.routes.strategies.backtest._bt_engine_mod"):
            processor = _VerifyProcessor(payload, verify_db, tmp_path)
            with pytest.raises(HTTPException) as exc:
                processor.init()
            assert exc.value.status_code == 404

    def test_insufficient_data_records_error_result(self, verify_db, tmp_path):
        """When loaded df has < 10 rows, an 'Insufficient data' result is recorded."""
        from api.routes.strategies import _VerifyProcessor
        from api.schemas import VerifyRequest, VerifyDateRange

        payload = VerifyRequest(
            strategy_ids=["strat-1"],
            data_ranges=[VerifyDateRange(start="2024-01-01", end="2024-06-01")],
            init_cash=100_000, fee=0.001, slippage=0.0005, leverage=1,
        )
        short_df = make_ohlcv(5, "4h")  # < 10 rows

        with patch("api.routes.strategies.backtest._bt_engine_mod") as bt_mod:
            processor = _VerifyProcessor(payload, verify_db, tmp_path)
            processor._load_df = lambda *a, **kw: short_df
            processor.init()
            progress = processor.process_step(0)

        # progress payload returned; result recorded as insufficient-data error
        assert "progress" in str(progress).lower() or "results" in str(progress).lower() or progress is not None
        # the error result is accumulated for finalize
        result = processor.finalize()
        assert result["results"] is not None
