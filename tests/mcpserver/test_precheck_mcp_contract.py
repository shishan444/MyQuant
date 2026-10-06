"""MCP 面契约测试（add-hypothesis-precheck tasks 4.1/4.2 验证锚）。

断言工具描述层的契约真实性（capability-truthfulness）：枚举串与
_TEMPLATE_REQUIRED_KEYS 一致（含 precheck_hypothesis 与补列的
regime_split）、G3 旧指引（condition_stats 逐条独立复核）退役且新
组合复核指引指向 precheck、阈值与双锚口径披露、register_hypothesis
提及 precheck_warnings 返回键。
"""
import asyncio

import pytest

pytestmark = [pytest.mark.integration]


def _tools_by_name():
    from mcpserver.server import build_server

    mcp, _ = build_server(profile="research")
    tools = asyncio.run(mcp.list_tools())
    return {t.name: t for t in tools}


class TestRunAnalysisContract:
    def test_enum_contains_precheck_and_regime_split(self) -> None:
        desc = _tools_by_name()["run_analysis"].description
        assert "precheck_hypothesis" in desc
        assert "regime_split" in desc, "枚举串补列 regime_split（外部评估发现）"

    def test_g3_old_wording_retired_and_new_guide_present(self) -> None:
        """G3 失实真身（tools.py run_analysis docstring）退役+新指引。"""
        desc = _tools_by_name()["run_analysis"].description
        assert "逐条独立复核" not in desc, \
            "旧指引「其余候选用 condition_stats 逐条独立复核」须退役"
        assert "组合复核" in desc
        assert "is_combination" in desc

    def test_threshold_and_dual_anchor_disclosed(self) -> None:
        desc = _tools_by_name()["run_analysis"].description
        assert "阈值 5 内建不可配" in desc          # D1 披露
        assert "≥5 pass" in desc
        assert "projected_trades" in desc           # 双锚口径说明
        assert "analysis_segment_trades" in desc
        assert "trigger_density_upper_bound" in desc  # 口径差披露


class TestRegisterHypothesisContract:
    def test_mentions_precheck_warnings_key(self) -> None:
        desc = _tools_by_name()["register_hypothesis"].description
        assert "precheck_warnings" in desc
        assert "analysis_segment_trades" in desc
