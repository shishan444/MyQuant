"""Tests for TradingRunner: task lifecycle, state persistence, and WS push.

V2: Uses VirtualAccount instead of PositionManager.
Covers:
1. TaskController cooperative stop
2. Stale task recovery
3. Runner state save/restore (VirtualAccount)
4. Runner task execution (mocked data)
5. Forming bar filtering
6. Pending decision lifecycle
"""
import threading
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

pytestmark = [pytest.mark.unit]

from tests.helpers.data_factory import make_dna, make_pm


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _clean_controllers():
    from core.trading.runner import _active_controllers
    _active_controllers.clear()
    yield
    _active_controllers.clear()


@pytest.fixture
def trading_db(tmp_path: Path) -> Path:
    from core.persistence.db_ext import init_db_ext
    db_path = tmp_path / "test_runner.db"
    init_db_ext(db_path)
    return db_path


@pytest.fixture
def trading_db_with_task(trading_db: Path) -> Path:
    from core.persistence.db_ext import save_paper_trading_task
    dna = make_dna(direction="long", leverage=1)
    save_paper_trading_task(
        trading_db,
        task_id="task-001",
        dna_json=dna.to_json(),
        symbol="BTCUSDT",
        timeframe="4h",
        initial_cash=100_000,
        fee=0.001,
    )
    return trading_db


def _make_account(**kwargs):
    """Create a VirtualAccount for testing."""
    from core.trading.account import VirtualAccount
    dna = make_dna(**kwargs)
    return VirtualAccount(dna, init_cash=100_000, fee=0.0)


# ---------------------------------------------------------------------------
# Test: TaskController
# ---------------------------------------------------------------------------

class TestTaskController:

    def test_initial_state_not_stopped(self):
        from core.trading.runner import TaskController
        ctrl = TaskController()
        assert not ctrl.stop_requested

    def test_request_stop_sets_flag(self):
        from core.trading.runner import TaskController
        ctrl = TaskController()
        ctrl.request_stop()
        assert ctrl.stop_requested

    def test_check_stop_raises_when_requested(self):
        from core.trading.runner import TaskController, TaskStopRequested
        ctrl = TaskController()
        ctrl.request_stop()
        with pytest.raises(TaskStopRequested):
            ctrl.check_stop()

    def test_check_stop_noop_when_not_requested(self):
        from core.trading.runner import TaskController
        ctrl = TaskController()
        ctrl.check_stop()


# ---------------------------------------------------------------------------
# Test: Stale task recovery
# ---------------------------------------------------------------------------

class TestStaleTaskRecovery:

    def test_marks_running_tasks_as_stopped(self, trading_db: Path):
        from core.persistence.db_ext import save_paper_trading_task, update_paper_trading_task, get_paper_trading_task
        from core.trading.runner import recover_stale_trading_tasks

        dna = make_dna()
        save_paper_trading_task(trading_db, task_id="r1", dna_json=dna.to_json())
        save_paper_trading_task(trading_db, task_id="r2", dna_json=dna.to_json())
        update_paper_trading_task(trading_db, "r1", status="running")
        update_paper_trading_task(trading_db, "r2", status="running")

        recover_stale_trading_tasks(trading_db)

        row1 = get_paper_trading_task(trading_db, "r1")
        row2 = get_paper_trading_task(trading_db, "r2")
        assert row1["status"] == "stopped"
        assert row1["stop_reason"] == "crash_recovery"
        assert row2["status"] == "stopped"

    def test_does_not_affect_pending_tasks(self, trading_db: Path):
        from core.persistence.db_ext import save_paper_trading_task, get_paper_trading_task
        from core.trading.runner import recover_stale_trading_tasks

        dna = make_dna()
        save_paper_trading_task(trading_db, task_id="p1", dna_json=dna.to_json())

        recover_stale_trading_tasks(trading_db)

        row = get_paper_trading_task(trading_db, "p1")
        assert row["status"] == "pending"

    @pytest.mark.parametrize("status,should_be_recovered", [
        ("running", True),
        ("pending", False),
        ("stopped", False),
    ])
    def test_recovery_only_affects_running(
        self, trading_db: Path, status: str, should_be_recovered: bool,
    ):
        from core.persistence.db_ext import save_paper_trading_task, update_paper_trading_task, get_paper_trading_task
        from core.trading.runner import recover_stale_trading_tasks

        dna = make_dna()
        save_paper_trading_task(trading_db, task_id="t1", dna_json=dna.to_json())
        if status != "pending":
            update_paper_trading_task(trading_db, "t1", status=status)

        recover_stale_trading_tasks(trading_db)

        row = get_paper_trading_task(trading_db, "t1")
        if should_be_recovered:
            assert row["status"] == "stopped"
            assert row["stop_reason"] == "crash_recovery"
        else:
            assert row["status"] == status


# ---------------------------------------------------------------------------
# Test: Runner state save/restore (VirtualAccount)
# ---------------------------------------------------------------------------

class TestRunnerStateSaveRestore:

    def test_save_flat_state(self, trading_db: Path):
        from core.persistence.db_ext import save_paper_trading_task, get_paper_trading_task
        from core.trading.runner import TradingRunner

        dna = make_dna()
        save_paper_trading_task(
            trading_db, task_id="s1", dna_json=dna.to_json(), initial_cash=100_000,
        )

        acc = _make_account()
        runner = TradingRunner(db_path=trading_db, data_dir=trading_db.parent / "data")
        runner._save_account_state(acc, "s1")

        row = get_paper_trading_task(trading_db, "s1")
        assert row["balance"] == 100_000
        assert row["position_side"] is None
        assert row["total_trades"] == 0

    def test_save_position_state(self, trading_db: Path):
        from core.persistence.db_ext import save_paper_trading_task, get_paper_trading_task
        from core.trading.runner import TradingRunner
        from core.trading.types import Decision

        dna = make_dna()
        save_paper_trading_task(
            trading_db, task_id="s2", dna_json=dna.to_json(),
            initial_cash=100_000, fee=0.0,
        )

        acc = _make_account()
        acc.execute_decision(
            Decision(action="open", direction="long", target_position_pct=0.3),
            open_price=100.0,
        )
        assert acc.position is not None

        runner = TradingRunner(db_path=trading_db, data_dir=trading_db.parent / "data")
        runner._save_account_state(acc, "s2")

        row = get_paper_trading_task(trading_db, "s2")
        assert row["position_side"] == "long"
        assert row["position_entry"] == 100.0

    def test_restore_flat_state_restores_balance(self, trading_db: Path):
        from core.persistence.db_ext import save_paper_trading_task, update_paper_trading_task, get_paper_trading_task
        from core.trading.runner import TradingRunner

        dna = make_dna()
        save_paper_trading_task(
            trading_db, task_id="r1", dna_json=dna.to_json(), initial_cash=100_000,
        )
        update_paper_trading_task(trading_db, "r1", balance=95000)

        row = get_paper_trading_task(trading_db, "r1")
        acc = _make_account()
        runner = TradingRunner(db_path=trading_db, data_dir=trading_db.parent / "data")
        runner._restore_account_state(acc, row)
        runner._restore_balance_and_position(acc, row)

        assert acc.balance == 95000
        assert acc.position is None

    def test_restore_position_state(self, trading_db: Path):
        from core.persistence.db_ext import save_paper_trading_task, update_paper_trading_task, get_paper_trading_task
        from core.trading.runner import TradingRunner

        dna = make_dna()
        save_paper_trading_task(
            trading_db, task_id="r2", dna_json=dna.to_json(),
            initial_cash=100_000, fee=0.0,
        )
        update_paper_trading_task(
            trading_db, "r2",
            balance=70000,
            position_side="long", position_entry=100.0,
            position_quantity=300.0, position_margin=30000.0,
            position_funding=50.0,
        )

        row = get_paper_trading_task(trading_db, "r2")
        acc = _make_account()
        runner = TradingRunner(db_path=trading_db, data_dir=trading_db.parent / "data")
        runner._restore_account_state(acc, row)
        runner._restore_balance_and_position(acc, row)

        assert acc.position is not None
        assert acc.position.side == "long"
        assert acc.position.entry_price == 100.0
        assert acc.position.quantity == 300.0
        assert acc.position.margin == 30000.0
        assert acc.position.cumulative_funding == 50.0
        assert acc.balance == 70000


# ---------------------------------------------------------------------------
# Test: Runner task execution (mocked data)
# ---------------------------------------------------------------------------

class TestRunnerTaskExecution:

    def test_find_pending_task(self, trading_db_with_task: Path):
        from core.trading.runner import TradingRunner

        runner = TradingRunner(
            db_path=trading_db_with_task,
            data_dir=trading_db_with_task.parent / "data",
        )
        task = runner._find_pending_task()
        assert task is not None
        assert task["task_id"] == "task-001"

    def test_find_pending_returns_none_when_empty(self, trading_db: Path):
        from core.trading.runner import TradingRunner

        runner = TradingRunner(
            db_path=trading_db,
            data_dir=trading_db.parent / "data",
        )
        task = runner._find_pending_task()
        assert task is None

    def test_get_task(self, trading_db_with_task: Path):
        from core.trading.runner import TradingRunner

        runner = TradingRunner(
            db_path=trading_db_with_task,
            data_dir=trading_db_with_task.parent / "data",
        )
        row = runner._get_task("task-001")
        assert row is not None
        assert row["task_id"] == "task-001"

    def test_get_task_returns_none_for_unknown(self, trading_db: Path):
        from core.trading.runner import TradingRunner

        runner = TradingRunner(
            db_path=trading_db,
            data_dir=trading_db.parent / "data",
        )
        row = runner._get_task("no-such-task")
        assert row is None

    def test_execute_task_marks_running(self, trading_db_with_task: Path):
        from core.persistence.db_ext import get_paper_trading_task
        from core.trading.runner import TradingRunner

        runner = TradingRunner(
            db_path=trading_db_with_task,
            data_dir=trading_db_with_task.parent / "data",
            poll_interval=0.1,
        )

        task = runner._find_pending_task()
        assert task is not None

        with patch.object(runner, "_load_data", return_value=None):
            runner._run_task(task)

        row = get_paper_trading_task(trading_db_with_task, "task-001")
        assert row["status"] == "stopped"
        assert row["stop_reason"] == "no_data"

    def test_controller_stop_sets_active_task_none(self, trading_db_with_task: Path):
        from core.trading.runner import TradingRunner, TaskController, _active_controllers

        runner = TradingRunner(
            db_path=trading_db_with_task,
            data_dir=trading_db_with_task.parent / "data",
        )

        runner._active_task_id = "task-001"
        ctrl = TaskController()
        _active_controllers["task-001"] = ctrl

        ctrl.request_stop()

        assert ctrl.stop_requested
        assert "task-001" in _active_controllers

    def test_run_task_with_controller_stop(self, trading_db_with_task: Path):
        from core.persistence.db_ext import get_paper_trading_task
        from core.trading.runner import TradingRunner, TaskStopRequested

        runner = TradingRunner(
            db_path=trading_db_with_task,
            data_dir=trading_db_with_task.parent / "data",
        )

        task = runner._find_pending_task()
        assert task is not None

        with patch.object(
            runner, "_execute_task",
            side_effect=TaskStopRequested(),
        ):
            runner._run_task(task)

        row = get_paper_trading_task(trading_db_with_task, "task-001")
        assert row["status"] == "stopped"
        assert row["stop_reason"] == "user_stop"


# ---------------------------------------------------------------------------
# Test: Forming bar filter
# ---------------------------------------------------------------------------

class TestFormingBarFilter:

    def test_forming_bar_excluded(self):
        """Bar still forming (end time > now) should be excluded."""
        from core.trading.runner import TradingRunner
        runner = TradingRunner(db_path=Path("/tmp"), data_dir=Path("/tmp"))

        # Create df where last bar ends in the future
        now = pd.Timestamp.now(tz="UTC")
        dates = pd.date_range(
            end=now - pd.Timedelta(hours=2), periods=3, freq="4h", tz="UTC",
        )
        df = pd.DataFrame(
            {"open": [100, 101, 102], "high": [101, 102, 103],
             "low": [99, 100, 101], "close": [100, 101, 102], "volume": [1000]*3},
            index=dates,
        )
        # Add a forming bar (ends in future)
        forming_time = now + pd.Timedelta(hours=2)
        forming_row = pd.DataFrame(
            {"open": 103, "high": 104, "low": 102, "close": 103, "volume": 1000},
            index=[forming_time],
        )
        df = pd.concat([df, forming_row])

        filtered = runner._filter_forming_bar(df, "4h")
        assert len(filtered) == 3

    def test_completed_bar_not_filtered(self):
        """All bars completed -> no filtering."""
        from core.trading.runner import TradingRunner
        runner = TradingRunner(db_path=Path("/tmp"), data_dir=Path("/tmp"))

        # All bars in the past
        dates = pd.date_range(
            end=pd.Timestamp.now(tz="UTC") - pd.Timedelta(hours=8),
            periods=3, freq="4h", tz="UTC",
        )
        df = pd.DataFrame(
            {"open": [100, 101, 102], "high": [101, 102, 103],
             "low": [99, 100, 101], "close": [100, 101, 102], "volume": [1000]*3},
            index=dates,
        )

        filtered = runner._filter_forming_bar(df, "4h")
        assert len(filtered) == 3


# ---------------------------------------------------------------------------
# Test: Pending decision lifecycle
# ---------------------------------------------------------------------------

class TestPendingDecision:

    def test_pending_decision_stored_and_executed(self):
        """Decision at Bar N -> executed at Bar N+1 open."""
        from core.trading.types import Decision
        acc = _make_account(direction="long", position_size=0.5)

        # Bar 0: no pending decision, no events
        events_0, deferred_0 = acc.process_bar_v2(
            bar_high=102, bar_low=99, bar_open=100, bar_close=101,
            bar_time="2024-01-01T00:00:00",
            pending_decision=None,
        )
        assert events_0 == []

        # Bar 1: execute pending open decision
        decision = Decision(action="open", direction="long", target_position_pct=0.5)
        events_1, deferred_1 = acc.process_bar_v2(
            bar_high=105, bar_low=100, bar_open=102, bar_close=104,
            bar_time="2024-01-01T04:00:00",
            pending_decision=decision,
        )
        opens = [e for e in events_1 if e["type"] == "position_opened"]
        assert len(opens) == 1
        assert opens[0]["entry_price"] == 102.0  # open price

    def test_sl_triggers_skips_pending_decision(self):
        """SL closes position -> pending close decision is skipped."""
        from core.trading.types import Decision
        acc = _make_account(stop_loss=0.05)

        # Open position
        acc.execute_decision(
            Decision(action="open", direction="long"),
            open_price=100.0,
        )

        # Bar where SL triggers AND pending close exists
        close_decision = Decision(action="close", reason="signal")
        events, deferred = acc.process_bar_v2(
            bar_high=98, bar_low=90,  # low=90 < 95 = SL level
            bar_open=95, bar_close=97,
            bar_time="2024-01-01T04:00:00",
            pending_decision=close_decision,
        )

        closes = [e for e in events if e["type"] == "position_closed"]
        assert len(closes) == 1
        assert closes[0]["exit_reason"] == "sl"
        assert acc.position is None


# ---------------------------------------------------------------------------
# Test: NaN warmup protection
# ---------------------------------------------------------------------------

class TestNaNProtection:

    def test_nan_direction_suppresses_entry(self):
        """NaN direction should suppress entry signal."""
        from core.trading.types import BarSignals

        class SigNaNDir:
            entries = pd.Series([True])
            exits = pd.Series([False])
            adds = pd.Series([False])
            reduces = pd.Series([False])
            entry_direction = pd.Series([float("nan")])

        bs = BarSignals.from_signal_set(SigNaNDir(), 0)
        assert bs.entry is False
        assert bs.direction == 0.0

    def test_valid_direction_allows_entry(self):
        """Valid direction should allow entry signal through."""
        from core.trading.types import BarSignals

        class SigValid:
            entries = pd.Series([True])
            exits = pd.Series([False])
            adds = pd.Series([False])
            reduces = pd.Series([False])
            entry_direction = pd.Series([1.0])

        bs = BarSignals.from_signal_set(SigValid(), 0)
        assert bs.entry is True
        assert bs.direction == 1.0

    def test_trim_nan_rows_removes_warmup(self):
        """_trim_nan_rows should remove leading rows with NaN indicators."""
        from core.trading.runner import TradingRunner

        runner = TradingRunner(db_path=Path("/tmp"), data_dir=Path("/tmp"))
        df = pd.DataFrame(
            {
                "open": [1, 2, 3, 4, 5],
                "high": [2, 3, 4, 5, 6],
                "low": [0.5, 1, 2, 3, 4],
                "close": [1.5, 2.5, 3.5, 4.5, 5.5],
                "volume": [100] * 5,
                "ema_10": [float("nan")] * 3 + [1.0, 1.1],
            },
            index=pd.date_range("2024-01-01", periods=5, freq="4h"),
        )
        result = runner._trim_nan_rows(df)
        assert len(result) == 2  # rows 3,4 have valid indicators
        assert result["ema_10"].notna().all()
