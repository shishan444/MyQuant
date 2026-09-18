"""Tests for paper trading DB CRUD, API routes, and runner state.

Covers:
1. DB migration creates paper_trading_task and paper_trade tables
2. CRUD operations for paper trading tasks
3. API routes (create, list, get, stop, restart)
4. Runner state save/restore (PositionManager <-> DB)
"""
import json
import pytest

pytestmark = [pytest.mark.unit]

from pathlib import Path
from unittest.mock import patch, MagicMock

from core.trading.position import PositionManager, Position
from core.trading.account import VirtualAccount
from core.trading.types import Decision
from core.strategy.dna import StrategyDNA
from tests.helpers.data_factory import make_dna, make_pm


def _make_dna_json(direction="long", leverage=1) -> str:
    dna = make_dna(direction=direction, leverage=leverage)
    return dna.to_json()


# ---------------------------------------------------------------------------
# Test: DB Migration
# ---------------------------------------------------------------------------

class TestPaperTradingMigration:

    def test_tables_created(self, tmp_path):
        db_path = tmp_path / "test.db"
        from core.persistence.db_ext import init_db_ext
        init_db_ext(db_path)

        from core.persistence.db_ext import _connect
        with _connect(db_path) as conn:
            tables = {r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()}
        assert "paper_trading_task" in tables
        assert "paper_trade" in tables

    def test_idempotent_migration(self, tmp_path):
        db_path = tmp_path / "test.db"
        from core.persistence.db_ext import init_db_ext
        init_db_ext(db_path)
        init_db_ext(db_path)  # should not raise


# ---------------------------------------------------------------------------
# Test: DB CRUD
# ---------------------------------------------------------------------------

class TestPaperTradingCRUD:

    @pytest.fixture(autouse=True)
    def setup_db(self, tmp_path):
        self.db_path = tmp_path / "test.db"
        from core.persistence.db_ext import init_db_ext
        init_db_ext(self.db_path)

    def test_save_and_get_task(self):
        from core.persistence.db_ext import save_paper_trading_task, get_paper_trading_task
        dna_json = _make_dna_json()
        save_paper_trading_task(
            self.db_path,
            task_id="test-001",
            dna_json=dna_json,
            symbol="BTCUSDT",
            timeframe="4h",
        )
        row = get_paper_trading_task(self.db_path, "test-001")
        assert row is not None
        assert row["status"] == "pending"
        assert row["symbol"] == "BTCUSDT"
        assert row["initial_cash"] == 100_000

    def test_get_nonexistent_task(self):
        from core.persistence.db_ext import get_paper_trading_task
        row = get_paper_trading_task(self.db_path, "no-such-id")
        assert row is None

    def test_update_task(self):
        from core.persistence.db_ext import save_paper_trading_task, update_paper_trading_task, get_paper_trading_task
        save_paper_trading_task(
            self.db_path, task_id="t1", dna_json=_make_dna_json(),
        )
        update_paper_trading_task(
            self.db_path, "t1", status="running", balance=95000,
        )
        row = get_paper_trading_task(self.db_path, "t1")
        assert row["status"] == "running"
        assert row["balance"] == 95000

    def test_list_tasks(self):
        from core.persistence.db_ext import save_paper_trading_task, list_paper_trading_tasks
        for i in range(3):
            save_paper_trading_task(
                self.db_path, task_id=f"t{i}", dna_json=_make_dna_json(),
            )
        tasks = list_paper_trading_tasks(self.db_path)
        assert len(tasks) == 3

    def test_list_tasks_by_status(self):
        from core.persistence.db_ext import save_paper_trading_task, update_paper_trading_task, list_paper_trading_tasks
        save_paper_trading_task(self.db_path, task_id="t1", dna_json=_make_dna_json())
        save_paper_trading_task(self.db_path, task_id="t2", dna_json=_make_dna_json())
        update_paper_trading_task(self.db_path, "t1", status="running")
        pending = list_paper_trading_tasks(self.db_path, status="pending")
        assert len(pending) == 1
        assert pending[0]["task_id"] == "t2"

    def test_save_and_list_trades(self):
        from core.persistence.db_ext import save_paper_trading_task, save_paper_trade, list_paper_trades
        save_paper_trading_task(
            self.db_path, task_id="t1", dna_json=_make_dna_json(),
        )
        save_paper_trade(
            self.db_path,
            task_id="t1", bar_time="2024-01-01T00:00:00",
            side="long", action="open", price=100.0, quantity=10.0,
        )
        save_paper_trade(
            self.db_path,
            task_id="t1", bar_time="2024-01-01T04:00:00",
            side="long", action="close", price=110.0, quantity=10.0,
            pnl=100.0, reason="signal",
        )
        trades = list_paper_trades(self.db_path, "t1")
        assert len(trades) == 2
        assert trades[0]["action"] == "close"  # DESC order


# ---------------------------------------------------------------------------
# Test: API Routes
# ---------------------------------------------------------------------------

class TestTradingAPIRoutes:

    @pytest.fixture(autouse=True)
    def setup(self, tmp_path):
        from api.app import create_app
        self.db_path = tmp_path / "test.db"
        self.data_dir = tmp_path / "market"
        self.app = create_app(db_path=self.db_path, data_dir=self.data_dir)
        self.client = MagicMock()  # Use TestClient would be better but avoid full startup

    def test_create_and_get_task_via_api(self):
        from fastapi.testclient import TestClient
        with TestClient(self.app) as client:
            dna_json = _make_dna_json()
            resp = client.post("/api/trading/tasks", json={
                "dna_json": dna_json,
                "symbol": "BTCUSDT",
                "timeframe": "4h",
                "initial_cash": 50000,
                "leverage": 3,
            })
            assert resp.status_code == 201
            data = resp.json()
            assert data["status"] == "pending"
            assert data["symbol"] == "BTCUSDT"
            assert data["initial_cash"] == 50000
            assert data["leverage"] == 3
            task_id = data["task_id"]

            # Get task
            resp2 = client.get(f"/api/trading/tasks/{task_id}")
            assert resp2.status_code == 200
            assert resp2.json()["task_id"] == task_id

    def test_list_tasks(self):
        from fastapi.testclient import TestClient
        with TestClient(self.app) as client:
            for i in range(3):
                client.post("/api/trading/tasks", json={
                    "dna_json": _make_dna_json(),
                })
            resp = client.get("/api/trading/tasks")
            assert resp.status_code == 200
            assert len(resp.json()["tasks"]) == 3

    def test_get_nonexistent_task_returns_404(self):
        from fastapi.testclient import TestClient
        with TestClient(self.app) as client:
            resp = client.get("/api/trading/tasks/no-exist")
            assert resp.status_code == 404

    def test_runner_status(self):
        from fastapi.testclient import TestClient
        with TestClient(self.app) as client:
            resp = client.get("/api/trading/runner-status")
            assert resp.status_code == 200
            data = resp.json()
            assert "is_alive" in data


# ---------------------------------------------------------------------------
# Test: Runner state persistence
# ---------------------------------------------------------------------------

class TestRunnerStatePersistence:

    def test_save_and_restore_flat_state(self, tmp_path):
        """PM with no position saves and restores correctly."""
        from core.persistence.db_ext import init_db_ext, save_paper_trading_task, get_paper_trading_task
        from core.trading.runner import TradingRunner

        db_path = tmp_path / "test.db"
        init_db_ext(db_path)

        dna_json = _make_dna_json()
        save_paper_trading_task(
            db_path, task_id="t1", dna_json=dna_json, initial_cash=100000,
        )

        dna = StrategyDNA.from_json(dna_json)
        acc = VirtualAccount(dna, init_cash=100000)

        runner = TradingRunner(db_path=db_path, data_dir=tmp_path / "data")
        runner._save_account_state(acc, "t1")

        row = get_paper_trading_task(db_path, "t1")
        assert row["balance"] == 100000
        assert row["position_side"] is None
        assert row["total_trades"] == 0

        # Restore
        acc2 = VirtualAccount(dna, init_cash=100000)
        runner._restore_account_state(acc2, row)
        runner._restore_balance_and_position(acc2, row)
        assert acc2.balance == 100000
        assert acc2.position is None

    def test_save_and_restore_position_state(self, tmp_path):
        """PM with open position saves and restores correctly."""
        from core.persistence.db_ext import init_db_ext, save_paper_trading_task, update_paper_trading_task, get_paper_trading_task
        from core.trading.runner import TradingRunner

        db_path = tmp_path / "test.db"
        init_db_ext(db_path)

        dna_json = _make_dna_json()
        save_paper_trading_task(
            db_path, task_id="t1", dna_json=dna_json,
            initial_cash=100000, fee=0.0,
        )

        dna = StrategyDNA.from_json(dna_json)
        acc = VirtualAccount(dna, init_cash=100000, fee=0.0)
        # Open a position
        acc.execute_decision(
            Decision(action="open", direction="long", target_position_pct=0.3),
            open_price=100.0,
        )
        assert acc.position is not None

        runner = TradingRunner(db_path=db_path, data_dir=tmp_path / "data")
        runner._save_account_state(acc, "t1")

        row = get_paper_trading_task(db_path, "t1")
        assert row["position_side"] == "long"
        assert row["position_entry"] == 100.0
        assert row["balance"] < 100000  # margin deducted

        # Restore
        acc2 = VirtualAccount(dna, init_cash=100000, fee=0.0)
        runner._restore_account_state(acc2, row)
        runner._restore_balance_and_position(acc2, row)
        assert acc2.position is not None
        assert acc2.position.side == "long"
        assert acc2.position.entry_price == 100.0
        assert acc2.balance < 100000


# ---------------------------------------------------------------------------
# Test: Full route state transitions
# ---------------------------------------------------------------------------

class TestTradingAPIStateTransitions:
    """Test all trading API route state transitions."""

    @pytest.fixture(autouse=True)
    def setup(self, tmp_path):
        from api.app import create_app
        from core.persistence.db_ext import init_db_ext
        self.db_path = tmp_path / "test.db"
        self.data_dir = tmp_path / "market"
        self.data_dir.mkdir()
        init_db_ext(self.db_path)
        self.app = create_app(db_path=self.db_path, data_dir=self.data_dir)

    def _create_task(self, client):
        """Helper: create a trading task and return task_id."""
        resp = client.post("/api/trading/tasks", json={
            "dna_json": _make_dna_json(),
            "symbol": "BTCUSDT",
            "timeframe": "4h",
        })
        assert resp.status_code == 201
        return resp.json()["task_id"]

    def test_stop_pending_task(self):
        from fastapi.testclient import TestClient
        with TestClient(self.app) as client:
            task_id = self._create_task(client)
            resp = client.post(f"/api/trading/tasks/{task_id}/stop")
            assert resp.status_code == 200
            data = resp.json()
            assert data["status"] == "stopped"
            assert data["stop_reason"] == "user_stop"

    @pytest.mark.parametrize("initial_status,expected_code", [
        ("stopped", 400),
        ("completed", 400),
    ])
    def test_stop_invalid_status_returns_400(self, initial_status, expected_code):
        from fastapi.testclient import TestClient
        from core.persistence.db_ext import update_paper_trading_task
        with TestClient(self.app) as client:
            task_id = self._create_task(client)
            update_paper_trading_task(self.db_path, task_id, status=initial_status)
            resp = client.post(f"/api/trading/tasks/{task_id}/stop")
            assert resp.status_code == expected_code

    def test_trades_empty_for_new_task(self):
        from fastapi.testclient import TestClient
        with TestClient(self.app) as client:
            task_id = self._create_task(client)
            resp = client.get(f"/api/trading/tasks/{task_id}/trades")
            assert resp.status_code == 200
            data = resp.json()
            assert data["trades"] == []
            assert data["total"] == 0

    def test_trades_with_records(self):
        from fastapi.testclient import TestClient
        from core.persistence.db_ext import save_paper_trade
        with TestClient(self.app) as client:
            task_id = self._create_task(client)

            save_paper_trade(
                self.db_path,
                task_id=task_id, bar_time="2024-01-01T00:00:00",
                side="long", action="open", price=100.0, quantity=10.0,
            )
            save_paper_trade(
                self.db_path,
                task_id=task_id, bar_time="2024-01-01T04:00:00",
                side="long", action="close", price=110.0, quantity=10.0,
                pnl=100.0, reason="signal",
            )

            resp = client.get(f"/api/trading/tasks/{task_id}/trades")
            assert resp.status_code == 200
            data = resp.json()
            assert data["total"] == 2
            assert data["trades"][0]["action"] == "close"  # DESC order

    def test_trades_limit_parameter(self):
        from fastapi.testclient import TestClient
        from core.persistence.db_ext import save_paper_trade
        with TestClient(self.app) as client:
            task_id = self._create_task(client)

            for i in range(5):
                save_paper_trade(
                    self.db_path,
                    task_id=task_id,
                    bar_time=f"2024-01-01T{i*4:02d}:00:00",
                    side="long", action="open", price=100.0, quantity=1.0,
                )

            resp = client.get(f"/api/trading/tasks/{task_id}/trades?limit=3")
            assert resp.status_code == 200
            assert len(resp.json()["trades"]) == 3

    def test_trades_for_nonexistent_task_returns_404(self):
        from fastapi.testclient import TestClient
        with TestClient(self.app) as client:
            resp = client.get("/api/trading/tasks/no-exist/trades")
            assert resp.status_code == 404
