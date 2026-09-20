"""研究域 repository 单测（add-ai-research-mcp 1.2）。

覆盖：迁移后表/约束存在、双写组合函数原子性（一表失败全回滚）、
幂等键部分唯一索引真实生效、假设条件迁移语义、任务句柄回收路径。
"""

import sqlite3
import subprocess
from pathlib import Path

import pytest
from sqlalchemy.exc import IntegrityError

pytestmark = [pytest.mark.unit]

from core.persistence.orm.models import ResearchLog
from core.persistence.repository import research as rr


RESEARCH_TABLES = [
    "research_hypotheses", "research_log", "analysis_tasks",
    "formula_plugins", "model_plugins", "signal_registry",
    "feature_requests", "validation_records",
]


@pytest.fixture()
def db_path(tmp_path: Path) -> Path:
    """升级到 head 的临时库（研究域八表就位）。"""
    db = tmp_path / "research.db"
    subprocess.run(
        ["venv/bin/python", "-m", "alembic", "-x", f"db_path={db}", "upgrade", "head"],
        capture_output=True, text=True, check=True,
    )
    return db


def _hyp_kwargs(hypothesis_id: str = "hyp_1", chash: str = "ch1", rhash: str = "rh1") -> dict:
    return dict(
        hypothesis_id=hypothesis_id,
        conditions='{"when_conditions": []}',
        conditions_hash=chash,
        statement="测试假设",
        data_ranges='[{"symbol": "BTCUSDT", "timeframe": "1h", "start": "2024-01-01", "end": "2024-06-01"}]',
        ranges_hash=rhash,
    )


class TestMigrationCreatesTables:
    def test_eight_tables_exist(self, db_path: Path) -> None:
        conn = sqlite3.connect(str(db_path))
        tables = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        conn.close()
        for t in RESEARCH_TABLES:
            assert t in tables, f"缺表 {t}"

    def test_partial_unique_index_exists(self, db_path: Path) -> None:
        """(conditions_hash,ranges_hash) WHERE status='active' 部分唯一索引在库。"""
        conn = sqlite3.connect(str(db_path))
        idx = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index'")}
        conn.close()
        assert "ux_hypotheses_active_identity" in idx
        assert "ux_validation_idem" in idx
        assert "ux_formula_plugins_name_version" in idx


class TestHypothesisDoubleWrite:
    def test_save_writes_both_tables(self, db_path: Path) -> None:
        rr.save_hypothesis_with_log(db_path, **_hyp_kwargs())
        hyp = rr.get_hypothesis(db_path, "hyp_1")
        assert hyp is not None and hyp["status"] == "active"
        logs = rr.list_research_log(db_path, hypothesis_id="hyp_1")
        assert len(logs) == 1 and logs[0]["entry_type"] == "register"

    def test_atomic_rollback_on_log_conflict(self, db_path: Path) -> None:
        """第二条 insert 失败（log_id 主键冲突）→ 假设行一并回滚。"""
        from core.persistence.repository.dataset_meta import _engine_for
        from sqlalchemy import insert

        # 预占 log_id，制造第二条 insert 的主键冲突
        with _engine_for(db_path).begin() as conn:
            conn.execute(insert(ResearchLog).values(
                log_id="occupied", entry_type="analysis", created_at="2026-09-18T00:00:00"))

        with pytest.raises(IntegrityError):
            rr.save_hypothesis_with_log(db_path, **_hyp_kwargs(), log_id="occupied")

        assert rr.get_hypothesis(db_path, "hyp_1") is None, "假设行应随日志冲突回滚"

    def test_identity_conflict_on_active(self, db_path: Path) -> None:
        """同幂等键且 active 的第二条假设被部分唯一索引拒绝。"""
        rr.save_hypothesis_with_log(db_path, **_hyp_kwargs(chash="c", rhash="r"))
        with pytest.raises(IntegrityError):
            rr.save_hypothesis_with_log(db_path, **_hyp_kwargs(hypothesis_id="hyp_2", chash="c", rhash="r"))

    def test_identity_query_finds_existing(self, db_path: Path) -> None:
        rr.save_hypothesis_with_log(db_path, **_hyp_kwargs(chash="c", rhash="r"))
        found = rr.find_active_hypothesis_by_identity(db_path, "c", "r")
        assert found is not None and found["hypothesis_id"] == "hyp_1"
        assert rr.find_active_hypothesis_by_identity(db_path, "other", "r") is None


class TestHypothesisTransition:
    def test_transition_writes_log_and_updates(self, db_path: Path) -> None:
        rr.save_hypothesis_with_log(db_path, **_hyp_kwargs())
        n = rr.transition_hypothesis(
            db_path, "hyp_1", target_status="refuted", terminal_ref="rec_1")
        assert n == 1
        hyp = rr.get_hypothesis(db_path, "hyp_1")
        assert hyp["status"] == "refuted" and hyp["terminal_ref"] == "rec_1"
        logs = [l for l in rr.list_research_log(db_path, hypothesis_id="hyp_1")
                if l["entry_type"] == "transition"]
        assert len(logs) == 1

    def test_terminal_rejects_transition(self, db_path: Path) -> None:
        """终态假设条件更新影响 0 行（调用方判读终态冲突）。"""
        rr.save_hypothesis_with_log(db_path, **_hyp_kwargs())
        rr.transition_hypothesis(db_path, "hyp_1", target_status="refuted", terminal_ref="rec_1")
        n = rr.transition_hypothesis(db_path, "hyp_1", target_status="supported", terminal_ref="rec_2")
        assert n == 0
        assert rr.get_hypothesis(db_path, "hyp_1")["status"] == "refuted"

    def test_same_key_after_terminal_allowed(self, db_path: Path) -> None:
        """终态后同幂等键可再登记（部分唯一索引只约束 active）。"""
        rr.save_hypothesis_with_log(db_path, **_hyp_kwargs(chash="c", rhash="r"))
        rr.transition_hypothesis(db_path, "hyp_1", target_status="refuted", terminal_ref="rec_1")
        # 同键新登记：不受部分唯一索引拦截
        rr.save_hypothesis_with_log(db_path, **_hyp_kwargs(hypothesis_id="hyp_2", chash="c", rhash="r"))
        assert rr.get_hypothesis(db_path, "hyp_2")["status"] == "active"


class TestValidationDoubleWrite:
    def test_save_and_identity_query(self, db_path: Path) -> None:
        rr.save_hypothesis_with_log(db_path, **_hyp_kwargs())
        rr.save_validation_with_log(
            db_path, record_id="rec_1", hypothesis_id="hyp_1", strategy_id="st_1",
            level="isolated_backtest", params='{"warmup": 200}', result='{}',
            verdict="pass", range_hash="rh_req")
        found = rr.find_validation_by_identity(db_path, "hyp_1", "st_1", "rh_req")
        assert found is not None and found["verdict"] == "pass"
        logs = [l for l in rr.list_research_log(db_path, hypothesis_id="hyp_1")
                if l["entry_type"] == "validate"]
        assert len(logs) == 1

    def test_duplicate_identity_rejected(self, db_path: Path) -> None:
        rr.save_hypothesis_with_log(db_path, **_hyp_kwargs())
        kw = dict(record_id="rec_1", hypothesis_id="hyp_1", strategy_id="st_1",
                  level="isolated_backtest", params='{}', result=None,
                  verdict="pass", range_hash="rh_req")
        rr.save_validation_with_log(db_path, **kw)
        with pytest.raises(IntegrityError):
            rr.save_validation_with_log(db_path, **{**kw, "record_id": "rec_2"})

    def test_atomic_rollback(self, db_path: Path) -> None:
        """日志冲突时验证记录回滚。"""
        from core.persistence.repository.dataset_meta import _engine_for
        from sqlalchemy import insert

        rr.save_hypothesis_with_log(db_path, **_hyp_kwargs())
        with _engine_for(db_path).begin() as conn:
            conn.execute(insert(ResearchLog).values(
                log_id="occupied", entry_type="analysis", created_at="2026-09-18T00:00:00"))
        with pytest.raises(IntegrityError):
            rr.save_validation_with_log(
                db_path, record_id="rec_x", hypothesis_id="hyp_1", strategy_id="st_1",
                level="isolated_backtest", params='{}', result=None,
                verdict="pass", range_hash="rk", log_id="occupied")
        assert rr.find_validation_by_identity(db_path, "hyp_1", "st_1", "rk") is None


class TestPluginDoubleWrite:
    def test_valid_formula_registers_signal(self, db_path: Path) -> None:
        entry = rr.save_plugin_with_registry(
            db_path, source="formula", plugin_id="p1", name="oi_mom", version=1,
            expression="zscore(close, 20)", output_columns='["oi_mom"]',
            entry_id="e1", column_name="oi_mom")
        assert entry == "e1"
        rows = rr.list_signal_registry_entries(db_path)
        assert len(rows) == 1 and rows[0]["column_name"] == "oi_mom"

    def test_rejected_plugin_skips_registry(self, db_path: Path) -> None:
        """rejected 只留插件记录，不进 signal_registry。"""
        rr.save_plugin_with_registry(
            db_path, source="formula", plugin_id="p2", name="bad", version=1,
            expression="hack()", output_columns='["bad"]',
            status="rejected", reject_reason="非法函数")
        assert rr.list_signal_registry_entries(db_path) == []

    def test_atomic_rollback(self, db_path: Path) -> None:
        """注册表冲突（entry_id 预占）→ 插件行回滚。"""
        from core.persistence.repository.dataset_meta import _engine_for
        from sqlalchemy import insert
        from core.persistence.orm.models import SignalRegistryEntry

        with _engine_for(db_path).begin() as conn:
            conn.execute(insert(SignalRegistryEntry).values(
                entry_id="occupied", source="formula", plugin_id="x",
                column_name="taken", version=1, strategy_visible=1,
                created_at="2026-09-18T00:00:00"))
        with pytest.raises(IntegrityError):
            rr.save_plugin_with_registry(
                db_path, source="formula", plugin_id="p3", name="n", version=1,
                expression="zscore(close, 20)", output_columns='["n"]',
                entry_id="occupied", column_name="n")
        # 插件行已回滚：同名重登记不触发唯一约束
        rr.save_plugin_with_registry(
            db_path, source="formula", plugin_id="p3b", name="n", version=1,
            expression="zscore(close, 20)", output_columns='["n"]',
            entry_id="e_new", column_name="n2")


class TestAnalysisTaskHandle:
    def test_lifecycle_and_finished_at(self, db_path: Path) -> None:
        rr.save_analysis_task(db_path, task_id="ant_1", template="condition_stats", params="{}")
        t = rr.get_analysis_task(db_path, "ant_1")
        assert t["status"] == "pending" and t["finished_at"] is None

        rr.update_analysis_task(db_path, task_id="ant_1", status="running")
        rr.update_analysis_task(db_path, task_id="ant_1", status="failed", reason="timeout")
        t = rr.get_analysis_task(db_path, "ant_1")
        assert t["status"] == "failed" and t["reason"] == "timeout"
        assert t["finished_at"] is not None

    def test_stale_running_recovery(self, db_path: Path) -> None:
        rr.save_analysis_task(db_path, task_id="ant_old", template="t", params="{}")
        rr.update_analysis_task(db_path, task_id="ant_old", status="running")
        rr.save_analysis_task(db_path, task_id="ant_new", template="t", params="{}")
        rr.update_analysis_task(db_path, task_id="ant_new", status="running")

        stale = rr.list_stale_running_tasks(db_path, created_before="2999-01-01T00:00:00")
        assert {t["task_id"] for t in stale} == {"ant_old", "ant_new"}

        # 中断回收：置 failed(interrupted)
        for t in stale:
            rr.update_analysis_task(db_path, task_id=t["task_id"],
                                    status="failed", reason="interrupted")
        assert rr.get_analysis_task(db_path, "ant_old")["reason"] == "interrupted"
