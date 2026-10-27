"""POST /api/strategies 同签名结构化分流（research-execution-config-
alignment 1.2，卡 B / C1-D 组）。

B8 病灶：save_strategy 同签名去重对非 draft 新行「保留更优否则静默
返回」——POST 面表现为落库 id 不存在转 500（无结构化语义）。修复=
创建前 find_by_gene_signature 预检：命中且不更优 → 200+duplicated
附既有 id（幂等事实非冲突）；未命中 → 201 正常创建；真错误沿既有
通道（两通道分离）。save_strategy 本体零改动（进化链行为锚点）。
"""
from __future__ import annotations

import json
import sqlite3

import pytest

pytestmark = [pytest.mark.integration]

from fastapi.testclient import TestClient


def _dna_dict() -> dict:
    return {
        "signal_genes": [
            {"indicator": "RSI", "params": {"period": 14},
             "role": "entry_trigger", "field": None,
             "condition": {"type": "lt", "threshold": 30}},
            {"indicator": "RSI", "params": {"period": 14},
             "role": "exit_trigger", "field": None,
             "condition": {"type": "gt", "threshold": 70}},
        ],
        "logic_genes": {"entry_logic": "AND", "exit_logic": "AND"},
        "execution_genes": {"timeframe": "4h", "symbol": "BTCUSDT"},
        "risk_genes": {"stop_loss": 0.05, "take_profit": 0.10,
                       "position_size": 0.3},
    }


def _body(dna=None) -> dict:
    return {"name": "b8 测试", "dna": dna or _dna_dict(),
            "symbol": "BTCUSDT", "timeframe": "4h"}


def _table_snapshot(db_path, strategy_id: str):
    """策略行快照（行数/目标行 updated_at）+验证表行数——零写入对账。"""
    conn = sqlite3.connect(db_path)
    try:
        n = conn.execute("SELECT count(*) FROM strategy").fetchone()[0]
        row = conn.execute(
            "SELECT updated_at FROM strategy WHERE strategy_id=?",
            (strategy_id,)).fetchone()
        v = conn.execute(
            "SELECT count(*) FROM validation_records").fetchone()[0]
    finally:
        conn.close()
    return {"count": n, "updated_at": row[0] if row else None,
            "validations": v}


class TestD1SameSignatureDup:
    def test_same_signature_post_200_structured_idempotent(
            self, api_client: TestClient):
        """D1：同签名 POST → 200 结构化幂等体四键（非 500），附既有
        strategy_id；零写入（策略表行数/既有行 updated_at/验证表均不变）。"""
        first = api_client.post("/api/strategies", json=_body())
        assert first.status_code == 201, first.text
        sid = first.json()["strategy_id"]
        snap = _table_snapshot(api_client.app.state.db_path, sid)

        again = api_client.post("/api/strategies", json=_body())
        assert again.status_code == 200, again.text
        body = again.json()
        assert body["strategy_id"] == sid
        assert body["duplicated"] is True
        assert body["gene_signature"]
        assert "保留既有" in body["note"]
        # 零写入对账：不产生半写状态（无幽灵行/既有行未触碰）
        snap2 = _table_snapshot(api_client.app.state.db_path, sid)
        assert snap2["count"] == snap["count"], "分流路径不得产生新行"
        assert snap2["updated_at"] == snap["updated_at"], \
            "既有行不得被触碰"
        assert snap2["validations"] == snap["validations"], \
            "验证表无幽灵记录"


class TestD2NewSignature:
    def test_new_signature_post_201_and_queryable(self, api_client):
        """D2 未命中：201 正常创建+回执可查。"""
        r = api_client.post("/api/strategies", json=_body())
        assert r.status_code == 201, r.text
        sid = r.json()["strategy_id"]
        g = api_client.get(f"/api/strategies/{sid}")
        assert g.status_code == 200, g.text
        assert g.json()["strategy_id"] == sid


class TestD2DraftHitBranch:
    def test_draft_only_hit_200_with_draft_note(self, api_client):
        """D2 边界：库内仅同签名 draft（非 draft 缺位）→ 命中 draft →
        200+duplicated 附 draft id（draft 无绩效语义不走保留更优——
        note 分态）；该场景修复前为 save_strategy 静默吞后 500。"""
        from core.persistence.repository.strategy import save_strategy
        from core.strategy.dna import StrategyDNA
        db = api_client.app.state.db_path
        dna = StrategyDNA.from_json(json.dumps(_dna_dict()))
        save_strategy(db, strategy_id="st_draft_b8", dna_json=dna.to_json(),
                      symbol="BTCUSDT", timeframe="4h", name="draft",
                      status="draft")
        r = api_client.post("/api/strategies", json=_body())
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["strategy_id"] == "st_draft_b8"
        assert body["duplicated"] is True
        assert "draft" in body["note"]

    def test_active_preferred_over_draft_hit(self, api_client):
        """非 draft 优先：同签名既有 active+draft 两行 → 命中 active。"""
        from core.persistence.repository.strategy import save_strategy
        from core.strategy.dna import StrategyDNA
        db = api_client.app.state.db_path
        dna = StrategyDNA.from_json(json.dumps(_dna_dict()))
        save_strategy(db, strategy_id="st_active_b8",
                      dna_json=dna.to_json(),
                      symbol="BTCUSDT", timeframe="4h", name="a",
                      status="active")
        save_strategy(db, strategy_id="st_draft_b8b",
                      dna_json=dna.to_json(),
                      symbol="BTCUSDT", timeframe="4h", name="d",
                      status="draft")
        r = api_client.post("/api/strategies", json=_body())
        assert r.status_code == 200, r.text
        assert r.json()["strategy_id"] == "st_active_b8"
        assert "绩效" in r.json()["note"]


class TestD3ErrorChannelSeparation:
    def test_invalid_dna_rejected_by_existing_channel(self, api_client):
        """D3 真错误通道分离：非法 DNA（Pydantic/值域）沿既有通道 422，
        不落入 200 分流（错误/幂等两通道分离）。"""
        bad = _dna_dict()
        bad["risk_genes"]["stop_loss"] = 99.0  # 越域（>0.20）
        r = api_client.post("/api/strategies", json=_body(dna=bad))
        assert r.status_code == 422, r.text
        assert r.json()["detail"]["code"] == "DNA_VALIDATION_FAILED"
