"""Integration tests for evolution flow: REST + WebSocket + Runner pipeline.

Verifies the end-to-end contract between:
- REST endpoints (task creation, progress queries)
- WebSocket push (task_started, generation_complete, task_snapshot)
- Runner execution (status transitions, DB writes)
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Dict
from unittest.mock import MagicMock, patch

import pytest

from fastapi.testclient import TestClient

from core.persistence.db import get_task, init_db, save_task, update_task
from tests.helpers.data_factory import make_dna

pytestmark = [pytest.mark.integration]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_dna() -> "StrategyDNA":
    return make_dna()


def _sample_evolution_create() -> Dict[str, Any]:
    return {
        "initial_dna": {
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
            "risk_genes": {"stop_loss": 0.05, "take_profit": 0.10, "position_size": 0.3},
        },
        "symbol": "BTCUSDT",
        "timeframe": "4h",
        "target_score": 80.0,
        "score_template": "profit_first",
        "population_size": 15,
        "max_generations": 200,
    }


# ---------------------------------------------------------------------------
# Fixtures (tmp_data_dir, db_path from conftest.py)
# ---------------------------------------------------------------------------

@pytest.fixture
def client(db_path: Path, tmp_data_dir: Path):
    from MyQuant.api.app import create_app

    app = create_app(db_path=db_path, data_dir=tmp_data_dir)
    with TestClient(app) as c:
        yield c


@pytest.fixture
def tmp_db(tmp_path: Path) -> Path:
    db_path = tmp_path / "test_runner_flow.db"
    init_db(db_path)
    return db_path


# ---------------------------------------------------------------------------
# Test 1: REST reflects runner progress
# ---------------------------------------------------------------------------

class TestRESTProgress:
    def test_rest_reflects_runner_progress(self, client: TestClient) -> None:
        """After runner updates DB, REST endpoint should reflect the progress."""
        # Create a task via REST
        create_resp = client.post("/api/evolution/tasks", json=_sample_evolution_create())
        assert create_resp.status_code == 201
        task_id = create_resp.json()["task_id"]

        # Simulate runner writing progress to DB
        db_path = client.app.state.db_path
        update_task(db_path, task_id, status="running")
        # Simulate a generation update (runner does this in on_generation)
        import sqlite3
        conn = sqlite3.connect(str(db_path))
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute(
            "UPDATE evolution_task SET current_generation = ?, best_score = ? WHERE task_id = ?",
            (5, 42.5, task_id),
        )
        conn.commit()
        conn.close()

        # REST should return the updated progress
        resp = client.get(f"/api/evolution/tasks/{task_id}")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "running"
        assert data["current_generation"] == 5
        assert data["best_score"] == 42.5


# ---------------------------------------------------------------------------
# Test 2: task_started push on runner begin
# ---------------------------------------------------------------------------

class TestTaskStartedPush:
    def test_task_started_pushed_on_runner_begin(
        self, tmp_db: Path, tmp_data_dir: Path
    ) -> None:
        """Runner should push task_started when it begins executing a task."""
        from api.runner import EvolutionRunner

        # Create a task
        dna = _make_dna()
        save_task(tmp_db, "task-start-push", 80.0, "profit_first", "BTCUSDT", "4h", dna)
        task_row = dict(get_task(tmp_db, "task-start-push"))

        # Capture WS push
        pushed_messages: list[dict] = []

        def mock_push(task_id: str, payload: dict) -> None:
            pushed_messages.append(payload)

        from api import runner as runner_mod
        original_push = runner_mod._push_ws
        runner_mod._push_ws = mock_push

        try:
            r = EvolutionRunner(db_path=tmp_db, data_dir=tmp_data_dir)
            # Run until data loading fails (no real data), but task_started should have fired
            with patch("core.data.mtf_loader.load_and_prepare_df", return_value=None):
                r._run_task(task_row)
        finally:
            runner_mod._push_ws = original_push

        # Verify task_started was pushed
        started_msgs = [m for m in pushed_messages if m.get("type") == "task_started"]
        assert len(started_msgs) >= 1, f"Expected task_started, got: {pushed_messages}"
        msg = started_msgs[0]
        assert msg["status"] == "running"
        assert msg["target_score"] == 80.0


# ---------------------------------------------------------------------------
# Test 3: WS snapshot on connect
# ---------------------------------------------------------------------------

class TestRunnerErrorRecovery:
    def test_runner_recovers_after_task_error(
        self, tmp_db: Path, tmp_data_dir: Path
    ) -> None:
        """After a task fails, runner should process the next one."""
        from api.runner import EvolutionRunner

        runner = EvolutionRunner(db_path=tmp_db, data_dir=tmp_data_dir)

        # First task: will fail
        dna = _make_dna()
        save_task(tmp_db, "task-err-1", 80.0, "profit_first", "BTCUSDT", "4h", dna)
        row1 = dict(get_task(tmp_db, "task-err-1"))

        with patch("core.data.mtf_loader.load_and_prepare_df", side_effect=RuntimeError("corrupt")):
            runner._run_task(row1)

        assert runner._active_task_id is None

        # Second task: succeeds (no data -> stopped with no_data)
        save_task(tmp_db, "task-err-2", 80.0, "profit_first", "BTCUSDT", "4h", dna)
        row2 = dict(get_task(tmp_db, "task-err-2"))

        with patch("core.data.mtf_loader.load_and_prepare_df", return_value=None):
            runner._run_task(row2)

        assert runner._active_task_id is None
        row = get_task(tmp_db, "task-err-2")
        assert row["status"] == "stopped"
        assert row["stop_reason"] == "no_data"
