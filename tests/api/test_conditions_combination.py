"""组合条件统计路由级测试（add-research-analysis-tools 0.4.1 任务 1.5）。

B2 键组与组合语义：二选一互斥、条数域 1..5、组合统计独立对照（手动
pandas 交集核算）、零样本合法、trigger_policy 作用于合并掩码、payload
echo、event_study 两形式装载差异可辨（S2：conditions 前扩+warmup_bars
披露 / 旧键 _load_df 冻结+payload 键集冻结）、装载引用并集（S2）、
subject 前置检查逐条化、TestClient 留痕全链。
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import pytest

pytestmark = [pytest.mark.integration]

from core.analysis.registry_meta import derive_warmup
from core.persistence.db_ext import init_db_ext

SYMBOL = "BTCUSDT"


@pytest.fixture()
def combo_env(tmp_path: Path):
    """900 bar 4h 合成数据（先降后升趋势）+ 独立 db。"""
    rng = np.random.default_rng(42)
    n = 900
    idx = pd.date_range("2024-01-01", periods=n, freq="4h", tz="UTC",
                        name="timestamp")
    seg1 = np.linspace(68000, 58000, 550)
    seg2 = np.linspace(58000, 72000, n - 550)
    close = np.concatenate([seg1, seg2]) + rng.normal(0, 120, n)
    df = pd.DataFrame({
        "open": close, "high": close + 100, "low": close - 100,
        "close": close, "volume": rng.uniform(100, 1000, n), "trades": 100,
    }, index=idx)
    data_dir = tmp_path / "market"
    data_dir.mkdir()
    df.to_parquet(data_dir / f"{SYMBOL}_4h.parquet")
    db = tmp_path / "quant.db"
    init_db_ext(db)
    return {"data_dir": data_dir, "db": db, "df": df}


def _wire(client, env):
    client.app.state.db_path = env["db"]
    client.app.state.data_dir = env["data_dir"]


def _body(template, params, **range_extra):
    rng = {"symbol": SYMBOL, "timeframe": "4h",
           "start": "2024-04-11T00:00:00Z", "end": "2024-05-01T00:00:00Z"}
    rng.update(range_extra)
    return {"template": template, "params": params, "data_range": rng}


GT_CLOSE = {"subject": "close", "action": "gt", "target": 62000.0}
LT_VOL = {"subject": "volume", "action": "lt", "target": 600.0}
ALWAYS_TRUE = {"subject": "close", "action": "gt", "target": 0.0}


class TestKeyGroups:
    """二选一键组（1.3）：互斥拒、缺组点名、旧组行为不变。"""

    def test_condition_and_conditions_mutually_exclusive(self, api_client,
                                                         combo_env):
        _wire(api_client, combo_env)
        resp = api_client.post("/api/research/analysis", json=_body(
            "condition_stats",
            {"indicator": "EMA", "params": {"period": 10},
             "condition": GT_CLOSE, "conditions": [GT_CLOSE]}))
        assert resp.status_code == 422, resp.text
        detail = resp.json()["detail"]
        assert detail["code"] == "RESEARCH_PARAM_INVALID"
        assert "互斥" in detail["message"]

    def test_event_study_conditions_only_form_accepted(self, api_client,
                                                       combo_env):
        """新组独立成请求（event_study 仅 conditions）——合法路径。"""
        _wire(api_client, combo_env)
        resp = api_client.post("/api/research/analysis", json=_body(
            "event_study", {"conditions": [GT_CLOSE], "min_events": 5}))
        assert resp.status_code == 200, resp.text
        payload = resp.json()["payload"]
        assert "conditions" in payload
        assert "condition" not in payload

    def test_old_key_form_unchanged_shape(self, api_client, combo_env):
        """旧组形制行为不变：condition_stats 旧三元组照常可用，payload
        形制与旧键一致（indicator/params/condition 键、无 conditions 键）。"""
        _wire(api_client, combo_env)
        resp = api_client.post("/api/research/analysis", json=_body(
            "condition_stats",
            {"indicator": "EMA", "params": {"period": 10},
             "condition": GT_CLOSE, "min_samples": 1}))
        assert resp.status_code == 200, resp.text
        payload = resp.json()["payload"]
        assert "condition" in payload
        assert "conditions" not in payload


class TestConditionsCountDomain:
    """条数域 1..5（1.4）：空数组/超限 422 披露上限；合法 5 条通过。"""

    @pytest.mark.parametrize(
        ("conditions", "kind"),
        [([], "空数组"), ([GT_CLOSE] * 6, "6 条超限")],
        ids=["空数组拒", "超上限拒"])
    def test_out_of_domain_rejected(self, api_client, combo_env, conditions,
                                    kind):
        _wire(api_client, combo_env)
        resp = api_client.post("/api/research/analysis", json=_body(
            "condition_stats",
            {"conditions": conditions, "min_samples": 1}))
        assert resp.status_code == 422, resp.text
        detail = resp.json()["detail"]
        assert detail["code"] == "RESEARCH_PARAM_INVALID"
        assert detail["detail"]["legal_range"] == "1..5"

    def test_five_conditions_pass(self, api_client, combo_env):
        _wire(api_client, combo_env)
        resp = api_client.post("/api/research/analysis", json=_body(
            "condition_stats",
            {"conditions": [ALWAYS_TRUE] * 5, "min_samples": 1}))
        assert resp.status_code == 200, resp.text

    def test_malformed_entry_rejected(self, api_client, combo_env):
        """条目形制非法（缺 target 键）结构化拒绝——沿单条件链三键
        结构。"""
        _wire(api_client, combo_env)
        resp = api_client.post("/api/research/analysis", json=_body(
            "condition_stats",
            {"conditions": [{"subject": "close", "action": "gt"}],
             "min_samples": 1}))
        assert resp.status_code == 422, resp.text
        detail = resp.json()["detail"]
        assert detail["code"] == "RESEARCH_CONDITION_INVALID"
        assert "subject/action/target" in detail["message"]


class TestCombinationStatistics:
    """组合统计正确性：独立对照（手动 pandas 交集核算——期望不经被测
    实现）；零样本合法；echo；warmup 装载锚。"""

    def test_combined_stats_match_manual_intersection(self, api_client,
                                                      combo_env):
        """两条件交集 hit_count == 手动 pandas 表达式在统计段的交集
        计数（独立推导）。"""
        _wire(api_client, combo_env)
        resp = api_client.post("/api/research/analysis", json=_body(
            "condition_stats",
            {"conditions": [GT_CLOSE, LT_VOL], "min_samples": 1}))
        assert resp.status_code == 200, resp.text
        payload = resp.json()["payload"]

        df = combo_env["df"]
        start = pd.Timestamp("2024-04-11T00:00:00Z")
        end = pd.Timestamp("2024-05-01T00:00:00Z")
        # 装载闭端过滤（load_and_prepare_df 既有语义）：end bar 计入
        # 统计段——in_stat = [start, end]
        in_stat = (df.index >= start) & (df.index <= end)
        manual = ((df["close"] > 62000.0) & (df["volume"] < 600.0)
                  & in_stat)
        assert payload["hit_count"] == int(manual.sum())
        assert payload["hit_rate"] == pytest.approx(
            float(manual.sum() / in_stat.sum()))
        # echo：conditions 原样数组
        assert payload["conditions"] == [GT_CLOSE, LT_VOL]

    def test_zero_sample_legal_with_min_samples_disabled(self, api_client,
                                                         combo_env):
        """组合零样本为合法结果（min_samples=0 禁用小样本检查的既有
        合法语义）——样本量 0 原样返回不视为错误。"""
        _wire(api_client, combo_env)
        impossible = {"subject": "close", "action": "gt",
                      "target": 1e12}  # 恒 False 条
        resp = api_client.post("/api/research/analysis", json=_body(
            "condition_stats",
            {"conditions": [GT_CLOSE, impossible], "min_samples": 0}))
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["sample_size"] == 0
        assert body["payload"]["hit_count"] == 0

    def test_trigger_policy_applies_to_combined_mask(self, api_client,
                                                     combo_env):
        """trigger_policy 作用于 AND 合并掩码：first_true 下 hit_count
        == 合并掩码的极大连续 True 段数（手动游程核算——独立推导）。"""
        _wire(api_client, combo_env)
        policy = {"mode": "first_true"}
        resp = api_client.post("/api/research/analysis", json=_body(
            "condition_stats",
            {"conditions": [GT_CLOSE], "trigger_policy": policy,
             "min_samples": 1}))
        assert resp.status_code == 200, resp.text
        payload = resp.json()["payload"]
        assert payload["trigger_policy"]["mode"] == "first_true"

        df = combo_env["df"]
        start = pd.Timestamp("2024-04-11T00:00:00Z")
        end = pd.Timestamp("2024-05-01T00:00:00Z")
        seg = (df.index >= start) & (df.index <= end)
        m = (df["close"] > 62000.0) & seg
        runs = int((m & ~m.shift(fill_value=False)).sum())
        assert payload["hit_count"] == runs

    def test_len1_vs_len2_form_self_consistency(self, api_client, combo_env):
        """形制自洽锚：conditions len=1 与 len=2（第二条恒 True）同走
        前扩装载——warmup_bars>0 且口径一致；统计结果一致。"""
        _wire(api_client, combo_env)
        outs = []
        for conds in ([GT_CLOSE], [GT_CLOSE, ALWAYS_TRUE]):
            resp = api_client.post("/api/research/analysis", json=_body(
                "condition_stats",
                {"conditions": conds, "min_samples": 1}))
            assert resp.status_code == 200, resp.text
            outs.append(resp.json()["payload"])
        assert outs[0]["warmup_bars"] > 0
        assert outs[0]["warmup_bars"] == outs[1]["warmup_bars"]
        assert outs[0]["hit_count"] == outs[1]["hit_count"]

    def test_warmup_union_reference_aggregation(self, api_client, combo_env):
        """S2 装载引用并集：加入 SMA 引用条目使 warmup 预算从 200
        floor 升至 derive_warmup(["SMA"])（注册表权威推导——期望值
        不经被测装载路径）。"""
        _wire(api_client, combo_env)
        sma_cond = {
            "subject": {"indicator": "SMA", "params": {"period": 50},
                        "field": "sma"},
            "action": "gt", "target": 0.0}

        def _warmup(conditions):
            resp = api_client.post("/api/research/analysis", json=_body(
                "condition_stats",
                {"conditions": conditions, "min_samples": 1}))
            assert resp.status_code == 200, resp.text
            return resp.json()["payload"]["warmup_bars"]

        base_only = _warmup([GT_CLOSE])
        with_sma = _warmup([GT_CLOSE, sma_cond])
        assert base_only == 200  # 纯数值 target 无指标引用 → floor 200
        assert with_sma == derive_warmup(["SMA"])
        assert with_sma > base_only


class TestEventStudyForms:
    """event_study 两形式：旧键 _load_df 冻结 + payload 键集冻结 /
    conditions 前扩三件套（request_start 切回 + warmup_bars 披露）；
    subject 前置检查逐条化。"""

    def test_old_key_payload_keyset_frozen(self, api_client, combo_env):
        """旧键 payload 键集冻结锚：无 warmup_bars 键、无 conditions
        键（加键即破坏逐字节锚）。"""
        _wire(api_client, combo_env)
        resp = api_client.post("/api/research/analysis", json=_body(
            "event_study", {"condition": GT_CLOSE, "min_events": 5}))
        assert resp.status_code == 200, resp.text
        payload = resp.json()["payload"]
        assert "warmup_bars" not in payload
        assert "conditions" not in payload
        assert "condition" in payload

    def test_conditions_warmup_disclosed(self, api_client, combo_env):
        """conditions 形式三件套：payload 独有 warmup_bars 披露（>0
        ——前扩装载可观察差异于旧键路径）。"""
        _wire(api_client, combo_env)
        resp = api_client.post("/api/research/analysis", json=_body(
            "event_study", {"conditions": [GT_CLOSE], "min_events": 5}))
        assert resp.status_code == 200, resp.text
        payload = resp.json()["payload"]
        assert payload["warmup_bars"] > 0
        assert "conditions" in payload

    def test_subject_check_per_entry(self, api_client, combo_env):
        """subject 前置检查逐条化：第二条字符串 subject 缺列 →
        结构化拒绝（RESEARCH_COLUMN_NOT_AVAILABLE），不以误导性
        「事件数不足」小样本拒绝收场。"""
        _wire(api_client, combo_env)
        bad = {"subject": "no_such_column", "action": "gt", "target": 1}
        resp = api_client.post("/api/research/analysis", json=_body(
            "event_study",
            {"conditions": [GT_CLOSE, bad], "min_events": 5}))
        assert resp.status_code == 422, resp.text
        detail = resp.json()["detail"]
        assert detail["code"] == "RESEARCH_COLUMN_NOT_AVAILABLE"
        assert detail["detail"]["column"] == "no_such_column"

    def test_combined_events_match_manual(self, api_client, combo_env):
        """conditions 交集事件数独立对照：n_events == 手动交集掩码
        （统计段内）的事件数。"""
        _wire(api_client, combo_env)
        window = 6
        resp = api_client.post("/api/research/analysis", json=_body(
            "event_study",
            {"conditions": [GT_CLOSE, LT_VOL], "window": window,
             "min_events": 5}))
        assert resp.status_code == 200, resp.text
        payload = resp.json()["payload"]

        df = combo_env["df"]
        start = pd.Timestamp("2024-04-11T00:00:00Z")
        end = pd.Timestamp("2024-05-01T00:00:00Z")
        in_stat = (df.index >= start) & (df.index <= end)
        m = (df["close"] > 62000.0) & (df["volume"] < 600.0) & in_stat
        # 尾部不完整窗口丢弃（装载帧末端=end bar）：事件 t 保留 ⟺
        # t + window×4h <= end（连续 4h bar 下位置换算）
        keep = df.index <= (end - pd.Timedelta(hours=4 * window))
        n_events = int((m & keep).sum())
        assert payload["n_events"] == n_events
        assert payload["warmup_bars"] > 0


class TestLogTrail:
    """TestClient 留痕全链：conditions 请求照常落 analysis 型研究日志
    （挂 hypothesis_id 由请求决定——None 不挂）。"""

    def test_conditions_request_logged(self, api_client, combo_env):
        import sqlite3
        _wire(api_client, combo_env)
        resp = api_client.post("/api/research/analysis", json=_body(
            "condition_stats",
            {"conditions": [GT_CLOSE, LT_VOL], "min_samples": 1}))
        assert resp.status_code == 200, resp.text
        with sqlite3.connect(combo_env["db"]) as conn:
            rows = conn.execute(
                "SELECT entry_type, ref_id, hypothesis_id FROM research_log "
                "WHERE ref_id = 'condition_stats'").fetchall()
        assert len(rows) == 1
        assert rows[0][0] == "analysis"
        assert rows[0][2] is None  # 未挂假设


class TestCounterProfileRoute:
    """counter_profile 路由双入口可达（0.4.1 任务 5.3）。"""

    def test_condition_form_reachable(self, api_client, combo_env):
        _wire(api_client, combo_env)
        resp = api_client.post("/api/research/analysis", json=_body(
            "counter_profile",
            {"condition": GT_CLOSE, "direction": "long",
             "min_samples": 5, "forward_bars": 12}))
        assert resp.status_code == 200, resp.text
        payload = resp.json()["payload"]
        assert payload["direction"] == "long"
        assert payload["groups"]["conform"]["count"] > 0

    def test_conditions_form_reachable(self, api_client, combo_env):
        _wire(api_client, combo_env)
        resp = api_client.post("/api/research/analysis", json=_body(
            "counter_profile",
            {"conditions": [GT_CLOSE, ALWAYS_TRUE], "direction": "short",
             "min_samples": 5, "forward_bars": 12}))
        assert resp.status_code == 200, resp.text
        assert "conditions" in resp.json()["payload"]

    def test_direction_required_by_key_group(self, api_client, combo_env):
        """键组：condition 与 direction 同组——缺 direction 按该组点名。"""
        _wire(api_client, combo_env)
        resp = api_client.post("/api/research/analysis", json=_body(
            "counter_profile", {"condition": GT_CLOSE}))
        assert resp.status_code == 422, resp.text
        assert resp.json()["detail"]["detail"]["param"] == "direction"

    def test_mixed_direction_rejected(self, api_client, combo_env):
        _wire(api_client, combo_env)
        resp = api_client.post("/api/research/analysis", json=_body(
            "counter_profile",
            {"condition": GT_CLOSE, "direction": "mixed",
             "min_samples": 5}))
        assert resp.status_code == 422, resp.text
        assert resp.json()["detail"]["detail"]["legal_range"] == [
            "long", "short"]
