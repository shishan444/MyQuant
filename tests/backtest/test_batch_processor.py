"""Integration tests for _BatchBacktestProcessor.init (strategies batch backtest).

_BatchBacktestProcessor.init mirrors _VerifyProcessor.init (group strategies by
symbol/timeframe, build step list). process_step uses ReplayRunner
(DecisionPipeline bar-by-bar) which needs heavier mocking; here we cover init()
grouping + the 404 path, reusing the same fixture pattern as test_verify_processor.
"""
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

pytestmark = [pytest.mark.integration]

from tests.helpers.data_factory import make_dna, make_ohlcv  # noqa: E402


@pytest.fixture
def batch_db(tmp_path: Path) -> Path:
    from core.persistence.repository.strategy import save_strategy
    from core.persistence.db_ext import init_db_ext
    db_path = tmp_path / "test_batch.db"
    init_db_ext(db_path)
    dna = make_dna(direction="long", leverage=1)
    save_strategy(db_path, strategy_id="strat-1", dna_json=dna.to_json(),
                  symbol="BTCUSDT", timeframe="4h", name="BatchStrat")
    return db_path


class TestBatchBacktestProcessorInit:
    """_BatchBacktestProcessor.init: strategy grouping + step list + 404."""

    def test_init_groups_strategies_and_builds_steps(self, batch_db, tmp_path):
        from api.routes.strategies import _BatchBacktestProcessor
        from api.schemas import VerifyDateRange
        from api.schemas import BatchBacktestRequest

        payload = BatchBacktestRequest(
            strategy_ids=["strat-1"],
            data_ranges=[VerifyDateRange(start="2024-01-01", end="2024-06-01")],
            init_cash=100_000, fee=0.001, slippage=0.0005, leverage=1,
        )
        with patch("api.routes.strategies.backtest._bt_engine_mod"):
            processor = _BatchBacktestProcessor(payload, batch_db, tmp_path)
            processor.init()

        # one group (BTCUSDT_4h) × one range = one step
        assert len(processor.steps) == 1
        group_key, group_strategies, dr = processor.steps[0]
        assert group_key == "BTCUSDT_4h"
        assert len(group_strategies) == 1
        assert group_strategies[0]["strategy_id"] == "strat-1"
        assert "strat-1" in processor.strategy_period_data

    def test_init_raises_404_when_no_valid_strategies(self, batch_db, tmp_path):
        from fastapi import HTTPException
        from api.routes.strategies import _BatchBacktestProcessor
        from api.schemas import VerifyDateRange, BatchBacktestRequest

        payload = BatchBacktestRequest(
            strategy_ids=["missing-strat"],
            data_ranges=[VerifyDateRange(start="2024-01-01", end="2024-06-01")],
            init_cash=100_000, fee=0.001, slippage=0.0005, leverage=1,
        )
        with patch("api.routes.strategies.backtest._bt_engine_mod"):
            processor = _BatchBacktestProcessor(payload, batch_db, tmp_path)
            with pytest.raises(HTTPException) as exc:
                processor.init()
            assert exc.value.status_code == 404


def _fake_replay_result(trades=3):
    """Stand-in for ReplayRunner.run() result with enough data for compute_metrics."""
    return SimpleNamespace(
        equity_curve=[100_000.0 + i * 500 for i in range(20)],
        total_trades=trades,
        events_log=[
            {"type": "position_closed", "side": "long", "entry_price": 100.0,
             "exit_price": 105.0, "pnl": 1000.0, "exit_reason": "signal"},
            {"type": "position_closed", "side": "long", "entry_price": 105.0,
             "exit_price": 100.0, "pnl": -500.0, "exit_reason": "signal"},
            {"type": "position_closed", "side": "long", "entry_price": 100.0,
             "exit_price": 108.0, "pnl": 800.0, "exit_reason": "signal"},
        ][:trades],
        total_return=0.05,
        bars_processed=20,
    )


class TestBatchBacktestProcessorProcessStep:
    """_BatchBacktestProcessor.process_step: ReplayRunner bar-by-bar execution."""

    def test_process_step_runs_replay_and_records_result(self, batch_db, tmp_path):
        from api.routes.strategies import _BatchBacktestProcessor
        from api.schemas import VerifyDateRange, BatchBacktestRequest

        payload = BatchBacktestRequest(
            strategy_ids=["strat-1"],
            data_ranges=[VerifyDateRange(start="2024-01-01", end="2024-06-01")],
            init_cash=100_000, fee=0.001, slippage=0.0005, leverage=1,
        )
        df = make_ohlcv(50, "4h")

        with patch("api.routes.strategies.backtest._bt_engine_mod"), \
             patch("core.trading.replay.ReplayRunner") as replay_cls:
            replay_cls.return_value.run.return_value = _fake_replay_result()
            processor = _BatchBacktestProcessor(payload, batch_db, tmp_path)
            processor._load_df = lambda *a, **kw: df
            processor._load_mtf = lambda *a, **kw: None
            processor.init()
            processor.process_step(0)
            result = processor.finalize()

        # finalize returns session-style payload with results aggregated
        assert "results" in result
        # a backtest_result row was written via save_backtest_result
        from core.persistence.repository.backtest_result import list_backtest_results
        rows = list_backtest_results(batch_db, strategy_id="strat-1")
        assert len(rows) >= 1

    def test_process_step_insufficient_data_records_error(self, batch_db, tmp_path):
        from api.routes.strategies import _BatchBacktestProcessor
        from api.schemas import VerifyDateRange, BatchBacktestRequest

        payload = BatchBacktestRequest(
            strategy_ids=["strat-1"],
            data_ranges=[VerifyDateRange(start="2024-01-01", end="2024-06-01")],
            init_cash=100_000, fee=0.001, slippage=0.0005, leverage=1,
        )
        short_df = make_ohlcv(5, "4h")  # < 10 rows

        with patch("api.routes.strategies.backtest._bt_engine_mod"), \
             patch("core.trading.replay.ReplayRunner") as replay_cls:
            processor = _BatchBacktestProcessor(payload, batch_db, tmp_path)
            processor._load_df = lambda *a, **kw: short_df
            processor.init()
            processor.process_step(0)

        # ReplayRunner.run never called (insufficient data short-circuits)
        assert replay_cls.return_value.run.call_count == 0
        # an error result was accumulated
        assert any(getattr(r, "error", None) for r in processor.all_results)
