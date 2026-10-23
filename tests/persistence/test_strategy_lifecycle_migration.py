"""1.2 迁移 de92805ed44c：事件表+双域触发器+存量分类。

沿 test_strategy_params_migration.py 的 subprocess alembic 惯例。
两条验证轨：
- 构造轨（fixture 库）：实库四验+触发器 ABORT 双面用例+存量分类
  四类参数化+幂等重跑+自然增长不解耦复证+断言集可红性自证；
- 真实库对账轨（主库 data/quant.db）：迁移不变量常驻断言（结构
  在场+六值域+记录性披露——与库内容自然增长解耦；主库不在或
  结构未就位则 skip）。

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

# 六态值域（迁移正确性不变量——与 strategy-lifecycle spec 同源）
_LIFECYCLE_DOMAIN = {"draft", "active", "paper", "candidate", "live",
                     "archived"}
# 迁移交付的结构面全名清单（sqlite_master 实查锚——禁模糊计数：
# 合法重建场景须红而非静默放过）
_EXPECTED_TRIGGERS = ("trg_strategy_status_domain",
                      "trg_strategy_status_insert_domain")


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


def _structure_present(conn: sqlite3.Connection) -> bool:
    """结构在场探测（skip 判定）：事件表+双触发器齐备。

    结构在场 ⟺ 库已行进至 de92805ed44c 或其任何后继（后续
    migration 不删结构——向后兼容保证；downgrade 才拆结构，回环
    测试在案）。ver==_REV 判定在 head 前进后会使活库恒 skip
    （豁免面自动扩大），故以结构判定充要。"""
    has_table = conn.execute(
        "SELECT COUNT(*) FROM sqlite_master WHERE type='table'"
        " AND name='strategy_transition_event'").fetchone()[0] > 0
    names = {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='trigger'")}
    return has_table and set(_EXPECTED_TRIGGERS) <= names


def _assert_lifecycle_invariants(conn: sqlite3.Connection) -> None:
    """迁移不变量断言集（A1 结构在场+A2 值域不变量）。

    主库对账轨/构造轨自然增长用例/可红性变异轨共用本单源——
    断言与库内容自然增长解耦（行数/状态分布随正常使用变化，
    不是迁移正确性判据；库使用状态经 A3 记录性披露）。"""
    # A1 结构在场：事件表+双触发器全名清单逐一在场（沿本文件
    # test_triggers_present_in_sqlite_master 的 sqlite_master 实查形制）
    has_table = conn.execute(
        "SELECT COUNT(*) FROM sqlite_master WHERE type='table'"
        " AND name='strategy_transition_event'").fetchone()[0]
    assert has_table == 1, (
        "strategy_transition_event 表缺失"
        f"（未升级至 {_REV} 或结构被拆）")
    names = {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='trigger'")}
    for trg in _EXPECTED_TRIGGERS:
        assert trg in names, f"触发器 {trg} 缺失（sqlite_master 实查）"
    # A2 值域不变量：全部行 status ∈ 六值域（存量分类归入六值域的
    # 迁移结果投影，读面兜底）
    rows = conn.execute(
        "SELECT strategy_id, status FROM strategy").fetchall()
    bad = [(sid, status) for sid, status in rows
           if status not in _LIFECYCLE_DOMAIN]
    assert not bad, f"status 越出六值域: {bad}"


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


class TestNaturalGrowthInvariants:
    """库自然增长不解耦性复证：断言集与库内容解耦的持续验证载体。

    模拟活库正常增长路径（draft 直插；非 draft 经 INSERT active
    初始集内插入后 UPDATE 流转至目标态——INSERT 侧触发器仅放行
    {draft, active}，六值域内 UPDATE 放行），断言逻辑仍绿——
    防未来断言改回快照锚。"""

    def test_growth_keeps_invariants_green(self, legacy_db: Path):
        _alembic(legacy_db, "upgrade", "head")
        with _conn(legacy_db) as conn:
            _insert_strategy(conn, "st_grow_draft", "draft")
            _insert_strategy(conn, "st_grow_flow", "active")
            conn.execute(
                "UPDATE strategy SET status='paper'"
                " WHERE strategy_id='st_grow_flow'")
            conn.execute(
                "UPDATE strategy SET status='archived'"
                " WHERE strategy_id='st_grow_flow'")
            conn.commit()
            _assert_lifecycle_invariants(conn)


class TestInvariantRedability:
    """断言集可红性自证（变异自检常驻化）。

    坏形态隔离库上断言集必须红——防断言退化恒真；全部变异在
    fixture 隔离库，绝不动主库。对象=迁移后状态不变量（结构/
    值域），非迁移过程。"""

    def test_a1_red_when_event_table_dropped(self, legacy_db: Path):
        _alembic(legacy_db, "upgrade", "head")
        with _conn(legacy_db) as conn:
            conn.execute("DROP TABLE strategy_transition_event")
            conn.commit()
        with _conn(legacy_db) as conn:
            with pytest.raises(AssertionError,
                               match="strategy_transition_event"):
                _assert_lifecycle_invariants(conn)

    @pytest.mark.parametrize("trg", _EXPECTED_TRIGGERS)
    def test_a1_red_when_trigger_dropped(self, legacy_db: Path, trg: str):
        _alembic(legacy_db, "upgrade", "head")
        with _conn(legacy_db) as conn:
            conn.execute(f"DROP TRIGGER {trg}")
            conn.commit()
        with _conn(legacy_db) as conn:
            with pytest.raises(AssertionError, match=trg):
                _assert_lifecycle_invariants(conn)

    def test_a2_red_on_out_of_domain_row(self, legacy_db: Path):
        """A2 红向：域外值直改被六值域守卫触发器 RAISE ABORT 挡住
        构造不出红——须 DROP TRIGGER 先行解除守卫再注入；DROP 后
        重建同名 no-op 空壳（A1 锚名字在场不查行为），使变异精确
        落在 A2（值域不变量）而非 A1 先红。"""
        _alembic(legacy_db, "upgrade", "head")
        with _conn(legacy_db) as conn:
            _insert_strategy(conn, "st_red", "draft")
            conn.execute("DROP TRIGGER trg_strategy_status_domain")
            conn.execute(
                "CREATE TRIGGER trg_strategy_status_domain "
                "BEFORE UPDATE ON strategy BEGIN SELECT 1; END")
            conn.execute(
                "UPDATE strategy SET status='bogus'"
                " WHERE strategy_id='st_red'")
            conn.commit()
        with _conn(legacy_db) as conn:
            with pytest.raises(AssertionError, match="六值域"):
                _assert_lifecycle_invariants(conn)

    def test_structure_absent_means_not_present(self, legacy_db: Path):
        """skip 分支可触发性：未升级形态（旧 head 空库）结构探测
        必为 False——主库轨据此 skip 并附理由。"""
        with _conn(legacy_db) as conn:
            assert not _structure_present(conn)


class TestMainDbZeroOpReconciliation:
    """真实库对账轨（主库）：迁移不变量常驻断言。

    断言集（_assert_lifecycle_invariants 单源）与库内容自然增长
    解耦——活库随正常使用发生行数增长与状态流转不破坏断言；
    「迁移时刻」语义（分类规则/零事件/幂等）由构造轨完整承接。
    库使用状态（行数/状态分布/版本号）经 A3 记录性披露（非断言）。
    主库不在→skip；结构未就位→skip（无从验证，先跑 alembic
    upgrade head）。
    """

    def test_main_db_five_assertions(self):
        if not _MAIN_DB.exists():
            pytest.skip("主库 data/quant.db 不在")
        with _conn(_MAIN_DB) as conn:
            if not _structure_present(conn):
                pytest.skip(
                    f"主库未升级至 {_REV}（结构不在场——先跑"
                    " alembic upgrade head）")
            _assert_lifecycle_invariants(conn)
            # A3 记录性披露（非断言）：库使用状态随正常使用变化，
            # 不是迁移正确性判据
            dist = conn.execute(
                "SELECT status, COUNT(*) FROM strategy"
                " GROUP BY status ORDER BY status").fetchall()
            ver = conn.execute(
                "SELECT version_num FROM alembic_version").fetchone()[0]
        total = sum(count for _, count in dist)
        print(f"[A3 披露] 主库 strategy 行数={total}"
              f" 状态分布={dict(dist)} alembic_version={ver}")
