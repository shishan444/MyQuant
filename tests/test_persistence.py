"""Tests for SQLite persistence and checkpoint resume."""

import pytest

pytestmark = [pytest.mark.unit]
import json
import sqlite3
from pathlib import Path

from MyQuant.core.strategy.dna import (
    SignalRole, SignalGene, LogicGenes, RiskGenes, ExecutionGenes, StrategyDNA,
)
from MyQuant.core.persistence.db import (
    init_db, save_task, update_task, get_running_task, get_task,
    save_snapshot, get_latest_snapshot,
    save_history, get_history, list_all_tasks,
)
from MyQuant.core.persistence.checkpoint import save_generation, resume_evolution

@pytest.fixture
def db_path(tmp_path):
    return tmp_path / "evolution.db"

@pytest.fixture
def sample_dna():
    return StrategyDNA(
        signal_genes=[
            SignalGene("RSI", {"period": 14}, SignalRole.ENTRY_TRIGGER, None,
                       {"type": "lt", "threshold": 30}),
            SignalGene("RSI", {"period": 14}, SignalRole.EXIT_TRIGGER, None,
                       {"type": "gt", "threshold": 70}),
        ],
        logic_genes=LogicGenes(entry_logic="AND", exit_logic="OR"),
        risk_genes=RiskGenes(stop_loss=0.05, position_size=0.3),
    )

@pytest.fixture
def initialized_db(db_path):
    init_db(db_path)
    return db_path

class TestInitDb:
    def test_creates_tables(self, db_path):
        init_db(db_path)
        conn = sqlite3.connect(str(db_path))
        tables = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
        table_names = {t[0] for t in tables}
        assert "evolution_task" in table_names
        assert "generation_snapshot" in table_names
        assert "evolution_history" in table_names
        conn.close()

    def test_idempotent(self, db_path):
        init_db(db_path)
        init_db(db_path)  # Should not raise

class TestTask:
    def test_save_and_get_task(self, initialized_db, sample_dna):
        save_task(
            db_path=initialized_db,
            task_id="task-001",
            target_score=80.0,
            template="profit_first",
            symbol="BTCUSDT",
            timeframe="4h",
            initial_dna=sample_dna,
        )
        task = get_task(initialized_db, "task-001")
        assert task is not None
        assert task["task_id"] == "task-001"
        assert task["status"] == "pending"  # D4: 创建原子进入 pending
        assert task["target_score"] == 80.0

    def test_update_task_status(self, initialized_db, sample_dna):
        save_task(initialized_db, "task-002", 80.0, "profit_first",
                  "BTCUSDT", "4h", sample_dna)
        update_task(initialized_db, "task-002",
                    status="completed", champion_dna=sample_dna,
                    stop_reason="target_reached")
        task = get_task(initialized_db, "task-002")
        assert task["status"] == "completed"
        assert task["stop_reason"] == "target_reached"

    def test_get_running_task(self, initialized_db, sample_dna):
        save_task(initialized_db, "task-003", 80.0, "profit_first",
                  "BTCUSDT", "4h", sample_dna)
        update_task(initialized_db, "task-003", status="running")  # runner 拾取
        running = get_running_task(initialized_db)
        assert running is not None
        assert running["task_id"] == "task-003"

    def test_no_running_task_returns_none(self, initialized_db, sample_dna):
        save_task(initialized_db, "task-004", 80.0, "profit_first",
                  "BTCUSDT", "4h", sample_dna)
        update_task(initialized_db, "task-004", status="completed")
        assert get_running_task(initialized_db) is None

class TestSnapshot:
    def test_save_and_get_snapshot(self, initialized_db, sample_dna):
        save_snapshot(
            db_path=initialized_db,
            task_id="task-001",
            generation=5,
            best_score=72.5,
            avg_score=60.0,
            best_dna=sample_dna,
            population=[sample_dna],
        )
        snap = get_latest_snapshot(initialized_db, "task-001")
        assert snap is not None
        assert snap["generation"] == 5
        assert snap["best_score"] == 72.5

    def test_latest_snapshot_is_highest_gen(self, initialized_db, sample_dna):
        for gen in [1, 3, 7]:
            save_snapshot(
                db_path=initialized_db,
                task_id="task-001",
                generation=gen,
                best_score=gen * 10.0,
                avg_score=gen * 5.0,
                best_dna=sample_dna,
                population=[sample_dna],
            )
        snap = get_latest_snapshot(initialized_db, "task-001")
        assert snap["generation"] == 7

class TestHistory:
    def test_save_and_get_history(self, initialized_db):
        for gen in [1, 2, 3]:
            save_history(
                db_path=initialized_db,
                task_id="task-001",
                generation=gen,
                best_score=gen * 20.0,
                avg_score=gen * 10.0,
                top3_summary=json.dumps([{"rank": 1, "score": gen * 20}]),
            )
        hist = get_history(initialized_db, "task-001")
        assert len(hist) == 3
        assert hist[0]["generation"] == 1

class TestCheckpoint:
    def test_save_generation_and_resume(self, initialized_db, sample_dna):
        save_task(initialized_db, "task-ck", 80.0, "profit_first",
                  "BTCUSDT", "4h", sample_dna)

        # resume_evolution 仅对 running 任务恢复（D4: 创建即 pending——显式推进模拟 runner 拾取）
        update_task(initialized_db, "task-ck", status="running")
        # Save 3 generations
        population = [sample_dna]
        for gen in [1, 2, 3]:
            save_generation(
                db_path=initialized_db,
                task_id="task-ck",
                generation=gen,
                best_score=gen * 25.0,
                avg_score=gen * 12.0,
                best_dna=sample_dna,
                population=population,
            )

        # Resume should find generation 3
        state = resume_evolution(initialized_db, "task-ck")
        assert state is not None
        assert state["generation"] == 3
        assert state["population"] is not None
        assert len(state["population"]) >= 1

    def test_resume_no_task_returns_none(self, initialized_db):
        state = resume_evolution(initialized_db, "nonexistent")
        assert state is None

class TestListAllTasks:
    def test_returns_all_tasks(self, initialized_db, sample_dna):
        for i in range(3):
            save_task(initialized_db, f"task-lst-{i}", 80.0, "profit_first",
                      "BTCUSDT", "4h", sample_dna)
        tasks = list_all_tasks(initialized_db)
        assert len(tasks) == 3

    def test_status_filter(self, initialized_db, sample_dna):
        save_task(initialized_db, "task-filter-1", 80.0, "profit_first",
                  "BTCUSDT", "4h", sample_dna)
        update_task(initialized_db, "task-filter-1", status="running")  # D4: 显式推进
        save_task(initialized_db, "task-filter-2", 80.0, "profit_first",
                  "BTCUSDT", "4h", sample_dna)
        update_task(initialized_db, "task-filter-2", status="completed")

        running = list_all_tasks(initialized_db, status="running")
        assert len(running) == 1
        assert running[0]["task_id"] == "task-filter-1"

    def test_limit(self, initialized_db, sample_dna):
        for i in range(5):
            save_task(initialized_db, f"task-limit-{i}", 80.0, "profit_first",
                      "BTCUSDT", "4h", sample_dna)
        tasks = list_all_tasks(initialized_db, limit=3)
        assert len(tasks) == 3
