"""研究域 repository 列表/迁移组合函数特征网（fix/mcp-research-upstream-review）。

修复期临时安全网（现状记录语义）：钉住 transition_hypothesis_with_promotion
（promote 四分支）、save_hypothesis_with_log 的 screen_use 谱系分支、
list_* 三函数的过滤组合与 offset/limit 边界、幂等/可见性/回收边界的
既有行为——上游修复触碰这些函数时，行为漂移在此显形。基准源：
core/persistence/DESIGN 卡语义经函数 docstring 与既有测试网
（test_research_repository.py）锚定。
"""

import subprocess
from pathlib import Path

import pytest

pytestmark = [pytest.mark.unit]

from sqlalchemy import insert, text, select

from core.persistence.orm.models import AnalysisTask, SignalRegistryEntry, Strategy
from core.persistence.repository import research as rr
from core.persistence.repository.dataset_meta import _engine_for


@pytest.fixture()
def db_path(tmp_path: Path) -> Path:
    """升级到 head 的临时库（研究域八表 + strategy 表就位）。"""
    db = tmp_path / "research_listing.db"
    subprocess.run(
        ["venv/bin/python", "-m", "alembic", "-x", f"db_path={db}", "upgrade", "head"],
        capture_output=True, text=True, check=True,
    )
    return db


def _insert_hyp(db_path: Path, hypothesis_id: str = "hyp_1",
                status: str = "active") -> None:
    """假设行 SQL 直插（retire-legacy 后登记/迁移写通道退役——
    读通道测试的种子构造改 vr_factory；终态行直接指定 status）。"""
    from tests.helpers.vr_factory import insert_hypothesis
    insert_hypothesis(db_path, hypothesis_id=hypothesis_id, status=status)


def _set_terminal(db_path: Path, hypothesis_id: str,
                  status: str = "refuted") -> None:
    """终态直改（读通道过滤面构造——不走已退役的迁移服务）。"""
    with _engine_for(db_path).begin() as conn:
        conn.execute(
            text("UPDATE research_hypotheses SET status = :s,"
                 " terminal_ref = 'rec_x', updated_at = '2026-10-02T00:00:00'"
                 " WHERE hypothesis_id = :i"),
            {"s": status, "i": hypothesis_id})


def _insert_strategy(db_path: Path, strategy_id: str, parent_ids: str,
                     status: str = "draft") -> None:
    """直插 strategy 行（promotion 分支的最小输入，不走业务装配）。"""
    now = "2026-10-01T00:00:00"
    with _engine_for(db_path).begin() as conn:
        conn.execute(insert(Strategy).values(
            strategy_id=strategy_id, name=strategy_id, dna_json="{}",
            status=status, symbol="BTCUSDT", timeframe="4h",
            parent_ids=parent_ids, created_at=now, updated_at=now))


def _save_pass(db_path: Path, hypothesis_id: str, strategy_id: str,
               record_id: str) -> None:
    from tests.helpers.vr_factory import insert_validation_record
    insert_validation_record(
        db_path, record_id=record_id, hypothesis_id=hypothesis_id,
        strategy_id=strategy_id, level="isolated_backtest", params={},
        result=None, verdict="pass", range_hash=f"rk_{record_id}")


def _promotion_logs(db_path: Path, hypothesis_id: str) -> list[dict]:
    return [e for e in rr.list_research_log(db_path, hypothesis_id=hypothesis_id)
            if e["entry_type"] == "promotion"]


class TestListHypotheses:
    """status 过滤 + offset/limit 分页边界。"""

    @pytest.fixture(autouse=True)
    def _three_hypotheses(self, db_path: Path) -> None:
        for i in range(3):
            _insert_hyp(db_path, hypothesis_id=f"hyp_{i}")
        _set_terminal(db_path, "hyp_2")

    def test_status_filter(self, db_path: Path) -> None:
        active = rr.list_hypotheses(db_path, status="active")
        assert {h["hypothesis_id"] for h in active} == {"hyp_0", "hyp_1"}
        refuted = rr.list_hypotheses(db_path, status="refuted")
        assert {h["hypothesis_id"] for h in refuted} == {"hyp_2"}

    def test_offset_limit_slices(self, db_path: Path) -> None:
        page1 = rr.list_hypotheses(db_path, limit=2)
        assert len(page1) == 2
        page2 = rr.list_hypotheses(db_path, offset=2, limit=2)
        assert len(page2) == 1
        assert {h["hypothesis_id"] for h in page1 + page2} == {
            "hyp_0", "hyp_1", "hyp_2"}, "分页合响应覆盖全量且不重叠"

    def test_offset_beyond_end_returns_empty(self, db_path: Path) -> None:
        assert rr.list_hypotheses(db_path, offset=100, limit=10) == []

    def test_status_filter_combines_with_pagination(self, db_path: Path) -> None:
        one = rr.list_hypotheses(db_path, status="active", limit=1)
        assert len(one) == 1 and one[0]["status"] == "active"


class TestListResearchLog:
    """hypothesis_id / entry_type 双过滤组合 + 分页边界。"""

    @pytest.fixture(autouse=True)
    def _logs(self, db_path: Path) -> None:
        _insert_hyp(db_path, "hyp_1")
        _insert_hyp(db_path, "hyp_2")
        # 登记日志行（读通道过滤计数需要——append_research_log 存活单点；
        # 原登记面自动双写改手动两行对齐既有计数锚）
        rr.append_research_log(db_path, hypothesis_id="hyp_1",
                               entry_type="register", summary="登记")
        rr.append_research_log(db_path, hypothesis_id="hyp_2",
                               entry_type="register", summary="登记")
        rr.append_research_log(db_path, hypothesis_id="hyp_1",
                               entry_type="adhoc", summary="快照留痕")
        rr.append_research_log(db_path, hypothesis_id=None,
                               entry_type="screen", ref_id="ant_s",
                               summary="全局扫描")

    def test_entry_type_filter(self, db_path: Path) -> None:
        screens = rr.list_research_log(db_path, entry_type="screen")
        assert len(screens) == 1 and screens[0]["hypothesis_id"] is None

    def test_combined_filter(self, db_path: Path) -> None:
        adhoc = rr.list_research_log(db_path, hypothesis_id="hyp_1",
                                     entry_type="adhoc")
        assert len(adhoc) == 1 and adhoc[0]["summary"] == "快照留痕"
        empty = rr.list_research_log(db_path, hypothesis_id="hyp_2",
                                     entry_type="adhoc")
        assert empty == []

    def test_offset_limit_slices(self, db_path: Path) -> None:
        all_logs = rr.list_research_log(db_path)
        assert len(all_logs) == 4
        first2 = rr.list_research_log(db_path, limit=2)
        rest = rr.list_research_log(db_path, offset=2, limit=10)
        assert len(first2) == 2 and len(rest) == 2
        assert {e["log_id"] for e in first2 + rest} == {
            e["log_id"] for e in all_logs}, "分页窗口平铺无重叠"

    def test_offset_beyond_end_returns_empty(self, db_path: Path) -> None:
        assert rr.list_research_log(db_path, offset=100) == []


class TestListValidationRecords:
    """hypothesis_id / strategy_id 过滤组合 + 分页边界。"""

    @pytest.fixture(autouse=True)
    def _records(self, db_path: Path) -> None:
        _insert_hyp(db_path, "hyp_1")
        _insert_hyp(db_path, "hyp_2")
        # 登记日志行（读通道过滤计数需要——append_research_log 存活单点；
        # 原登记面自动双写改手动两行对齐既有计数锚）
        rr.append_research_log(db_path, hypothesis_id="hyp_1",
                               entry_type="register", summary="登记")
        rr.append_research_log(db_path, hypothesis_id="hyp_2",
                               entry_type="register", summary="登记")
        from tests.helpers.vr_factory import insert_validation_record
        for i, (hyp, st) in enumerate([("hyp_1", "st_a"), ("hyp_1", "st_b"),
                                       ("hyp_2", "st_a")]):
            insert_validation_record(
                db_path, record_id=f"rec_{i}", hypothesis_id=hyp,
                strategy_id=st, level="isolated_backtest", params={},
                result=None, verdict="pass", range_hash=f"rk_{i}")

    def test_hypothesis_filter(self, db_path: Path) -> None:
        rows = rr.list_validation_records(db_path, hypothesis_id="hyp_1")
        assert {r["record_id"] for r in rows} == {"rec_0", "rec_1"}

    def test_strategy_filter(self, db_path: Path) -> None:
        rows = rr.list_validation_records(db_path, strategy_id="st_a")
        assert {r["record_id"] for r in rows} == {"rec_0", "rec_2"}

    def test_combined_filter(self, db_path: Path) -> None:
        rows = rr.list_validation_records(db_path, hypothesis_id="hyp_1",
                                          strategy_id="st_b")
        assert {r["record_id"] for r in rows} == {"rec_1"}

    def test_no_filter_returns_all(self, db_path: Path) -> None:
        assert len(rr.list_validation_records(db_path)) == 3

    def test_offset_limit_slices(self, db_path: Path) -> None:
        assert len(rr.list_validation_records(db_path, limit=2)) == 2
        assert len(rr.list_validation_records(db_path, offset=2, limit=5)) == 1
        assert rr.list_validation_records(db_path, offset=99) == []


class TestSignalRegistryVisibility:
    """only_visible 过滤（strategy_visible=0 的下架列不可见）。"""

    def test_only_visible_excludes_hidden(self, db_path: Path) -> None:
        rr.save_plugin_with_registry(
            db_path, source="formula", plugin_id="p1", name="vis", version=1,
            expression="zscore(close, 20)", output_columns='["vis_col"]',
            entry_id="e1", column_name="vis_col")
        with _engine_for(db_path).begin() as conn:
            conn.execute(insert(SignalRegistryEntry).values(
                entry_id="e2", source="formula", plugin_id="p2",
                column_name="hidden_col", version=1,
                strategy_visible=0, created_at="2026-10-01T00:00:00"))

        visible = rr.list_signal_registry_entries(db_path, only_visible=True)
        assert {r["column_name"] for r in visible} == {"vis_col"}
        assert {r["column_name"]
                for r in rr.list_signal_registry_entries(db_path)} == {
            "vis_col", "hidden_col"}, "缺省不过滤，全量可见"


class TestAnalysisTaskNoopAndBoundary:
    """update 空参 no-op 与 stale 回收的严格小于边界。"""

    def test_update_with_all_none_is_noop(self, db_path: Path) -> None:
        rr.save_analysis_task(db_path, task_id="ant_1",
                              template="t", params="{}")
        rr.update_analysis_task(db_path, task_id="ant_1", status="running")
        rr.update_analysis_task(db_path, task_id="ant_1")  # 全 None

        t = rr.get_analysis_task(db_path, "ant_1")
        assert t["status"] == "running" and t["finished_at"] is None

    def test_get_missing_returns_none(self, db_path: Path) -> None:
        assert rr.get_analysis_task(db_path, "ant_none") is None

    def test_stale_strictly_before_threshold(self, db_path: Path) -> None:
        """created_at < 阈值为严格小于：等值行不回收。"""
        with _engine_for(db_path).begin() as conn:
            for tid, created in [("ant_eq", "2026-06-01T00:00:00"),
                                 ("ant_after", "2026-07-01T00:00:00"),
                                 ("ant_done", "2026-05-01T00:00:00")]:
                status = "failed" if tid == "ant_done" else "running"
                conn.execute(insert(AnalysisTask).values(
                    task_id=tid, template="t", params="{}",
                    status=status, created_at=created))

        stale = rr.list_stale_running_tasks(
            db_path, created_before="2026-06-01T00:00:00")
        assert {t["task_id"] for t in stale} == set(), "等值与晚于阈值均不回收"

        stale2 = rr.list_stale_running_tasks(
            db_path, created_before="2026-08-01T00:00:00")
        assert {t["task_id"] for t in stale2} == {"ant_eq", "ant_after"}, \
            "仅早于阈值的 running 行回收，failed 终态不重复回收"


class TestListPluginsRepository:
    """CARD-06 收口资产：插件审计清单同构查询（source 分派 + name 过滤）。"""

    def test_source_dispatch_and_name_filter(self, db_path: Path) -> None:
        rr.save_plugin_with_registry(
            db_path, source="formula", plugin_id="pf_1", name="oi_mom",
            version=1, expression="zscore(close, 20)",
            output_columns='["oi_mom"]', entry_id="e_f", column_name="oi_mom")
        rr.save_plugin_with_registry(
            db_path, source="model", plugin_id="pm_1", name="roll_model",
            version=1, config="{}", signal_column="model_col",
            entry_id="e_m", column_name="model_col")

        formulas = rr.list_plugins(db_path, source="formula")
        assert {r["plugin_id"] for r in formulas} == {"pf_1"}
        assert formulas[0]["name"] == "oi_mom"
        models = rr.list_plugins(db_path, source="model")
        assert {r["plugin_id"] for r in models} == {"pm_1"}
        # name 精确过滤：命中同名族、未命中空集
        assert {r["plugin_id"]
                for r in rr.list_plugins(db_path, source="formula",
                                         name="oi_mom")} == {"pf_1"}
        assert rr.list_plugins(db_path, source="formula", name="nope") == []

    def test_version_audit_order(self, db_path: Path) -> None:
        """同名多版本按 version 升序（审计读序）。"""
        for v in (1, 2):
            rr.save_plugin_with_registry(
                db_path, source="formula", plugin_id=f"pf_v{v}", name="same",
                version=v, expression="zscore(close, 20)",
                output_columns='["same"]', entry_id=f"e_v{v}",
                column_name=f"same_v{v}")
        rows = rr.list_plugins(db_path, source="formula", name="same")
        assert [r["version"] for r in rows] == [1, 2]

