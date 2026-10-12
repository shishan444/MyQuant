"""API 层分批组键传输与组装（review-042 阶段四 A-1 盲区消解）。

连接处锚：HypothesisCreate 顶层组键三参数 → 路由并入 conditions
（非 None 才写键——缺省不写键保 conditions_hash 连续）→ 落库与翻译
全链。此前 tests/api 对组键零覆盖（覆盖率 Miss hypotheses.py:101
确证带值并入行从未执行）。
"""
import json
from pathlib import Path

import pytest

pytestmark = [pytest.mark.integration]

from core.analysis.executor import ensure_research_tables
from core.persistence.db_ext import init_db_ext
from core.persistence.repository.research import get_hypothesis

RSI = {"indicator": "RSI", "params": {"period": 14}, "field": "rsi"}


def _base() -> dict:
    return {
        "conditions": {
            "when_conditions": [{"subject": RSI, "action": "lt", "target": 45}],
            "then_conditions": [{"subject": RSI, "action": "gt", "target": 75}],
        },
        "statement": "api-groups",
        "data_ranges": [{"symbol": "BTCUSDT", "timeframe": "4h",
                         "start": "2024-01-01T00:00:00Z",
                         "end": "2024-02-01T00:00:00Z"}],
    }


@pytest.fixture()
def client(tmp_path: Path, tmp_data_dir: Path):
    from fastapi.testclient import TestClient

    from api.app import create_app

    db = tmp_path / "q.db"
    init_db_ext(db)
    ensure_research_tables(db)
    app = create_app(db_path=db, data_dir=tmp_data_dir)
    with TestClient(app) as c:
        yield c, db


class TestGroupKeysTransport:
    def test_groups_merged_into_conditions_and_translated(self, client):
        """带三组键 POST：组键并入 conditions 落库（Miss 101 行的执行
        锚），翻译产物含三角色基因（连接处全链）。"""
        c, db = client
        r = c.post("/api/research/hypotheses", json=dict(
            _base(),
            exit_partial_conditions=[{"subject": RSI, "action": "gt",
                                      "target": 70}],
            add_trigger_conditions=[{"subject": RSI, "action": "lt",
                                     "target": 40}],
            add_suppress_conditions=[{"subject": RSI, "action": "gt",
                                      "target": 80}]))
        assert r.status_code == 200, r.text
        hid = r.json()["hypothesis_id"]
        conds = json.loads(get_hypothesis(db, hid)["conditions"])
        assert "exit_partial_conditions" in conds
        assert "add_trigger_conditions" in conds
        assert "add_suppress_conditions" in conds
        # 翻译全链：三组键 → 三角色基因
        from core.analysis.hypothesis_to_strategy import translate_hypothesis
        from core.persistence.repository.strategy import get_strategy

        t = translate_hypothesis(db, hypothesis_id=hid)
        genes = json.loads(
            get_strategy(db, t["strategy_id"])["dna_json"])["signal_genes"]
        roles = {g["role"] for g in genes}
        assert {"reduce_trigger", "add_trigger", "add_inhibit"} <= roles

    def test_none_omitted_keeps_fingerprint_continuous(self, client):
        """None 省略指纹连续：先带组键登记，再以同 when/then 不带组键
        POST——conditions 不同（组键参与指纹）不命中幂等，产新身份。"""
        c, _ = client
        group = [{"subject": RSI, "action": "gt", "target": 70}]
        r1 = c.post("/api/research/hypotheses", json=dict(
            _base(), exit_partial_conditions=group))
        assert r1.status_code == 200 and r1.json()["duplicated"] is False
        r2 = c.post("/api/research/hypotheses", json=_base())
        assert r2.status_code == 200
        # 组键参与身份：不带组键=不同 conditions_hash → 新身份非 duplicated
        assert r2.json()["duplicated"] is False
        assert r2.json()["hypothesis_id"] != r1.json()["hypothesis_id"]
        # 省略键不进 conditions（与 service 直调同语义）
        r3 = c.post("/api/research/hypotheses", json=_base())
        assert r3.json()["duplicated"] is True  # 同不带键=同指纹命中

    def test_group_entry_schema_validated(self, client):
        """组键条目经 ConditionItem 校验：坏结构（缺 action/target）
        422 在 schema 层阻断，不流入登记链。"""
        c, _ = client
        r = c.post("/api/research/hypotheses", json=dict(
            _base(), add_trigger_conditions=[{"subject": RSI}]))
        assert r.status_code == 422


class TestUnknownParamKeyRejected:
    def test_unknown_key_rejected_with_field(self, client):
        """声明块未知键拒绝（A-2：4.1 三键扩白名单后原直锚退役，
        此处重建）：422 RESEARCH_PARAM_INVALID，内层 detail 附
        field/unknown_keys 与八键合法集（错误即文档）。"""
        c, _ = client
        r = c.post("/api/research/hypotheses", json=dict(
            _base(), strategy_params={"foo": 1}))
        assert r.status_code == 422
        outer = r.json()["detail"]
        assert outer["code"] == "RESEARCH_PARAM_INVALID"
        inner = outer["detail"]
        assert inner["field"] == "strategy_params.foo"
        assert inner["unknown_keys"] == ["foo"]
        for key in ("stop_loss", "take_profit", "sl_mode", "position_size",
                    "leverage", "exit_fraction", "add_fraction",
                    "max_exposure"):
            assert key in inner["legal_range"]
