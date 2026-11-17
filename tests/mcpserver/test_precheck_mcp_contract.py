"""MCP 面契约测试（add-hypothesis-precheck tasks 4.1/4.2 验证锚）。

断言工具描述层的契约真实性（capability-truthfulness）：枚举串与
_TEMPLATE_REQUIRED_KEYS 一致（含 precheck_hypothesis 与补列的
regime_split）、G3 旧指引（condition_stats 逐条独立复核）退役且新
组合复核指引指向 precheck、阈值与双锚口径披露。（register_
hypothesis 侧 precheck_warnings 披露用例随工具退役删除——
retire-legacy 任务 9。）
"""
import pytest

pytestmark = [pytest.mark.integration]


def _tools_by_name():
    from mcpserver.server import build_server
    from tests.mcpserver.test_contract_parity import run_async

    mcp, _ = build_server(profile="research")
    tools = run_async(mcp.list_tools())   # e2e 泄漏 loop 兼容（run_async 形制）
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
        assert "projected_trades" in desc           # 锚口径（CARD-06 改单锚）
        # 双锚口径退役注记（CARD-06）：analysis_segment_trades 字样随
        # 假设登记链退役，改述为单锚+退役注记披露——句跨行，断言前
        # squash 规整（docstring 换行断词防假红）
        assert "分析段实际触发数锚已随假设登记链退役" in "".join(desc.split())
        assert "trigger_density_upper_bound" in desc  # 口径差披露
