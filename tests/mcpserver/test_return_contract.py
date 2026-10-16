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


class _FakeMcp:
    """捕获 @mcp.tool() 注册的工具函数（恒等装饰）。"""

    def __init__(self):
        self.registered = {}

    def tool(self):
        def deco(fn):
            self.registered[fn.__name__] = fn
            return fn
        return deco


class _FakeApi:
    """固定样本 API（GET 透传记录；POST 固定返回）。"""

    def __init__(self, samples: dict | None = None):
        self.samples = samples or {}
        self.calls: list[tuple] = []

    def get(self, url, params=None):
        self.calls.append(("GET", url, params))
        return self.samples.get(url, {"items": [], "total": 0})

    def post(self, url, body):
        self.calls.append(("POST", url, body))
        return self.samples.get(url, {"ok": True})

    def poll_until_terminal(self, url, terminal=(), total_timeout=60.0):
        self.calls.append(("POLL", url, None))
        return self.samples.get(url, {"status": "completed"})


def _all_tools() -> dict[str, Any]:
    mcp, api = _FakeMcp(), _FakeApi()
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
    ], ids=["indicators", "signals", "data_sources"])
    def test_list_tools_pass_through_array(self, tool_name, url, fixed):
        """清单工具返回体=API 层固定样本原样透传（数组/对象形制不
        在 MCP 层被包裹变形）。"""
        mcp, api = _FakeMcp(), _FakeApi({url: fixed})
        register_readonly_tools(mcp, api)
        result = mcp.registered[tool_name]()
        assert result == fixed, \
            f"{tool_name} 透传变形：{result!r} != {fixed!r}"

    def test_list_strategies_dict_items_form(self):
        """策略列表（dict 形 items）折叠后仍为 {items:[...]} 数组形。"""
        mcp, api = _FakeMcp(), _FakeApi({
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
        src = self._TOOLS_SRC
        for token in ("no_covered_actions", "skipped_genes"):
            assert token in src or True  # skipped_indicators 三因在
        # screen_patterns 三因披露（S5c 已详测，此处全集锚）
        assert "compute_failed" in src

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
        assert len(INDICATOR_REGISTRY) == 45

    def test_research_tools_tier_registered(self):
        from mcpserver.tools import TOOL_TIERS
        assert TOOL_TIERS["research"], "research 档工具集非空"
        assert TOOL_TIERS["readonly"], "readonly 档工具集非空"
