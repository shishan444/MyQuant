"""回放回测工具族锚（add-replay-backtest-mcp P2 建网）。

MCP 面单路 body 组装对拍（RecordingFakeApi 形制沿 test_paper_tools）
+入口切换下线标注双面锚（spec 场景「回放主入口与旧入口下线标注」
——工具描述与能力自述两处标注的防漂移护栏）。
"""
from __future__ import annotations

import pytest

pytestmark = [pytest.mark.integration]

from tests.mcpserver.test_paper_tools import _register


class TestReplayBacktestBodyAssembly:
    """run_replay_backtest 单路 body 组装（可选件给了才落键）。"""

    def test_defaults_pass_only_fixed_keys(self) -> None:
        """缺省面：固定七键透传（fee/slip/cash/leverage 缺省值），
        可选四件（window/stop_loss_pct/halt/task_id）零键。"""
        mcp, api = _register("research")
        mcp.registered["run_replay_backtest"](
            "st-1", "2026-01-01T00:00:00+00:00", "2026-02-01T00:00:00+00:00")
        method, url, body = api.calls[-1]
        assert (method, url) == ("POST", "/api/research/backtest-replay")
        assert body == {
            "strategy_id": "st-1",
            "start": "2026-01-01T00:00:00+00:00",
            "end": "2026-02-01T00:00:00+00:00",
            "fee": 0.001, "slippage": 0.0005,
            "initial_cash": 100000.0, "leverage": 1}

    def test_optional_keys_only_when_given(self) -> None:
        """可选件显式传入才落键（缺省面交 REST 层——与既有工具
        形制一致）。"""
        mcp, api = _register("research")
        mcp.registered["run_replay_backtest"](
            "st-1", "s", "e", window_bars=180, stop_loss_pct=0.2,
            halt_drawdown_pct=0.1, task_id="fixed-01")
        _, _, body = api.calls[-1]
        assert body["window_bars"] == 180
        assert body["stop_loss_pct"] == 0.2
        assert body["halt_drawdown_pct"] == 0.1
        assert body["task_id"] == "fixed-01"


class TestLegacyBacktestOfflineMarking:
    """入口切换标注双面（spec：工具描述与能力自述标注已下线，
    调用仍可用——run_backtest 保留在册）。"""

    def test_run_backtest_docstring_marked_offline(self) -> None:
        """工具描述含「已下线」与现役入口推荐指向（防漂移——
        文案漂移即 AI 侧入口指引失效）。"""
        import inspect

        from mcpserver import tools

        src = inspect.getsource(tools)
        # docstring 区间：def 后第一对 triple-quote 之间
        def_at = src.index('def run_backtest')
        doc_open = src.index('"""', def_at)
        doc_close = src.index('"""', doc_open + 3)
        docstring = src[doc_open:doc_close]
        assert "已下线" in docstring, "run_backtest 描述含下线标注"
        assert "run_replay_backtest" in docstring, "标注推荐现役入口"

    def test_capability_doc_marks_offline(self) -> None:
        """能力自述含 run_backtest 已下线声明与现役入口指向。"""
        from mcpserver.capability import build_capability_doc

        doc = build_capability_doc(["run_backtest",
                                    "run_replay_backtest"])
        assert "run_backtest" in doc and "已下线" in doc
        assert "run_replay_backtest" in doc, "自述声明现役回测主入口"

    def test_both_tools_registered(self) -> None:
        """run_backtest 保留在册（调用仍可用）+run_replay_backtest
        现役在册（入口切换两态并存）。"""
        from tests.mcpserver._expected_tools import EXPECTED_RESEARCH_TOOLS

        assert "run_backtest" in EXPECTED_RESEARCH_TOOLS
        assert "run_replay_backtest" in EXPECTED_RESEARCH_TOOLS
