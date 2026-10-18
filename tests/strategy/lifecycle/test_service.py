"""迁移服务+凭证判定+水位推导测试（build-strategy-lifecycle 2.1-2.3）。

三族返回全路径+CAS 并发两分支（同连接注入器模拟载入与 CAS 之间的
变更窗口）+conn 透传同事务+evidence 按 trigger 分派+not_migrated
六+二场景+错误三码 detail 面；credential 三态与守卫两分支；水位
五类（含往返不回落与 revalidate 不归零）+纯查询断言。

前置态构造用直 SQL 合法值直改（UPDATE 触发器只拦六值外——诚实
边界，被测对象是 transit 自身，构造不依赖被测对象）。
"""
from __future__ import annotations

import inspect
import json
from pathlib import Path

import pytest
from sqlalchemy import Update, text

from core.persistence.db_ext import init_db_ext
from core.persistence.orm.engine import engine_for
from core.persistence.repository import strategy as repo
from core.strategy.lifecycle import credential
from core.strategy.lifecycle.machine import LifecycleError, SUCCESSORS
from core.strategy.lifecycle.service import (
    NOT_MIGRATED_REASONS, compute_waterline, transit)

pytestmark = [pytest.mark.integration]

_T = "2026-10-18T00:00:00"


@pytest.fixture
def db(tmp_path: Path) -> Path:
    path = tmp_path / "svc.db"
    init_db_ext(path)
    return path


def _add(db, sid, status="draft", parent=None):
    repo.save_strategy(db, strategy_id=sid, dna_json="{}", symbol="BTCUSDT",
                       timeframe="4h", status=status, parent_ids=parent,
                       gene_signature=f"sig-{sid}")


def _status(db, sid) -> str:
    engine = engine_for(db)
    with engine.connect() as conn:
        return conn.execute(text(
            "SELECT status FROM strategy WHERE strategy_id=:i"),
            {"i": sid}).scalar_one()


def _set_status(db, sid, status):
    engine = engine_for(db)
    with engine.begin() as conn:
        conn.execute(text(
            "UPDATE strategy SET status=:s WHERE strategy_id=:i"),
            {"s": status, "i": sid})


def _events(db, sid=None):
    engine = engine_for(db)
    with engine.connect() as conn:
        if sid is None:
            return conn.execute(text(
                "SELECT COUNT(*) FROM strategy_transition_event")).scalar_one()
        return conn.execute(text(
            "SELECT from_status, to_status, trigger, evidence_json"
            " FROM strategy_transition_event WHERE strategy_id=:i"
            " ORDER BY created_at"), {"i": sid}).all()


def _add_event(db, sid, f, t, trigger):
    engine = engine_for(db)
    with engine.begin() as conn:
        conn.execute(text(
            "INSERT INTO strategy_transition_event (event_id, strategy_id,"
            " from_status, to_status, trigger, created_at)"
            " VALUES (:e, :i, :f, :t, :g, :c)"),
            {"e": f"ev_{f}_{t}_{trigger}", "i": sid, "f": f, "t": t,
             "g": trigger, "c": _T})


def _add_pass_record(db, hyp, sid):
    """直插 hypothesis+verdict=pass 记录（credential 凭证构造）。

    hash 按 hyp 参数化避开 (conditions_hash, ranges_hash) 唯一键；
    已存在同 id hypothesis 时跳过插入（多策略挂同一假设场景）。
    """
    engine = engine_for(db)
    with engine.begin() as conn:
        exists = conn.execute(text(
            "SELECT 1 FROM research_hypotheses"
            " WHERE hypothesis_id=:h"), {"h": hyp}).first()
        if exists is None:
            conn.execute(text(
                "INSERT INTO research_hypotheses (hypothesis_id, status,"
                " conditions, conditions_hash, statement, data_ranges,"
                " ranges_hash, created_at, updated_at)"
                " VALUES (:h, 'active', '{}', :ch, 's', '[]', :rh, :t, :t)"),
                {"h": hyp, "ch": f"ch_{hyp}", "rh": f"rh_{hyp}", "t": _T})
        conn.execute(text(
            "INSERT INTO validation_records (record_id, hypothesis_id,"
            " strategy_id, level, params, verdict, range_hash, created_at)"
            " VALUES (:r, :h, :s, 'isolated_backtest', '{}', 'pass',"
            " 'rh1', :t)"),
            {"r": f"vr_{sid}", "h": hyp, "s": sid, "t": _T})


def _add_task(db, tid, sid, status):
    engine = engine_for(db)
    with engine.begin() as conn:
        conn.execute(text(
            "INSERT INTO pt_task (task_id, status, strategy_id,"
            " dna_snapshot_json, symbol, timeframe,"
            " research_status_snapshot, created_at, updated_at)"
            " VALUES (:t, :s, :i, '{}', 'BTCUSDT', '4h', 'unverified',"
            " :c, :c)"),
            {"t": tid, "s": status, "i": sid, "c": _T})


class _RaceInjector:
    """CAS 窗口注入器：拦截首条 strategy UPDATE，先经同一连接把
    status 改成注入值（模拟载入与 CAS 之间的并发变更——同连接无
    跨连接锁问题，事务语义保真）。"""

    def __init__(self, conn, inject_status: str):
        self._conn = conn
        self._inject = inject_status
        self._done = False

    def execute(self, stmt, *args, **kwargs):
        if (not self._done and isinstance(stmt, Update)
                and stmt.table.name == "strategy"):
            self._done = True
            self._conn.execute(text(
                "UPDATE strategy SET status=:s"), {"s": self._inject})
        return self._conn.execute(stmt, *args, **kwargs)


class TestSuccessFamily:
    """成功族：首迁+事件落库+evidence 序列化。"""

    def test_draft_to_active_with_pass_credential(self, db):
        """draft→active（credential）：凭证成立，事件含 evidence_json。"""
        _add(db, "st_ok", parent="hyp_ok")
        _add_pass_record(db, "hyp_ok", "st_ok")
        out = transit(db, "st_ok", target="active", trigger="credential",
                      evidence={"record_id": "vr_st_ok"})
        assert out["migrated"] is True
        assert out["from_status"] == "draft" and out["to_status"] == "active"
        assert out["idempotent"] is False and out["event_id"]
        assert _status(db, "st_ok") == "active"
        rows = _events(db, "st_ok")
        assert len(rows) == 1
        assert rows[0][:3] == ("draft", "active", "credential")
        assert json.loads(rows[0][3]) == {"record_id": "vr_st_ok"}

    def test_manual_edge_evidence_optional(self, db):
        """manual 边 evidence 可空（manual_archive 无载荷成功）。"""
        _add(db, "st_man", status="active")
        out = transit(db, "st_man", target="archived",
                      trigger="manual_archive")
        assert out["migrated"] is True
        rows = _events(db, "st_man")
        assert rows[0][3] is None  # evidence_json NULL


class TestNotMigratedFamily:
    """not_migrated 族：同态六格+task_end 非最后任务（六+二场景）+封闭枚举锚。"""

    def test_reason_closed_enum_anchor(self):
        """封闭枚举常量锚（spec：not_migrated_reason 封闭枚举恰两值
        {active_tasks_remain, already_in_status}）——未来扩张第三值
        须先改 spec 再改此处（无声扩张必红）。"""
        assert NOT_MIGRATED_REASONS == frozenset({
            "active_tasks_remain", "already_in_status"})


    @pytest.mark.parametrize("status,trigger", [
        ("draft", "credential"),       # 草稿重复登记场景
        ("active", "mount"),           # 重复 promote 已 active
        ("paper", "task_end"),         # 挂盘续挂已 paper
        ("candidate", "qualify"),      # 重复达标已 candidate
        ("live", "retire"),            # 重复退役已 live
        ("archived", "manual_archive"),  # 重复归档已 archived
    ])
    def test_same_status_six_cells_not_migrated(
            self, db, status, trigger):
        """同态六态任一→not_migrated('already_in_status') 非 409，
        事件零写入 status 不变（一律在凭证分派之前返回）。"""
        _add(db, "st_same", status="draft")
        _set_status(db, "st_same", status)
        out = transit(db, "st_same", target=status, trigger=trigger)
        assert out == {"migrated": False, "strategy_id": "st_same",
                       "not_migrated_reason": "already_in_status",
                       "from_status": status, "to_status": status}
        assert _status(db, "st_same") == status
        assert _events(db) == 0

    def test_task_end_not_last_task_active_remain(self, db):
        """paper→active 非最后任务：另一任务仍活跃→not_migrated
        ('active_tasks_remain')，strategy 停留 paper。"""
        _add(db, "st_gd", status="active")
        _set_status(db, "st_gd", "paper")
        _add_task(db, "tk_a", "st_gd", "running")      # 仍活跃
        _add_task(db, "tk_b", "st_gd", "completed")    # 刚结束（本凭证）
        out = transit(db, "st_gd", target="active", trigger="task_end",
                      evidence={"task_id": "tk_b",
                                "final_status": "completed"})
        assert out["migrated"] is False
        assert out["not_migrated_reason"] == "active_tasks_remain"
        assert _status(db, "st_gd") == "paper"
        assert _events(db) == 0


class TestErrorFamily:
    """失败族：404+409 非法边（含合法后继集）+422 凭证（含缺失清单）
    +409 RACE。"""

    def test_not_found_404(self, db):
        with pytest.raises(LifecycleError) as ei:
            transit(db, "ghost", target="active", trigger="credential",
                    evidence={"record_id": "x"})
        assert ei.value.status == 404
        assert ei.value.code == "STRATEGY_NOT_FOUND"

    def test_illegal_edge_409_with_successors(self, db):
        """异态非法边（draft→paper 不在矩阵）→409 detail 含合法
        后继集；事件零写入 status 不变。"""
        _add(db, "st_bad", status="draft")
        with pytest.raises(LifecycleError) as ei:
            transit(db, "st_bad", target="paper", trigger="mount",
                    evidence={"task_id": "t1"})
        assert ei.value.status == 409
        assert ei.value.code == "STRATEGY_ILLEGAL_TRANSITION"
        # 契约三键完整：detail 含 from/to/合法后继集
        assert ei.value.detail["from_status"] == "draft"
        assert ei.value.detail["to_status"] == "paper"
        assert ei.value.detail["allowed_successors"] == sorted(
            SUCCESSORS["draft"])
        assert _status(db, "st_bad") == "draft"
        assert _events(db) == 0

    @pytest.mark.parametrize("trigger", [
        "credential", "qualify", "task_end", "refute"])
    def test_required_evidence_missing_422(self, db, trigger):
        """必填 trigger 缺 evidence→422 缺失清单点名（沿各自合法边）。"""
        edges = {"credential": ("draft", "active", "hyp_e"),
                 "qualify": ("paper", "candidate", None),
                 "task_end": ("paper", "active", None),
                 "refute": ("draft", "archived", None)}
        f, t, parent = edges[trigger]
        _add(db, "st_ev", status="draft", parent=parent)
        _set_status(db, "st_ev", f)
        with pytest.raises(LifecycleError) as ei:
            transit(db, "st_ev", target=t, trigger=trigger)
        assert ei.value.status == 422
        assert ei.value.code == "STRATEGY_CREDENTIAL_INSUFFICIENT"
        assert ei.value.detail["missing"] == ["evidence"]

    def test_task_end_key_level_missing_422(self, db):
        """task_end 键级缺失：有 task_id 无 final_status→清单点名。"""
        _add(db, "st_k", status="active")
        _set_status(db, "st_k", "paper")
        with pytest.raises(LifecycleError) as ei:
            transit(db, "st_k", target="active", trigger="task_end",
                    evidence={"task_id": "tk_k"})
        assert ei.value.detail["missing"] == ["final_status"]

    def test_credential_no_pass_record_422(self, db):
        """draft→active 无挂本策略的 pass 记录→422（清单点名）。"""
        _add(db, "st_np", parent="hyp_np")
        _add_pass_record(db, "hyp_np", "other_strategy")  # 挂别家
        with pytest.raises(LifecycleError) as ei:
            transit(db, "st_np", target="active", trigger="credential",
                    evidence={"record_id": "vr_x"})
        assert ei.value.status == 422
        assert ei.value.detail["missing"] == ["validation_record(verdict=pass)"]

    def test_race_409_when_third_status_injected(self, db):
        """CAS 窗口内前态被改为第三态→409 RACE（detail 含现态）。"""
        _add(db, "st_race", status="draft")
        engine = engine_for(db)
        with engine.begin() as conn:
            injector = _RaceInjector(conn, "paper")
            with pytest.raises(LifecycleError) as ei:
                transit(db, "st_race", target="archived",
                        trigger="manual_archive", conn=injector)
        assert ei.value.status == 409
        assert ei.value.code == "STRATEGY_TRANSITION_RACE"
        assert ei.value.detail["current_status"] == "paper"
        assert _events(db) == 0


class TestIdempotentAndTransaction:
    """CAS 幂等分支+conn 透传同事务。"""

    def test_cas_idempotent_when_target_reached_in_window(self, db):
        """CAS 窗口内已并发完成同边迁移→幂等成功响应（不产生第二
        事件，event_id 回填既有事件）。"""
        _add(db, "st_idem", status="draft")
        _add_event(db, "st_idem", "draft", "archived", "manual_archive")
        engine = engine_for(db)
        with engine.begin() as conn:
            injector = _RaceInjector(conn, "archived")
            out = transit(db, "st_idem", target="archived",
                          trigger="manual_archive", conn=injector)
        assert out["migrated"] is True and out["idempotent"] is True
        assert out["event_id"] == "ev_draft_archived_manual_archive"
        # 幂等不产生第二事件（事件行集恒一条）
        assert len(_events(db, "st_idem")) == 1

    def test_conn_passthrough_same_transaction_rollback_both(self, db):
        """conn 透传同事务：外层回滚→迁移与事件两侧皆无。"""
        _add(db, "st_tx", parent="hyp_tx")
        _add_pass_record(db, "hyp_tx", "st_tx")
        engine = engine_for(db)
        with engine.begin() as conn:
            out = transit(db, "st_tx", target="active",
                          trigger="credential",
                          evidence={"record_id": "vr_st_tx"}, conn=conn)
            assert out["migrated"] is True
            conn.rollback()
        assert _status(db, "st_tx") == "draft"
        assert _events(db) == 0


class TestCredentialFunctions:
    """credential.py 三态判定+守卫两分支+收紧位锚（2.2）。"""

    def test_has_own_pass_three_states(self, db):
        """三态：有 pass/无 pass/无谱系挂点。"""
        _add(db, "st_c1", parent="hyp_c1")
        _add(db, "st_c2", parent="hyp_c2")
        _add(db, "st_c3")  # 无 parent_ids
        _add_pass_record(db, "hyp_c1", "st_c1")
        _add_pass_record(db, "hyp_c2", "other")
        engine = engine_for(db)
        with engine.connect() as conn:
            assert credential.has_own_pass(conn, "st_c1") is True
            assert credential.has_own_pass(conn, "st_c2") is False
            assert credential.has_own_pass(conn, "st_c3") is False

    def test_has_no_active_tasks_two_branches(self, db):
        """守卫两分支：有活跃任务/全终态（或无任务）。"""
        _add(db, "st_g1", status="active")
        _add(db, "st_g2", status="active")
        _add_task(db, "tk_g1", "st_g1", "running")
        _add_task(db, "tk_g2a", "st_g2", "completed")
        _add_task(db, "tk_g2b", "st_g2", "stopped")
        engine = engine_for(db)
        with engine.connect() as conn:
            assert credential.has_no_active_tasks(conn, "st_g1") is False
            assert credential.has_no_active_tasks(conn, "st_g2") is True

    def test_missing_evidence_non_enum_trigger_empty(self):
        """非必填 trigger 缺失清单恒空（函数级行为）：manual 枚举外
        字符串同样返回空清单。CARD-04 后 transit 值域校验前置，本
        函数面不再被非法 trigger 触达——断言保留为函数行为锚。"""
        assert credential.missing_evidence("manual_archive", None) == []
        assert credential.missing_evidence("bogus_trigger", None) == []

    def test_req301_tightening_anchor_in_docstring(self):
        """收紧位锚在场：REQ-3-01 单点收紧位 docstring 声明。"""
        src = inspect.getsource(credential)
        assert "REQ-3-01" in src


class TestTriggerEnumRuntimeSemantics:
    """非枚举 trigger 运行时语义（CARD-04 收紧后正确行为）。

    沿革：修复前非必填集外 trigger 走 manual 通道放行且事件表记原值
    （曾按特征测试语义记录现状并上报）；Team Lead 定夺=TRIGGERS 九值
    封闭本就是常量契约，运行时不校验是实现缺口——收紧为 transit 值
    域校验（422 STRATEGY_TRIGGER_INVALID），本类随卡改为正确行为。
    """

    def test_non_enum_trigger_rejected_422(self, db):
        """非枚举 trigger→422 STRATEGY_TRIGGER_INVALID（detail 含
        trigger/九值排序清单）+零写入（status 不变+事件零行）。"""
        _add(db, "st_ne", status="active")
        with pytest.raises(LifecycleError) as ei:
            transit(db, "st_ne", target="archived",
                    trigger="bogus_trigger")
        assert ei.value.status == 422
        assert ei.value.code == "STRATEGY_TRIGGER_INVALID"
        assert ei.value.detail["trigger"] == "bogus_trigger"
        assert ei.value.detail["strategy_id"] == "st_ne"
        from core.strategy.lifecycle.machine import TRIGGERS
        assert ei.value.detail["valid_triggers"] == sorted(TRIGGERS)
        assert _status(db, "st_ne") == "active"
        assert _events(db) == 0

    def test_nine_enums_pass_domain_check_on_legal_edges(self, db):
        """九值各取一合法边全部通过值域校验（无 TRIGGER_INVALID——
        真迁移或正常凭证分流，枚举面不拦截合法 trigger）。"""
        from core.strategy.lifecycle.machine import (
            TRIGGERS, TRIGGER_EDGES)

        for trig in sorted(TRIGGERS):
            frm, to = sorted(TRIGGER_EDGES[trig])[0]
            sid = f"st_nine_{trig}"
            _add(db, sid)
            _set_status(db, sid, frm)
            try:
                transit(db, sid, target=to, trigger=trig,
                        evidence={"record_id": "vr_x", "task_id": "tk_x",
                                  "final_status": "completed"})
            except LifecycleError as exc:
                assert exc.code != "STRATEGY_TRIGGER_INVALID", trig


class TestComputeWaterline:
    """水位五类+纯查询（2.3）。"""

    def test_pure_stock_no_events(self, db):
        """纯存量：无事件取当前态侧（draft=0/active=1）。"""
        _add(db, "st_w1", status="draft")
        _add(db, "st_w2", status="active")
        assert compute_waterline(db, "st_w1") == 0
        assert compute_waterline(db, "st_w2") == 1

    def test_events_outweigh_current(self, db):
        """有事件：事件侧（to=paper 序 2）高于当前态（draft 0）。"""
        _add(db, "st_w3", status="draft")
        _add_event(db, "st_w3", "active", "paper", "mount")
        assert compute_waterline(db, "st_w3") == 2

    def test_current_status_outweighs_events(self, db):
        """当前态侧高于事件侧：当前 live(4)×事件仅 to=active(1)→取
        max=4（两侧取大非事件侧独大）。"""
        _add(db, "st_w8", status="draft")
        _add_event(db, "st_w8", "draft", "active", "credential")
        _set_status(db, "st_w8", "live")
        assert compute_waterline(db, "st_w8") == 4

    def test_events_all_to_archived_excluded(self, db):
        """事件侧全为 to=archived：事件行在场但序集侧空（archived 不
        入序）+当前态 draft=0→水位 0（事件存在不等于抬水位）。"""
        _add(db, "st_w9", status="draft")
        _add_event(db, "st_w9", "draft", "archived", "manual_archive")
        assert compute_waterline(db, "st_w9") == 0

    def test_roundtrip_no_fallback(self, db):
        """往返回落：active→paper→active 后水位=2（全史 max 非末条）。"""
        _add(db, "st_w4", status="active")
        _add_event(db, "st_w4", "active", "paper", "mount")
        _add_event(db, "st_w4", "paper", "active", "task_end")
        assert _status(db, "st_w4") == "active"
        assert compute_waterline(db, "st_w4") == 2

    def test_revalidate_does_not_reset(self, db):
        """revalidate 后不归零：live→archived→draft 全程后水位仍 4
        （→archived 事件的 to 不入序集但 candidate→live 的峰值保留）。"""
        _add(db, "st_w5", status="draft")
        _add_event(db, "st_w5", "paper", "candidate", "qualify")
        _add_event(db, "st_w5", "candidate", "live", "manual_promote")
        _add_event(db, "st_w5", "live", "archived", "retire")
        _add_event(db, "st_w5", "archived", "draft", "revalidate")
        _set_status(db, "st_w5", "draft")
        assert compute_waterline(db, "st_w5") == 4

    def test_both_sides_empty_archived_stock(self, db):
        """双侧空：存量 archived 无事件→0（历史不可溯诚实表达）。"""
        _add(db, "st_w6", status="active")
        _set_status(db, "st_w6", "archived")
        assert compute_waterline(db, "st_w6") == 0

    def test_pure_query_no_write(self, db):
        """纯查询：调用前后事件行与 strategy 行零变化（零写入零缓存
        ——两次调用同值现算）。"""
        _add(db, "st_w7", status="active")
        _add_event(db, "st_w7", "active", "paper", "mount")
        before_events = _events(db, "st_w7")
        before_status = _status(db, "st_w7")
        first = compute_waterline(db, "st_w7")
        second = compute_waterline(db, "st_w7")
        assert first == second == 2
        assert _events(db, "st_w7") == before_events
        assert _status(db, "st_w7") == before_status
