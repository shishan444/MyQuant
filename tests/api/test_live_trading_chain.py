"""be-live-trading-chain 契约测试（D4 决策快照+D5 状态迁移基线）。

覆盖：迁移可升级可回滚+PRAGMA 索引实证/decisions 404·空集·分页/append-only
结构断言/start-stop 状态全矩阵（合法·409·404）/三字段透传。
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

pytestmark = [pytest.mark.integration]

from fastapi.testclient import TestClient


@pytest.fixture()
def client(tmp_path: Path):
    from core.persistence.db_ext import init_db_ext
    from api.app import create_app
    db = tmp_path / "q.db"
    init_db_ext(db)
    app = create_app(db_path=db, data_dir=tmp_path / "data")
    with TestClient(app, raise_server_exceptions=False) as c:
        c._db = db  # type: ignore[attr-defined]
        yield c


VALID_DNA = json.dumps({
    "signal_genes": [
        {"indicator": "rsi", "params": {"period": 14}, "role": "entry_trigger",
         "field_name": None, "condition": {"type": "cross_below", "threshold": 30}},
        {"indicator": "rsi", "params": {"period": 14}, "role": "exit_trigger",
         "field_name": None, "condition": {"type": "cross_above", "threshold": 70}},
    ],
    "logic_genes": {"entry_logic": "AND", "exit_logic": "OR"},
    "execution_genes": {"timeframe": "4h", "symbol": "BTCUSDT"},
    "risk_genes": {"stop_loss": 0.05, "take_profit": 0.1, "position_size": 0.3,
                   "leverage": 2, "direction": "long", "sl_mode": "pct"},
})


def _make_task(client) -> str:
    r = client.post("/api/live-trading/tasks", json={
        "symbol": "BTCUSDT", "timeframe": "4h",
        "dna_json": VALID_DNA, "direction": "long",
        "config_json": "{}"})
    assert r.status_code == 201, r.text
    return r.json()["task_id"]


class TestMigration:
    def test_upgrade_downgrade_and_pragma(self, tmp_path):
        """迁移可升级可回滚+索引 (task_id,bar_time) PRAGMA 实证。"""
        from core.persistence.db_ext import init_db_ext
        from sqlalchemy import create_engine, inspect, text
        db = tmp_path / "mig.db"
        init_db_ext(db)  # 全链升到 head
        eng = create_engine(f"sqlite:///{db}")
        insp = inspect(eng)
        assert insp.has_table("live_decisions")
        cols = {c["name"] for c in insp.get_columns("live_trading_task")}
        assert "error_detail" in cols
        with eng.connect() as conn:
            idx = conn.execute(text(
                "SELECT name FROM pragma_index_list('live_decisions')")).all()
        assert any("task_bar" in r[0] for r in idx), idx
        # 回滚一级再升回——url 注入走 Config.attributes（env.py 优先
        # 通道；set_main_option 会被 env.py 的 db_path 推导覆盖——
        # 降级落错库静默空操作，实测实录）
        from alembic.config import Config
        from alembic import command
        cfg = Config("alembic.ini")
        cfg.attributes["db_path"] = str(db)
        command.downgrade(cfg, "c3f8a2e6d9b1")
        eng2 = create_engine(f"sqlite:///{db}")
        assert not inspect(eng2).has_table("live_decisions")
        cfg2 = Config("alembic.ini")
        cfg2.attributes["db_path"] = str(db)
        command.upgrade(cfg2, "head")
        assert inspect(create_engine(f"sqlite:///{db}")).has_table("live_decisions")


class TestDecisions:
    def test_404_unknown_task(self, client):
        r = client.get("/api/live-trading/tasks/lt_none/decisions")
        assert r.status_code == 404
        assert r.json()["detail"] == "live trading task not found"

    def test_empty_200(self, client):
        tid = _make_task(client)
        r = client.get(f"/api/live-trading/tasks/{tid}/decisions")
        assert r.status_code == 200
        body = r.json()
        assert body["items"] == [] and body["total"] == 0
        assert body["has_more"] is False

    def test_pagination_and_order(self, client):
        from core.persistence.repository import live_trading as repo
        tid = _make_task(client)
        for i in range(3):
            repo.append_live_decision(
                client._db, task_id=tid,
                bar_time=f"2026-01-0{i+1}T00:00:00Z", action="skip",
                reason_json='{"why": "no_signal"}')
        r = client.get(f"/api/live-trading/tasks/{tid}/decisions",
                       params={"limit": 2})
        body = r.json()
        assert body["total"] == 3
        assert len(body["items"]) == 2 and body["has_more"] is True
        # bar_time 升序审计读序
        assert body["items"][0]["bar_time"] < body["items"][1]["bar_time"]
        r2 = client.get(f"/api/live-trading/tasks/{tid}/decisions",
                        params={"limit": 2, "offset": 2})
        assert len(r2.json()["items"]) == 1

    def test_append_only_no_update_delete_channel(self, client):
        """append-only 结构断言：仓储面无 update/delete 决策函数（静态
        ——API 面无对应 HTTP 方法）。"""
        from core.persistence.repository import live_trading as repo
        assert not any(
            n.startswith(("update_live_decision", "delete_live_decision"))
            for n in dir(repo))
        # 行为级：写动词全部 405（GET 路由在册——写通道不存在）
        tid2 = _make_task(client)
        for method in ("put", "patch", "delete", "post"):
            r = getattr(client, method)(
                f"/api/live-trading/tasks/{tid2}/decisions")
            assert r.status_code == 405, (method, r.status_code)


class TestDecisionsParamValidation:
    def test_limit_offset_422(self, client):
        """F2 锚：负 limit 矛盾体（items=[] 且 has_more=True）与无上界
        已由 Query(ge/le) 收口。"""
        tid = _make_task(client)
        for params in ({"limit": 0}, {"limit": -1}, {"limit": 501},
                       {"offset": -1}):
            r = client.get(f"/api/live-trading/tasks/{tid}/decisions",
                           params=params)
            assert r.status_code == 422, params


class TestStartStop:
    def test_start_pending_to_running(self, client):
        tid = _make_task(client)
        before = client.get(f"/api/live-trading/tasks/{tid}").json()["updated_at"]
        r = client.post(f"/api/live-trading/tasks/{tid}/start")
        assert r.status_code == 200
        body = r.json()
        assert body["status"] == "running"
        # F-R1 锚：CAS 直写仍刷 updated_at（丢刷新回归即红）
        assert body["updated_at"] != before

    def test_start_running_409(self, client):
        tid = _make_task(client)
        client.post(f"/api/live-trading/tasks/{tid}/start")
        r = client.post(f"/api/live-trading/tasks/{tid}/start")
        assert r.status_code == 409
        assert "illegal transition" in r.json()["detail"]

    def test_stop_running_to_stopped_reason(self, client):
        tid = _make_task(client)
        client.post(f"/api/live-trading/tasks/{tid}/start")
        r = client.post(f"/api/live-trading/tasks/{tid}/stop")
        assert r.status_code == 200
        body = r.json()
        assert body["status"] == "stopped"
        assert body["stop_reason"] == "user_stop"  # graceful 可观察落因

    def test_stop_stopped_409(self, client):
        tid = _make_task(client)
        client.post(f"/api/live-trading/tasks/{tid}/start")
        client.post(f"/api/live-trading/tasks/{tid}/stop")
        r = client.post(f"/api/live-trading/tasks/{tid}/stop")
        assert r.status_code == 409

    def test_stop_pending_409(self, client):
        tid = _make_task(client)
        r = client.post(f"/api/live-trading/tasks/{tid}/stop")
        assert r.status_code == 409

    def test_404_unknown(self, client):
        assert client.post("/api/live-trading/tasks/lt_x/start").status_code == 404
        assert client.post("/api/live-trading/tasks/lt_x/stop").status_code == 404


class TestStateFields:
    def test_three_fields_passthrough(self, client):
        """三字段透传：error_detail 写入→单查响应可见（heartbeat/last_bar
        模型先例已在 schema——本锚验证 error_detail 新增列全链）。"""
        from core.persistence.repository import live_trading as repo
        tid = _make_task(client)
        repo.update_live_trading_task(
            client._db, tid,
            heartbeat_at="2026-01-01T00:00:00Z",
            last_bar_time="2026-01-01T00:00:00Z",
            error_detail="boom")
        r = client.get(f"/api/live-trading/tasks/{tid}")
        body = r.json()
        assert body["heartbeat_at"] == "2026-01-01T00:00:00Z"
        assert body["last_bar_time"] == "2026-01-01T00:00:00Z"
        assert body["error_detail"] == "boom"
        # start 迁移清 error_detail（新运行段干净开始）
        client.post(f"/api/live-trading/tasks/{tid}/start")
        assert client.get(
            f"/api/live-trading/tasks/{tid}").json()["error_detail"] is None
