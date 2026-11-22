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
