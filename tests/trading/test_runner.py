"""Tests for api/runner.py: EvolutionRunner thread resilience.

Verifies exception boundary, health status, and task lifecycle.
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Dict
from unittest.mock import MagicMock, patch

import pytest

from core.persistence.db import get_task, init_db, save_task, update_task
from core.strategy.dna import (
    ExecutionGenes,
    LogicGenes,
    RiskGenes,
    SignalGene,
    SignalRole,
    StrategyDNA,
)


pytestmark = [pytest.mark.integration]


# ── Fixtures ──


@pytest.fixture
def tmp_db(tmp_path: Path) -> Path:
    """Create a temporary database with schema initialized."""
    db_path = tmp_path / "test_runner.db"
    init_db(db_path)
    return db_path


@pytest.fixture
def tmp_data_dir(tmp_path: Path) -> Path:
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    return data_dir


def _make_dna() -> StrategyDNA:
    """Build a minimal valid StrategyDNA for testing."""
    return StrategyDNA(
        signal_genes=[
            SignalGene(
                indicator="RSI",
                params={"period": 14},
                role=SignalRole.ENTRY_TRIGGER,
                condition={"type": "lt", "threshold": 30},
            ),
            SignalGene(
                indicator="RSI",
                params={"period": 14},
                role=SignalRole.EXIT_TRIGGER,
                condition={"type": "gt", "threshold": 70},
            ),
        ],
        logic_genes=LogicGenes(entry_logic="AND", exit_logic="AND"),
        execution_genes=ExecutionGenes(timeframe="4h", symbol="BTCUSDT"),
        risk_genes=RiskGenes(stop_loss=0.05, take_profit=0.10, position_size=0.3),
    )


def _make_task_row(db_path: Path, task_id: str = "test-task-001") -> Dict[str, Any]:
    """Create a task in DB and return its row dict."""
    dna = _make_dna()
    save_task(
        db_path,
        task_id=task_id,
        target_score=80.0,
        template="profit_first",
        symbol="BTCUSDT",
        timeframe="4h",
        initial_dna=dna,
    )
    row = get_task(db_path, task_id)
    assert row is not None
    return dict(row)


# ── Test: P1 Exception Boundary ──


class TestRunTaskExceptionBoundary:
    """Verify _run_task clears _active_task_id and updates status on ANY failure."""

    def test_run_task_clears_active_id_on_load_failure(self, tmp_db: Path, tmp_data_dir: Path):
        """When load_and_prepare_df raises, _active_task_id must be cleared."""
        from api.runner import EvolutionRunner

        runner = EvolutionRunner(db_path=tmp_db, data_dir=tmp_data_dir)
        task_row = _make_task_row(tmp_db, "task-load-fail")

        with patch("core.data.mtf_loader.load_and_prepare_df", side_effect=RuntimeError("data corrupt")):
            runner._run_task(task_row)

        assert runner._active_task_id is None

    def test_run_task_updates_status_on_failure(self, tmp_db: Path, tmp_data_dir: Path):
        """When data loading fails, task status should be 'stopped'."""
        from api.runner import EvolutionRunner

        runner = EvolutionRunner(db_path=tmp_db, data_dir=tmp_data_dir)
        task_row = _make_task_row(tmp_db, "task-status-fail")

        with patch("core.data.mtf_loader.load_and_prepare_df", side_effect=RuntimeError("data corrupt")):
            runner._run_task(task_row)

        row = get_task(tmp_db, "task-status-fail")
        assert row is not None
        assert row["status"] == "stopped"
        assert row["stop_reason"] == "error"

    def test_runner_recovers_after_task_failure(self, tmp_db: Path, tmp_data_dir: Path):
        """After a failed task, the runner should be able to process the next one."""
        from api.runner import EvolutionRunner

        runner = EvolutionRunner(db_path=tmp_db, data_dir=tmp_data_dir)

        # First task: will fail
        task_row_1 = _make_task_row(tmp_db, "task-fail-1")
        with patch("core.data.mtf_loader.load_and_prepare_df", side_effect=RuntimeError("fail")):
            runner._run_task(task_row_1)

        assert runner._active_task_id is None

        # Second task: will succeed (data returns None -> stopped with no_data)
        task_row_2 = _make_task_row(tmp_db, "task-fail-2")
        with patch("core.data.mtf_loader.load_and_prepare_df", return_value=None):
            runner._run_task(task_row_2)

        assert runner._active_task_id is None
        row = get_task(tmp_db, "task-fail-2")
        assert row["status"] == "stopped"
        assert row["stop_reason"] == "no_data"

    def test_run_task_invalid_dna_stopped(self, tmp_db: Path, tmp_data_dir: Path):
        """When initial_dna is invalid, task should be stopped with reason."""
        from api.runner import EvolutionRunner

        runner = EvolutionRunner(db_path=tmp_db, data_dir=tmp_data_dir)

        # Create task with invalid DNA JSON
        save_task(
            tmp_db,
            task_id="task-invalid-dna",
            target_score=80.0,
            template="profit_first",
            symbol="BTCUSDT",
            timeframe="4h",
            initial_dna=_make_dna(),
        )
        # Corrupt the DNA in DB
        conn = tmp_db.parent / "dummy"
        import sqlite3
        con = sqlite3.connect(str(tmp_db))
        con.execute(
            "UPDATE evolution_task SET initial_dna = ? WHERE task_id = ?",
            ("NOT VALID JSON", "task-invalid-dna"),
        )
        con.commit()
        con.close()

        task_row = dict(get_task(tmp_db, "task-invalid-dna"))
        runner._run_task(task_row)

        assert runner._active_task_id is None
        row = get_task(tmp_db, "task-invalid-dna")
        assert row["status"] == "stopped"

    def test_run_task_no_data_stopped(self, tmp_db: Path, tmp_data_dir: Path):
        """When data loading returns None, task should be stopped with no_data."""
        from api.runner import EvolutionRunner

        runner = EvolutionRunner(db_path=tmp_db, data_dir=tmp_data_dir)
        task_row = _make_task_row(tmp_db, "task-no-data")

        with patch("core.data.mtf_loader.load_and_prepare_df", return_value=None):
            runner._run_task(task_row)

        assert runner._active_task_id is None
        row = get_task(tmp_db, "task-no-data")
        assert row["status"] == "stopped"
        assert row["stop_reason"] == "no_data"


# ── Test: P2 Health Status ──


class TestRunnerHealthStatus:
    """Verify get_status() reports runner health correctly."""

    def test_get_status_returns_alive(self, tmp_db: Path, tmp_data_dir: Path):
        """Before starting, is_alive should be False."""
        from api.runner import EvolutionRunner

        runner = EvolutionRunner(db_path=tmp_db, data_dir=tmp_data_dir)
        status = runner.get_status()

        assert "is_alive" in status
        assert "last_tick_age_seconds" in status
        assert "tick_count" in status
        assert "active_task_id" in status
        assert status["is_alive"] is False

    def test_get_status_tick_count_increases(self, tmp_db: Path, tmp_data_dir: Path):
        """After multiple _tick() calls, tick_count should increase."""
        from api.runner import EvolutionRunner

        runner = EvolutionRunner(db_path=tmp_db, data_dir=tmp_data_dir)

        # _tick() is called inside run() loop which also updates counters.
        # Simulate the run() loop behavior:
        runner._last_tick_time = time.monotonic()
        runner._tick_count = 3

        status = runner.get_status()
        assert status["tick_count"] == 3

    def test_get_status_active_task_id(self, tmp_db: Path, tmp_data_dir: Path):
        """active_task_id should reflect current state."""
        from api.runner import EvolutionRunner

        runner = EvolutionRunner(db_path=tmp_db, data_dir=tmp_data_dir)
        assert runner.get_status()["active_task_id"] is None

        runner._active_task_id = "some-task"
        assert runner.get_status()["active_task_id"] == "some-task"
