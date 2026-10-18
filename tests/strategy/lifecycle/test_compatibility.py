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
- E5 中间态防误报锚：先行窗「paper 态任务结束停留=预期」锚已在
  5.2 接入时移除并替换为触发链恢复锚（paper 态任务终态→回流
  active——两窗各钉一面）。
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
    """E1：解析函数（三序规则接管后）六态输入不崩溃不劣化——签名
    (db_path, strategy_row) 真实行形制（5.3 后无事件子集行为=旧规则：
    非 draft 态落 qualified 分支原样）。"""

    @pytest.mark.parametrize("status", [
        "draft", "active", "paper", "candidate", "live", "archived"])
    def test_six_statuses_do_not_crash_or_degrade(self, db, status):
        """六态逐态×qualified 三值（无事件世界）：不抛异常且输出恒
        四值域内（paper/candidate/live/archived 落 qualified 分支=
        既有规则原样——序③兜底）。"""
        from sqlalchemy import text

        from core.persistence.orm.engine import engine_for

        save_strategy(db, strategy_id="st_e1", dna_json="{}",
                      symbol="BTCUSDT", timeframe="4h", status="draft",
                      gene_signature="sig_e1")
        for qualified in (None, 1, 0):
            with engine_for(db).begin() as conn:
                conn.execute(text(
                    "UPDATE strategy SET status=:s, qualified=:q"
                    " WHERE strategy_id='st_e1'"),
                    {"s": status, "q": qualified})
            row = {"strategy_id": "st_e1", "status": status,
                   "qualified": qualified}
            out = resolve_research_status(db, row)
            assert out in {"draft", "unverified", "verified",
                           "verification_failed"}, (status, qualified, out)

    def test_draft_short_circuits_before_qualified(self, db):
        """draft 先于 qualified 判定（序①语义先行窗已在——三序规则
        的序①与现状一致）。"""
        row = {"strategy_id": "st_e1b", "status": "draft", "qualified": 1}
        assert resolve_research_status(db, row) == "draft"

    def test_non_draft_maps_qualified_three_values(self, db):
        """非 draft 态无事件按 qualified 三值映射（旧规则保持=序③）。"""
        for qualified, expected in ((None, "unverified"), (1, "verified"),
                                    (0, "verification_failed")):
            row = {"strategy_id": "st_e1c", "status": "active",
                   "qualified": qualified}
            assert resolve_research_status(db, row) == expected


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
            db, {"strategy_id": "st_c_d", "status": "draft",
                 "qualified": None}) == "draft"
        assert resolve_research_status(
            db, {"strategy_id": "st_c_a", "status": "active",
                 "qualified": None}) == "unverified"
        assert resolve_research_status(
            db, {"strategy_id": "st_c_r", "status": "archived",
                 "qualified": None}) == "unverified"

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


class TestE5TriggerChainRestored:
    """E5：触发链恢复锚（延后窗一面——先行窗「paper 态任务结束
    停留=预期」锚随 5.2 接入移除，本锚替换钉反面：触发链已恢复）。"""

    def test_paper_task_end_returns_strategy_to_active(self, db):
        """paper 态策略的纸盘任务终态→经调度器回调回流 active（5.2
        接入后触发链恢复——任务结束不再停留 paper）。"""
        from core.paper_trading.runtime.scheduler import PaperScheduler
        from core.persistence.repository import paper as paper_repo

        save_strategy(db, strategy_id="st_e5", dna_json="{}",
                      symbol="BTCUSDT", timeframe="4h", status="active",
                      gene_signature="sig_e5")
        from sqlalchemy import text
        from core.persistence.orm.engine import engine_for
        with engine_for(db).begin() as conn:
            conn.execute(text("UPDATE strategy SET status='paper'"
                              " WHERE strategy_id='st_e5'"))
        paper_repo.create_task(
            db, task_id="pt_e500000001", strategy_id="st_e5",
            dna_snapshot_json="{}", symbol="BTCUSDT", timeframe="4h",
            research_status_snapshot="unverified")
        paper_repo.update_task(db, task_id="pt_e500000001",
                               status="running",
                               started_at="2030-01-01T00:00:00+00:00")
        paper_repo.update_task(db, task_id="pt_e500000001",
                               status="completed",
                               stopped_at="2030-01-01T00:00:00+00:00")
        scheduler = PaperScheduler(db, data_dir=db.parent)
        scheduler._executor_concluded("pt_e500000001", "completed")
        with engine_for(db).connect() as conn:
            status = conn.execute(text("SELECT status FROM strategy"
                                       " WHERE strategy_id='st_e5'")).scalar_one()
            events = conn.execute(text(
                "SELECT from_status, to_status, trigger FROM"
                " strategy_transition_event WHERE strategy_id='st_e5'")).all()
        assert status == "active"
        assert events == [("paper", "active", "task_end")]

    def test_scheduler_uses_lifecycle_transit(self):
        """触发链在场面：scheduler 已接 lifecycle 公共入口（AST import
        扫描正锚——先行窗反向锚（零接线）的反转面）。"""
        import core.paper_trading.runtime.scheduler as scheduler_module

        targets = set()
        for node in ast.walk(ast.parse(
                Path(scheduler_module.__file__).read_text(
                    encoding="utf-8"))):
            if isinstance(node, ast.ImportFrom) and node.module:
                targets.add(node.module)
            elif isinstance(node, ast.Import):
                targets.update(a.name for a in node.names)
        hits = {t for t in targets
                if t == "core.strategy.lifecycle"
                or t.startswith("core.strategy.lifecycle.")}
        assert hits, "scheduler 须接 lifecycle（5.2 触发链恢复）"
