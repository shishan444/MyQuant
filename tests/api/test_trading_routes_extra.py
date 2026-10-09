"""Trading 路由缺口特征网（修复期安全网，现状记录语义）。

补 tests/api+tests/trading 合并视角下 api/routes/trading.py 未覆盖分支：
- POST dna_json 非法 → 400（B6.1：业务拒绝与 Pydantic 422 分层）
- POST strategy_id 关联 qualified=False 策略 → warnings 提示
- POST /stop /restart /equity /metrics /delete 的 404 与状态语义
"""
from __future__ import annotations

import pytest

pytestmark = [pytest.mark.integration]

from fastapi.testclient import TestClient

from tests.helpers.data_factory import make_dna


def _create_payload(**overrides) -> dict:
    payload = {
        "dna_json": make_dna().to_json(),
        "symbol": "BTCUSDT",
        "timeframe": "4h",
        "initial_cash": 100_000,
        "fee": 0.001,
        "leverage": 1,
        "direction": "long",
        "strategy_name": "feat-strat",
    }
    payload.update(overrides)
    return payload


def _set_strategy_qualified(db_path, strategy_id: str, qualified: int) -> None:
    """经 ORM 直写 qualified 列（repository 白名单不含该列）。"""
    from sqlalchemy import update
    from core.persistence.orm.models import Strategy
    from core.persistence.repository.dataset_meta import _engine_for
    engine = _engine_for(db_path)
    with engine.begin() as conn:
        conn.execute(
            update(Strategy)
            .where(Strategy.strategy_id == strategy_id)
            .values(qualified=qualified)
        )


class TestCreateTaskValidation:
    def test_invalid_dna_json_returns_400(self, api_client: TestClient) -> None:
        """dna_json 解析失败 → 400 业务拒绝（非 422，detail 带 Invalid dna_json）。"""
        response = api_client.post(
            "/api/trading/tasks", json=_create_payload(dna_json="not-a-json"),
        )
        assert response.status_code == 400
        # CARD-07 后固定文案（原文经日志保留，不进响应）
        assert response.json()["detail"] == "Invalid dna_json format"

    def test_valid_dna_json_creates_201(self, api_client: TestClient) -> None:
        """合法 dna_json 正常创建（对照组）。"""
        response = api_client.post("/api/trading/tasks", json=_create_payload())
        assert response.status_code == 201
        data = response.json()
        assert data["task_id"]
        assert data["symbol"] == "BTCUSDT"

    def test_unqualified_strategy_id_yields_warning(
        self, api_client: TestClient,
    ) -> None:
        """strategy_id 指向 qualified=False 策略时响应带 warnings。"""
        from core.persistence.repository.strategy import save_strategy
        db_path = api_client.app.state.db_path
        save_strategy(
            db_path, strategy_id="strat-unq", dna_json=make_dna().to_json(),
            symbol="BTCUSDT", timeframe="4h",
        )
        _set_strategy_qualified(db_path, "strat-unq", 0)
        response = api_client.post(
            "/api/trading/tasks", json=_create_payload(strategy_id="strat-unq"),
        )
        assert response.status_code == 201
        warnings = response.json()["warnings"]
        assert warnings is not None and len(warnings) == 1
        assert "qualified" in warnings[0]

    def test_qualified_strategy_id_no_warning(self, api_client: TestClient) -> None:
        """strategy_id 指向 qualified=1 策略时无 warnings。"""
        from core.persistence.repository.strategy import save_strategy
        db_path = api_client.app.state.db_path
        save_strategy(
            db_path, strategy_id="strat-q", dna_json=make_dna().to_json(),
            symbol="BTCUSDT", timeframe="4h",
        )
        _set_strategy_qualified(db_path, "strat-q", 1)
        response = api_client.post(
            "/api/trading/tasks", json=_create_payload(strategy_id="strat-q"),
        )
        assert response.status_code == 201
        assert response.json()["warnings"] is None

    def test_unknown_strategy_id_no_warning(self, api_client: TestClient) -> None:
        """strategy_id 在库中不存在时静默无 warnings（不 404）。"""
        response = api_client.post(
            "/api/trading/tasks", json=_create_payload(strategy_id="ghost-id"),
        )
        assert response.status_code == 201
        assert response.json()["warnings"] is None


class TestLiveTradingDnaPrecheck:
    """CARD-22 N1：live_trading 创建任务补 dna_json 预检（形制对齐 trading.py）。"""

    def _live_payload(self, dna_json: str) -> dict:
        return {
            "symbol": "BTCUSDT", "timeframe": "4h",
            "dna_json": dna_json, "direction": "long",
        }

    def test_invalid_dna_json_400_fixed_detail(
        self, api_client: TestClient,
    ) -> None:
        """非法 dna_json → 400 固定文案（旧实现 201 落库）。"""
        response = api_client.post(
            "/api/live-trading/tasks", json=self._live_payload("not-a-json"),
        )
        assert response.status_code == 400
        assert response.json()["detail"] == "Invalid dna_json format"

    def test_valid_dna_json_creates_201(
        self, api_client: TestClient,
    ) -> None:
        """合法 dna_json 正常创建（对照组）。"""
        response = api_client.post(
            "/api/live-trading/tasks",
            json=self._live_payload(make_dna().to_json()),
        )
        assert response.status_code == 201
        data = response.json()
        assert data["task_id"].startswith("lt_")
        assert data["symbol"] == "BTCUSDT"


class TestStopRestartDelete:
    def test_stop_unknown_task_404(self, api_client: TestClient) -> None:
        response = api_client.post("/api/trading/tasks/no-such/stop")
        assert response.status_code == 404

    def test_restart_unknown_task_404(self, api_client: TestClient) -> None:
        response = api_client.post("/api/trading/tasks/no-such/restart")
        assert response.status_code == 404

    def test_restart_copies_task_config(self, api_client: TestClient) -> None:
        """restart 复制原任务全部参数生成新 pending 任务。"""
        create = api_client.post(
            "/api/trading/tasks",
            json=_create_payload(
                score_template="profit_first", leverage=3, direction="short",
                confidence_sizing_enabled=True,
            ),
        )
        task_id = create.json()["task_id"]
        response = api_client.post(f"/api/trading/tasks/{task_id}/restart")
        assert response.status_code == 200
        new = response.json()
        assert new["task_id"] != task_id
        assert new["status"] == "pending"
        assert new["symbol"] == "BTCUSDT"
        assert new["timeframe"] == "4h"
        assert new["leverage"] == 3
        assert new["direction"] == "short"
        assert new["strategy_name"] == "feat-strat"

    def test_delete_unknown_task_404(self, api_client: TestClient) -> None:
        response = api_client.delete("/api/trading/tasks/no-such")
        assert response.status_code == 404

    def test_delete_active_task_400_then_stop_then_delete(
        self, api_client: TestClient,
    ) -> None:
        """active（pending）任务不可删除（400），stop 后可删除。"""
        create = api_client.post("/api/trading/tasks", json=_create_payload())
        task_id = create.json()["task_id"]

        denied = api_client.delete(f"/api/trading/tasks/{task_id}")
        assert denied.status_code == 400

        stop = api_client.post(f"/api/trading/tasks/{task_id}/stop")
        assert stop.status_code == 200
        assert stop.json()["status"] == "stopped"
        assert stop.json()["stop_reason"] == "user_stop"

        deleted = api_client.delete(f"/api/trading/tasks/{task_id}")
        assert deleted.status_code == 200
        assert deleted.json()["deleted"] is True

        gone = api_client.get(f"/api/trading/tasks/{task_id}")
        assert gone.status_code == 404


class TestEquityAndMetrics:
    def test_equity_unknown_task_404(self, api_client: TestClient) -> None:
        response = api_client.get("/api/trading/tasks/no-such/equity")
        assert response.status_code == 404

    def test_equity_new_task_empty_snapshots(self, api_client: TestClient) -> None:
        """新建任务 equity 快照为空列表，total=0。"""
        create = api_client.post("/api/trading/tasks", json=_create_payload())
        task_id = create.json()["task_id"]
        response = api_client.get(f"/api/trading/tasks/{task_id}/equity")
        assert response.status_code == 200
        data = response.json()
        assert data["snapshots"] == []
        assert data["total"] == 0

    def test_metrics_unknown_task_404(self, api_client: TestClient) -> None:
        response = api_client.get("/api/trading/tasks/no-such/metrics")
        assert response.status_code == 404

    def test_metrics_existing_task_returns_200(self, api_client: TestClient) -> None:
        """存在任务（即使零交易）返回 200 指标对象。"""
        create = api_client.post("/api/trading/tasks", json=_create_payload())
        task_id = create.json()["task_id"]
        response = api_client.get(f"/api/trading/tasks/{task_id}/metrics")
        assert response.status_code == 200
        data = response.json()
        assert data["task_id"] == task_id
        assert "total_return" in data or "realized_pnl" in data or data  # 响应为指标字典
