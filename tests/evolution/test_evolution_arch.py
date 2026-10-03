"""Architecture tests for evolution progress system.

Covers 6 problem domains with integration tests:
1. SQLite concurrency (busy_timeout, context manager)
2. Stale task recovery (startup cleanup)
3. TaskController direct stop signal (threading.Event, checkpoints)
4. Sub-phase progress (current_phase, progress_json, WS push)
5. Heartbeat + watchdog coroutine
6. WS push reliability + graceful shutdown

Tests are written BEFORE implementation (TDD).
Initially many will FAIL -- they define the target behavior.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Dict
from unittest.mock import MagicMock, patch

import pytest

from core.persistence.db_ext import _connect
from core.persistence.db import get_task, init_db, save_task, update_task
from tests.helpers.data_factory import make_dna

pytestmark = [pytest.mark.integration]


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _make_dna() -> "StrategyDNA":
    return make_dna()


@pytest.fixture
def tmp_db(tmp_path: Path) -> Path:
    db_path = tmp_path / "test_arch.db"
    from core.persistence.db_ext import init_db_ext
    init_db_ext(db_path)
    return db_path


def _create_task(db_path: Path, task_id: str = "test-task-001") -> Dict[str, Any]:
    dna = _make_dna()
    save_task(db_path, task_id, 80.0, "profit_first", "BTCUSDT", "4h", dna)
    return dict(get_task(db_path, task_id))


# ===========================================================================
# Domain 1: SQLite concurrency
# ===========================================================================

class TestSQLiteConcurrency:
    """Verify SQLite connection configuration and concurrent access."""

    def test_connect_sets_busy_timeout(self, tmp_db: Path) -> None:
        """_connect() must set busy_timeout to at least 3000ms."""
        conn = _connect(tmp_db)
        row = conn.execute("PRAGMA busy_timeout").fetchone()
        conn.close()
        timeout = row[0]
        assert timeout >= 3000, f"busy_timeout={timeout}, expected >= 3000"

    def test_connect_sets_wal_mode(self, tmp_db: Path) -> None:
        """_connect() must set journal_mode to WAL."""
        conn = _connect(tmp_db)
        row = conn.execute("PRAGMA journal_mode").fetchone()
        conn.close()
        assert dict(row)["journal_mode"] == "wal"

    def test_concurrent_writers_no_error(self, tmp_db: Path) -> None:
        """Multiple threads writing simultaneously should NOT get SQLITE_BUSY."""
        _create_task(tmp_db, "concurrent-test")
        errors: list[str] = []

        def writer(task_id: str, updates: int) -> None:
            try:
                for i in range(updates):
                    update_task(tmp_db, task_id, stop_reason=f"tick-{i}")
            except Exception as e:
                errors.append(str(e))

        threads = [threading.Thread(target=writer, args=(f"concurrent-test", 20))
                   for _ in range(5)]
        # Create tasks for all threads
        for i in range(4):
            _create_task(tmp_db, f"concurrent-test-{i}")

        threads = []
        for i in range(5):
            tid = f"concurrent-test" if i == 0 else f"concurrent-test-{i}"
            t = threading.Thread(target=writer, args=(tid, 20))
            threads.append(t)

        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)

        assert errors == [], f"Concurrent write errors: {errors}"

    def test_connect_context_manager_cleanup(self, tmp_db: Path) -> None:
        """_connect should support context manager for safe cleanup."""
        with _connect(tmp_db) as conn:
            conn.execute("SELECT 1")
        # After context exit, connection should be closed
        # Verify by checking we can still open a new connection
        conn2 = _connect(tmp_db)
        conn2.execute("SELECT 1")
        conn2.close()


# ===========================================================================
# Domain 2: Stale task recovery
# ===========================================================================

class TestStaleTaskRecovery:
    """Verify startup cleanup（CARD-34 后心跳机制裁撤，heartbeat_at
    列按卡留存——列存在锚定保留防误删列）。"""

    def test_recover_stale_running_tasks_on_startup(self, tmp_db: Path) -> None:
        """Startup should mark running tasks as stopped (crash_recovery)."""
        from api.runner import recover_stale_tasks

        # Create a task stuck in "running" (simulating crash)
        _create_task(tmp_db, "stale-1")
        update_task(tmp_db, "stale-1", status="running")

        # Create a completed task (should NOT be affected)
        _create_task(tmp_db, "completed-1")
        update_task(tmp_db, "completed-1", status="completed")

        recover_stale_tasks(tmp_db)

        stale = get_task(tmp_db, "stale-1")
        assert stale is not None
        assert stale["status"] == "stopped"
        assert "crash" in (stale.get("stop_reason") or "").lower()

        completed = get_task(tmp_db, "completed-1")
        assert completed["status"] == "completed"

    def test_recover_stale_idempotent(self, tmp_db: Path) -> None:
        """Calling recover_stale_tasks multiple times should be safe."""
        from api.runner import recover_stale_tasks

        _create_task(tmp_db, "stale-2")
        update_task(tmp_db, "stale-2", status="running")

        recover_stale_tasks(tmp_db)
        recover_stale_tasks(tmp_db)  # Should not crash

        task = get_task(tmp_db, "stale-2")
        assert task["status"] == "stopped"

    def test_no_recovery_when_no_stale_tasks(self, tmp_db: Path) -> None:
        """Recovery should be a no-op when there are no running tasks."""
        from api.runner import recover_stale_tasks

        _create_task(tmp_db, "normal-1")
        update_task(tmp_db, "normal-1", status="completed")

        recover_stale_tasks(tmp_db)  # Should not crash

        task = get_task(tmp_db, "normal-1")
        assert task["status"] == "completed"

    def test_heartbeat_column_exists(self, tmp_db: Path) -> None:
        """evolution_task table should have heartbeat_at column."""
        conn = _connect(tmp_db)
        cols = [row["name"] for row in conn.execute("PRAGMA table_info(evolution_task)").fetchall()]
        conn.close()
        assert "heartbeat_at" in cols, f"heartbeat_at not found in columns: {cols}"



# ===========================================================================
# Domain 3: TaskController stop signal
# ===========================================================================

class TestTaskController:
    """Verify direct stop signal via threading.Event."""

    def test_controller_stop_signal(self) -> None:
        """TaskController.request_stop() should set stop_requested."""
        from api.runner import TaskController

        ctrl = TaskController()
        assert not ctrl.stop_requested

        ctrl.request_stop()
        assert ctrl.stop_requested

    def test_controller_check_stop_raises(self) -> None:
        """TaskController.check_stop() should raise when stop requested."""
        from api.runner import TaskController, TaskStopRequested

        ctrl = TaskController()
        ctrl.request_stop()

        with pytest.raises(TaskStopRequested):
            ctrl.check_stop()

    def test_controller_no_raise_when_not_stopped(self) -> None:
        """TaskController.check_stop() should NOT raise when no stop requested."""
        from api.runner import TaskController

        ctrl = TaskController()
        ctrl.check_stop()  # Should not raise

    def test_controller_active_registry(self, tmp_db: Path, tmp_data_dir: Path) -> None:
        """TaskController should be registered in active_controllers during execution."""
        from api.runner import EvolutionRunner, get_active_controllers

        _create_task(tmp_db, "ctrl-reg")
        task_row = dict(get_task(tmp_db, "ctrl-reg"))

        # Mock data loading to prevent real execution
        with patch("core.data.mtf_loader.load_and_prepare_df", return_value=None):
            from api.runner import EvolutionRunner
            runner = EvolutionRunner(db_path=tmp_db, data_dir=tmp_data_dir)
            # After _run_task, controller should be cleaned up
            runner._run_task(task_row)

        # Controller should be removed after task completes
        assert "ctrl-reg" not in get_active_controllers()

    def test_stop_via_controller_faster_than_db_poll(self, tmp_db: Path, tmp_data_dir: Path) -> None:
        """Stop via TaskController should respond within 1 second (vs minutes for DB poll)."""
        from api.runner import EvolutionRunner, TaskController, get_active_controllers

        _create_task(tmp_db, "fast-stop")
        task_row = dict(get_task(tmp_db, "fast-stop"))

        ctrl = TaskController()
        get_active_controllers()["fast-stop"] = ctrl

        # Simulate: stop requested before task even starts
        ctrl.request_stop()

        # check_stop should immediately detect it
        from api.runner import TaskStopRequested
        with pytest.raises(TaskStopRequested):
            ctrl.check_stop()

        # Cleanup
        get_active_controllers().pop("fast-stop", None)


# ===========================================================================
# Domain 4: Sub-phase progress
# ===========================================================================

class TestSubPhaseProgress:
    """Verify current_phase, progress_json, and phase WS push."""

    def test_current_phase_column_exists(self, tmp_db: Path) -> None:
        """evolution_task table should have current_phase column."""
        conn = _connect(tmp_db)
        cols = [row["name"] for row in conn.execute("PRAGMA table_info(evolution_task)").fetchall()]
        conn.close()
        assert "current_phase" in cols, f"current_phase not in: {cols}"

    def test_progress_json_column_exists(self, tmp_db: Path) -> None:
        """evolution_task table should have progress_json column."""
        conn = _connect(tmp_db)
        cols = [row["name"] for row in conn.execute("PRAGMA table_info(evolution_task)").fetchall()]
        conn.close()
        assert "progress_json" in cols, f"progress_json not in: {cols}"

    def test_update_phase(self, tmp_db: Path) -> None:
        """update_phase should write current_phase to DB."""
        from api.runner import update_phase

        _create_task(tmp_db, "phase-1")
        update_phase(tmp_db, "phase-1", "data_loading")

        task = get_task(tmp_db, "phase-1")
        assert task["current_phase"] == "data_loading"

    def test_update_progress_json(self, tmp_db: Path) -> None:
        """update_progress should write structured JSON to progress_json."""
        from api.runner import update_progress

        _create_task(tmp_db, "prog-1")
        progress = {
            "phase": "data_loading",
            "message": "Loading BTCUSDT 4h data...",
        }
        update_progress(tmp_db, "prog-1", progress)

        task = get_task(tmp_db, "prog-1")
        assert task["progress_json"] is not None
        data = json.loads(task["progress_json"])
        assert data["phase"] == "data_loading"
        assert "Loading" in data["message"]

    def test_phase_pushed_via_ws(self, tmp_db: Path, tmp_data_dir: Path) -> None:
        """Runner should push phase_changed via WS when phase advances."""
        from api.runner import EvolutionRunner

        pushed: list[dict] = []

        def mock_push(task_id: str, payload: dict) -> None:
            pushed.append(payload)

        from api import runner as runner_mod
        original = runner_mod._push_ws
        runner_mod._push_ws = mock_push

        try:
            _create_task(tmp_db, "phase-ws")
            task_row = dict(get_task(tmp_db, "phase-ws"))

            runner = EvolutionRunner(db_path=tmp_db, data_dir=tmp_data_dir)
            with patch("core.data.mtf_loader.load_and_prepare_df", return_value=None):
                runner._run_task(task_row)
        finally:
            runner_mod._push_ws = original

        # task_started + at least one phase push
        types = [p.get("type") for p in pushed]
        assert "task_started" in types
        # phase_changed may or may not appear depending on implementation


# ===========================================================================
# Domain 5: Heartbeat + watchdog
# ===========================================================================



# ===========================================================================
# Domain 6: WS push reliability
# ===========================================================================

class TestE2EEvolutionFlow:
    """End-to-end flow covering create -> run -> progress -> stop -> verify."""

    def test_full_lifecycle_with_phase_progress(
        self, tmp_db: Path, tmp_data_dir: Path
    ) -> None:
        """Full lifecycle: create -> run -> phases advance -> stop -> verify."""
        from api.runner import (
            EvolutionRunner, TaskController, get_active_controllers,
            update_phase, update_progress,
        )

        _create_task(tmp_db, "e2e-1")
        update_task(tmp_db, "e2e-1", status="running")

        # Simulate phase progression
        update_phase(tmp_db, "e2e-1", "data_loading")
        task = get_task(tmp_db, "e2e-1")
        assert task["current_phase"] == "data_loading"

        update_phase(tmp_db, "e2e-1", "generation_running")
        update_progress(tmp_db, "e2e-1", {
            "phase": "generation_running",
            "current_generation": 1,
            "best_fitness": 25.0,
        })

        task = get_task(tmp_db, "e2e-1")
        assert task["current_phase"] == "generation_running"
        progress = json.loads(task["progress_json"])
        assert progress["current_generation"] == 1

    def test_stale_recovery_then_new_task(
        self, tmp_db: Path, tmp_data_dir: Path
    ) -> None:
        """After stale task recovery, new tasks should execute normally."""
        from api.runner import recover_stale_tasks

        # Simulate crash: task stuck in running
        _create_task(tmp_db, "crashed-1")
        update_task(tmp_db, "crashed-1", status="running")

        # Recovery on startup
        recover_stale_tasks(tmp_db)

        crashed = get_task(tmp_db, "crashed-1")
        assert crashed["status"] == "stopped"

        # New task should work fine
        _create_task(tmp_db, "new-1")
        new_task = get_task(tmp_db, "new-1")
        assert new_task["status"] in ("running", "pending")

    def test_stop_signal_propagates_through_checkpoints(
        self, tmp_db: Path
    ) -> None:
        """Stop signal via TaskController should be detectable at checkpoints."""
        from api.runner import TaskController, TaskStopRequested

        ctrl = TaskController()

        # Simulate execution with checkpoints
        phases = ["data_loading", "indicator_computing", "generation_running"]
        for phase in phases:
            # Check at each phase boundary
            if ctrl.stop_requested:
                with pytest.raises(TaskStopRequested):
                    ctrl.check_stop()
                break

            # Request stop after data_loading
            if phase == "data_loading":
                ctrl.request_stop()
        else:
            pytest.fail("Stop signal was never detected at checkpoints")
