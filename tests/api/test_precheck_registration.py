"""登记自动挂接预检测试（add-hypothesis-precheck tasks 3.1/3.2 验证锚）。

四例（3.1）：正常登记带警告键（锚=分析段触发数）/预检注入异常降级
（spy 触达证明）/幂等命中不跑预检/多区间最差锚。
回归（3.2）：校验链错误码序（结构校验先于预检——非法条件零预检调用）
+ provenance 程序化登记同样带警告段。
"""
import importlib
import json

import numpy as np
import pandas as pd
import pytest

pytestmark = [pytest.mark.integration]

# 模块本体（importlib 取 sys.modules 实体——聚合包的 from-import 使
# 包属性 precheck_hypothesis 被同名函数遮蔽，直接 from import 拿到函数）
precheck_mod = importlib.import_module(
    "core.analysis.templates.precheck_hypothesis")
from core.persistence.db_ext import init_db_ext
from core.persistence.repository import research as rr

SYMBOL = "BTCUSDT"


@pytest.fixture()
def reg_env(tmp_path):
    """900 bar 4h 阶梯价格数据（A 段 [650,750) 恒 100、B 段 [750,900)
    恒 200——阈值 150 精确切分两段的触发密度）+ 独立 db。"""
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
    db = tmp_path / "quant.db"
    init_db_ext(db)
    return {"data_dir": data_dir, "db": db, "df": df}


def _wire(api_client, env):
    api_client.app.state.db_path = env["db"]
    api_client.app.state.data_dir = env["data_dir"]


def _ranges(df, two=False):
    end = df.index[-1].isoformat()
    if not two:
        return [{"symbol": SYMBOL, "timeframe": "4h",
                 "start": df.index[650].isoformat(), "end": end}]
    mid = df.index[750].isoformat()
    return [
        {"symbol": SYMBOL, "timeframe": "4h",
         "start": df.index[650].isoformat(), "end": mid},   # A 段：0 触发
        {"symbol": SYMBOL, "timeframe": "4h",
         "start": mid, "end": end},                          # B 段：150 触发
    ]


def _payload(df, when=None, ranges=None, provenance=None):
    body = {
        "conditions": {
            "when_conditions": when or [
                {"subject": "close", "action": "gt", "target": 0}],
            "then_conditions": [
                {"subject": "close", "action": "gt", "target": 0}],
        },
        "statement": "预检挂接测试假设",
        "data_ranges": ranges or _ranges(df),
    }
    if provenance is not None:
        body["provenance"] = provenance
    return body


@pytest.fixture()
def spy(monkeypatch):
    """run_registration_precheck 探针（服务内延迟 import 取模块属性，
    patch 模块级目标生效）——触达证明：不以静默通过冒充降级验证。
    trigger_policy 透传（trigger-policy 卡2：登记挂接传参通道）。"""
    calls = []
    real = precheck_mod.run_registration_precheck   # 先存原函数（防自递归）

    def _wrapper(db_path, data_dir, when, data_ranges, trigger_policy=None):
        calls.append({"when": json.dumps(when, default=str),
                      "n_ranges": len(data_ranges),
                      "trigger_policy": trigger_policy})
        return real(db_path, data_dir, when, data_ranges,
                    trigger_policy=trigger_policy)

    monkeypatch.setattr(precheck_mod, "run_registration_precheck", _wrapper)
    return calls


class TestRegistrationPrecheck:
    def test_register_returns_warnings_analysis_anchor(self, api_client, reg_env, spy):
        """正常登记：警告键 + 锚=分析段触发数（阶梯数据 B 段满密度）
        + 挂接留痕（CARD-03，spec R4：entry_type=precheck 不挂假设、
        ref_id 区分双锚来源、summary 携判定锚与逐区间明细）。"""
        _wire(api_client, reg_env)
        resp = api_client.post("/api/research/hypotheses",
                               json=_payload(reg_env["df"]))
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["duplicated"] is False
        w = body["precheck_warnings"]
        assert w["verdict"] == "pass"          # 249 bar 全触发 ≥5
        assert w["analysis_segment_trades"] == 249
        assert w["threshold"] == 5
        assert w["per_range"][0]["n_triggers"] == 249
        assert len(spy) == 1                   # 触达证明

        entries = [e for e in rr.list_research_log(
            reg_env["db"], hypothesis_id=None)
            if e["entry_type"] == "precheck"]
        assert len(entries) == 1               # 挂接留痕恰一条
        entry = entries[0]
        assert entry["hypothesis_id"] is None  # 不挂假设（R4/S11 结构性）
        assert entry["ref_id"] == "registration_precheck"   # 双锚来源区分
        summary = json.loads(entry["summary"])
        assert summary["verdict"] == "pass"
        assert summary["analysis_segment_trades"] == 249
        assert len(summary["per_range"]) == 1
        assert summary["per_range"][0]["n_triggers"] == 249
        # 区间原值回读（审计可对上登记 data_ranges）
        assert json.loads(entry["data_ranges"]) == _ranges(reg_env["df"])

    def test_register_degrades_when_precheck_raises(
            self, api_client, reg_env, spy, monkeypatch):
        """预检注入异常：登记照常+无警告键+spy 触达（降级非静默跳过）
        + 失败预检不留痕（spec 场景限定「成功执行」才落日志）。"""
        _wire(api_client, reg_env)

        def _boom(*a, **k):
            raise RuntimeError("注入的预检故障")

        monkeypatch.setattr(precheck_mod, "run_registration_precheck", _boom)
        resp = api_client.post("/api/research/hypotheses",
                               json=_payload(reg_env["df"]))
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["precheck_warnings"] is None   # 降级：不携带警告
        # 假设已持久化（受保护状态重查询比对）
        hyp = rr.get_hypothesis(reg_env["db"], body["hypothesis_id"])
        assert hyp is not None and hyp["status"] == "active"
        # 降级无痕：precheck 条目零新增
        assert not any(e["entry_type"] == "precheck" for e in
                       rr.list_research_log(reg_env["db"],
                                            hypothesis_id=None))

    def test_idempotent_hit_skips_precheck(self, api_client, reg_env, spy):
        """幂等命中：duplicated=true、无警告键、预检仅首次调用
        + 幂等路径不留新痕（首登留痕恰一条，重复登记不新增）。"""
        _wire(api_client, reg_env)
        first = api_client.post("/api/research/hypotheses",
                                json=_payload(reg_env["df"])).json()
        assert len(spy) == 1
        second = api_client.post("/api/research/hypotheses",
                                 json=_payload(reg_env["df"])).json()
        assert second["hypothesis_id"] == first["hypothesis_id"]
        assert second["duplicated"] is True
        assert second["precheck_warnings"] is None  # 幂等：不携带警告
        assert len(spy) == 1                   # 幂等路径零预检
        entries = [e for e in rr.list_research_log(
            reg_env["db"], hypothesis_id=None)
            if e["entry_type"] == "precheck"]
        assert len(entries) == 1               # 仅首登留痕，幂等不新增

    def test_multi_range_worst_anchor(self, api_client, reg_env, spy):
        """多区间最差锚：A 段 0 触发 → 锚=0 → reject_suggestion +
        警告段披露逐区间明细；留痕 per_range 同步披露明细（审计走留痕
        ——design「返回体精简、详细留痕」分工）。"""
        _wire(api_client, reg_env)
        resp = api_client.post("/api/research/hypotheses", json=_payload(
            reg_env["df"],
            when=[{"subject": "close", "action": "gt", "target": 150}],
            ranges=_ranges(reg_env["df"], two=True)))
        assert resp.status_code == 200, resp.text
        w = resp.json()["precheck_warnings"]
        assert [x["n_triggers"] for x in w["per_range"]] == [0, 149]
        assert w["analysis_segment_trades"] == 0    # 最小值进锚
        assert w["verdict"] == "reject_suggestion"
        # 留痕携带逐区间明细（含最差锚——审计「预检结论 vs 实际考试」
        # 对照依据）
        entry = [e for e in rr.list_research_log(
            reg_env["db"], hypothesis_id=None)
            if e["entry_type"] == "precheck"][0]
        summary = json.loads(entry["summary"])
        assert summary["verdict"] == "reject_suggestion"
        assert summary["analysis_segment_trades"] == 0
        assert [x["n_triggers"] for x in summary["per_range"]] == [0, 149]


class TestRegistrationRegression:
    def test_structure_validation_precedes_precheck(
            self, api_client, reg_env, spy):
        """校验链错误码序：非法条件（缺 target）422 且预检零调用——
        预检不吞校验异常。"""
        _wire(api_client, reg_env)
        resp = api_client.post("/api/research/hypotheses", json=_payload(
            reg_env["df"],
            when=[{"subject": {"indicator": "GHOST_IND", "params": {},
                               "field": "x"},
                   "action": "gt", "target": 0}]))
        assert resp.status_code == 422, resp.text
        assert "RESEARCH_CONDITION_INVALID" in resp.text
        assert len(spy) == 0

    def test_provenance_path_carries_warnings(self, api_client, reg_env, spy):
        """程序化登记（screen 谱系回链——G3 失实发生地）同样带警告段。"""
        _wire(api_client, reg_env)
        # 造一个 succeeded 扫描任务记录（provenance 校验需任务存在且带区间）
        rr.save_analysis_task(
            reg_env["db"], task_id="ant_screen01",
            template="screen_patterns",
            params=json.dumps({
                "data_range": {"symbol": SYMBOL, "timeframe": "4h",
                               "start": reg_env["df"].index[650].isoformat(),
                               "end": reg_env["df"].index[-1].isoformat()}}))
        resp = api_client.post("/api/research/hypotheses", json=_payload(
            reg_env["df"],
            provenance={"task_id": "ant_screen01",
                        "candidate_fingerprint": "fp_test_01"}))
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["precheck_warnings"]["verdict"] == "pass"
        # 谱系回链日志同事务落库（既有语义不因预检改变）
        entries = rr.list_research_log(reg_env["db"], hypothesis_id=None)
        assert any(e["entry_type"] == "screen_use"
                   for e in entries)
