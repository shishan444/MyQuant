"""旧键路径 timeframe×插件列组合边界红锚（dna-strong-typing-and-
disclosure-hygiene 件⑧/Q-2=补，design 3.3 / specs ai-research-tools
「插件列与高周期 timeframe 组合边界」八路径覆盖域）。

契约出处：event_study 与 regime_split 的旧键 condition 路径补
「timeframe 键×fx_/model_ 前缀列引用」组合边界判定——与新键
conditions 路径校验本体（analysis.py:128-143）同码同文案同 detail
（RESEARCH_CONDITION_INVALID，detail 携带 plugin_columns 与 hint
「移除 timeframe 键（基准周期求值）或改用内置指标列引用」）。
现状缺位：event_study 旧键有插件列接线但无 timeframe 互斥（高周期
键被吞按基准求值/先撞可达性码）；regime_split 旧键无接线无互斥。

边界写明：regime_split 旧键的插件列整体支持状态维持现状
（conditions 形式支持），本件只补组合守卫不扩接线。

红锚语义：两路径在守卫落地前为红——event_study 现况返回可达性
错误码（RESEARCH_PLUGIN_COLUMN_NOT_FOUND）或静默基准求值而非组合
边界码；regime_split 现况无任何组合拒绝。护栏（G 组）双态绿：同
形制不带 timeframe 键的插件列条件不触组合守卫（走既有通道语义）。
"""
from __future__ import annotations

from pathlib import Path

import pytest

pytestmark = [pytest.mark.integration]

from tests.api.test_conditions_combination import _body, _wire, combo_env

TF_PLUGIN_COND = {
    "subject": "fx_demo_mix_col", "action": "gt", "target": 0.5,
    "timeframe": "1d",
}


class TestLegacyKeyTfPluginMix:
    def test_event_study_legacy_key_mix_rejected(self, api_client,
                                                 combo_env):
        """event_study 旧键：timeframe 键×fx_ 前缀 subject → 422
        RESEARCH_CONDITION_INVALID（detail 含 plugin_columns+hint）
        ——非可达性码、非静默基准求值。"""
        _wire(api_client, combo_env)
        resp = api_client.post("/api/research/analysis", json=_body(
            "event_study", {"condition": TF_PLUGIN_COND}))
        assert resp.status_code == 422, (
            f"现状 {resp.status_code}：timeframe 键被吞（静默基准求值）"
            f"或先撞可达性码——组合边界守卫未生效：{resp.text[:300]}")
        detail = resp.json()["detail"]
        assert detail["code"] == "RESEARCH_CONDITION_INVALID"
        assert "fx_demo_mix_col" in detail["detail"]["plugin_columns"]
        assert "移除 timeframe" in detail["detail"]["hint"]

    def test_regime_split_legacy_key_mix_rejected(self, api_client,
                                                  combo_env):
        """regime_split 旧键：同款组合 → 同码同 detail（现状无任何
        组合拒绝——静默）。"""
        _wire(api_client, combo_env)
        resp = api_client.post("/api/research/analysis", json=_body(
            "regime_split", {"condition": TF_PLUGIN_COND}))
        assert resp.status_code == 422, (
            f"现状 {resp.status_code}：无组合边界守卫（静默通过装载）"
            f"——守卫未生效：{resp.text[:300]}")
        detail = resp.json()["detail"]
        assert detail["code"] == "RESEARCH_CONDITION_INVALID"
        assert "fx_demo_mix_col" in detail["detail"]["plugin_columns"]

    def test_plugin_without_tf_not_caught_by_mix_guard(
            self, api_client, combo_env):
        """护栏：插件列条件不带 timeframe 键不触组合守卫——走既有
        通道语义（可达性校验：fx_demo_mix_col 未注册 →
        RESEARCH_PLUGIN_COLUMN_NOT_FOUND，非组合边界码）。"""
        _wire(api_client, combo_env)
        cond = {k: v for k, v in TF_PLUGIN_COND.items() if k != "timeframe"}
        resp = api_client.post("/api/research/analysis", json=_body(
            "event_study", {"condition": cond}))
        assert resp.status_code == 422
        assert resp.json()["detail"]["code"] == \
            "RESEARCH_PLUGIN_COLUMN_NOT_FOUND"

    def test_plugin_with_tf_equal_base_exempted(self, api_client,
                                                 combo_env):
        """CARD-03 豁免锚（三分支「等于基准」半边）：timeframe 键值
        ==基准周期（4h）与缺省同语义（键归一）——不触组合守卫，走
        基准求值通道（可达性校验先出：未注册列 →
        RESEARCH_PLUGIN_COLUMN_NOT_FOUND，非组合边界码）。与上一用例
        （无键→可达性码）、拒绝锚（1d≠4h→组合边界码）构成三分支
        对照：无键/等于基准同语义，严格高于才组合边界拒绝。"""
        _wire(api_client, combo_env)
        cond = dict(TF_PLUGIN_COND, timeframe="4h")  # ==基准周期
        resp = api_client.post("/api/research/analysis", json=_body(
            "event_study", {"condition": cond}))
        assert resp.status_code == 422, (
            f"tf==base 豁免后应走可达性校验（未注册列 422），"
            f"现状 {resp.status_code}：{resp.text[:200]}")
        assert resp.json()["detail"]["code"] == \
            "RESEARCH_PLUGIN_COLUMN_NOT_FOUND", (
            "tf==base 被组合边界误拒（豁免未生效——键归一与缺省"
            "同语义，三分支契约）")

    def test_new_key_mix_guard_not_regressed(self, api_client, combo_env):
        """护栏：新键 conditions 路径既有互斥不回退（校验本体仍在
        新键路径生效）。"""
        _wire(api_client, combo_env)
        resp = api_client.post("/api/research/analysis", json=_body(
            "condition_stats", {
                "indicator": "EMA", "params": {"period": 10},
                "conditions": [TF_PLUGIN_COND]}))
        assert resp.status_code == 422
        detail = resp.json()["detail"]
        assert detail["code"] == "RESEARCH_CONDITION_INVALID"
        assert "fx_demo_mix_col" in detail["detail"]["plugin_columns"]
