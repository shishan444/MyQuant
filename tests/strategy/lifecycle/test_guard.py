"""写面收敛符号级断言（build-strategy-lifecycle 4.1，族 B5 三层形制）。

四断言（tester B4 视角——与 1.2 迁移验收/1.3 白名单单测并存，本文件
收敛「三层守卫组合完整性」视角）：
① UPDATE 触发器只拦非法值——合法值直改不受 DB 拦=诚实边界如实断言
   （防护归④写者唯一纪律，机器可复核目标态，非 DB 运行时强制）；
② INSERT 白名单层②——save_strategy 函数级结构化 422（错误码面）；
③ INSERT 触发器层①——绕过函数直 INSERT 非法初始态收 SQLite
   RAISE ABORT（sqlite3 报错面——与②分立两验收点）；
④ 写者唯一纪律符号级 grep——status 写点全域唯一=lifecycle/service.py
   的 CAS（SQL 字面形全域零命中=无裸 SQL 直写；SQLAlchemy 表达式形
   status= 字面唯一命中=service CAS。动态 values(**fields) 通道的防护
   =白名单显式拒绝 status 键〔1.3 落地，test_repository_strategy_ext
   TestWriteSurfaceLockdown 覆盖〕——文本级与行为级两层互补）。

触发器用例须实库在场（alembic head），不读声明式 schema——ORM
metadata 建库不带触发器（工程坑位：IF NOT EXISTS 静默架空）。
负例自证：④的扫描器对内联已知正例命中（恒空/恒过时负例红）。
"""
from __future__ import annotations

import re
import sqlite3
import subprocess
from pathlib import Path

import pytest

pytestmark = [pytest.mark.integration]

_REPO_ROOT = Path(__file__).resolve().parents[3]
_SCAN_DIRS = ("core", "api", "mcpserver")


def _alembic(db: Path, *args: str) -> None:
    subprocess.run(
        ["venv/bin/python", "-m", "alembic", "-x", f"db_path={db}", *args],
        capture_output=True, text=True, check=True, cwd=_REPO_ROOT,
    )


@pytest.fixture()
def guard_db(tmp_path: Path) -> Path:
    """alembic head 库（双触发器实库在场）+一行 draft。"""
    db = tmp_path / "guard.db"
    _alembic(db, "upgrade", "head")
    with sqlite3.connect(db) as conn:
        conn.execute(
            "INSERT INTO strategy (strategy_id, dna_json, symbol, timeframe,"
            " status, created_at, updated_at)"
            " VALUES ('st_guard', '{}', 'BTCUSDT', '4h', 'draft',"
            " '2026-10-18T00:00:00', '2026-10-18T00:00:00')")
        conn.commit()
    return db


class TestUpdateTriggerHonestBoundary:
    """①UPDATE 触发器只拦非法值（合法值直改不受 DB 拦——如实边界）。"""

    @pytest.mark.parametrize("status", ["active", "paper", "candidate",
                                        "live", "archived"])
    def test_legal_value_direct_update_passes(
            self, guard_db: Path, status: str):
        """六值内合法值直改成功（含初始集外值）——触发器不做合法域
        强制，防护归层②写者唯一纪律（诚实边界，不虚标 DB 防护）。"""
        with sqlite3.connect(guard_db) as conn:
            conn.execute(
                "UPDATE strategy SET status=? WHERE strategy_id='st_guard'",
                (status,))
            after = conn.execute(
                "SELECT status FROM strategy"
                " WHERE strategy_id='st_guard'").fetchone()[0]
        assert after == status

    def test_illegal_value_direct_update_aborts(self, guard_db: Path):
        """六值外值直改→RAISE ABORT（域完整性兜底）。"""
        with sqlite3.connect(guard_db) as conn:
            with pytest.raises(sqlite3.IntegrityError):
                conn.execute(
                    "UPDATE strategy SET status='bogus'"
                    " WHERE strategy_id='st_guard'")


class TestInsertDualLayers:
    """②函数级白名单（结构化 422）与③DB 触发器（RAISE ABORT）分立。"""

    def test_save_strategy_whitelist_structured_422(self, guard_db: Path):
        """层②：save_strategy 传初始集外 status→LifecycleError 结构化
        错误码面（先于触发器——函数级拦截）。"""
        from core.persistence.repository.strategy import save_strategy
        from core.strategy.lifecycle.machine import LifecycleError

        with pytest.raises(LifecycleError) as ei:
            save_strategy(
                guard_db, strategy_id="st_wl", dna_json="{}",
                symbol="BTCUSDT", timeframe="4h", status="paper")
        assert ei.value.status == 422
        assert ei.value.code == "STRATEGY_INITIAL_STATUS_INVALID"
        assert ei.value.detail["allowed"] == ["active", "draft"]

    def test_direct_sql_insert_outside_initial_set_aborts(
            self, guard_db: Path):
        """层①：绕过函数直 INSERT 初始集外 status（live）→SQLite
        RAISE ABORT（sqlite3 报错面——与②的白名单错误面分立，不混写）。"""
        with sqlite3.connect(guard_db) as conn:
            with pytest.raises(sqlite3.IntegrityError):
                conn.execute(
                    "INSERT INTO strategy (strategy_id, dna_json, symbol,"
                    " timeframe, status, created_at, updated_at)"
                    " VALUES ('st_raw', '{}', 'BTCUSDT', '4h', 'live',"
                    " '2026-10-18T00:00:00', '2026-10-18T00:00:00')")


def _iter_py_sources():
    """core/+api/+mcpserver/ 全域 .py 源（扫描域不含 tests——测试侧
    直 SQL 构造属测试输入，非工程写点）。"""
    for d in _SCAN_DIRS:
        for p in (_REPO_ROOT / d).rglob("*.py"):
            if "__pycache__" not in p.parts:
                yield p


def _full_stmt(src: str, hit: int) -> str:
    """命中点所在完整执行语句文本：回溯最近 conn.execute( 后按括号
    配平截取（链式 .where/.values 全含；配平天然跨多行与嵌套调用）。"""
    start = src.rfind("conn.execute(", 0, hit)
    if start < 0:
        start = hit
    depth = 0
    for j in range(start, min(len(src), start + 5000)):
        char = src[j]
        if char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
            if depth == 0:
                return src[start:j + 1]
    return src[start:start + 5000]


def _sqlalchemy_status_writers():
    """update(Strategy) 表达式形全域命中中，语句段含 status= 字面的
    写点清单（(路径, 行号)——文本级 status 直写点枚举）。"""
    writers = []
    for p in _iter_py_sources():
        src = p.read_text(encoding="utf-8")
        idx = 0
        while True:
            i = src.find("update(Strategy)", idx)
            if i < 0:
                break
            if re.search(r"\bstatus\s*=", _full_stmt(src, i)):
                writers.append((p, src[:i].count("\n") + 1))
            idx = i + 1
    return writers


class TestWriterUniqueness:
    """④写者唯一纪律：status 写点全域唯一=lifecycle/service.py CAS。"""

    def test_sql_literal_status_write_zero_hits(self):
        """SQL 字面形「UPDATE strategy SET status」全域零命中=工程内
        无裸 SQL 直写 status（正例自证见 test_scanner_detects）。"""
        for p in _iter_py_sources():
            src = p.read_text(encoding="utf-8")
            assert "UPDATE strategy SET status" not in src, \
                f"{p} 含裸 SQL status 直写"

    def test_sqlalchemy_status_writer_unique(self):
        """SQLAlchemy 表达式形 status= 写点唯一=lifecycle/service.py
        的 CAS（三写点收编后 repository 侧零残留）。"""
        writers = _sqlalchemy_status_writers()
        assert writers, "扫描器失效（恒空——先看负例自证）"
        assert len(writers) == 1, f"status 写点多于唯一 CAS: {writers}"
        path, _line = writers[0]
        assert path.name == "service.py"
        assert "lifecycle" in path.parts, \
            f"唯一写点不在 lifecycle 域: {path}"

    def test_scanner_detects_known_positives(self):
        """负例自证：字面扫描与语句窗口检测对已知正例命中（防扫描器
        恒空/恒过——两检测器各配一条正例）。"""
        positive_sql = "conn.execute(text(\"UPDATE strategy SET status='x'\"))"
        assert "UPDATE strategy SET status" in positive_sql
        positive_orm = (
            "conn.execute(\n"
            "    update(Strategy)\n"
            "    .where(Strategy.strategy_id == sid)\n"
            "    .values(status=target, updated_at=now))\n")
        hit = positive_orm.find("update(Strategy)")
        assert re.search(r"\bstatus\s*=", _full_stmt(positive_orm, hit)), \
            "语句窗口检测漏检链式 values 内的 status="
        # 反例：无 status= 的语句不误报（窗口不越界到后续语句）
        negative_orm = (
            "conn.execute(\n"
            "    update(Strategy)\n"
            "    .where(Strategy.strategy_id == sid)\n"
            "    .values(name='x'))\n"
            "conn.execute(insert(Strategy).values(status='draft'))\n")
        hit2 = negative_orm.find("update(Strategy)")
        assert not re.search(r"\bstatus\s*=",
                             _full_stmt(negative_orm, hit2)), \
            "窗口越界：INSERT 语句的 status 被误记到 UPDATE 头上"


class TestEventAppendOnlyGuard:
    """⑤事件表 append-only 符级守卫（spec「迁移事件表……MUST 为迁移
    审计单一事实源，append-only（无 update/delete 通道）」）。

    双书写形制全域零命中：SQL 字面形（UPDATE/DELETE FROM）+
    SQLAlchemy 表达式形（update()/delete()）——工程内事件表写通道
    唯一合法形=insert（service.py 事件落库）。负例自证防恒空恒过。
    """

    _LITERALS = (
        "UPDATE strategy_transition_event",
        "DELETE FROM strategy_transition_event",
        "update(StrategyTransitionEvent",
        "delete(StrategyTransitionEvent",
    )

    def test_no_event_update_delete_channel_anywhere(self):
        """四面全域零命中=事件表无 update/delete 写通道（append-only
        由「只有 insert 写点+无改删通道」两面合成；写点唯一由
        TestWriterUniqueness 同族视角承载）。"""
        for p in _iter_py_sources():
            src = p.read_text(encoding="utf-8")
            for lit in self._LITERALS:
                assert lit not in src, \
                    f"{p} 含事件表改删通道字面: {lit!r}"

    def test_scanner_detects_known_positives(self):
        """负例自证：四面各对内联已知正例命中（扫描器失效必红）。"""
        positives = [
            'conn.execute(text("UPDATE strategy_transition_event SET'
            ' trigger=:t"))',
            'conn.execute(text("DELETE FROM strategy_transition_event'
            ' WHERE strategy_id=:i"))',
            "conn.execute(update(StrategyTransitionEvent)"
            ".values(trigger='x'))",
            "conn.execute(delete(StrategyTransitionEvent)"
            ".where(StrategyTransitionEvent.strategy_id == sid))",
        ]
        for positive in positives:
            hits = [lit for lit in self._LITERALS if lit in positive]
            assert len(hits) == 1, \
                f"扫描器漏检/误检已知正例: {positive!r} -> {hits}"


class TestQualifiedDeadColumnNoRegression:
    """A1 死列防回潮（build-strategy-lifecycle 5.3④）：strategy.qualified
    零写点三层符号级断言+同名异列守护（backtest_result.qualified 真数据
    活列照常写入——死列处置不得误伤）。"""

    def test_save_strategy_signature_has_no_qualified(self):
        """层①签名：save_strategy 参数签名无 qualified（死列零写点——
        声明后从未写入）。"""
        import inspect

        from core.persistence.repository.strategy import save_strategy

        assert "qualified" not in inspect.signature(
            save_strategy).parameters

    def test_allowed_update_fields_excludes_qualified(self):
        """层②白名单：_ALLOWED_UPDATE_FIELDS 不含 qualified（update
        通道亦零写点）。"""
        from core.persistence.repository.strategy import (
            _ALLOWED_UPDATE_FIELDS,
        )

        assert "qualified" not in _ALLOWED_UPDATE_FIELDS

    def test_no_sql_literal_qualified_write(self):
        """层③SQL 字面：「UPDATE strategy SET qualified」全域零命中
        （core/api/mcpserver 三目录——裸 SQL 直写面亦零）。"""
        for p in _iter_py_sources():
            src = p.read_text(encoding="utf-8")
            assert "UPDATE strategy SET qualified" not in src, \
                f"{p} 含裸 SQL qualified 直写"

    def test_scanner_detects_known_positive(self):
        """负例自证：字面扫描器对已知正例命中（防恒空/恒过）。"""
        positive = "conn.execute(text(\"UPDATE strategy SET qualified=1\"))"
        assert "UPDATE strategy SET qualified" in positive

    def test_backtest_result_qualified_flow_untouched(self):
        """同名异列守护锚：batch.py 的 qualified 流（backtest_result.
        qualified 真数据活列）符号级在场——save_batch_results 调用带
        qualified 实参未被死列处置误伤（行为锚=
        tests/api/test_batch_backtest_feature.py 批量回测既有断言复跑）。"""
        batch_path = _REPO_ROOT / "api" / "routes" / "strategies" / "batch.py"
        src = batch_path.read_text(encoding="utf-8")
        assert "qualified" in src, "batch.py qualified 流失踪（死列误伤）"
        assert re.search(r"qualified\s*=", src), \
            "backtest_result.qualified 写入实参不在场"
