"""R1 支撑/阻力动作参数键入口级锚（indicator-and-action-repair 卡 E
——review2 CARD-03 测试锚：schema 四可选字段+服务层值域校验）。

三面（卡面）：API 入口带参登记成功（原 422 extra_forbidden 转绿）/
坏值 422 detail 四要素/translate 产物携带恰登记键（D3 直调锚之外
的入口级——经 API 登记的参数全链进产物）。边界放行与指纹两态
（参数键进身份指纹，不合并幂等）。
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import pytest

pytestmark = [pytest.mark.integration]

from core.analysis.hypothesis_service import register_hypothesis
from core.analysis.hypothesis_to_strategy import translate_hypothesis
from core.persistence.db_ext import init_db_ext

SYMBOL = "BTCUSDT"
_BB = {"indicator": "BB", "params": {"period": 20, "std": 2},
       "field": "lower"}


@pytest.fixture()
def reg_env(tmp_path: Path):
    """阶梯价格数据+独立 db（api_client 的 state 覆写先例形制——
    test_precheck_registration._wire）。"""
    idx = pd.date_range("2024-01-01", periods=900, freq="4h", tz="UTC",
                        name="timestamp")
    close = np.concatenate([np.full(750, 100.0), np.full(150, 200.0)])
    df = pd.DataFrame({
        "open": close, "high": close + 1, "low": close - 1,
        "close": close, "volume": 100.0,
    }, index=idx)
    data_dir = tmp_path / "market"
    data_dir.mkdir()
    df.to_parquet(data_dir / f"{SYMBOL}_4h.parquet")
    db = tmp_path / "q.db"
    init_db_ext(db)
    return {"data_dir": data_dir, "db": db, "df": df}


def _wire(api_client, env):
    api_client.app.state.db_path = env["db"]
    api_client.app.state.data_dir = env["data_dir"]


def _ranges(df):
    return [{"symbol": SYMBOL, "timeframe": "4h",
             "start": df.index[650].isoformat(),
             "end": df.index[-1].isoformat()}]


def _body(df, when_cond):
    return {
        "conditions": {
            "when_conditions": [when_cond],
            # then 用可翻译指标形（本文件锚走 translate 全链——
            # close 基础列+数值 target 在翻译面不可表达；RSI 阈值形
            # 沿 test_action_domain_gate._register 先例）
            "then_conditions": [
                {"subject": {"indicator": "RSI", "params": {"period": 30}},
                 "action": "gt", "target": 60}],
        },
        "statement": "card03 入口级锚",
        "data_ranges": _ranges(df),
        "strategy_params": {"stop_loss": 0.05},
    }


def _tb(direction=None, proximity_pct=None):
    cond = {"subject": _BB, "action": "touch_bounce", "target": 0}
    if direction is not None:
        cond["direction"] = direction
    if proximity_pct is not None:
        cond["proximity_pct"] = proximity_pct
    return cond


def _rr(role=None, lookback=None):
    cond = {"subject": _BB, "action": "role_reversal", "target": 0}
    if role is not None:
        cond["role"] = role
    if lookback is not None:
        cond["lookback"] = lookback
    return cond


class TestR1ParamApiRegistration:
    """API 入口面（POST /api/research/hypotheses）。"""

    def test_touch_bounce_full_params_register_200(self, api_client, reg_env):
        """带参登记成功（原 422 extra_forbidden 转绿——schema 四可选
        字段收键；参数进指纹不触发幂等合并）。"""
        _wire(api_client, reg_env)
        r = api_client.post("/api/research/hypotheses", json=_body(
            reg_env["df"],
            _tb(direction="support", proximity_pct=0.02)))
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["duplicated"] is False
        assert body["hypothesis_id"].startswith("hyp_")

    def test_role_reversal_full_params_register_200(self, api_client, reg_env):
        _wire(api_client, reg_env)
        r = api_client.post("/api/research/hypotheses", json=_body(
            reg_env["df"], _rr(role="resistance", lookback=15)))
        assert r.status_code == 200, r.text
        assert r.json()["duplicated"] is False

    def test_boundary_values_pass(self, api_client, reg_env):
        """边界放行：lookback=1（>=1 下界）与 proximity_pct=1（>0 边
        界值）均合法登记。"""
        _wire(api_client, reg_env)
        r1 = api_client.post("/api/research/hypotheses", json=_body(
            reg_env["df"], _rr(lookback=1)))
        r2 = api_client.post("/api/research/hypotheses", json=_body(
            reg_env["df"], _tb(proximity_pct=1)))
        assert r1.status_code == 200, r1.text
        assert r2.status_code == 200, r2.text

    def test_params_into_fingerprint_two_states(self, api_client, reg_env):
        """参数键进身份指纹：不带参与带参两态各自独立登记（不同
        hypothesis_id、duplicated 均 False——不因同 subject/action 合并）。"""
        _wire(api_client, reg_env)
        r_bare = api_client.post("/api/research/hypotheses", json=_body(
            reg_env["df"], _tb()))
        r_full = api_client.post("/api/research/hypotheses", json=_body(
            reg_env["df"], _tb(direction="resistance", proximity_pct=0.05)))
        assert r_bare.status_code == 200 and r_full.status_code == 200
        assert r_bare.json()["duplicated"] is False
        assert r_full.json()["duplicated"] is False
        assert r_bare.json()["hypothesis_id"] != r_full.json()[
            "hypothesis_id"], "两态不合并幂等（参数键进指纹）"

    @pytest.mark.parametrize(("name", "cond", "legal_hint"), [
        ("direction 坏串", _tb(direction="north"),
         ["support", "resistance"]),
        ("proximity_pct 零", _tb(proximity_pct=0), None),
        ("proximity_pct 负", _tb(proximity_pct=-0.01), None),
        ("role 坏串", _rr(role="pivot"), ["support", "resistance"]),
        ("lookback 零", _rr(lookback=0), None),
        ("lookback 负", _rr(lookback=-3), None),
    ], ids=["dir-bad", "prox-zero", "prox-neg", "role-bad",
            "lb-zero", "lb-neg"])
    def test_bad_values_422_detail(self, api_client, reg_env, name, cond,
                                   legal_hint):
        """坏值 422 结构化（RESEARCH_CONDITION_INVALID+detail 四要素
        field/action/value/legal_range——服务层值域校验的 API 面触达）。"""
        _wire(api_client, reg_env)
        r = api_client.post("/api/research/hypotheses",
                            json=_body(reg_env["df"], cond))
        assert r.status_code == 422, f"{name}: {r.text[:200]}"
        d = r.json()["detail"]
        assert d["code"] == "RESEARCH_CONDITION_INVALID", name
        det = d["detail"]
        assert det["action"] == cond["action"], name
        key = "direction" if "direction" in cond else (
            "role" if "role" in cond else (
                "proximity_pct" if "proximity_pct" in cond else "lookback"))
        assert det["field"].endswith(f".{key}"), (name, det["field"])
        assert key in det["value"] or det["value"] is not None, name
        assert det["legal_range"], f"{name}: legal_range 非空"
        if legal_hint is not None:
            assert det["legal_range"] == legal_hint, name

    def test_translate_product_carries_registered_keys(
            self, api_client, reg_env):
        """translate 产物携带恰登记键（入口级 D3——经 API 登记的参数
        全链进产物基因）：touch_bounce 全参→恰 {type, direction,
        proximity_pct}；role_reversal 全参→恰 {type, role, lookback}。"""
        import json as _json
        from core.persistence.repository.strategy import get_strategy
        _wire(api_client, reg_env)
        hid_tb = api_client.post("/api/research/hypotheses", json=_body(
            reg_env["df"],
            _tb(direction="support", proximity_pct=0.02))).json()[
            "hypothesis_id"]
        hid_rr = api_client.post("/api/research/hypotheses", json=_body(
            reg_env["df"], _rr(role="support", lookback=20))).json()[
            "hypothesis_id"]
        for hid, want_keys in (
                (hid_tb, {"type", "direction", "proximity_pct"}),
                (hid_rr, {"type", "role", "lookback"})):
            out = translate_hypothesis(
                reg_env["db"], data_dir=reg_env["data_dir"],
                hypothesis_id=hid)
            dna = _json.loads(
                get_strategy(reg_env["db"], out["strategy_id"])["dna_json"])
            entries = [g for g in dna["signal_genes"]
                       if g["role"] == "entry_trigger"]
            cond = entries[0]["condition"]
            assert set(cond) == want_keys, (hid, set(cond))


    def test_bool_coercion_api_surface_recorded(self, api_client, reg_env):
        """bool coercion API 面现状记录（developer 裁决 b，2026-10-27
        维持现状）：pydantic 平台级 bool→数值 coercion（True→1.0）使
        服务层 bool 拒分支在 API 面结构上不可达——经 API 传 True 登记
        200 且参数被洗为 1.0 落进产物（与直调面拒构成两面分层：API=
        schema 先洗的平台行为，直调=服务层 isinstance 防线，锚定于
        test_bool_rejected_in_service_layer）。裁决理由：四键单独
        Strict 会与 target/window 等同 schema 字段行为分叉；True→1.0
        落合法值域无危险行为面；R4 window 先例同款家族一致。此锚锁
        现状防静默漂移——若未来统一改 Strict 收口，本锚红出即迁移
        提示（连同 test_bool_rejected_in_service_layer 的直调面语义
        一起重审）。"""
        import json as _json
        from core.persistence.repository.strategy import get_strategy
        _wire(api_client, reg_env)
        hid = api_client.post("/api/research/hypotheses", json=_body(
            reg_env["df"], _tb(proximity_pct=True))).json()["hypothesis_id"]
        hid2 = api_client.post("/api/research/hypotheses", json=_body(
            reg_env["df"], _rr(lookback=True))).json()["hypothesis_id"]
        for hid, key, coerced in ((hid, "proximity_pct", 1.0),
                                  (hid2, "lookback", 1)):
            out = translate_hypothesis(
                reg_env["db"], data_dir=reg_env["data_dir"],
                hypothesis_id=hid)
            dna = _json.loads(
                get_strategy(reg_env["db"], out["strategy_id"])["dna_json"])
            cond = [g for g in dna["signal_genes"]
                    if g["role"] == "entry_trigger"][0]["condition"]
            assert cond[key] == coerced, \
                f"{key}: True 经 coercion 落产物为 {coerced}（现状记录）"


class TestR1ParamServiceDirectCall:
    """服务层直调面（register_hypothesis 直调——bool 拒分支在 API 面
    被 pydantic coercion 前置洗白（True→1.0 合法值放行），bool 拒的
    触达证明须走直调面；API 面 bool 行为的处置裁决见 review2 回报）。"""

    def test_bool_rejected_in_service_layer(self, tmp_path):
        """proximity_pct=True/lookback=True 服务层直调拒（isinstance
        bool 分支——直调面/内部链真实触达；API 面经 pydantic 转 1.0
        放行为现状披露，处置待裁决）。"""
        from core.analysis.errors import ResearchError
        db = tmp_path / "q2.db"
        init_db_ext(db)
        ranges = [{"symbol": SYMBOL, "timeframe": "4h",
                   "start": "2024-01-01T00:00:00Z",
                   "end": "2024-02-01T00:00:00Z"}]
        for cond, key in (
                (_tb(proximity_pct=True), "proximity_pct"),
                (_rr(lookback=True), "lookback")):
            with pytest.raises(ResearchError) as ei:
                register_hypothesis(
                    db,
                    conditions={"when_conditions": [cond],
                                "then_conditions": [
                                    {"subject": "close", "action": "lt",
                                     "target": -1}]},
                    statement="bool 拒", data_ranges=ranges,
                    strategy_params={"stop_loss": 0.05})
            assert ei.value.code == "RESEARCH_CONDITION_INVALID", key
            assert ei.value.detail["field"].endswith(f".{key}"), key
