"""be-strategy-transitions 端点测试（D7 事件史+rider doc 锚）。

正向（迁移事件升序+evidence 解析）/零事件 200/404/坏 JSON 降级如实。
"""
from __future__ import annotations

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


def _make_strategy(client) -> str:
    r = client.post("/api/strategies", json={
        "name": "t", "symbol": "BTCUSDT", "timeframe": "4h",
        "dna": {"signal_genes": [
            {"indicator": "rsi", "params": {"period": 14}, "role": "entry_trigger",
             "field_name": None, "condition": {"type": "cross_below", "threshold": 30}},
            {"indicator": "rsi", "params": {"period": 14}, "role": "exit_trigger",
             "field_name": None, "condition": {"type": "cross_above", "threshold": 70}}],
            "logic_genes": {"entry_logic": "AND", "exit_logic": "OR"},
                "execution_genes": {"timeframe": "4h", "symbol": "BTCUSDT"},
                "risk_genes": {"stop_loss": 0.05, "take_profit": 0.1,
                                "position_size": 0.3, "leverage": 2,
                                "direction": "long", "sl_mode": "pct"}},
    })
    assert r.status_code in (200, 201), r.text
    return r.json()["strategy_id"]


class TestTransitions:
    def test_404(self, client):
        assert client.get("/api/strategies/st_none/transitions").status_code == 404

    def test_empty_200(self, client):
        sid = _make_strategy(client)
        r = client.get(f"/api/strategies/{sid}/transitions")
        assert r.status_code == 200
        body = r.json()
        assert body == {"items": [], "total": 0}

    def test_events_asc_with_evidence(self, client):
        """正向：经 lifecycle transit 产两事件（draft→active→archived）——
        asc 时序+evidence 解析嵌入。"""
        from core.strategy.lifecycle.service import transit
        sid = _make_strategy(client)
        # save_strategy 缺省 status=active（stock 分类）——事件序列经
        # mount（active→paper）+task_end（paper→active）+manual_archive
        transit(client._db, sid, target="paper", trigger="mount",
               evidence={"task": "pt_1"})
        transit(client._db, sid, target="active", trigger="task_end",
               evidence={"task_id": "pt_1", "final_status": "completed"})
        transit(client._db, sid, target="archived", trigger="manual_archive",
               evidence=None)
        r = client.get(f"/api/strategies/{sid}/transitions")
        body = r.json()
        assert body["total"] == 3
        items = body["items"]
        # asc 时序（created_at 升序——同秒粒度下按插入序稳定）
        assert items[0]["from_status"] == "active"
        assert items[0]["to_status"] == "paper"
        assert items[0]["evidence"] == {"task": "pt_1"}
        assert items[1]["to_status"] == "active"
        assert items[2]["evidence"] is None  # manual 边 NULL→None

    def test_bad_json_degraded_as_is(self, client):
        """坏 JSON 手改行降级如实（500 非静默）——append-only 通道外
        手改面不存在于生产，防御位以端点 500 形态锁定（不吞不造）。"""
        from core.persistence.repository.strategy import list_transition_events
        sid = _make_strategy(client)
        from core.strategy.lifecycle.service import transit
        transit(client._db, sid, target="paper", trigger="mount", evidence={})
        # 通道外直改（测试专用面）
        from core.persistence.orm.engine import engine_for
        eng = engine_for(client._db)
        with eng.begin() as conn:
            from sqlalchemy import text as sa_text
            conn.execute(sa_text(
                "UPDATE strategy_transition_event SET evidence_json='{broken' "
                "WHERE strategy_id=:sid").bindparams(sid=sid))
        r = client.get(f"/api/strategies/{sid}/transitions")
        # 坏串 json.loads 抛——端点 500 如实（无吞咽通道）
        assert r.status_code == 500


class TestVerifyStarRider:
    def test_doc_anchor_no_patch_surface(self, client):
        """rider doc 形锚：PATCH 无 verify_star 受控入面（extra=forbid
        拒收）+models.py 列注值域锚在场。"""
        sid = _make_strategy(client)
        r = client.put(f"/api/strategies/{sid}",
                         json={"verify_star": 6})
        # PUT 面收 StrategyUpdate（无 verify_star 字段+extra=forbid）
        # ——非受控入面实证（422 拒收）
        assert r.status_code == 422
        from core.persistence.orm import models
        import inspect
        src = inspect.getsource(models)
        assert "verify_star 值域锚定 0-5" in src
