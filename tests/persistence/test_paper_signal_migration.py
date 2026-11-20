"""paper-signal-log 任务 1 迁移轨：pt_signal 建表（b8d4f2a6c9e1）+
pt_fill 核算三列（c2f8e6b1d3a7）。

沿 test_strategy_lifecycle_migration.py 的 subprocess alembic 惯例。
验证面（tasks.md 任务 1 完成标准①②⑦⑧）：
- 实库 PRAGMA 断言（index_list+table_info）——声明≠实存，ORM/Alembic
  两方不一致会静默架空（工程坑位）；
- ORM/Alembic 双方声明逐列一致（ORM create_all 产物 vs 迁移链产物）；
- 存量库升级路径（旧 head 有数据→upgrade head：既有数据完好、
  pt_fill 新列 NULL 零回填）；
- 既有表列零改动（R3：除 pt_fill 追加三列外全部表列集逐位不变）。
"""
from __future__ import annotations

import sqlite3
import subprocess
from pathlib import Path

import pytest

pytestmark = [pytest.mark.integration]

_OLD_HEAD = "a7f3d2c8e9b4"  # 本 change 两条迁移前的 head
_REPO_ROOT = Path(__file__).resolve().parents[2]

# pt_signal 22 列权威字典（design §2 逐列：(列名, 类型, NOT NULL)）
# 类型为 PRAGMA table_info 渲染形（INTEGER/TEXT/REAL）
_SIGNAL_COLUMNS = (
    ("id", "INTEGER", True),
    ("task_id", "TEXT", True),
    ("strategy_id", "TEXT", True),
    ("bar_time", "TEXT", True),
    ("exec_bar_time", "TEXT", True),
    ("seq", "INTEGER", True),
    ("signal_kind", "TEXT", True),
    ("direction", "TEXT", False),
    ("size_fraction", "REAL", False),
    ("gene_hits", "TEXT", True),
    ("indicator_snapshot", "TEXT", True),
    ("outcome", "TEXT", True),
    ("reject_reason", "TEXT", False),
    ("fill_seq", "INTEGER", False),
    ("capped", "INTEGER", False),
    ("equity", "REAL", True),
    ("balance", "REAL", True),
    ("unrealized_pnl", "REAL", True),
    ("position_side", "TEXT", False),
    ("position_quantity", "REAL", False),
    ("position_margin", "REAL", False),
    ("created_at", "TEXT", True),
)

# 迁移交付的索引面全名清单（实库 index_list 锚——禁模糊计数）
_SIGNAL_INDEXES = ("ux_pt_signal", "ix_pt_signal_strategy")


def _alembic(db: Path, *args: str) -> None:
    subprocess.run(
        ["venv/bin/python", "-m", "alembic", "-x", f"db_path={db}", *args],
        capture_output=True, text=True, check=True, cwd=_REPO_ROOT,
    )


def _conn(db: Path) -> sqlite3.Connection:
    return sqlite3.connect(db)


@pytest.fixture()
def legacy_db(tmp_path: Path) -> Path:
    """旧 head（a7f3d2c8e9b4）空库——本 change 两条迁移前的基线。"""
    db = tmp_path / "mig.db"
    _alembic(db, "upgrade", _OLD_HEAD)
    return db


class TestPtSignalSchema:
    """实库四验之 schema 面：pt_signal 表结构（直查 PRAGMA，不读声明）。"""

    def test_signal_table_22_columns(self, legacy_db: Path) -> None:
        """22 列齐：列名/类型/NOT NULL 逐列对权威字典。"""
        _alembic(legacy_db, "upgrade", "head")
        with _conn(legacy_db) as conn:
            cols = [(r[1], r[2].upper(), bool(r[3]))
                    for r in conn.execute("PRAGMA table_info(pt_signal)")]
        assert cols == list(_SIGNAL_COLUMNS), (
            f"pt_signal 列形与权威字典不符: {cols}")

    def test_signal_indexes_in_pragma(self, legacy_db: Path) -> None:
        """两索引实库在场（唯一键 ux_pt_signal+二级 ix_pt_signal_strategy）
        +唯一性位断言。"""
        _alembic(legacy_db, "upgrade", "head")
        with _conn(legacy_db) as conn:
            rows = list(conn.execute("PRAGMA index_list(pt_signal)"))
        by_name = {r[1]: r for r in rows}
        for name in _SIGNAL_INDEXES:
            assert name in by_name, f"索引 {name} 实库缺失"
        # index_list 第 3 列 origin（c=CREATE INDEX）、第 2 列 unique
        assert by_name["ux_pt_signal"][2] == 1, "ux_pt_signal 须为唯一索引"
        assert by_name["ix_pt_signal_strategy"][2] == 0, \
            "ix_pt_signal_strategy 须为非唯一"
        # 唯一键列序=幂等键语义 (task_id, exec_bar_time, seq)
        with _conn(legacy_db) as conn:
            cols = [r[2] for r in conn.execute(
                "PRAGMA index_info(ux_pt_signal)")]
        assert cols == ["task_id", "exec_bar_time", "seq"]

    def test_pt_fill_three_columns_present(self, legacy_db: Path) -> None:
        """pt_fill 三列实存（全 REAL+NULLable）。"""
        _alembic(legacy_db, "upgrade", "head")
        with _conn(legacy_db) as conn:
            cols = {r[1]: (r[2].upper(), bool(r[3]))
                    for r in conn.execute("PRAGMA table_info(pt_fill)")}
        for col in ("equity_before", "available_before", "margin_used"):
            assert col in cols, f"pt_fill.{col} 实库缺失"
            assert cols[col] == ("REAL", False), \
                f"pt_fill.{col} 须为 REAL NULLable: {cols[col]}"


class TestOrmAlembicDualDeclaration:
    """ORM/Alembic 双方声明逐列一致（对照断言——两方不一致静默架空）。"""

    def test_create_all_matches_migration_chain(self, tmp_path: Path):
        """ORM create_all 产物 vs 迁移链 head 产物：全表列集/类型/
        NOT NULL 全等（pt_signal 与 pt_fill 增列是本 change 面，
        两侧全等即双声明一致的强证据）。"""
        from sqlalchemy import create_engine

        from core.persistence.orm import Base

        orm_db = tmp_path / "orm.db"
        engine = create_engine(f"sqlite:///{orm_db}")
        Base.metadata.create_all(engine)
        engine.dispose()

        chain_db = tmp_path / "chain.db"
        _alembic(chain_db, "upgrade", "head")

        def _schema(db: Path):
            conn = sqlite3.connect(str(db))
            out = {}
            for (t,) in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' "
                "AND name NOT LIKE 'sqlite_%' AND name != 'alembic_version'"
            ):
                out[t] = {c[1]: (c[2].upper(), bool(c[3]))
                          for c in conn.execute(f"PRAGMA table_info({t})")}
            conn.close()
            return out

        a, b = _schema(orm_db), _schema(chain_db)
        assert set(a) == set(b), f"表集差异: {set(a) ^ set(b)}"
        for t in sorted(a):
            assert set(a[t]) == set(b[t]), f"{t} 列集差异: {set(a[t]) ^ set(b[t])}"
            assert a[t] == b[t], f"{t} 列形差异: ORM={a[t]} 链={b[t]}"


class TestExistingTablesUnchanged:
    """R3 既有表列零改动：升级前后全部表列集逐位对照（唯一允许差异=
    pt_fill 追加三列；列序对照排除该表）。"""

    def test_existing_columns_untouched(self, legacy_db: Path) -> None:
        with _conn(legacy_db) as conn:
            before = {
                t: [r[1] for r in conn.execute(f"PRAGMA table_info({t})")]
                for (t,) in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' "
                    "AND name NOT LIKE 'sqlite_%'"
                    " AND name != 'alembic_version'")}
        _alembic(legacy_db, "upgrade", "head")
        with _conn(legacy_db) as conn:
            after = {
                t: [r[1] for r in conn.execute(f"PRAGMA table_info({t})")]
                for (t,) in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' "
                    "AND name NOT LIKE 'sqlite_%'"
                    " AND name != 'alembic_version'")}

        assert set(before) < set(after), "升级只增不改表集"
        new_tables = set(after) - set(before)
        # pt_signal（本 change）+ 验证明细两表（后续 add-validation-detail
        # 追加迁移——head 上移后同链建出，非本 change 爆炸半径）
        assert new_tables == {"pt_signal", "vr_trade_detail",
                              "vr_equity_curve", "live_decisions",
                              "vr_signal_trace"}, \
            f"新表集: {new_tables}"  # live_decisions/vr_signal_trace 随 head 上移同链建出
        for t in sorted(before):
            if t == "pt_task":
                # 追加面（paper-trading-revamp 组 B + sandbox-and-report-
                # supplement D3② + fix-replay-task-lifecycle origin 列）：
                # 均表尾追加
                assert after[t][:len(before[t])] == before[t], \
                    "pt_task 既有列不得改动（含列序）"
                assert after[t][len(before[t]):] == [
                    "first_met_at", "halt_drawdown_pct", "halted_at",
                    "halt_reason", "origin"]
                continue
            if t == "pt_equity":
                # 追加面（paper-trading-revamp 组 E）：既有列前缀逐位保持
                # +新增恰一列 trail_sl_price（ALTER ADD COLUMN 表尾追加）
                assert after[t][:len(before[t])] == before[t], \
                    "pt_equity 既有列不得改动（含列序）"
                assert after[t][len(before[t]):] == ["trail_sl_price"], \
                    f"pt_equity 新增恰 trail_sl_price: {after[t][len(before[t]):]}"
                continue
            if t == "pt_fill":
                # 追加面：既有列前缀逐位保持+新增恰三列（ALTER ADD COLUMN
                # 语义=表尾追加）
                assert after[t][:len(before[t])] == before[t], \
                    "pt_fill 既有列不得改动（含列序）"
                assert set(after[t]) - set(before[t]) == {
                    "equity_before", "available_before", "margin_used"}
            elif t == "live_trading_task":
                assert after[t][:len(before[t])] == before[t], \
                    f"{t} 既有列零改动: {before[t]} → {after[t]}"
                assert after[t][len(before[t]):] == ["error_detail"], \
                    f"live_trading_task 新增恰 error_detail: {after[t][len(before[t]):]}"
            elif t == "validation_records":
                # add-vr-signal-trace 组 1：表尾追加三布尔（signal_trace/
                # trade_semantics/stop_price）——既有列零改动（含列序）
                assert after[t][:len(before[t])] == before[t], \
                    f"{t} 既有列零改动（含列序）: {before[t]} → {after[t]}"
                assert after[t][len(before[t]):] == [
                    "signal_trace", "trade_semantics", "stop_price"], \
                    f"validation_records 新增恰三布尔: {after[t][len(before[t]):]}"
            else:
                assert after[t] == before[t], \
                    f"{t} 既有列零改动（含列序）: {before[t]} → {after[t]}"


class TestStockUpgradePath:
    """⑦存量库升级路径：旧库有业务数据→升级→既有数据完好、新列 NULL。"""

    def test_stock_rows_survive_and_new_columns_null(
            self, legacy_db: Path) -> None:
        with _conn(legacy_db) as conn:
            conn.execute(
                "INSERT INTO pt_task (task_id, status, strategy_id,"
                " dna_snapshot_json, symbol, timeframe,"
                " research_status_snapshot, created_at, updated_at)"
                " VALUES ('tk_sig', 'running', 'st_sig', '{}', 'BTCUSDT',"
                " '4h', 'unverified', '2026-10-28T00:00:00',"
                " '2026-10-28T00:00:00')")
            conn.execute(
                "INSERT INTO pt_fill (task_id, bar_time, seq, kind, side,"
                " price, quantity, fee, realized_pnl, reason)"
                " VALUES ('tk_sig', '2026-10-28T00:00:00', 1, 'open',"
                " 'long', 100.0, 1.5, 0.15, NULL, 'signal')")
            conn.commit()
        _alembic(legacy_db, "upgrade", "head")
        with _conn(legacy_db) as conn:
            task_ok = conn.execute(
                "SELECT status, strategy_id FROM pt_task"
                " WHERE task_id='tk_sig'").fetchone()
            fill = conn.execute(
                "SELECT kind, price, equity_before, available_before,"
                " margin_used FROM pt_fill WHERE task_id='tk_sig'"
                " AND seq=1").fetchone()
        assert task_ok == ("running", "st_sig"), "存量 pt_task 行完好"
        assert fill == ("open", 100.0, None, None, None), \
            "存量 pt_fill 行既有值完好+新三列 NULL（零回填）"

    def test_upgrade_idempotent_rerun(self, legacy_db: Path) -> None:
        """二次 upgrade 幂等（版本表短路）。"""
        _alembic(legacy_db, "upgrade", "head")
        _alembic(legacy_db, "upgrade", "head")  # 不炸即幂等
