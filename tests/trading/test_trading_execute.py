"""Integration tests for TradingRunner._execute_task main loop.

_execute_task is the trading orchestrator: load data -> indicators -> MTF ->
DecisionPipeline -> per-bar decisions -> persist. It was at ~53% coverage
(test_trading_runner covers DB helpers but not the main loop). These tests
mock only the I/O boundaries (_load_data, _fetch_and_update, _init_predictor)
and use a fake controller that stops after one wait, so the loop runs exactly
one iteration without sleeping.
"""
from pathlib import Path
from unittest.mock import patch

import pytest

pytestmark = [pytest.mark.integration]

from tests.helpers.data_factory import make_dna, make_ohlcv  # noqa: E402


@pytest.fixture
def trading_db(tmp_path: Path) -> Path:
    from core.persistence.db_ext import init_db_ext
    db_path = tmp_path / "test_execute.db"
    init_db_ext(db_path)
    return db_path


@pytest.fixture
def trading_db_with_task(trading_db: Path) -> Path:
    from core.persistence.db_ext import save_paper_trading_task
    dna = make_dna(direction="long", leverage=1)
    save_paper_trading_task(
        trading_db, task_id="task-001", dna_json=dna.to_json(),
        symbol="BTCUSDT", timeframe="4h", initial_cash=100_000, fee=0.001,
    )
    return trading_db


class _StopAfterOneWait:
    """Fake controller: stop_requested becomes True after the first wait()."""

    def __init__(self):
        self._stopped = False

    @property
    def stop_requested(self):
        return self._stopped

    def check_stop(self):
        from core.trading.runner import TaskStopRequested
        if self._stopped:
            raise TaskStopRequested()

    def wait(self, timeout):
        self._stopped = True  # stop the loop on the first wait

    def request_stop(self):
        self._stopped = True


class TestExecuteTaskMainLoop:
    """_execute_task: orchestrates data -> pipeline -> persist over one tick."""

    def test_runs_one_iteration_and_returns_account(self, trading_db_with_task, tmp_path):
        from core.trading.runner import TradingRunner
        from core.trading.account import VirtualAccount
        from core.persistence.db_ext import get_paper_trading_task

        runner = TradingRunner(db_path=trading_db_with_task, data_dir=tmp_path)
        task_row = get_paper_trading_task(trading_db_with_task, "task-001")
        task_row["created_at"] = None  # don't filter out historical bars

        df_initial = make_ohlcv(100, "4h")
        df_extended = make_ohlcv(105, "4h")
        controller = _StopAfterOneWait()

        with patch.object(runner, "_load_data", return_value=df_initial), \
             patch.object(runner, "_fetch_and_update", return_value=df_extended), \
             patch.object(runner, "_init_predictor", return_value=None), \
             patch.object(runner, "_push_position_update"):
            result = runner._execute_task(task_row, "task-001", controller)

        # _execute_task returns (account, pending_decision)
        assert isinstance(result, tuple)
        account = result[0]
        assert isinstance(account, VirtualAccount)

    def test_no_data_marks_task_stopped(self, trading_db_with_task, tmp_path):
        """When _load_data returns None, task is stopped with reason 'no_data'."""
        from core.trading.runner import TradingRunner
        from core.persistence.db_ext import get_paper_trading_task, get_paper_trading_task as _get

        runner = TradingRunner(db_path=trading_db_with_task, data_dir=tmp_path)
        task_row = get_paper_trading_task(trading_db_with_task, "task-001")
        controller = _StopAfterOneWait()

        with patch.object(runner, "_load_data", return_value=None), \
             patch.object(runner, "_push_position_update"):
            result = runner._execute_task(task_row, "task-001", controller)

        # returns None (early stop) and task marked stopped
        assert result is None
        row = _get(trading_db_with_task, "task-001")
        assert row["status"] == "stopped"
        assert "no_data" in (row.get("stop_reason") or "")

    def test_crash_recovery_marks_task_stopped(self, trading_db_with_task, tmp_path):
        """A pending task with last_bar_time set triggers crash_recovery stop."""
        from core.trading.runner import TradingRunner
        from core.persistence.db_ext import (
            get_paper_trading_task, update_paper_trading_task,
        )

        runner = TradingRunner(db_path=trading_db_with_task, data_dir=tmp_path)
        # inject last_bar_time -> signals a crashed/resuming task
        update_paper_trading_task(
            trading_db_with_task, "task-001", last_bar_time="2024-01-01T00:00:00",
        )
        task_row = get_paper_trading_task(trading_db_with_task, "task-001")
        controller = _StopAfterOneWait()

        with patch.object(runner, "_load_data", return_value=make_ohlcv(50, "4h")), \
             patch.object(runner, "_fetch_and_update", return_value=None), \
             patch.object(runner, "_init_predictor", return_value=None), \
             patch.object(runner, "_push_position_update"):
            result = runner._execute_task(task_row, "task-001", controller)

        assert result is None
        row = get_paper_trading_task(trading_db_with_task, "task-001")
        assert row["status"] == "stopped"
        assert "crash_recovery" in (row.get("stop_reason") or "")


class TestInitPredictor:
    """_init_predictor: default DNA vs invalid DNA (graceful None)."""

    def test_default_predictor_initialized(self, tmp_path):
        from core.trading.runner import TradingRunner
        runner = TradingRunner(db_path=tmp_path / "p.db", data_dir=tmp_path)
        df = make_ohlcv(100, "4h")
        # no prediction_dna_json -> default PredictionDNA -> predictor built
        predictor = runner._init_predictor(df, {})
        assert predictor is not None

    def test_invalid_dna_json_returns_none(self, tmp_path):
        from core.trading.runner import TradingRunner
        runner = TradingRunner(db_path=tmp_path / "p.db", data_dir=tmp_path)
        df = make_ohlcv(100, "4h")
        # malformed json -> exception caught -> None (runs without prediction)
        predictor = runner._init_predictor(df, {"prediction_dna_json": "{bad json"})
        assert predictor is None

    def test_empty_df_returns_predictor(self, tmp_path):
        """With n_warmup=0 (empty/short df) warmup is skipped but predictor still built."""
        from core.trading.runner import TradingRunner
        runner = TradingRunner(db_path=tmp_path / "p.db", data_dir=tmp_path)
        predictor = runner._init_predictor(make_ohlcv(2, "4h"), {})
        assert predictor is not None


class TestExecuteTaskMtfPath:
    """_execute_task: MTF data integrity check (mtf_data_missing stop)."""

    def test_mtf_data_missing_stops_task(self, tmp_path):
        from core.trading.runner import TradingRunner
        from core.persistence.db_ext import (
            init_db_ext, save_paper_trading_task, get_paper_trading_task,
        )
        from tests.helpers.data_factory import make_mtf_dna

        db = tmp_path / "mtf.db"
        init_db_ext(db)
        dna = make_mtf_dna()
        save_paper_trading_task(
            db, task_id="mtf-1", dna_json=dna.to_json(),
            symbol="BTCUSDT", timeframe="4h", initial_cash=100_000, fee=0.001,
        )
        runner = TradingRunner(db_path=db, data_dir=tmp_path)
        task_row = get_paper_trading_task(db, "mtf-1")
        task_row["created_at"] = None
        controller = _StopAfterOneWait()

        with patch.object(runner, "_load_data", return_value=make_ohlcv(100, "4h")), \
             patch("core.trading.runner.load_mtf_data", return_value=None), \
             patch.object(runner, "_init_predictor", return_value=None), \
             patch.object(runner, "_push_position_update"):
            result = runner._execute_task(task_row, "mtf-1", controller)

        # MTF data missing -> early stop
        assert result is None
        row = get_paper_trading_task(db, "mtf-1")
        assert row["status"] == "stopped"
        assert "mtf_data_missing" in (row.get("stop_reason") or "")
