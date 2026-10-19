"""delete-task-reentry-guard 1.2：DELETE 补回流四路径断言锚。

契约源=design 卡 E（方案甲：路由层删除完成后补一次 paper→active
回流尝试，trigger=task_end，evidence={task_id, final_status} 取删除
前内存持有行；全部边界由 transit 单点承担；失败仅记日志不绑架删除；
响应契约零变化）。

构造形制按 design「测试状态构造」节三要素（不真实复现竞态，直构
库状态）：终态 pt_task 行在场+strategy.status='paper'+该策略无
task_end 迁移事件。基座沿 tests/paper_trading/test_api_paper.py
（TestClient 不进 with 块+app.state 手动挂载；任务行经仓库直建/
update_task 合法迁移链推终态——change 4 后终态查询为短路形态，
直插库行即无影响）。

四路径：
①补尝试（三终态参数化）——迁移发生+事件落库（trigger=task_end，
  evidence 两键指向已删任务）；
②幂等（回流已发生）——already_in_status 无第二次迁移事件；
③幂等（另有活跃任务）——active_tasks_remain 正当不触发；
④失败语义——transit 抛 LifecycleError 时删除不回滚不 500。

边界三则：409 stop first 早退零回流副作用；active 存量（非 paper 态
未上盘）路由无前置判态不炸；paper 任务在场期间策略被 manual_archive
归档（真实非法前态面——archived→active 边不在矩阵）被吞不炸。
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

import pytest

pytestmark = [pytest.mark.integration]

BASE_TIME = "2026-10-01T00:00:00+00:00"
ROUTE_LOGGER = "api.routes.paper_trading"  # 被测路由 logger（error/info/debug 三分支所在）


# ── 基建（沿 test_api_paper.py 形制） ──


@pytest.fixture
def paper_api(tmp_path: Path):
    """(client, db)：REST 全链 + 同步调度器（无守护线程）。"""
    from fastapi.testclient import TestClient

    from api.app import create_app
    from core.paper_trading.runtime.scheduler import PaperScheduler
    from core.persistence.db_ext import init_db_ext

    db = tmp_path / "paper_api.db"
    data_dir = tmp_path / "market"
    data_dir.mkdir()
    init_db_ext(db)
    app = create_app(db_path=db, data_dir=data_dir)
    app.state.db_path = db
    app.state.data_dir = data_dir
    app.state.paper_scheduler = PaperScheduler(db_path=db, data_dir=data_dir)
    yield TestClient(app), db


def _seed_strategy(db: Path, strategy_id: str = "st-1", *,
                   status: str = "active") -> str:
    """造策略库条目（初始态白名单 {draft, active} 内直建）。"""
    from core.persistence.repository.strategy import save_strategy
    from tests.helpers.data_factory import make_dna

    save_strategy(
        db, strategy_id=strategy_id, dna_json=make_dna(
            timeframe="4h", symbol="BTCUSDT", leverage=3).to_json(),
        symbol="BTCUSDT", timeframe="4h", status=status,
        gene_signature=f"sig_{strategy_id}")
    return strategy_id


def _create_task_via_post(client, strategy_id: str = "st-1") -> str:
    """POST /tasks 建任务（路由内同事务 mount：active→paper+事件）。"""
    resp = client.post("/api/paper-trading/tasks",
                       json={"strategy_id": strategy_id})
    assert resp.status_code == 201, resp.text
    return resp.json()["task_id"]


def _push_task_terminal(db: Path, task_id: str, status: str) -> None:
    """仓库通道推终态（合法迁移链：completed 两跳；stopped/error
    自 pending 单跳——缺陷⑤守门后非法直迁被拒）。"""
    from core.persistence.repository import paper

    if status == "completed":
        paper.update_task(db, task_id, status="running",
                          started_at=BASE_TIME)
        paper.update_task(db, task_id, status="completed",
                          stopped_at=BASE_TIME)
    else:
        paper.update_task(db, task_id, status=status, started_at=BASE_TIME,
                          stopped_at=BASE_TIME)


def _seed_terminal_task(db: Path, task_id: str, *,
                        strategy_id: str = "st-1",
                        status: str = "completed") -> str:
    """仓库直建终态任务（测试直建通道：不查 strategy 前置态、不触发
    mount——回流已发生/存量态构造用）。"""
    from core.persistence.repository import paper

    paper.create_task(
        db, task_id=task_id, strategy_id=strategy_id,
        dna_snapshot_json='{"risk_genes": {"leverage": 2}}',
        symbol="BTCUSDT", timeframe="4h",
        research_status_snapshot="unverified")
    _push_task_terminal(db, task_id, status)
    return task_id


def _seed_active_task(db: Path, task_id: str, *,
                      strategy_id: str = "st-1",
                      status: str = "pending") -> str:
    """仓库直建活跃任务（pending 直插；running 一跳合法前态）。"""
    from core.persistence.repository import paper

    paper.create_task(
        db, task_id=task_id, strategy_id=strategy_id,
        dna_snapshot_json="{}", symbol="BTCUSDT", timeframe="4h",
        research_status_snapshot="unverified")
    if status == "running":
        paper.update_task(db, task_id, status="running")
    else:
        assert status == "pending"
    return task_id


def _strategy_status(db: Path, strategy_id: str) -> str:
    from sqlalchemy import text

    from core.persistence.orm.engine import engine_for

    with engine_for(db).connect() as conn:
        return conn.execute(text("SELECT status FROM strategy"
                                 " WHERE strategy_id=:i"),
                            {"i": strategy_id}).scalar_one()


def _events_by_trigger(db: Path, strategy_id: str) -> dict:
    """{trigger: [(from,to), ...]}——按 trigger 索引规避 created_at
    同刻排序不稳（append-only 表无次序键保证）。"""
    from sqlalchemy import text

    from core.persistence.orm.engine import engine_for

    with engine_for(db).connect() as conn:
        rows = conn.execute(text(
            "SELECT from_status, to_status, trigger FROM"
            " strategy_transition_event WHERE strategy_id=:i"),
            {"i": strategy_id}).all()
    out: dict = {}
    for frm, to, trig in rows:
        out.setdefault(trig, []).append((frm, to))
    return out


def _task_end_evidences(db: Path, strategy_id: str) -> list:
    """trigger=task_end 事件的 evidence 载荷清单（json 解析后）。"""
    from sqlalchemy import text

    from core.persistence.orm.engine import engine_for

    with engine_for(db).connect() as conn:
        raws = conn.execute(text(
            "SELECT evidence_json FROM strategy_transition_event"
            " WHERE strategy_id=:i AND trigger='task_end'"),
            {"i": strategy_id}).scalars().all()
    return [json.loads(r) for r in raws]


def _task_exists(db: Path, task_id: str) -> bool:
    from core.persistence.repository import paper

    return paper.get_task(db, task_id) is not None


# ── 路径①：补尝试——删除完成后回流迁移+事件落库 ──


class TestReentryMigrationOnDelete:
    @pytest.mark.parametrize("final_status", ["completed", "stopped", "error"])
    def test_delete_migrates_paper_to_active_with_event(
            self, paper_api, final_status) -> None:
        """三要素构造态（paper+终态任务+无 task_end 事件）→ DELETE：
        策略回流 active+事件 (paper, active, task_end) 落库+evidence
        两键指向已删任务（task_id=被删行、final_status=删除前终态——
        内存持有非重查）。三终态各自触发。"""
        client, db = paper_api
        _seed_strategy(db, status="active")
        tid = _create_task_via_post(client)   # mount: active→paper（事件1）
        assert _strategy_status(db, "st-1") == "paper", "构造前置：在验态"
        _push_task_terminal(db, tid, final_status)

        resp = client.delete(f"/api/paper-trading/tasks/{tid}")

        assert resp.status_code == 200
        assert resp.json() == {"deleted": True}, "响应契约零变化"
        assert _strategy_status(db, "st-1") == "active", "回流迁移发生"
        events = _events_by_trigger(db, "st-1")
        assert events.get("mount") == [("active", "paper")]
        assert events.get("task_end") == [("paper", "active")], \
            "补回流事件落库"
        assert _task_end_evidences(db, "st-1") == [
            {"task_id": tid, "final_status": final_status}], \
            "evidence 含已删 task_id+删除前终态"
        assert not _task_exists(db, tid), "任务行已删（事件证据非行残留）"


# ── 路径②：幂等——回流已发生，无第二次迁移 ──


class TestIdempotentAfterReentry:
    def test_delete_when_already_active_no_second_event(
            self, paper_api, caplog) -> None:
        """回流已发生态（策略 active+已有 1 条 task_end 事件）→ 删除
        同策略另一终态任务：transit 同态事前入口 already_in_status
        正当不触发——不产生第二条 task_end 事件+响应正常。"""
        client, db = paper_api
        from core.strategy.lifecycle import transit

        _seed_strategy(db, status="active")
        tid_a = _create_task_via_post(client)  # mount（事件1）
        _push_task_terminal(db, tid_a, "completed")
        # 真实通道执行一次回流（构造「窗口已过」态：策略 active+
        # 事件表已有 (paper, active, task_end)）
        transit(db, "st-1", target="active", trigger="task_end",
                evidence={"task_id": tid_a, "final_status": "completed"})
        assert _strategy_status(db, "st-1") == "active", "构造前置：已回流"
        tid_b = _seed_terminal_task(db, "pt_reentry_done_b")

        with caplog.at_level(logging.DEBUG, logger=ROUTE_LOGGER):
            resp = client.delete(f"/api/paper-trading/tasks/{tid_b}")

        assert resp.status_code == 200
        assert resp.json() == {"deleted": True}
        events = _events_by_trigger(db, "st-1")
        assert events.get("task_end") == [("paper", "active")], \
            "无第二次 task_end 迁移事件（仍仅回流时那一条）"
        assert _strategy_status(db, "st-1") == "active", "态不抖动"
        assert any("already_in_status" in m for m in caplog.messages), \
            "not_migrated 分支可达（already_in_status）"
        assert not _task_exists(db, tid_b)


# ── 路径③：幂等——同策略另有活跃任务，正当不触发 ──


class TestIdempotentSiblingActiveTask:
    @pytest.mark.parametrize("sibling", ["pending", "running"])
    def test_delete_with_active_sibling_not_migrated(
            self, paper_api, caplog, sibling) -> None:
        """paper 态+终态任务 A+同策略活跃任务 B（pending/running）→
        删除 A：task_end 多任务守卫 active_tasks_remain 正当不触发
        ——无迁移事件+策略仍 paper+B 不被误删。"""
        client, db = paper_api
        _seed_strategy(db, status="active")
        tid_a = _create_task_via_post(client)  # mount（事件1）
        _push_task_terminal(db, tid_a, "completed")
        tid_b = _seed_active_task(db, f"pt_sibling_{sibling}", status=sibling)

        with caplog.at_level(logging.DEBUG, logger=ROUTE_LOGGER):
            resp = client.delete(f"/api/paper-trading/tasks/{tid_a}")

        assert resp.status_code == 200
        assert resp.json() == {"deleted": True}
        assert _strategy_status(db, "st-1") == "paper", \
            "另有活跃任务正当不回流"
        assert "task_end" not in _events_by_trigger(db, "st-1"), \
            "零迁移事件"
        assert any("active_tasks_remain" in m for m in caplog.messages), \
            "not_migrated 分支可达（active_tasks_remain）"
        assert not _task_exists(db, tid_a), "目标任务已删"
        assert _task_exists(db, tid_b), "旁系活跃任务不被误删"


# ── 路径④：失败语义——LifecycleError 不绑架删除 ──


class TestFailureSemantics:
    def test_delete_survives_transit_lifecycle_error(
            self, paper_api, monkeypatch, caplog) -> None:
        """transit 抛 LifecycleError（构造 STRATEGY_TRANSITION_RACE）：
        删除响应仍 {"deleted": True}+error 日志在案（含 code/message）+
        任务行确已删除——删除是既成事实不回滚。调用形制锚：补尝试
        时点任务行已不在库（删除先于回流）。"""
        client, db = paper_api
        import core.strategy.lifecycle as lifecycle_mod
        from core.strategy.lifecycle.machine import LifecycleError

        _seed_strategy(db, status="active")
        tid = _create_task_via_post(client)  # mount（事件1）
        _push_task_terminal(db, tid, "completed")
        assert _strategy_status(db, "st-1") == "paper"

        calls: list = []

        def _boom(db_path, strategy_id, *, target, trigger,
                  evidence=None, conn=None):
            from core.persistence.repository import paper as paper_repo

            calls.append({"strategy_id": strategy_id, "target": target,
                          "trigger": trigger, "evidence": evidence})
            assert paper_repo.get_task(db_path, tid) is None, \
                "补尝试时点任务行已删（执行序：删除完成先于回流）"
            raise LifecycleError(
                code="STRATEGY_TRANSITION_RACE",
                message="状态迁移竞争：构造失败面")

        monkeypatch.setattr(lifecycle_mod, "transit", _boom)

        with caplog.at_level(logging.ERROR, logger=ROUTE_LOGGER):
            resp = client.delete(f"/api/paper-trading/tasks/{tid}")

        assert resp.status_code == 200, "失败不 500 不 409"
        assert resp.json() == {"deleted": True}, "删除事实不因回流失败回滚"
        assert len(calls) == 1, "补回流尝试恰好一次"
        assert calls[0]["strategy_id"] == "st-1"
        assert calls[0]["target"] == "active"
        assert calls[0]["trigger"] == "task_end"
        assert calls[0]["evidence"] == {
            "task_id": tid, "final_status": "completed"}, \
            "evidence 两键满足 task_end 凭证（credential 必填键）"
        errors = [m for r, m in zip(caplog.records, caplog.messages)
                  if r.levelno == logging.ERROR]
        assert errors, "error 日志在案"
        assert any("STRATEGY_TRANSITION_RACE" in m
                   and "状态迁移竞争" in m for m in errors), \
            "日志含 code/message（沿 _replay_task_end 形制）"
        assert not _task_exists(db, tid), "任务行确已删除"
        assert _strategy_status(db, "st-1") == "paper", "回流失败不迁移"
        assert "task_end" not in _events_by_trigger(db, "st-1"), \
            "零迁移事件"

    def test_delete_archived_strategy_illegal_transition_swallowed(
            self, paper_api, caplog) -> None:
        """真实非法前态面（非 mock）：任务在场期间策略经 manual_archive
        归档（paper→archived 合法边）→ 删除补回流命中边校验
        STRATEGY_ILLEGAL_TRANSITION（archived→active 不在矩阵）→
        LifecycleError 被路由吞：error 日志在案+删除响应不变。"""
        client, db = paper_api
        from core.strategy.lifecycle import transit

        _seed_strategy(db, status="active")
        tid = _create_task_via_post(client)  # mount（事件1）
        _push_task_terminal(db, tid, "completed")
        transit(db, "st-1", target="archived", trigger="manual_archive")
        assert _strategy_status(db, "st-1") == "archived", "构造前置：已归档"

        with caplog.at_level(logging.ERROR, logger=ROUTE_LOGGER):
            resp = client.delete(f"/api/paper-trading/tasks/{tid}")

        assert resp.status_code == 200
        assert resp.json() == {"deleted": True}, "真实异常面同样不绑架删除"
        assert _strategy_status(db, "st-1") == "archived", "态不被破坏"
        assert any("STRATEGY_ILLEGAL_TRANSITION" in m
                   for m in caplog.messages), "error 日志含 code"
        assert not _task_exists(db, tid)
        assert "task_end" not in _events_by_trigger(db, "st-1")


# ── 边界：409 早退零回流副作用 / 非 paper 态无前置判态不炸 ──


class TestDeleteBoundaries:
    @pytest.mark.parametrize("status", ["pending", "running"])
    def test_delete_active_task_409_no_reentry_side_effects(
            self, paper_api, status) -> None:
        """既有行为保持（409 stop first）+回流不先于删除门槛：活跃
        任务删除被拒时零回流副作用——任务行保留+策略仍 paper+零
        task_end 事件。"""
        client, db = paper_api
        from core.persistence.repository import paper

        _seed_strategy(db, status="active")
        tid = _create_task_via_post(client)  # mount（事件1）
        if status == "running":
            paper.update_task(db, tid, status="running")

        resp = client.delete(f"/api/paper-trading/tasks/{tid}")

        assert resp.status_code == 409
        assert resp.json()["detail"] == "stop first"
        assert _task_exists(db, tid), "任务行保留"
        assert _strategy_status(db, "st-1") == "paper", "未回流"
        assert set(_events_by_trigger(db, "st-1")) == {"mount"}, \
            "仅 mount 事件，零 task_end（409 早退于补回流）"

    def test_delete_stock_active_strategy_not_migrated_no_crash(
            self, paper_api, caplog) -> None:
        """非 paper 态（active 存量未上盘，零事件）→ DELETE：路由无
        前置判态——transit 同态入口 already_in_status 正当不触发，
        不抛不炸+响应契约不变+零事件。"""
        client, db = paper_api
        _seed_strategy(db, status="active")  # 存量：从未上盘
        tid = _seed_terminal_task(db, "pt_stock_active")

        with caplog.at_level(logging.DEBUG, logger=ROUTE_LOGGER):
            resp = client.delete(f"/api/paper-trading/tasks/{tid}")

        assert resp.status_code == 200
        assert resp.json() == {"deleted": True}
        assert _strategy_status(db, "st-1") == "active", "存量态不动"
        assert _events_by_trigger(db, "st-1") == {}, \
            "零迁移事件（存量从未上盘）"
        assert any("already_in_status" in m for m in caplog.messages)
        assert not _task_exists(db, tid)
