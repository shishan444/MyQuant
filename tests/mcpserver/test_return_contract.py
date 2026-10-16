"""MCP 工具返回契约自检测试（unify-tool-return-contract 任务 10）。

契约四规则对 readonly 13 + research 工具全集的固定样本断言：
①同一工具首次/幂等返回结构一致（验证族在 test_validation_payload_
contract 独立承载——S1a-f）；②身份信息回显（翻译三键在
test_translate_receipt 承载——S2a-f）；③清单类输出统一数组形制；
④系统自主决定（跳过/降级/分页）附原因。

基座：_FakeMcp/_FakeApi 捕获模式（test_tools_passthrough 先例）。
V2-V6 断言面由前序任务的独立测试文件承载（S1/S2/S5/S6/S7/S8/S12/
S3/S4 锚），本文件收口全集四规则+V8 存量逐字节锚。
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

pytestmark = [pytest.mark.integration]

from mcpserver.tools import register_readonly_tools, register_research_tools

from conftest import _FakeMcp, RecordingFakeApi  # 同目录（pytest prepend）


def _all_tools() -> dict[str, Any]:
    mcp, api = _FakeMcp(), RecordingFakeApi()
    register_readonly_tools(mcp, api)
    register_research_tools(mcp, api)
    return mcp.registered


class TestToolInventory:
    """全集在场锚：readonly 13 + research 工具全部注册。"""

    def test_full_inventory_registered(self):
        tools = _all_tools()
        readonly_expected = {
            "list_data_sources", "list_strategies", "get_strategy",
            "list_evolution_tasks", "list_indicators", "list_signal_columns",
            "union_balance", "get_market_snapshot", "list_hypotheses",
            "get_hypothesis", "get_research_log", "get_analysis_task",
            "get_validation_report", "list_feature_requests",
        }
        research_expected = {
            "run_analysis", "inspect_cases", "query_indicator",
            "register_hypothesis", "transition_hypothesis",
            "run_isolated_validation", "run_random_window_test",
            "run_staged_validation", "run_backtest",
            "register_formula_plugin", "register_model_plugin",
            "translate_hypothesis_to_strategy", "submit_feature_request",
            "trigger_evolution", "poll_evolution_task",
        }
        for name in readonly_expected | research_expected:
            assert name in tools, f"工具 {name} 未注册"


class TestListArrayShape:
    """规则③：清单类输出统一数组形制（透传面锚——GAP-05 落点在
    MCP 协议层包裹形制，HTTP 层已标准；断言锁定透传不变形，复现才修
    （A2 目标形制 {items:[...]}））。"""

    @pytest.mark.parametrize("tool_name,url,fixed", [
        ("list_indicators", "/api/research/indicators",
         [{"name": "RSI", "params": {}}]),
        ("list_signal_columns", "/api/research/signals",
         [{"column": "rsi_14"}]),
        ("list_data_sources", "/api/data/available-sources",
         {"sources": [{"symbol": "BTCUSDT"}]}),
        ("list_hypotheses", "/api/research/hypotheses",
         {"items": [{"hypothesis_id": "h1"}], "total": 1}),
        ("list_evolution_tasks", "/api/evolution/tasks",
         {"items": [{"task_id": "t1"}], "total": 1}),
        ("list_feature_requests", "/api/research/feature-requests",
         {"items": [{"request_id": "f1"}], "total": 1}),
    ], ids=["indicators", "signals", "data_sources",
            "hypotheses", "evolution_tasks", "feature_requests"])
    def test_list_tools_pass_through_array(self, tool_name, url, fixed):
        """清单工具返回体=API 层固定样本原样透传（数组/对象形制不
        在 MCP 层被包裹变形）。"""
        mcp, api = _FakeMcp(), RecordingFakeApi({url: fixed})
        register_readonly_tools(mcp, api)
        result = mcp.registered[tool_name]()
        assert result == fixed, \
            f"{tool_name} 透传变形：{result!r} != {fixed!r}"

    def test_list_strategies_dict_items_form(self):
        """策略列表（dict 形 items）：折叠变形是既有设计（D8 折叠后
        条目无 dna），但形制仍为 {items:[...]} 数组形——规则③的
        适用面（list_strategies 不在透传参数化里，因其有意变形）。"""
        mcp, api = _FakeMcp(), RecordingFakeApi({
            "/api/strategies": {"items": [{"strategy_id": "s1",
                                           "name": "n", "symbol": "BTCUSDT",
                                           "timeframe": "4h"}],
                                "total": 1}})
        register_readonly_tools(mcp, api)
        result = mcp.registered["list_strategies"]()
        assert isinstance(result, dict) and isinstance(result["items"], list)
        assert result["total"] == 1


class TestAutonomyDisclosure:
    """规则④：系统自主决定附原因——工具描述披露锚（skipped/
    分页/降级的可观测性声明在描述文本在场）。"""

    _TOOLS_SRC = Path("mcpserver/tools.py").read_text(encoding="utf-8")

    def test_skip_disclosure_in_docs(self):
        """跳过原因披露全集锚：三枚举值在工具描述源码在场（S5c 在
        test_screen_patterns 详测，此处防全集漂移）。"""
        src = self._TOOLS_SRC
        for token in ("no_default_params", "compute_failed",
                      "no_covered_actions"):
            assert token in src, f"工具描述缺跳过原因枚举: {token}"

    def test_error_channel_never_raises(self):
        """tool_safe 错误通道：API 失败转 {"error": {...}} 不 raise
        （自主失败可观测——统一错误通道 0.3.2 契约在全集回归）。"""
        from mcpserver.api_client import ApiError
        from mcpserver import errors as mcp_errors

        class _FailingApi:
            def get(self, url, params=None):
                raise ApiError(422, "RESEARCH_X", "m", None)

        mcp, api = _FakeMcp(), _FailingApi()
        register_readonly_tools(mcp, api)
        result = mcp.registered["list_indicators"]()
        assert isinstance(result, dict) and "error" in result
        assert result["error"]["code"] == "RESEARCH_X"


class TestCountAndParityReentry:
    """V5 计数断言与 V6 page 三层断言的契约再入口（独立文件详测，
    此处全集在场锚防漂移）。"""

    def test_indicator_count_assertions_exist(self):
        from core.features.registry import INDICATOR_REGISTRY
        assert len(INDICATOR_REGISTRY) == 48

    def test_research_tools_tier_registered(self):
        """两档工具集非空（自足注册——TOOL_TIERS 由装配函数运行时填充
        （tools.py），无装配副作用的单跑场景由本用例显式注册，消除对
        同 session 先前测试的顺序依赖；set.add 幂等，全量重复注册无害）。"""
        from conftest import _FakeApi, _FakeMcp  # 同目录（pytest prepend）
        from mcpserver.tools import (
            TOOL_TIERS, register_readonly_tools, register_research_tools)
        mcp, api = _FakeMcp(), _FakeApi()
        register_readonly_tools(mcp, api)
        register_research_tools(mcp, api)
        assert TOOL_TIERS["research"], "research 档工具集非空"
        assert TOOL_TIERS["readonly"], "readonly 档工具集非空"

    def test_receipt_bool_not_penetrating(self):
        """C6（security F-5）：bool 不再按 int 穿透回执——bool 是 int
        子类，穿透会以 1/0 冒充合法 bar 数/时刻；真值 3/600 照常回显。
        两函数多位（max_hold/time_exit_utc/risk_fraction）全验；六键
        形制（K1 接线：position_mode/risk_fraction 进回执）。"""
        from core.analysis.hypothesis_to_strategy import (
            _identity_reflection, _translate_receipt)
        r = _translate_receipt("long", True, True, "fixed", True, False)
        assert r["max_hold"] is None and r["time_exit_utc"] is None
        assert r["risk_fraction"] is None, "bool 冒充 1.0 比例须拒"
        r2 = _translate_receipt(
            "long", 3, 600, "risk_budget", 0.04, False)
        assert r2["max_hold"] == 3 and r2["time_exit_utc"] == 600
        assert r2["position_mode"] == "risk_budget"
        assert r2["risk_fraction"] == 0.04
        r3 = _translate_receipt("long", 3, 600, "bogus", 0.04, False)
        assert r3["position_mode"] == "fixed", "非法模式收敛 fixed"
        row = {"dna_json": '{"risk_genes": {"direction": "long", '
                            '"max_hold": true, "time_exit_utc": true}}'}
        ir = _identity_reflection(row)
        assert ir["max_hold"] is None and ir["time_exit_utc"] is None
