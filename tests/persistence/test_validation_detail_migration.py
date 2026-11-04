"""add-validation-detail 任务 1 迁移轨：vr_trade_detail + vr_equity_curve
（c3f8a2e6d9b1）。

沿 test_paper_signal_migration.py 的 subprocess alembic 惯例。验证面
（tasks.md 任务 1 完成标准①②③④⑤，tester B4-9/B4-10）：
- 实库 PRAGMA 断言（table_info+index_list）——声明≠实存，三重声明
  不一致会静默架空（工程坑位）；
- ORM create_all 产物 vs 迁移链产物逐列一致（对照断言）；
- ensure_research_tables 空库建出十表（含两新表）；
- 存量库升级路径（旧 head 有数据→upgrade head：既有数据完好、
  明细表零行——历史 289 条零回填的库级面）；
- 在场守卫：create_all 形库先建表再跑迁移零撞表；
- 库级唯一索引拒绝重复 (record_id, exec_ref, seq)（防御层在场）。
"""
from __future__ import annotations

import sqlite3
import subprocess
from pathlib import Path

import pytest

pytestmark = [pytest.mark.integration]

_OLD_HEAD = "b8d5f0a2c3e6"  # 本 change 迁移前的 head
_REPO_ROOT = Path(__file__).resolve().parents[2]

# vr_trade_detail 14 列权威字典（design §3.1 逐列：(列名, 类型, NOT NULL)）
_TRADE_COLUMNS = (
    ("id", "INTEGER", True),
    ("record_id", "TEXT", True),
    ("exec_ref", "TEXT", True),
    ("exec_index", "INTEGER", True),
    ("range_start", "TEXT", True),
    ("range_end", "TEXT", True),
    ("seq", "INTEGER", True),
    ("entry_time", "TEXT", True),
    # NULL=尾部未平仓（vbt Status=Open——保真不伪造平仓时点）
    ("exit_time", "TEXT", False),
    ("entry_price", "REAL", True),
    ("exit_price", "REAL", True),
    ("size", "REAL", True),
    ("direction", "TEXT", True),
    ("pnl", "REAL", False),
    # decision_json 退役（retire-vr-decision-json——a8e4f7d2c9b3 列撤）
)

# vr_equity_curve 12 列权威字典
_EQUITY_COLUMNS = (
    ("id", "INTEGER", True),
    ("record_id", "TEXT", True),
    ("exec_ref", "TEXT", True),
    ("exec_index", "INTEGER", True),
    ("range_start", "TEXT", True),
    ("range_end", "TEXT", True),
    ("grid_aligned", "INTEGER", True),
    ("timeframe", "TEXT", True),
    ("start", "TEXT", False),
    ("length", "INTEGER", False),
    ("timestamps", "TEXT", False),
    ("values", "TEXT", True),
)


def _alembic(db: Path, *args: str) -> None:
    subprocess.run(
        ["venv/bin/python", "-m", "alembic", "-x", f"db_path={db}", *args],
        capture_output=True, text=True, check=True, cwd=_REPO_ROOT,
    )


def _conn(db: Path) -> sqlite3.Connection:
    return sqlite3.connect(db)


@pytest.fixture()
def legacy_db(tmp_path: Path) -> Path:
    """旧 head（b8d5f0a2c3e6）空库——本 change 迁移前的基线。"""
    db = tmp_path / "mig.db"
    _alembic(db, "upgrade", _OLD_HEAD)
    return db


class TestDetailSchema:
    """实库 schema 面：两明细表结构（直查 PRAGMA，不读声明）。"""

    def test_trade_table_14_columns(self, legacy_db: Path) -> None:
        """14 列齐：列名/类型/NOT NULL 逐列对权威字典。"""
        _alembic(legacy_db, "upgrade", "head")
        with _conn(legacy_db) as conn:
            cols = [(r[1], r[2].upper(), bool(r[3]))
                    for r in conn.execute("PRAGMA table_info(vr_trade_detail)")]
        assert cols == list(_TRADE_COLUMNS), (
            f"vr_trade_detail 列形与权威字典不符: {cols}")

    def test_equity_table_12_columns(self, legacy_db: Path) -> None:
        """12 列齐：紧凑/降级互斥列（start/length/timestamps NULLable）。"""
        _alembic(legacy_db, "upgrade", "head")
        with _conn(legacy_db) as conn:
            cols = [(r[1], r[2].upper(), bool(r[3]))
                    for r in conn.execute("PRAGMA table_info(vr_equity_curve)")]
        assert cols == list(_EQUITY_COLUMNS), (
            f"vr_equity_curve 列形与权威字典不符: {cols}")

    def test_four_indexes_in_pragma(self, legacy_db: Path) -> None:
        """四索引实库在场+唯一性位+唯一键列序（B4-9）。"""
        _alembic(legacy_db, "upgrade", "head")
        with _conn(legacy_db) as conn:
            t_idx = {r[1]: r for r in
                     conn.execute("PRAGMA index_list(vr_trade_detail)")}
            e_idx = {r[1]: r for r in
                     conn.execute("PRAGMA index_list(vr_equity_curve)")}
        assert t_idx["ux_vr_trade_ident"][2] == 1, "ux_vr_trade_ident 须唯一"
        assert t_idx["ix_vr_trade_record"][2] == 0, "ix_vr_trade_record 非唯一"
        assert e_idx["ux_vr_equity_ident"][2] == 1, "ux_vr_equity_ident 须唯一"
        assert e_idx["ix_vr_equity_record"][2] == 0, "ix_vr_equity_record 非唯一"
        with _conn(legacy_db) as conn:
            t_cols = [r[2] for r in conn.execute(
                "PRAGMA index_info(ux_vr_trade_ident)")]
            e_cols = [r[2] for r in conn.execute(
                "PRAGMA index_info(ux_vr_equity_ident)")]
        assert t_cols == ["record_id", "exec_ref", "seq"], \
            "trade 唯一键列序=幂等键语义 (record_id, exec_ref, seq)"
        assert e_cols == ["record_id", "exec_ref"], \
            "equity 唯一键列序=一份一行 (record_id, exec_ref)"

    def test_duplicate_exec_trade_rejected_at_db_level(
            self, tmp_path: Path) -> None:
        """B4-10：绕过应用层直插重复 (record_id, exec_ref, seq) 被库级
        拒绝——防御层在场（幂等前置使重插理论不可达，索引兜底）。"""
        from core.persistence.db_ext import init_db_ext
        from core.analysis.executor import ensure_research_tables

        db = tmp_path / "dup.db"
        init_db_ext(db)
        ensure_research_tables(db)
        row_sql = (
            "INSERT INTO vr_trade_detail (record_id, exec_ref, exec_index,"
            " range_start, range_end, seq, entry_time, exit_time,"
            " entry_price, exit_price, size, direction, pnl)"
            " VALUES ('rec_x', 'main', 0, 's', 'e', 0, 't1', 't2',"
            " 100.0, 110.0, 1.0, 'Long', 1.5)")
        with _conn(db) as conn:
            conn.execute(row_sql)
        with pytest.raises(sqlite3.IntegrityError):
            with _conn(db) as conn:
                conn.execute(row_sql)


class TestTripleDeclaration:
    """三重声明一致：ORM create_all vs 迁移链 vs ensure 清单。"""

    def test_create_all_matches_migration_chain(self, tmp_path: Path) -> None:
        """ORM create_all 产物 vs 迁移链 head 产物：两新表列集/类型/
        NOT NULL 全等（对照断言——两方不一致静默架空）。"""
        from sqlalchemy import create_engine

        from core.persistence.orm import Base
        from core.persistence.orm.models import VrEquityCurve, VrTradeDetail

        orm_db = tmp_path / "orm.db"
        eng = create_engine(f"sqlite:///{orm_db}")
        Base.metadata.create_all(
            eng, tables=[VrTradeDetail.__table__, VrEquityCurve.__table__])

        mig_db = tmp_path / "mig.db"
        _alembic(mig_db, "upgrade", "head")

        for table in ("vr_trade_detail", "vr_equity_curve"):
            with _conn(orm_db) as c1, _conn(mig_db) as c2:
                orm_cols = [(r[1], r[2].upper(), bool(r[3])) for r in
                            c1.execute(f"PRAGMA table_info({table})")]
                mig_cols = [(r[1], r[2].upper(), bool(r[3])) for r in
                            c2.execute(f"PRAGMA table_info({table})")]
            assert orm_cols == mig_cols, (
                f"{table} ORM/迁移两方列形不一致: {orm_cols} vs {mig_cols}")

    def test_ensure_research_tables_builds_ten(self, tmp_path: Path) -> None:
        """③ensure_research_tables 对空库建出十表（含两新表）。"""
        from core.analysis.executor import ensure_research_tables

        db = tmp_path / "ensure.db"
        ensure_research_tables(db)
        with _conn(db) as conn:
            names = {r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'")}
        expected = {
            "research_hypotheses", "research_log", "analysis_tasks",
            "formula_plugins", "model_plugins", "signal_registry",
            "feature_requests", "validation_records",
            "vr_trade_detail", "vr_equity_curve"}
        assert expected <= names, f"缺表: {expected - names}"

    def test_guard_create_all_then_upgrade_no_crash(
            self, tmp_path: Path) -> None:
        """⑤在场守卫：ensure 先建表（create_all 形库）→ stamp 旧 head →
        upgrade head 零撞表（沿 b8d4f2a6c9e1 先例）。"""
        from core.persistence.db_ext import init_db_ext
        from core.analysis.executor import ensure_research_tables

        db = tmp_path / "guard.db"
        init_db_ext(db)
        ensure_research_tables(db)
        _alembic(db, "stamp", _OLD_HEAD)
        _alembic(db, "upgrade", "head")  # 在场守卫内 return，不撞表


class TestUpgradePath:
    """存量库升级路径：既有数据完好+历史明细零行（零回填的库级面）。"""

    def test_legacy_data_survives_and_no_backfill(
            self, legacy_db: Path) -> None:
        """④旧 head 写入假设+验证记录 → upgrade head：记录完好、
        两明细表零行（历史 289 条零回填——明细行存在性判定可复核性）。"""
        with _conn(legacy_db) as conn:
            conn.execute(
                "INSERT INTO research_hypotheses (hypothesis_id,"
                " conditions, conditions_hash, statement, data_ranges,"
                " ranges_hash, status, created_at, updated_at)"
                " VALUES ('hyp_m1', '{}', 'ch1', 's', '[]', 'rh0',"
                " 'active', '2026-11-02T00:00:00Z',"
                " '2026-11-02T00:00:00Z')")
            conn.execute(
                "INSERT INTO validation_records (record_id, hypothesis_id,"
                " strategy_id, level, segmentation, params, result,"
                " verdict, range_hash, created_at)"
                " VALUES ('rec_hist1', 'hyp_m1', 'st_x',"
                " 'isolated_backtest', '', '{}', '{}', 'pass', 'rh1',"
                " '2026-11-02T00:00:00Z')")
        _alembic(legacy_db, "upgrade", "head")
        with _conn(legacy_db) as conn:
            rec = conn.execute(
                "SELECT record_id, verdict FROM validation_records"
                " WHERE record_id='rec_hist1'").fetchone()
            t_cnt = conn.execute(
                "SELECT COUNT(*) FROM vr_trade_detail").fetchone()[0]
            e_cnt = conn.execute(
                "SELECT COUNT(*) FROM vr_equity_curve").fetchone()[0]
        assert rec == ("rec_hist1", "pass"), "存量记录升级后必须完好"
        assert t_cnt == 0 and e_cnt == 0, "历史记录零回填（明细零行）"
