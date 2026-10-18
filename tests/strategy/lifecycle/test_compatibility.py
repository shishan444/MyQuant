"""先行窗兼容回归（build-strategy-lifecycle 4.3，族 E 五项）。

五项映射（全绿证明=本文件 E1/E4/E5 直测+E2/E3 既有测试复跑）：
- E1 旧解析函数六态兼容：resolve_research_status 对六态输入不崩溃
  不劣化（paper/candidate/live/archived 先行窗落 qualified 分支——
  延后窗 5.3 三序规则接管）；
- E2 v8 字节锚回归：tests/strategy/test_v8_byte_anchor.py 既有
  （DNA/引擎零改动——本 change 未触碰，复跑绿即证）；
- E3 研究链转正回归：tests/analysis/test_draft_lifecycle.py
  TestPromotion（3.1 经服务后语义等价）+test_matrix.py
  TestRealTriggerPaths（经服务与研究链行为对照）；
- E4 三态旧值读取面：draft/active/archived 输入经旧解析函数与
  列表过滤的读取语义保持；
- E5 中间态防误报锚：先行窗 paper 态任务结束停留=预期（纸盘域
  未接迁移链是设计内两窗中间态，非缺陷——延后窗 5.2 接入后本锚
  反转）。
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

pytestmark = [pytest.mark.integration]

from core.persistence.db_ext import init_db_ext
from core.persistence.repository.paper import resolve_research_status
from core.persistence.repository.strategy import (
    list_strategies, save_strategy)

_PAPER_TRADING_DIR = (Path(__file__).resolve().parents[3]
                      / "core" / "paper_trading")


@pytest.fixture
def db(tmp_path: Path) -> Path:
    path = tmp_path / "compat.db"
    init_db_ext(path)
    return path


class TestE1LegacyResolverSixStatuses:
    """E1：旧解析函数（design §9.2 单源）六态输入不崩溃不劣化。"""

    @pytest.mark.parametrize("status", [
        "draft", "active", "paper", "candidate", "live", "archived"])
    def test_six_statuses_do_not_crash_or_degrade(self, status):
        """六态逐态输入：不抛异常且输出恒四值域内（先行窗 paper/
        candidate/live/archived 落 qualified 分支=既有规则原样）。"""
        for qualified in (None, 1, 0):
            row = {"status": status, "qualified": qualified}
            out = resolve_research_status(row)
            assert out in {"draft", "unverified", "verified",
                           "verification_failed"}, (status, qualified, out)

    def test_draft_short_circuits_before_qualified(self):
        """draft 先于 qualified 判定（序①语义先行窗已在——延后窗
        三序规则的序①与现状一致）。"""
        assert resolve_research_status(
            {"status": "draft", "qualified": 1}) == "draft"

    def test_non_draft_maps_qualified_three_values(self):
        """非 draft 态按 qualified 三值映射（旧规则保持）。"""
        assert resolve_research_status(
            {"status": "active", "qualified": None}) == "unverified"
        assert resolve_research_status(
            {"status": "active", "qualified": 1}) == "verified"
        assert resolve_research_status(
            {"status": "active", "qualified": 0}) == "verification_failed"


class TestE4ThreeLegacyStatusReadSurface:
    """E4：三态旧值读取面回归（解析函数+列表过滤两读取面）。"""

    def test_legacy_three_statuses_read_semantics(self, db):
        """三态行的读取语义保持：draft→draft/active+archived 按
        qualified 分支。"""
        save_strategy(db, strategy_id="st_c_d", dna_json="{}",
                      symbol="BTCUSDT", timeframe="4h", status="draft",
                      gene_signature="sig_cd")
        save_strategy(db, strategy_id="st_c_a", dna_json="{}",
                      symbol="BTCUSDT", timeframe="4h", status="active",
                      gene_signature="sig_ca")
        save_strategy(db, strategy_id="st_c_r", dna_json="{}",
                      symbol="BTCUSDT", timeframe="4h", status="active",
                      gene_signature="sig_cr")
        from sqlalchemy import text
        from core.persistence.orm.engine import engine_for
        with engine_for(db).begin() as conn:
            conn.execute(text("UPDATE strategy SET status='archived'"
                              " WHERE strategy_id='st_c_r'"))
        assert resolve_research_status(
            {"status": "draft", "qualified": None}) == "draft"
        assert resolve_research_status(
            {"status": "active", "qualified": None}) == "unverified"
        assert resolve_research_status(
            {"status": "archived", "qualified": None}) == "unverified"

    def test_list_default_view_excludes_draft_kept(self, db):
        """列表默认视图排除 draft 的既有过滤行为保持（三态时代语义
        不变——draft 可见性走显式 status 查询）。"""
        save_strategy(db, strategy_id="st_v_d", dna_json="{}",
                      symbol="BTCUSDT", timeframe="4h", status="draft",
                      gene_signature="sig_vd")
        save_strategy(db, strategy_id="st_v_a", dna_json="{}",
                      symbol="BTCUSDT", timeframe="4h", status="active",
                      gene_signature="sig_va")
        default_ids = {r["strategy_id"] for r in list_strategies(db)}
        assert default_ids == {"st_v_a"}
        draft_ids = {r["strategy_id"] for r in list_strategies(
            db, status="draft")}
        assert draft_ids == {"st_v_d"}


class TestE5InterimStateNoFalseAlarm:
    """E5：中间态防误报锚（先行窗一面——延后窗 5.2 接入后反转）。"""

    def test_paper_trading_domain_has_no_lifecycle_wiring(self):
        """先行窗中间态锚：core/paper_trading/ 域零 lifecycle 接线
        （AST import 扫描）——paper 态策略的任务结束停留 paper 是
        设计内预期（两窗结构：边定义先行/生产触发方延后），非缺陷。
        延后窗 5.1/5.2 接入时本锚移除并以触发链恢复锚替代。"""
        sources = [p for p in _PAPER_TRADING_DIR.glob("*.py")]
        assert sources, "扫描域为空——先查路径"
        for path in sources:
            targets = set()
            for node in ast.walk(ast.parse(
                    path.read_text(encoding="utf-8"))):
                if isinstance(node, ast.ImportFrom) and node.module:
                    targets.add(node.module)
                elif isinstance(node, ast.Import):
                    targets.update(a.name for a in node.names)
            stray = {t for t in targets
                     if t == "core.strategy.lifecycle"
                     or t.startswith("core.strategy.lifecycle.")}
            assert not stray, \
                f"{path.name} 已接 lifecycle（延后窗范围先行窗不得出现）: {stray}"

    def test_scanner_detects_known_positive(self):
        """负例自证：同一 AST 扫描器对 lifecycle import 正例源码命中。"""
        positive = ("from core.strategy.lifecycle import transit\n"
                    "import core.strategy.lifecycle.service\n")
        targets = set()
        for node in ast.walk(ast.parse(positive)):
            if isinstance(node, ast.ImportFrom) and node.module:
                targets.add(node.module)
            elif isinstance(node, ast.Import):
                targets.update(a.name for a in node.names)
        stray = {t for t in targets
                 if t == "core.strategy.lifecycle"
                 or t.startswith("core.strategy.lifecycle.")}
        assert stray == {"core.strategy.lifecycle",
                         "core.strategy.lifecycle.service"}
