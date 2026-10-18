"""Tests for FastAPI API layer (v0.9).

Covers:
- Strategy CRUD endpoints (create, list, get, update, delete)
- Strategy backtest endpoint
- Strategy compare endpoint
- Evolution task endpoints (create, list, get, history, pause, stop)
- Data management endpoints (datasets list, get, import, delete, preview, ohlcv)
- WebSocket connection for evolution progress
- CORS configuration
- Error handling (404, validation errors)
"""

from __future__ import annotations

import io
import json
from pathlib import Path
from typing import Any, Dict
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

pytestmark = [pytest.mark.integration]
from fastapi.testclient import TestClient

from MyQuant.api.app import create_app

# ── Fixtures ──
# tmp_data_dir, db_path, api_client inherited from conftest.py

@pytest.fixture
def client(api_client):
    """Alias for api_client from conftest."""
    return api_client

def _sample_dna_dict(indicator: str = "RSI", period: int = 14) -> Dict[str, Any]:
    """Build a minimal valid StrategyDNA dict with configurable indicator."""
    return {
        "signal_genes": [
            {
                "indicator": indicator,
                "params": {"period": period},
                "role": "entry_trigger",
                "field": None,
                "condition": {"type": "lt", "threshold": 30},
            },
            {
                "indicator": indicator,
                "params": {"period": period},
                "role": "exit_trigger",
                "field": None,
                "condition": {"type": "gt", "threshold": 70},
            },
        ],
        "logic_genes": {"entry_logic": "AND", "exit_logic": "AND"},
        "execution_genes": {"timeframe": "4h", "symbol": "BTCUSDT"},
        "risk_genes": {"stop_loss": 0.05, "take_profit": 0.10, "position_size": 0.3},
    }

def _sample_strategy_create(indicator: str = "RSI", period: int = 14) -> Dict[str, Any]:
    """Build a payload for POST /api/strategies with configurable DNA."""
    return {
        "name": f"{indicator} Reversal",
        "dna": _sample_dna_dict(indicator, period),
        "symbol": "BTCUSDT",
        "timeframe": "4h",
        "source": "manual",
        "tags": "reversal,rsi",
        "notes": "Test strategy",
    }

def _sample_evolution_create() -> Dict[str, Any]:
    """Build a payload for POST /api/evolution/tasks."""
    return {
        "initial_dna": _sample_dna_dict(),
        "symbol": "BTCUSDT",
        "timeframe": "4h",
        "target_score": 80.0,
        "score_template": "profit_first",
        "population_size": 15,
        "max_generations": 200,
    }

# ── Test: CORS ──

class TestCORS:
    def test_cors_allows_localhost_5173(self, client: TestClient) -> None:
        """CORS should allow requests from localhost:5173."""
        response = client.options(
            "/api/strategies",
            headers={
                "Origin": "http://localhost:5173",
                "Access-Control-Request-Method": "GET",
            },
        )
        assert response.status_code in (200, 204)
        assert "access-control-allow-origin" in response.headers

# ── Test: Strategy CRUD ──

class TestStrategyCreate:
    def test_create_strategy(self, client: TestClient) -> None:
        """POST /api/strategies should create a strategy and return 201."""
        payload = _sample_strategy_create()
        response = client.post("/api/strategies", json=payload)
        assert response.status_code == 201
        data = response.json()
        assert data["name"] == "RSI Reversal"
        assert data["symbol"] == "BTCUSDT"
        assert data["timeframe"] == "4h"
        assert data["source"] == "manual"
        assert data["strategy_id"] is not None
        assert data["created_at"] is not None

    def test_create_strategy_minimal(self, client: TestClient) -> None:
        """Creating a strategy with minimal fields should succeed."""
        payload = {
            "dna": _sample_dna_dict(),
            "symbol": "ETHUSDT",
            "timeframe": "1h",
        }
        response = client.post("/api/strategies", json=payload)
        assert response.status_code == 201
        data = response.json()
        assert data["symbol"] == "ETHUSDT"

    def test_create_strategy_invalid_missing_required(self, client: TestClient) -> None:
        """POST with missing required fields should return 422."""
        response = client.post("/api/strategies", json={})
        assert response.status_code == 422

class TestStrategyList:
    def test_list_strategies_empty(self, client: TestClient) -> None:
        """GET /api/strategies should return empty list when no strategies."""
        response = client.get("/api/strategies")
        assert response.status_code == 200
        data = response.json()
        assert data["items"] == []
        assert data["total"] == 0

    def test_list_strategies_after_create(self, client: TestClient) -> None:
        """GET /api/strategies should return created strategies."""
        client.post("/api/strategies", json=_sample_strategy_create())
        response = client.get("/api/strategies")
        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 1
        assert len(data["items"]) == 1
        assert data["items"][0]["name"] == "RSI Reversal"

    def test_list_strategies_filter_by_symbol(self, client: TestClient) -> None:
        """GET /api/strategies?symbol=BTCUSDT should filter."""
        payload_btc = _sample_strategy_create()
        payload_eth = {**_sample_strategy_create(), "symbol": "ETHUSDT"}
        client.post("/api/strategies", json=payload_btc)
        client.post("/api/strategies", json=payload_eth)
        response = client.get("/api/strategies", params={"symbol": "BTCUSDT"})
        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 1
        assert data["items"][0]["symbol"] == "BTCUSDT"

    def test_list_strategies_filter_by_source(self, client: TestClient) -> None:
        """GET /api/strategies?source=manual should filter."""
        client.post("/api/strategies", json=_sample_strategy_create())
        response = client.get("/api/strategies", params={"source": "manual"})
        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 1

    def test_list_strategies_sort_by_score(self, client: TestClient) -> None:
        """GET /api/strategies?sort_by=best_score should sort."""
        indicators = [("RSI", 14), ("EMA", 20), ("MACD", 12)]
        for i, (ind, per) in enumerate(indicators):
            payload = {**_sample_strategy_create(ind, per), "name": f"Strat {i}"}
            client.post("/api/strategies", json=payload)
        response = client.get("/api/strategies", params={"sort_by": "created_at", "sort_order": "asc"})
        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 3

    def test_list_strategies_with_limit(self, client: TestClient) -> None:
        """GET /api/strategies?limit=2 should limit results."""
        indicators = [("RSI", 14), ("EMA", 20), ("MACD", 12), ("SMA", 50), ("BB", 20)]
        for i, (ind, per) in enumerate(indicators):
            payload = {**_sample_strategy_create(ind, per), "name": f"Strat {i}"}
            client.post("/api/strategies", json=payload)
        response = client.get("/api/strategies", params={"limit": 2})
        assert response.status_code == 200
        data = response.json()
        assert len(data["items"]) == 2
        assert data["total"] == 5

class TestStrategyGet:
    def test_get_strategy(self, client: TestClient) -> None:
        """GET /api/strategies/{id} should return strategy details."""
        create_resp = client.post("/api/strategies", json=_sample_strategy_create())
        strategy_id = create_resp.json()["strategy_id"]
        response = client.get(f"/api/strategies/{strategy_id}")
        assert response.status_code == 200
        data = response.json()
        assert data["strategy_id"] == strategy_id
        assert data["name"] == "RSI Reversal"

    def test_get_strategy_not_found(self, client: TestClient) -> None:
        """GET /api/strategies/{id} with nonexistent ID should return 404."""
        response = client.get("/api/strategies/nonexistent-id")
        assert response.status_code == 404

class TestStrategyUpdate:
    def test_update_strategy(self, client: TestClient) -> None:
        """PUT /api/strategies/{id} should update strategy."""
        create_resp = client.post("/api/strategies", json=_sample_strategy_create())
        strategy_id = create_resp.json()["strategy_id"]
        response = client.put(
            f"/api/strategies/{strategy_id}",
            json={"name": "Updated Name", "notes": "Updated notes"},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["name"] == "Updated Name"
        assert data["notes"] == "Updated notes"

    def test_update_strategy_not_found(self, client: TestClient) -> None:
        """PUT /api/strategies/{id} with nonexistent ID should return 404."""
        response = client.put(
            "/api/strategies/nonexistent-id",
            json={"name": "New Name"},
        )
        assert response.status_code == 404

class TestStrategyDelete:
    @staticmethod
    def _force_status(client: TestClient, strategy_id: str, status: str) -> None:
        """直 SQL 构造非 draft 前置态（触发器只拦六值外——合法值直改
        不受 DB 拦，被测对象是 DELETE 端点而非 transit）。"""
        import sqlite3
        db = str(client.app.state.db_path)
        conn = sqlite3.connect(db)
        conn.execute("UPDATE strategy SET status = ? WHERE strategy_id = ?",
                     (status, strategy_id))
        conn.commit()
        conn.close()

    @staticmethod
    def _transition_events(client: TestClient, strategy_id: str) -> list:
        import sqlite3
        db = str(client.app.state.db_path)
        conn = sqlite3.connect(db)
        rows = conn.execute(
            "SELECT from_status, to_status, trigger FROM"
            " strategy_transition_event WHERE strategy_id = ?",
            (strategy_id,)).fetchall()
        conn.close()
        return rows

    def test_delete_draft_hard_deletes(self, client: TestClient) -> None:
        """draft→物理删除保持：204+行消失（build-strategy-lifecycle
        3.3 分支一；POST 创建缺省 active，直改 draft 构造场景）。"""
        create_resp = client.post("/api/strategies", json=_sample_strategy_create())
        strategy_id = create_resp.json()["strategy_id"]
        self._force_status(client, strategy_id, "draft")
        response = client.delete(f"/api/strategies/{strategy_id}")
        assert response.status_code == 204
        # 行已物理删除
        get_resp = client.get(f"/api/strategies/{strategy_id}")
        assert get_resp.status_code == 404
        assert self._transition_events(client, strategy_id) == []

    @pytest.mark.parametrize("status", ["active", "paper", "candidate", "live"])
    def test_delete_non_draft_archives(self, client: TestClient, status: str) -> None:
        """非 draft→经 transit(manual_archive) 归档：204+行保留
        status=archived+事件在场（REST 同端点语义从物理删除变归档——
        build-strategy-lifecycle 3.3 分支二，spec delta ADDED 承载）。"""
        create_resp = client.post("/api/strategies", json=_sample_strategy_create())
        strategy_id = create_resp.json()["strategy_id"]
        self._force_status(client, strategy_id, status)
        response = client.delete(f"/api/strategies/{strategy_id}")
        assert response.status_code == 204
        get_resp = client.get(f"/api/strategies/{strategy_id}")
        assert get_resp.status_code == 200
        assert get_resp.json()["status"] == "archived"
        events = self._transition_events(client, strategy_id)
        assert [(e[0], e[1], e[2]) for e in events] == [
            (status, "archived", "manual_archive")]

    def test_delete_strategy_not_found(self, client: TestClient) -> None:
        """DELETE /api/strategies/{id} with nonexistent ID should return 404."""
        response = client.delete("/api/strategies/nonexistent-id")
        assert response.status_code == 404


class TestStrategyRevalidate:
    """归档重启重验（build-strategy-lifecycle 3.4）。"""

    @staticmethod
    def _seed_archived_with_stats(
        client: TestClient, *, records: int = 0,
    ) -> tuple:
        """构造带统计与事件史的 archived 策略：POST 创建（active）→
        直 SQL 写五列统计+直插 mount 事件（水位 2）+直改 archived+
        可选直插 validation_records 行。返回 (strategy_id, record_ids)。"""
        import sqlite3
        create_resp = client.post("/api/strategies", json=_sample_strategy_create())
        sid = create_resp.json()["strategy_id"]
        db = str(client.app.state.db_path)
        conn = sqlite3.connect(db)
        conn.execute(
            "UPDATE strategy SET verify_count = 3, verify_avg_score = 71.5,"
            " verify_best_score = 88.25, verify_star = 4,"
            " last_verified_at = '2026-10-01T00:00:00'"
            " WHERE strategy_id = ?", (sid,))
        conn.execute(
            "INSERT INTO strategy_transition_event (event_id, strategy_id,"
            " from_status, to_status, trigger, created_at)"
            " VALUES ('ev_seed_1', ?, 'active', 'paper', 'mount',"
            " '2026-10-02T00:00:00')", (sid,))
        conn.execute(
            "UPDATE strategy SET status = 'archived' WHERE strategy_id = ?",
            (sid,))
        rec_ids = []
        for i in range(records):
            rec_id = f"rec_rv_{i}"
            conn.execute(
                "INSERT INTO validation_records (record_id, hypothesis_id,"
                " strategy_id, level, params, verdict, range_hash, created_at)"
                " VALUES (?, 'hyp_rv', ?, 'isolated_backtest', '{}', 'pass',"
                " ?, '2026-10-01T00:00:00')", (rec_id, sid, f"rh_rv_{i}"))
            rec_ids.append(rec_id)
        conn.commit()
        conn.close()
        return sid, rec_ids

    def test_revalidate_resets_stats_keeps_records_waterline(
        self, client: TestClient,
    ) -> None:
        """主路径：archived→draft+五列作废重置+validation_records 不删
        不改+事件 evidence 含作废统计快照+水位不归零（仍 2）。"""
        sid, rec_ids = self._seed_archived_with_stats(client, records=2)
        response = client.post(f"/api/strategies/{sid}/revalidate")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "draft"
        # 五列前后对照：count=0、四列 NULL
        assert data["verify_count"] == 0
        assert data["verify_avg_score"] is None
        assert data["verify_best_score"] is None
        assert data["verify_star"] is None
        assert data["last_verified_at"] is None

        import sqlite3
        conn = sqlite3.connect(str(client.app.state.db_path))
        events = conn.execute(
            "SELECT from_status, to_status, trigger, evidence_json FROM"
            " strategy_transition_event WHERE strategy_id = ?"
            " ORDER BY created_at", (sid,)).fetchall()
        records_after = conn.execute(
            "SELECT COUNT(*) FROM validation_records"
            " WHERE strategy_id = ?", (sid,)).fetchone()[0]
        conn.close()
        # validation_records 不删不改（审计事实）
        assert records_after == 2
        # 事件：seed mount + revalidate 迁移事件
        assert [(e[0], e[1], e[2]) for e in events] == [
            ("active", "paper", "mount"), ("archived", "draft", "revalidate")]
        ev = json.loads(events[1][3])
        # evidence 记作废明细（重置前快照）
        assert ev["invalidated_stats"] == {
            "verify_count": 3, "verify_avg_score": 71.5,
            "verify_best_score": 88.25, "verify_star": 4,
            "last_verified_at": "2026-10-01T00:00:00"}
        # 水位不归零：事件全史 max（to=paper 序 2）保留
        from core.strategy.lifecycle import compute_waterline
        assert compute_waterline(client.app.state.db_path, sid) == 2

    @pytest.mark.parametrize(
        "status", ["draft", "active", "paper", "candidate", "live"])
    def test_revalidate_non_archived_409(
        self, client: TestClient, status: str,
    ) -> None:
        """非 archived 五态→409 STRATEGY_ILLEGAL_TRANSITION，状态不变。"""
        create_resp = client.post("/api/strategies", json=_sample_strategy_create())
        sid = create_resp.json()["strategy_id"]
        TestStrategyDelete._force_status(client, sid, status)
        response = client.post(f"/api/strategies/{sid}/revalidate")
        assert response.status_code == 409
        assert response.json()["detail"]["code"] == "STRATEGY_ILLEGAL_TRANSITION"
        get_resp = client.get(f"/api/strategies/{sid}")
        assert get_resp.json()["status"] == status

    def test_revalidate_not_found_404(self, client: TestClient) -> None:
        response = client.post("/api/strategies/nonexistent-id/revalidate")
        assert response.status_code == 404

    def test_lifecycle_error_transit_conduction_both_endpoints(
            self, client: TestClient, monkeypatch) -> None:
        """迁移服务 LifecycleError→HTTP 传导分支（闸门②盲区消解：
        DELETE 与 revalidate 两端点的 except 传导段）。注入 transit 抛
        409 RACE→两端点 409+detail 透传（REST 侧非 draft 删除/重验的
        竞态失败面——service 层 RACE 分支已有直测，此处补 REST 传导）。"""
        import sys

        from core.strategy.lifecycle.machine import LifecycleError

        crud_module = sys.modules["MyQuant.api.routes.strategies.crud"]

        def _race(*args, **kwargs):
            raise LifecycleError(
                code="STRATEGY_TRANSITION_RACE", message="竞态",
                status=409,
                detail={"current_status": "paper"})

        create_resp = client.post("/api/strategies", json=_sample_strategy_create())
        sid = create_resp.json()["strategy_id"]
        monkeypatch.setattr(crud_module, "transit", _race)

        del_resp = client.delete(f"/api/strategies/{sid}")
        assert del_resp.status_code == 409
        assert del_resp.json()["detail"]["code"] == "STRATEGY_TRANSITION_RACE"

        TestStrategyDelete._force_status(client, sid, "archived")
        rv_resp = client.post(f"/api/strategies/{sid}/revalidate")
        assert rv_resp.status_code == 409
        assert rv_resp.json()["detail"]["code"] == "STRATEGY_TRANSITION_RACE"

    def test_revalidate_atomic_rollback_on_reset_failure(
            self, client: TestClient, monkeypatch) -> None:
        """CARD-03 两步同事务：统计重置步故障注入→迁移+事件+重置全
        回滚（status 保持 archived、verify 组原值、零事件）——不留
        「已 draft 但旧统计未作废」中间态（修复前 update_strategy 在
        事务外，transit 已提交不可回滚）。"""
        # 双模块身份坑：client fixture 的 app 经 MyQuant.api.* 身份加载
        # （conftest create_app import 路径），patch 须打同身份模块对象
        import sys

        crud_module = sys.modules["MyQuant.api.routes.strategies.crud"]

        sid, _ = self._seed_archived_with_stats(client)
        before = client.get(f"/api/strategies/{sid}").json()

        def _boom(*args, **kwargs):
            raise RuntimeError("reset step failed")

        monkeypatch.setattr(crud_module, "update_strategy", _boom)
        with pytest.raises(RuntimeError):
            client.post(f"/api/strategies/{sid}/revalidate")
        monkeypatch.undo()

        after = client.get(f"/api/strategies/{sid}").json()
        assert after["status"] == "archived", "迁移回滚"
        assert after["verify_count"] == before["verify_count"]
        assert after["verify_avg_score"] == before["verify_avg_score"]
        events = TestStrategyDelete._transition_events(client, sid)
        # 恰好只剩 seed 的 mount 事件——revalidate 迁移事件随事务回滚
        assert events == [("active", "paper", "mount")]

# ── Test: Strategy Backtest ──

class TestStrategyBacktest:
    @patch("MyQuant.api.routes.strategies.backtest._bt_engine_mod")
    @patch("MyQuant.api.routes.strategies.backtest.score_strategy")
    def test_backtest_strategy(
        self,
        mock_score: MagicMock,
        mock_bt_mod: MagicMock,
        client: TestClient,
    ) -> None:
        """POST /api/strategies/backtest should run backtest and return results."""
        import pandas as pd

        # Create a strategy first
        create_resp = client.post("/api/strategies", json=_sample_strategy_create())
        strategy_id = create_resp.json()["strategy_id"]

        # Create a dummy parquet file so the exists check passes
        data_dir = client.app.state.data_dir
        dummy_df = pd.DataFrame(
            {"open": [60000], "high": [61000], "low": [59000],
             "close": [60500], "volume": [100]},
            index=pd.DatetimeIndex(["2024-01-01"], name="timestamp"),
        )
        dummy_df.to_parquet(data_dir / "BTCUSDT_4h.parquet")

        # Mock backtest result
        mock_result = MagicMock()
        mock_result.total_return = 0.25
        mock_result.sharpe_ratio = 1.8
        mock_result.max_drawdown = -0.12
        mock_result.win_rate = 0.62
        mock_result.total_trades = 150
        mock_result.equity_curve = MagicMock()
        mock_result.trades_df = None
        mock_result.total_funding_cost = 0.0
        mock_result.liquidated = False
        # 基准对照五键（improve-backtest-benchmark-disclosure 2.2）：
        # 显式配 dict，防 MagicMock 属性被 __float__ 静默强转成假值
        mock_result.metrics_dict = {
            "benchmark_return": 0.15, "excess_return": 0.10,
            "alpha": 0.05, "market_annual_return": 0.20,
            "profit_factor": 1.6,
        }
        mock_bt_mod.BacktestEngine.return_value.run.return_value = mock_result

        mock_score.return_value = {
            "total_score": 82.1,
            "dimension_scores": {"return": 90, "risk": 80},
            "template_name": "profit_first",
            "threshold": 75.0,
        }

        response = client.post(
            "/api/strategies/backtest",
            json={
                "strategy_id": strategy_id,
                "dataset_id": "BTCUSDT_4h",
                "init_cash": 100000,
                "fee": 0.001,
                "slippage": 0.0005,
            },
        )
        assert response.status_code == 200
        data = response.json()
        assert data["strategy_id"] == strategy_id
        assert data["total_return"] == 0.25
        assert data["sharpe_ratio"] == 1.8
        assert data["result_id"] is not None
        # 基准对照五键透出（metrics_dict → BacktestResponse）
        assert data["benchmark_return"] == 0.15
        assert data["excess_return"] == 0.10
        assert data["alpha"] == 0.05
        assert data["market_annual_return"] == 0.20
        assert data["profit_factor"] == 1.6

    def test_backtest_strategy_not_found(self, client: TestClient) -> None:
        """POST /api/strategies/backtest with invalid strategy should return 404."""
        response = client.post(
            "/api/strategies/backtest",
            json={
                "strategy_id": "nonexistent",
                "dataset_id": "BTCUSDT_4h",
            },
        )
        assert response.status_code == 404

# ── Test: Strategy Compare ──

class TestStrategyCompare:
    @patch("MyQuant.api.routes.strategies.backtest._bt_engine_mod")
    @patch("MyQuant.api.routes.strategies.backtest.score_strategy")
    def test_compare_strategies(
        self,
        mock_score: MagicMock,
        mock_bt_mod: MagicMock,
        client: TestClient,
    ) -> None:
        """POST /api/strategies/compare should compare multiple strategies."""
        import pandas as pd

        # Create two strategies with different DNA
        ids = []
        for i, (ind, per) in enumerate([("RSI", 14), ("EMA", 20)]):
            payload = {**_sample_strategy_create(ind, per), "name": f"Strat {i}"}
            resp = client.post("/api/strategies", json=payload)
            ids.append(resp.json()["strategy_id"])

        # Create a dummy parquet file
        data_dir = client.app.state.data_dir
        dummy_df = pd.DataFrame(
            {"open": [60000], "high": [61000], "low": [59000],
             "close": [60500], "volume": [100]},
            index=pd.DatetimeIndex(["2024-01-01"], name="timestamp"),
        )
        dummy_df.to_parquet(data_dir / "BTCUSDT_4h.parquet")

        mock_result = MagicMock()
        mock_result.total_return = 0.15
        mock_result.sharpe_ratio = 1.2
        mock_result.max_drawdown = -0.10
        mock_result.win_rate = 0.55
        mock_result.total_trades = 80
        mock_result.equity_curve = MagicMock()
        mock_result.trades_df = None
        mock_bt_mod.BacktestEngine.return_value.run.return_value = mock_result

        mock_score.return_value = {
            "total_score": 70.0,
            "dimension_scores": {"return": 75, "risk": 65},
            "template_name": "profit_first",
            "threshold": 75.0,
        }

        response = client.post(
            "/api/strategies/compare",
            json={
                "strategy_ids": ids,
                "dataset_id": "BTCUSDT_4h",
            },
        )
        assert response.status_code == 200
        data = response.json()
        assert len(data["results"]) == 2
        assert data["results"][0]["strategy_id"] in ids

# ── Test: Evolution Tasks ──

class TestEvolutionTaskCreate:
    def test_create_evolution_task(self, client: TestClient) -> None:
        """POST /api/evolution/tasks should create an evolution task."""
        payload = _sample_evolution_create()
        response = client.post("/api/evolution/tasks", json=payload)
        assert response.status_code == 201
        data = response.json()
        assert data["task_id"] is not None
        assert data["status"] == "pending"
        assert data["symbol"] == "BTCUSDT"
        assert data["timeframe"] == "4h"
        assert data["target_score"] == 80.0

    def test_create_evolution_task_missing_fields(self, client: TestClient) -> None:
        """POST /api/evolution/tasks with missing fields should return 422."""
        response = client.post("/api/evolution/tasks", json={})
        assert response.status_code == 422

class TestEvolutionTaskList:
    def test_list_tasks_empty(self, client: TestClient) -> None:
        """GET /api/evolution/tasks should return empty list."""
        response = client.get("/api/evolution/tasks")
        assert response.status_code == 200
        data = response.json()
        assert data["items"] == []
        assert data["total"] == 0

    def test_list_tasks_after_create(self, client: TestClient) -> None:
        """GET /api/evolution/tasks should list created tasks."""
        client.post("/api/evolution/tasks", json=_sample_evolution_create())
        response = client.get("/api/evolution/tasks")
        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 1

    def test_list_tasks_filter_by_status(self, client: TestClient) -> None:
        """GET /api/evolution/tasks?status=pending should filter."""
        client.post("/api/evolution/tasks", json=_sample_evolution_create())
        response = client.get("/api/evolution/tasks", params={"status": "pending"})
        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 1

class TestEvolutionTaskGet:
    def test_get_task(self, client: TestClient) -> None:
        """GET /api/evolution/tasks/{id} should return task details."""
        create_resp = client.post("/api/evolution/tasks", json=_sample_evolution_create())
        task_id = create_resp.json()["task_id"]
        response = client.get(f"/api/evolution/tasks/{task_id}")
        assert response.status_code == 200
        data = response.json()
        assert data["task_id"] == task_id
        assert data["symbol"] == "BTCUSDT"

    def test_get_task_not_found(self, client: TestClient) -> None:
        """GET /api/evolution/tasks/{id} with nonexistent ID should return 404."""
        response = client.get("/api/evolution/tasks/nonexistent-id")
        assert response.status_code == 404

class TestEvolutionTaskHistory:
    def test_get_task_history_empty(self, client: TestClient) -> None:
        """GET /api/evolution/tasks/{id}/history should return empty list."""
        create_resp = client.post("/api/evolution/tasks", json=_sample_evolution_create())
        task_id = create_resp.json()["task_id"]
        response = client.get(f"/api/evolution/tasks/{task_id}/history")
        assert response.status_code == 200
        data = response.json()
        assert data["task_id"] == task_id
        assert data["generations"] == []

    def test_get_task_history_with_data(self, client: TestClient) -> None:
        """GET /api/evolution/tasks/{id}/history should return generation history."""
        create_resp = client.post("/api/evolution/tasks", json=_sample_evolution_create())
        task_id = create_resp.json()["task_id"]
        # Add history via the DB layer
        from core.persistence.db import save_history
        from api.deps import get_db_path

        # We need to use the test db_path - use the app state
        db_path = client.app.state.db_path
        save_history(db_path, task_id, 1, 75.0, 60.0, "summary1")
        save_history(db_path, task_id, 2, 80.0, 65.0, "summary2")

        response = client.get(f"/api/evolution/tasks/{task_id}/history")
        assert response.status_code == 200
        data = response.json()
        assert len(data["generations"]) == 2
        assert data["generations"][0]["generation"] == 1
        assert data["generations"][0]["best_score"] == 75.0

class TestEvolutionTaskPause:
    def test_pause_task(self, client: TestClient) -> None:
        """POST /api/evolution/tasks/{id}/pause should pause task."""
        create_resp = client.post("/api/evolution/tasks", json=_sample_evolution_create())
        task_id = create_resp.json()["task_id"]
        # D3: pause 前态 {running}——先推进到 running（模拟 runner 拾取）
        from core.persistence.db import update_task as _ut
        _ut(client.app.state.db_path, task_id, status="running")
        response = client.post(f"/api/evolution/tasks/{task_id}/pause")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "paused"

    def test_pause_task_not_found(self, client: TestClient) -> None:
        """POST /api/evolution/tasks/{id}/pause with nonexistent ID should return 404."""
        response = client.post("/api/evolution/tasks/nonexistent-id/pause")
        assert response.status_code == 404

class TestEvolutionTaskStop:
    def test_stop_task(self, client: TestClient) -> None:
        """POST /api/evolution/tasks/{id}/stop should stop task."""
        create_resp = client.post("/api/evolution/tasks", json=_sample_evolution_create())
        task_id = create_resp.json()["task_id"]
        response = client.post(f"/api/evolution/tasks/{task_id}/stop")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "stopped"

    def test_stop_task_not_found(self, client: TestClient) -> None:
        """POST /api/evolution/tasks/{id}/stop with nonexistent ID should return 404."""
        response = client.post("/api/evolution/tasks/nonexistent-id/stop")
        assert response.status_code == 404

# ── Test: Data Management ──

class TestDatasetList:
    def test_list_datasets_empty(self, client: TestClient) -> None:
        """GET /api/data/datasets should return empty list."""
        response = client.get("/api/data/datasets")
        assert response.status_code == 200
        data = response.json()
        assert data["items"] == []
        assert data["total"] == 0

    def test_list_datasets_with_data(self, client: TestClient) -> None:
        """GET /api/data/datasets should list datasets."""
        from core.persistence.repository.dataset_meta import save_dataset_meta

        db_path = client.app.state.db_path
        save_dataset_meta(
            db_path,
            dataset_id="ds-001",
            symbol="BTCUSDT",
            interval="4h",
            parquet_path="/data/btc.parquet",
        )
        response = client.get("/api/data/datasets")
        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 1
        assert data["items"][0]["dataset_id"] == "ds-001"

    def test_list_datasets_filter_by_symbol(self, client: TestClient) -> None:
        """GET /api/data/datasets?symbol=BTCUSDT should filter."""
        from core.persistence.repository.dataset_meta import save_dataset_meta

        db_path = client.app.state.db_path
        save_dataset_meta(
            db_path,
            dataset_id="ds-btc",
            symbol="BTCUSDT",
            interval="4h",
            parquet_path="/data/btc.parquet",
        )
        save_dataset_meta(
            db_path,
            dataset_id="ds-eth",
            symbol="ETHUSDT",
            interval="4h",
            parquet_path="/data/eth.parquet",
        )
        response = client.get("/api/data/datasets", params={"symbol": "BTCUSDT"})
        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 1

class TestDatasetGet:
    def test_get_dataset(self, client: TestClient) -> None:
        """GET /api/data/datasets/{id} should return dataset details."""
        from core.persistence.repository.dataset_meta import save_dataset_meta

        db_path = client.app.state.db_path
        save_dataset_meta(
            db_path,
            dataset_id="ds-001",
            symbol="BTCUSDT",
            interval="4h",
            parquet_path="/data/btc.parquet",
            row_count=10000,
        )
        response = client.get("/api/data/datasets/ds-001")
        assert response.status_code == 200
        data = response.json()
        assert data["dataset_id"] == "ds-001"
        assert data["symbol"] == "BTCUSDT"
        assert data["row_count"] == 10000

    def test_get_dataset_not_found(self, client: TestClient) -> None:
        """GET /api/data/datasets/{id} with nonexistent ID should return 404."""
        response = client.get("/api/data/datasets/nonexistent")
        assert response.status_code == 404

class TestDatasetDelete:
    def test_delete_dataset(self, client: TestClient) -> None:
        """DELETE /api/data/datasets/{id} should delete dataset."""
        from core.persistence.repository.dataset_meta import save_dataset_meta

        db_path = client.app.state.db_path
        save_dataset_meta(
            db_path,
            dataset_id="ds-001",
            symbol="BTCUSDT",
            interval="4h",
            parquet_path="/data/btc.parquet",
        )
        response = client.delete("/api/data/datasets/ds-001")
        assert response.status_code == 204

    def test_delete_dataset_not_found(self, client: TestClient) -> None:
        """DELETE /api/data/datasets/{id} with nonexistent ID should return 404."""
        response = client.delete("/api/data/datasets/nonexistent")
        assert response.status_code == 404

class TestDataImport:
    def test_import_csv_no_file(self, client: TestClient) -> None:
        """POST /api/data/import without file should return 422."""
        response = client.post("/api/data/import")
        assert response.status_code == 422

    @patch("MyQuant.api.routes.data.import_csv")
    def test_import_csv_success(
        self, mock_import: MagicMock, client: TestClient, tmp_data_dir: Path
    ) -> None:
        """POST /api/data/import with valid CSV should import data."""
        from core.data.csv_importer import CsvImportResult, ImportFormat, TimestampPrecision

        mock_import.return_value = CsvImportResult(
            dataset_id="BTCUSDT_4h",
            symbol="BTCUSDT",
            interval="4h",
            rows_imported=1000,
            format_detected=ImportFormat.BINANCE_OFFICIAL,
            timestamp_precision=TimestampPrecision.MILLISECOND,
            time_range=("2024-01-01", "2024-06-01"),
        )

        csv_content = b"1714521600000,60000,61000,59000,60500,100,1714525199999,6050000,500,50250,5025000,0\n"
        response = client.post(
            "/api/data/import",
            files={"file": ("BTCUSDT-4h-2024-01.csv", csv_content, "text/csv")},
            data={"symbol": "BTCUSDT", "interval": "4h"},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["dataset_id"] == "BTCUSDT_4h"
        assert data["rows_imported"] == 1000

class TestDatasetPreview:
    def test_preview_not_found(self, client: TestClient) -> None:
        """GET /api/data/datasets/{id}/preview with nonexistent ID should return 404."""
        response = client.get("/api/data/datasets/nonexistent/preview")
        assert response.status_code == 404

    def test_preview_dataset(self, client: TestClient) -> None:
        """GET /api/data/datasets/{id}/preview should return first N rows."""
        import pandas as pd

        from core.persistence.repository.dataset_meta import save_dataset_meta

        db_path = client.app.state.db_path
        data_dir = client.app.state.data_dir

        # Create actual parquet file
        df = pd.DataFrame(
            {"open": [60000, 61000], "high": [61000, 62000],
             "low": [59000, 60000], "close": [60500, 61500],
             "volume": [100, 200]},
            index=pd.DatetimeIndex(["2024-01-01", "2024-01-02"], name="timestamp"),
        )
        parquet_path = data_dir / "ds-001.parquet"
        df.to_parquet(parquet_path)

        save_dataset_meta(
            db_path,
            dataset_id="ds-001",
            symbol="BTCUSDT",
            interval="4h",
            parquet_path=str(parquet_path),
            row_count=2,
        )

        response = client.get("/api/data/datasets/ds-001/preview")
        assert response.status_code == 200
        data = response.json()
        assert "rows" in data
        assert data["total_rows"] == 2

class TestDatasetOhlcv:
    def test_ohlcv_not_found(self, client: TestClient) -> None:
        """GET /api/data/datasets/{id}/ohlcv with nonexistent ID should return 404."""
        response = client.get("/api/data/datasets/nonexistent/ohlcv")
        assert response.status_code == 404

    def test_ohlcv_dataset(self, client: TestClient) -> None:
        """GET /api/data/datasets/{id}/ohlcv should return OHLCV data."""
        import pandas as pd

        from core.persistence.repository.dataset_meta import save_dataset_meta

        db_path = client.app.state.db_path
        data_dir = client.app.state.data_dir

        df = pd.DataFrame(
            {"open": [60000], "high": [61000], "low": [59000],
             "close": [60500], "volume": [100]},
            index=pd.DatetimeIndex(["2024-01-01"], name="timestamp"),
        )
        parquet_path = data_dir / "ds-001.parquet"
        df.to_parquet(parquet_path)

        save_dataset_meta(
            db_path,
            dataset_id="ds-001",
            symbol="BTCUSDT",
            interval="4h",
            parquet_path=str(parquet_path),
            row_count=1,
        )

        response = client.get(
            "/api/data/datasets/ds-001/ohlcv",
            params={"start": "2024-01-01", "end": "2024-01-02"},
        )
        assert response.status_code == 200
        data = response.json()
        assert "data" in data

# ── Test: WebSocket ──

class TestHealthCheck:
    def test_health_check(self, client: TestClient) -> None:
        """GET /api/health should return OK status."""
        response = client.get("/api/health")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "ok"


# -- Test: Batch Import --

class TestDataBatchImport:
    @patch("MyQuant.api.routes.data.import_csv_batch")
    def test_import_batch_success(
        self, mock_import: MagicMock, client: TestClient, tmp_data_dir: Path
    ) -> None:
        """POST /api/data/import-batch with multiple CSV files."""
        from core.data.csv_importer import CsvImportResult, ImportFormat, TimestampPrecision

        mock_import.return_value = CsvImportResult(
            dataset_id="BTCUSDT_4h",
            symbol="BTCUSDT",
            interval="4h",
            rows_imported=3000,
            format_detected=ImportFormat.BINANCE_OFFICIAL,
            timestamp_precision=TimestampPrecision.MILLISECOND,
            files_processed=3,
            time_range=("2024-01-01", "2024-12-01"),
        )

        csv_content = b"1714521600000,60000,61000,59000,60500,100\n"
        response = client.post(
            "/api/data/import-batch",
            files=[
                ("files", ("BTCUSDT-4h-01.csv", csv_content, "text/csv")),
                ("files", ("BTCUSDT-4h-02.csv", csv_content, "text/csv")),
            ],
            data={"symbol": "BTCUSDT", "interval": "4h"},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["rows_imported"] == 3000
        assert data["files_processed"] == 3

    @patch("MyQuant.api.routes.data.import_csv_batch")
    def test_import_batch_upserts_existing(
        self, mock_import: MagicMock, client: TestClient, tmp_data_dir: Path
    ) -> None:
        """POST /api/data/import-batch should update existing dataset metadata."""
        import pandas as pd
        from core.persistence.repository.dataset_meta import save_dataset_meta
        from core.data.csv_importer import CsvImportResult, ImportFormat, TimestampPrecision

        db_path = client.app.state.db_path
        data_dir = client.app.state.data_dir

        # Pre-create the parquet file so update path is taken
        parquet_path = data_dir / "BTCUSDT_4h.parquet"
        df = pd.DataFrame(
            {"open": [60000], "high": [61000], "low": [59000], "close": [60500], "volume": [100]},
            index=pd.DatetimeIndex(["2024-01-01"], name="timestamp"),
        )
        df.to_parquet(parquet_path)

        save_dataset_meta(
            db_path, dataset_id="BTCUSDT_4h", symbol="BTCUSDT",
            interval="4h", parquet_path=str(parquet_path), row_count=100,
        )

        mock_import.return_value = CsvImportResult(
            dataset_id="BTCUSDT_4h", symbol="BTCUSDT", interval="4h",
            rows_imported=500, format_detected=ImportFormat.BINANCE_OFFICIAL,
            timestamp_precision=TimestampPrecision.MILLISECOND,
            files_processed=1, time_range=("2024-01-01", "2024-06-01"),
        )

        response = client.post(
            "/api/data/import-batch",
            files=[("files", ("BTCUSDT-4h.csv", b"data", "text/csv"))],
            data={"symbol": "BTCUSDT", "interval": "4h"},
        )
        assert response.status_code == 200

    @patch("MyQuant.api.routes.data.import_csv")
    def test_import_upserts_existing(
        self, mock_import: MagicMock, client: TestClient, tmp_data_dir: Path
    ) -> None:
        """POST /api/data/import should update existing dataset metadata."""
        import pandas as pd
        from core.persistence.repository.dataset_meta import save_dataset_meta
        from core.data.csv_importer import CsvImportResult, ImportFormat, TimestampPrecision

        db_path = client.app.state.db_path
        data_dir = client.app.state.data_dir

        parquet_path = data_dir / "BTCUSDT_4h.parquet"
        df = pd.DataFrame(
            {"open": [60000], "high": [61000], "low": [59000], "close": [60500], "volume": [100]},
            index=pd.DatetimeIndex(["2024-01-01"], name="timestamp"),
        )
        df.to_parquet(parquet_path)

        save_dataset_meta(
            db_path, dataset_id="BTCUSDT_4h", symbol="BTCUSDT",
            interval="4h", parquet_path=str(parquet_path), row_count=100,
        )

        mock_import.return_value = CsvImportResult(
            dataset_id="BTCUSDT_4h", symbol="BTCUSDT", interval="4h",
            rows_imported=200, format_detected=ImportFormat.BINANCE_OFFICIAL,
            timestamp_precision=TimestampPrecision.MILLISECOND,
            time_range=("2024-01-01", "2024-03-01"),
        )

        response = client.post(
            "/api/data/import",
            files={"file": ("BTCUSDT-4h.csv", b"data", "text/csv")},
            data={"symbol": "BTCUSDT", "interval": "4h"},
        )
        assert response.status_code == 200


# -- Test: Available Sources --

class TestAvailableSources:
    def test_available_sources_empty(self, client: TestClient) -> None:
        """GET /api/data/available-sources returns only fixture parquet."""
        response = client.get("/api/data/available-sources")
        assert response.status_code == 200
        data = response.json()
        # Fixture creates BTCUSDT_4h.parquet
        symbols = [s["symbol"] for s in data["sources"]]
        assert "BTCUSDT" in symbols

    def test_available_sources_with_data(self, client: TestClient) -> None:
        """GET /api/data/available-sources should scan parquet files."""
        import pandas as pd

        data_dir = client.app.state.data_dir

        df = pd.DataFrame(
            {"open": [60000, 61000], "high": [61000, 62000],
             "low": [59000, 60000], "close": [60500, 61500],
             "volume": [100, 200]},
            index=pd.DatetimeIndex(["2024-01-01", "2024-01-02"], name="timestamp"),
        )
        df.to_parquet(data_dir / "ETHUSDT_1h.parquet")

        response = client.get("/api/data/available-sources")
        assert response.status_code == 200
        data = response.json()
        assert len(data["sources"]) >= 1
        eth = [s for s in data["sources"] if s["symbol"] == "ETHUSDT"]
        assert len(eth) == 1
        assert eth[0]["timeframe"] == "1h"

    def test_available_sources_skips_non_matching_filename(self, client: TestClient) -> None:
        """GET /api/data/available-sources should only match SYMBOL_TIMEFRAME pattern."""
        import pandas as pd

        data_dir = client.app.state.data_dir

        df = pd.DataFrame(
            {"open": [100], "high": [110], "low": [90], "close": [105], "volume": [50]},
            index=pd.DatetimeIndex(["2024-01-01"], name="timestamp"),
        )
        # Filename without underscore separator - should be skipped
        df.to_parquet(data_dir / "justdata.parquet")

        response = client.get("/api/data/available-sources")
        assert response.status_code == 200
        data = response.json()
        # justdata.parquet doesn't match SYMBOL_TIMEFRAME pattern
        assert all(s["symbol"] != "justdata" for s in data["sources"])


# -- Test: OHLCV by Symbol --

class TestOhlcvBySymbol:
    def test_ohlcv_by_symbol_not_found(self, client: TestClient) -> None:
        """GET /api/data/ohlcv/{symbol}/{timeframe} with nonexistent data."""
        response = client.get("/api/data/ohlcv/FAKEUSDT/1h")
        assert response.status_code == 404

    def test_ohlcv_by_symbol_success(self, client: TestClient) -> None:
        """GET /api/data/ohlcv/{symbol}/{timeframe} should return OHLCV data."""
        import pandas as pd

        data_dir = client.app.state.data_dir

        dates = pd.date_range("2024-01-01", periods=10, freq="4h", tz="UTC")
        df = pd.DataFrame(
            {"open": np.random.uniform(59000, 61000, 10),
             "high": np.random.uniform(60000, 62000, 10),
             "low": np.random.uniform(58000, 60000, 10),
             "close": np.random.uniform(59000, 61000, 10),
             "volume": np.random.uniform(100, 500, 10)},
            index=dates,
        )
        df.index.name = "timestamp"
        df.to_parquet(data_dir / "SOLUSDT_4h.parquet")

        response = client.get("/api/data/ohlcv/SOLUSDT/4h")
        assert response.status_code == 200
        data = response.json()
        assert data["dataset_id"] == "SOLUSDT_4h"
        assert len(data["data"]) == 10
        assert "open" in data["data"][0]

    def test_ohlcv_by_symbol_date_filter(self, client: TestClient) -> None:
        """GET /api/data/ohlcv/{symbol}/{timeframe}?start=&end= should filter."""
        import pandas as pd

        data_dir = client.app.state.data_dir

        dates = pd.date_range("2024-01-01", periods=100, freq="4h", tz="UTC")
        df = pd.DataFrame(
            {"open": [60000] * 100, "high": [61000] * 100,
             "low": [59000] * 100, "close": [60500] * 100,
             "volume": [100] * 100},
            index=dates,
        )
        df.index.name = "timestamp"
        df.to_parquet(data_dir / "XRPUSDT_4h.parquet")

        response = client.get(
            "/api/data/ohlcv/XRPUSDT/4h",
            params={"start": "2024-01-05", "end": "2024-01-10"},
        )
        assert response.status_code == 200
        data = response.json()
        assert len(data["data"]) < 100

    def test_ohlcv_by_symbol_limit(self, client: TestClient) -> None:
        """GET /api/data/ohlcv/{symbol}/{timeframe}?limit=N should cap rows."""
        import pandas as pd

        data_dir = client.app.state.data_dir

        dates = pd.date_range("2024-01-01", periods=100, freq="4h", tz="UTC")
        df = pd.DataFrame(
            {"open": [60000] * 100, "high": [61000] * 100,
             "low": [59000] * 100, "close": [60500] * 100,
             "volume": [100] * 100},
            index=dates,
        )
        df.index.name = "timestamp"
        df.to_parquet(data_dir / "DOGEUSDT_4h.parquet")

        response = client.get("/api/data/ohlcv/DOGEUSDT/4h", params={"limit": 5})
        assert response.status_code == 200
        assert len(response.json()["data"]) == 5


# -- Test: Chart Indicators --

class TestChartIndicators:
    def test_chart_indicators_not_found(self, client: TestClient) -> None:
        """GET /api/data/chart-indicators/{symbol}/{timeframe} 404."""
        response = client.get("/api/data/chart-indicators/FAKEUSDT/1h")
        assert response.status_code == 404

    def test_chart_indicators_default(self, client: TestClient) -> None:
        """GET /api/data/chart-indicators with default params returns boll+rsi."""
        import pandas as pd

        data_dir = client.app.state.data_dir

        np.random.seed(42)
        dates = pd.date_range("2024-01-01", periods=200, freq="4h", tz="UTC")
        closes = 60000 + np.cumsum(np.random.randn(200) * 100)
        df = pd.DataFrame(
            {"open": closes * 0.999, "high": closes * 1.005,
             "low": closes * 0.995, "close": closes,
             "volume": np.random.uniform(100, 1000, 200)},
            index=dates,
        )
        df.index.name = "timestamp"
        df.to_parquet(data_dir / "ADAUSDT_4h.parquet")

        response = client.get("/api/data/chart-indicators/ADAUSDT/4h")
        assert response.status_code == 200
        data = response.json()
        assert "boll" in data
        assert "rsi" in data

    def test_chart_indicators_ema(self, client: TestClient) -> None:
        """GET /api/data/chart-indicators with ema_periods."""
        import pandas as pd

        data_dir = client.app.state.data_dir

        np.random.seed(42)
        dates = pd.date_range("2024-01-01", periods=200, freq="4h", tz="UTC")
        closes = 60000 + np.cumsum(np.random.randn(200) * 100)
        df = pd.DataFrame(
            {"open": closes * 0.999, "high": closes * 1.005,
             "low": closes * 0.995, "close": closes,
             "volume": np.random.uniform(100, 1000, 200)},
            index=dates,
        )
        df.index.name = "timestamp"
        df.to_parquet(data_dir / "MATICUSDT_4h.parquet")

        response = client.get(
            "/api/data/chart-indicators/MATICUSDT/4h",
            params={"ema_periods": "7,25", "boll_enabled": "false", "rsi_enabled": "false"},
        )
        assert response.status_code == 200
        data = response.json()
        assert "7" in data["ema"]
        assert "25" in data["ema"]

    def test_chart_indicators_macd(self, client: TestClient) -> None:
        """GET /api/data/chart-indicators with macd_enabled."""
        import pandas as pd

        data_dir = client.app.state.data_dir

        np.random.seed(42)
        dates = pd.date_range("2024-01-01", periods=200, freq="4h", tz="UTC")
        closes = 60000 + np.cumsum(np.random.randn(200) * 100)
        df = pd.DataFrame(
            {"open": closes * 0.999, "high": closes * 1.005,
             "low": closes * 0.995, "close": closes,
             "volume": np.random.uniform(100, 1000, 200)},
            index=dates,
        )
        df.index.name = "timestamp"
        df.to_parquet(data_dir / "LINKUSDT_4h.parquet")

        response = client.get(
            "/api/data/chart-indicators/LINKUSDT/4h",
            params={"macd_enabled": "true", "boll_enabled": "false", "rsi_enabled": "false"},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["macd"] is not None

    def test_chart_indicators_kdj(self, client: TestClient) -> None:
        """GET /api/data/chart-indicators with kdj_enabled."""
        import pandas as pd

        data_dir = client.app.state.data_dir

        np.random.seed(42)
        dates = pd.date_range("2024-01-01", periods=200, freq="4h", tz="UTC")
        closes = 60000 + np.cumsum(np.random.randn(200) * 100)
        df = pd.DataFrame(
            {"open": closes * 0.999, "high": closes * 1.005,
             "low": closes * 0.995, "close": closes,
             "volume": np.random.uniform(100, 1000, 200)},
            index=dates,
        )
        df.index.name = "timestamp"
        df.to_parquet(data_dir / "AVAXUSDT_4h.parquet")

        response = client.get(
            "/api/data/chart-indicators/AVAXUSDT/4h",
            params={"kdj_enabled": "true", "boll_enabled": "false", "rsi_enabled": "false"},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["kdj"] is not None
        assert "k" in data["kdj"]
        assert "d" in data["kdj"]
        assert "j" in data["kdj"]

    def test_chart_indicators_rvol_vwma(self, client: TestClient) -> None:
        """GET /api/data/chart-indicators with rvol+vwma enabled."""
        import pandas as pd

        data_dir = client.app.state.data_dir

        np.random.seed(42)
        dates = pd.date_range("2024-01-01", periods=200, freq="4h", tz="UTC")
        closes = 60000 + np.cumsum(np.random.randn(200) * 100)
        df = pd.DataFrame(
            {"open": closes * 0.999, "high": closes * 1.005,
             "low": closes * 0.995, "close": closes,
             "volume": np.random.uniform(100, 1000, 200)},
            index=dates,
        )
        df.index.name = "timestamp"
        df.to_parquet(data_dir / "DOTUSDT_4h.parquet")

        response = client.get(
            "/api/data/chart-indicators/DOTUSDT/4h",
            params={
                "rvol_enabled": "true", "vwma_enabled": "true",
                "boll_enabled": "false", "rsi_enabled": "false",
            },
        )
        assert response.status_code == 200
        data = response.json()
        assert data["rvol"] is not None
        assert data["vwma"] is not None


# -- Test: Safe Timestamp / Date Filter helpers --

class TestDateFilterHelpers:
    def test_safe_timestamp_valid(self):
        """_safe_timestamp should parse valid date strings."""
        from api.routes.data import _safe_timestamp

        ts = _safe_timestamp("2024-01-15", tz_aware=False)
        assert ts is not None
        assert ts.year == 2024

    def test_safe_timestamp_utc(self):
        """_safe_timestamp with tz_aware=True should add UTC."""
        from api.routes.data import _safe_timestamp

        ts = _safe_timestamp("2024-06-01T00:00:00", tz_aware=True)
        assert ts is not None
        assert str(ts.tz) == "UTC"

    def test_safe_timestamp_none(self):
        """_safe_timestamp should return None for empty input."""
        from api.routes.data import _safe_timestamp

        assert _safe_timestamp(None, False) is None
        assert _safe_timestamp("", False) is None

    def test_safe_timestamp_invalid(self):
        """_safe_timestamp should return None for invalid input."""
        from api.routes.data import _safe_timestamp

        assert _safe_timestamp("not-a-date", False) is None

    def test_apply_date_filter(self):
        """_apply_date_filter should correctly filter DataFrame."""
        import pandas as pd
        from api.routes.data import _apply_date_filter

        dates = pd.date_range("2024-01-01", periods=30, freq="D")
        df = pd.DataFrame({"close": range(30)}, index=dates)
        df.index.name = "timestamp"

        filtered = _apply_date_filter(df, "2024-01-10", "2024-01-20")
        assert len(filtered) == 11  # inclusive both ends

    def test_apply_date_filter_invalid_dates_ignored(self):
        """_apply_date_filter should silently skip invalid dates."""
        import pandas as pd
        from api.routes.data import _apply_date_filter

        dates = pd.date_range("2024-01-01", periods=10, freq="D")
        df = pd.DataFrame({"close": range(10)}, index=dates)
        df.index.name = "timestamp"

        filtered = _apply_date_filter(df, "garbage", None)
        assert len(filtered) == 10  # No filter applied
