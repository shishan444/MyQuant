"""1.2 迁移 de92805ed44c：事件表+双域触发器+存量分类。

沿 test_strategy_params_migration.py 的 subprocess alembic 惯例。
两条验证轨：
- 构造轨（fixture 库）：实库四验+触发器 ABORT 双面用例+存量分类
  四类参数化+幂等重跑；
- 真实库对账轨（主库 data/quant.db）：零操作对账五断言（主库不在
  或未升级则 skip——升级入口=验证命令 alembic upgrade head）。

触发器验证必须查实库 sqlite_master（PRAGMA/SELECT），不读声明式
schema——ORM/Alembic 两方不一致时 IF NOT EXISTS 会静默架空（工程坑位）。
"""
from __future__ import annotations

import sqlite3
import subprocess
from pathlib import Path

import pytest

pytestmark = [pytest.mark.integration]

_OLD_HEAD = "d8a2f6c3e9b1"  # lifecycle 迁移前的 head
_REV = "de92805ed44c"
_REPO_ROOT = Path(__file__).resolve().parents[2]
_MAIN_DB = _REPO_ROOT / "data" / "quant.db"

# 主库存量快照（任务书只读核验 2026-10-17：11 行全 draft）——行数
# 自然增长时同步此锚
_MAIN_DB_STRATEGY_ROWS = 11


def _alembic(db: Path, *args: str) -> None:
    subprocess.run(
        ["venv/bin/python", "-m", "alembic", "-x", f"db_path={db}", *args],
        capture_output=True, text=True, check=True, cwd=_REPO_ROOT,
    )


def _conn(db: Path) -> sqlite3.Connection:
    return sqlite3.connect(db)


def _insert_strategy(conn: sqlite3.Connection, sid: str, status: str) -> None:
    conn.execute(
        "INSERT INTO strategy (strategy_id, dna_json, symbol, timeframe,"
        " status, created_at, updated_at) VALUES (?,?,?,?,?,?,?)",
        (sid, "{}", "BTCUSDT", "4h", status,
         "2026-10-17T00:00:00", "2026-10-17T00:00:00"))


def _insert_pt_task(
    conn: sqlite3.Connection, tid: str, sid: str, status: str,
) -> None:
    conn.execute(
        "INSERT INTO pt_task (task_id, status, strategy_id,"
        " dna_snapshot_json, symbol, timeframe, research_status_snapshot,"
        " created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?)",
        (tid, status, sid, "{}", "BTCUSDT", "4h", "unverified",
         "2026-10-17T00:00:00", "2026-10-17T00:00:00"))


def _event_rows(conn: sqlite3.Connection) -> int:
    return conn.execute(
        "SELECT COUNT(*) FROM strategy_transition_event").fetchone()[0]


@pytest.fixture()
def legacy_db(tmp_path: Path) -> Path:
    """旧 head（d8a2f6c3e9b1）空库——触发器与事件表就位前的基线。"""
    db = tmp_path / "mig.db"
    _alembic(db, "upgrade", _OLD_HEAD)
    return db


class TestEventTableSchema:
    """实库四验之 schema 面：事件表结构（直查 PRAGMA，不读声明）。"""

    def test_table_columns_and_index(self, legacy_db: Path) -> None:
        """列集/类型/非空+Index(strategy_id, created_at) 实库在场。"""
        _alembic(legacy_db, "upgrade", "head")
        with _conn(legacy_db) as conn:
            cols = {r[1]: (r[2].upper(), bool(r[3]))
                    for r in conn.execute(
                        "PRAGMA table_info(strategy_transition_event)")}
            indexes = {r[1] for r in conn.execute(
                "PRAGMA index_list(strategy_transition_event)")}
        assert set(cols) == {"event_id", "strategy_id", "from_status",
                             "to_status", "trigger", "evidence_json",
                             "created_at"}
        for name, (typ, notnull) in cols.items():
            assert typ == "TEXT", name
            assert notnull is (name != "evidence_json"), name
        assert "idx_strategy_transition_event_strategy_created" in indexes

    def test_triggers_present_in_sqlite_master(self, legacy_db: Path) -> None:
        """双触发器实库在场（sqlite_master 非声明式 schema）。"""
        _alembic(legacy_db, "upgrade", "head")
        with _conn(legacy_db) as conn:
            names = {r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='trigger'")}
        assert "trg_strategy_status_domain" in names
        assert "trg_strategy_status_insert_domain" in names


class TestTriggerAborts:
    """双触发器 RAISE ABORT 用例（直 SQL 面）。"""

    @pytest.fixture()
    def upgraded_db(self, legacy_db: Path) -> Path:
        with _conn(legacy_db) as conn:
            _insert_strategy(conn, "st_trg", "draft")
            conn.commit()
        _alembic(legacy_db, "upgrade", "head")
        return legacy_db

    def test_update_to_value_outside_domain_aborts(self, upgraded_db: Path):
        """UPDATE status 出六值域 → RAISE ABORT。"""
        with _conn(upgraded_db) as conn:
            with pytest.raises(sqlite3.IntegrityError):
                conn.execute(
                    "UPDATE strategy SET status='bogus'"
                    " WHERE strategy_id='st_trg'")

    def test_insert_outside_initial_set_aborts(self, upgraded_db: Path):
        """INSERT 初始集外 status（candidate）→ RAISE ABORT。"""
        with _conn(upgraded_db) as conn:
            with pytest.raises(sqlite3.IntegrityError):
                conn.execute(
                    "INSERT INTO strategy (strategy_id, dna_json, symbol,"
                    " timeframe, status, created_at, updated_at)"
                    " VALUES ('st_bad', '{}', 'BTCUSDT', '4h', 'candidate',"
                    " '2026-10-18T00:00:00', '2026-10-18T00:00:00')")

    @pytest.mark.parametrize("status", ["draft", "active"])
    def test_insert_initial_set_passes(self, upgraded_db: Path, status: str):
        """INSERT draft/active 放行（初始集内）。"""
        with _conn(upgraded_db) as conn:
            conn.execute(
                "INSERT INTO strategy (strategy_id, dna_json, symbol,"
                " timeframe, status, created_at, updated_at)"
                f" VALUES ('st_ok_{status}', '{{}}', 'BTCUSDT', '4h',"
                f" '{status}', '2026-10-18T00:00:00', '2026-10-18T00:00:00')")
            val = conn.execute(
                "SELECT status FROM strategy"
                f" WHERE strategy_id='st_ok_{status}'").fetchone()[0]
        assert val == status

    def test_insert_default_status_passes(self, upgraded_db: Path):
        """INSERT 不带 status（server_default 'active'）放行。"""
        with _conn(upgraded_db) as conn:
            conn.execute(
                "INSERT INTO strategy (strategy_id, dna_json, symbol,"
                " timeframe, created_at, updated_at)"
                " VALUES ('st_dflt', '{}', 'BTCUSDT', '4h',"
                " '2026-10-18T00:00:00', '2026-10-18T00:00:00')")
            val = conn.execute(
                "SELECT status FROM strategy"
                " WHERE strategy_id='st_dflt'").fetchone()[0]
        assert val == "active"


class TestStockClassification:
    """存量分类四类参数化（构造轨——真实库当前零命中，规则全覆盖
    由 fixture 承载；零事件=纯净性原则）。"""

    @pytest.mark.parametrize("scenario", [
        "active有活跃任务", "active有运行中任务", "active仅终态任务",
        "active无任务", "draft沿用", "archived沿用"])
    def test_classification_rule_and_zero_events(
            self, tmp_path: Path, scenario: str):
        """规则四类+活跃细分：迁移后状态符合预期且事件表零行。"""
        table = {
            "active有活跃任务": ("active", "pending", "paper"),
            "active有运行中任务": ("active", "running", "paper"),
            "active仅终态任务": ("active", "completed", "active"),
            "active无任务": ("active", None, "active"),
            "draft沿用": ("draft", "pending", "draft"),
            "archived沿用": ("archived", "pending", "archived"),
        }
        status, task_status, expected = table[scenario]
        db = tmp_path / f"cls_{abs(hash(scenario)) % 10**10}.db"
        _alembic(db, "upgrade", _OLD_HEAD)
        with _conn(db) as conn:
            _insert_strategy(conn, "st_cls", status)
            if task_status is not None:
                _insert_pt_task(conn, "tk_cls", "st_cls", task_status)
            conn.commit()
        _alembic(db, "upgrade", "head")
        with _conn(db) as conn:
            after = conn.execute(
                "SELECT status FROM strategy"
                " WHERE strategy_id='st_cls'").fetchone()[0]
            events = _event_rows(conn)
        assert after == expected
        assert events == 0

    def test_rerun_data_migration_idempotent(self, tmp_path: Path):
        """幂等重跑安全：升级后手动重跑同款分类 UPDATE——paper 行
        不再命中（非 active），零变化零新事件。"""
        db = tmp_path / "idem.db"
        _alembic(db, "upgrade", _OLD_HEAD)
        with _conn(db) as conn:
            _insert_strategy(conn, "st_idem", "active")
            _insert_pt_task(conn, "tk_idem", "st_idem", "pending")
            conn.commit()
        _alembic(db, "upgrade", "head")
        with _conn(db) as conn:
            cur = conn.execute(
                "UPDATE strategy SET status = 'paper'"
                " WHERE status = 'active' AND strategy_id IN ("
                "  SELECT DISTINCT strategy_id FROM pt_task"
                "  WHERE status IN ('pending','running'))")
            assert cur.rowcount == 0, "已分类行不得再次命中"
            after = conn.execute(
                "SELECT status FROM strategy"
                " WHERE strategy_id='st_idem'").fetchone()[0]
            events = _event_rows(conn)
        assert after == "paper"
        assert events == 0

    def test_downgrade_upgrade_roundtrip(self, tmp_path: Path):
        """降级拆结构（触发器+事件表）数据保留；再升级重建且幂等。"""
        db = tmp_path / "rt.db"
        _alembic(db, "upgrade", _OLD_HEAD)
        with _conn(db) as conn:
            _insert_strategy(conn, "st_rt", "active")
            _insert_pt_task(conn, "tk_rt", "st_rt", "pending")
            conn.commit()
        _alembic(db, "upgrade", "head")
        _alembic(db, "downgrade", _OLD_HEAD)
        with _conn(db) as conn:
            triggers = {r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='trigger'")}
            has_table = conn.execute(
                "SELECT COUNT(*) FROM sqlite_master WHERE type='table'"
                " AND name='strategy_transition_event'").fetchone()[0]
        assert "trg_strategy_status_domain" not in triggers
        assert has_table == 0
        # 数据不回滚：分类值保留（六值域内合法）
        with _conn(db) as conn:
            assert conn.execute(
                "SELECT status FROM strategy"
                " WHERE strategy_id='st_rt'").fetchone()[0] == "paper"
        _alembic(db, "upgrade", "head")
        with _conn(db) as conn:
            assert conn.execute(
                "SELECT status FROM strategy"
                " WHERE strategy_id='st_rt'").fetchone()[0] == "paper"
            assert _event_rows(conn) == 0


class TestMainDbZeroOpReconciliation:
    """真实库对账轨（主库）：零操作对账五断言。

    主库存量 11 行全 draft+无活跃 pt_task——分类规则当前零命中
    （真实零操作）。主库不在或未升级至本 revision 时 skip（升级入口
    =验证命令 venv/bin/python -m alembic upgrade head）。
    """

    def test_main_db_five_assertions(self):
        if not _MAIN_DB.exists():
            pytest.skip("主库 data/quant.db 不在")
        with _conn(_MAIN_DB) as conn:
            ver = conn.execute(
                "SELECT version_num FROM alembic_version").fetchone()
        if ver is None or ver[0] != _REV:
            pytest.skip(f"主库未升级至 {_REV}（先跑 alembic upgrade head）")

        with _conn(_MAIN_DB) as conn:
            rows = conn.execute(
                "SELECT strategy_id, status FROM strategy").fetchall()
            events = _event_rows(conn)
        from core.strategy.lifecycle.machine import WATERLINE_ORDER
        # ① 11=11 行数对账（存量快照锚：行数自然增长时同步）
        assert len(rows) == _MAIN_DB_STRATEGY_ROWS
        # ② 全 draft 状态不变（迁移前即全 draft）
        assert all(status == "draft" for _, status in rows)
        # ③ 零触碰（无任何行被分类改写——分类只动 active 行，库内零 active）
        assert not any(status == "paper" for _, status in rows)
        # ④ 事件表零行（存量分类不产生事件——纯净性原则）
        assert events == 0
        # ⑤ 存量行水位全 0（draft 序 0+事件侧零贡献——推导基线=当前归类态）
        assert WATERLINE_ORDER["draft"] == 0
        assert all(status not in WATERLINE_ORDER
                   or WATERLINE_ORDER[status] == 0
                   for _, status in rows)
