"""矩阵边逐边行为测试（build-strategy-lifecycle 4.2，族 B1/B2/B6/B7）。

合法 (from,to) 11 边各至少一正向用例（B1，用例表与 TRANSITION_MATRIX
互锁——计数锚联动）；36 全网格三组封闭参数化（合法 11 迁移/异态非法
19→409/同态 6→200 not_migrated）；计数锚五联动；paper→active 多任务
守卫（fixture 两活跃任务场景）；预留三边接口级可调+凭证占位断言
（真实性校验归后续 change 不虚标）；自动触发边真实链路（3.1 研究链
转正/3.2 证伪/3.3 DELETE 归档/3.4 revalidate）。

先行窗中间态声明：active→paper/paper→active 生产触发方（挂盘端点/
任务终态回调）延后窗接入——本文件经接口级注入触发（trigger 直调
transit），矩阵边行为先行定义（proposal 两窗结构）。
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

pytestmark = [pytest.mark.integration]

from core.persistence.db_ext import init_db_ext
from core.persistence.orm.engine import engine_for
from core.persistence.repository import research as rr
from core.persistence.repository.strategy import (
    get_strategy, save_strategy)
from core.strategy.lifecycle.machine import (
    STRATEGY_STATUSES, TRANSITION_MATRIX, LifecycleError)
from core.strategy.lifecycle.service import transit

_T = "2026-10-18T00:00:00"

_CONDS = {
    "when_conditions": [
        {"subject": {"indicator": "RSI", "params": {"period": 14}},
         "action": "lt", "target": 35}],
    "then_conditions": [
        {"subject": {"indicator": "RSI", "params": {"period": 30}},
         "action": "gt", "target": 60}],
}
_RANGES = [{"symbol": "BTCUSDT", "timeframe": "4h",
            "start": "2024-01-01T00:00:00Z", "end": "2024-02-01T00:00:00Z"}]

# B1 合法边用例表：(from, to, trigger, evidence)——边集与
# TRANSITION_MATRIX 互锁（表错/矩阵错都红）；trigger 按边真实分派
_LEGAL_EDGE_TABLE = [
    ("draft", "active", "credential", {"record_id": "vr_m"}),
    ("active", "paper", "mount", {"task_id": "tk_m"}),
    ("paper", "active", "task_end",
     {"task_id": "tk_m2", "final_status": "completed"}),
    ("paper", "candidate", "qualify", {"criterion": "placeholder"}),
    ("candidate", "live", "manual_promote", None),
    ("live", "archived", "retire", None),
    ("archived", "draft", "revalidate", None),
    ("draft", "archived", "manual_archive", None),
    ("active", "archived", "manual_archive", None),
    ("paper", "archived", "manual_archive", None),
    ("candidate", "archived", "manual_archive", None),
]


@pytest.fixture
def db(tmp_path: Path) -> Path:
    path = tmp_path / "matrix.db"
    init_db_ext(path)
    return path


def _add(db, sid, parent=None):
    save_strategy(db, strategy_id=sid, dna_json="{}", symbol="BTCUSDT",
                  timeframe="4h", status="draft", parent_ids=parent,
                  gene_signature=f"sig-{sid}")


def _set_status(db, sid, status):
    from sqlalchemy import text
    with engine_for(db).begin() as conn:
        conn.execute(text("UPDATE strategy SET status=:s"
                          " WHERE strategy_id=:i"),
                     {"s": status, "i": sid})


def _add_pass_record(db, hyp, sid):
    from sqlalchemy import text
    with engine_for(db).begin() as conn:
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
            " 'rhm', :t)"),
            {"r": "vr_m", "h": hyp, "s": sid, "t": _T})


def _add_task(db, tid, sid, status):
    from sqlalchemy import text
    with engine_for(db).begin() as conn:
        conn.execute(text(
            "INSERT INTO pt_task (task_id, status, strategy_id,"
            " dna_snapshot_json, symbol, timeframe, research_status_snapshot,"
            " created_at, updated_at)"
            " VALUES (:t, :s, :i, '{}', 'BTCUSDT', '4h', 'unverified',"
            " :c, :c)"),
            {"t": tid, "s": status, "i": sid, "c": _T})


def _events(db, sid):
    from sqlalchemy import text
    with engine_for(db).connect() as conn:
        return conn.execute(text(
            "SELECT from_status, to_status, trigger FROM"
            " strategy_transition_event WHERE strategy_id=:i"
            " ORDER BY created_at"), {"i": sid}).all()


class TestLegalEdgesForward:
    """B1：合法边 11 逐边正向（计数锚联动——表与矩阵互锁见
    TestCountAnchors）。"""

    @pytest.mark.parametrize("frm,to,trigger,evidence", _LEGAL_EDGE_TABLE)
    def test_edge_migrates_with_event(self, db, frm, to, trigger, evidence):
        """每合法边一正向用例：迁移成功+事件 (from,to,trigger) 落表。"""
        sid = f"st_{frm}_{to}"
        _add(db, sid, parent="hyp_m" if frm == "draft" else None)
        if frm == "draft" and trigger == "credential":
            _add_pass_record(db, "hyp_m", sid)
        _set_status(db, sid, frm)
        out = transit(db, sid, target=to, trigger=trigger, evidence=evidence)
        assert out["migrated"] is True
        assert out["from_status"] == frm and out["to_status"] == to
        assert _events(db, sid) == [(frm, to, trigger)]


class TestFullGrid36Closed:
    """B2：36 全网格三组封闭（合法迁移/同态 not_migrated/异态非法 409）
    ——行为侧逐格实证（常量侧分类见 test_machine）。"""

    @staticmethod
    def _grid_cases():
        table = {(f, t): (tr, ev) for f, t, tr, ev in _LEGAL_EDGE_TABLE}
        for frm in sorted(STRATEGY_STATUSES):
            for to in sorted(STRATEGY_STATUSES):
                trigger, evidence = table.get((frm, to), ("manual_archive", None))
                yield frm, to, trigger, evidence

    @pytest.mark.parametrize(
        "frm,to,trigger,evidence", list(_grid_cases()),
        ids=[f"{f}->{t}" for f in sorted(STRATEGY_STATUSES)
             for t in sorted(STRATEGY_STATUSES)])
    def test_grid_cell(self, db, frm, to, trigger, evidence):
        sid = f"st_g_{frm}_{to}"
        _add(db, sid, parent="hyp_g" if frm == "draft" else None)
        if frm == "draft" and trigger == "credential":
            _add_pass_record(db, "hyp_g", sid)
        _set_status(db, sid, frm)
        if (frm, to) in TRANSITION_MATRIX:
            out = transit(db, sid, target=to, trigger=trigger,
                          evidence=evidence)
            assert out["migrated"] is True
            assert _events(db, sid) == [(frm, to, trigger)]
        elif frm == to:
            out = transit(db, sid, target=to, trigger=trigger,
                          evidence=evidence)
            assert out["migrated"] is False
            assert out["not_migrated_reason"] == "already_in_status"
            assert _events(db, sid) == []
        else:
            with pytest.raises(LifecycleError) as ei:
                transit(db, sid, target=to, trigger=trigger,
                        evidence=evidence)
            assert ei.value.status == 409
            assert ei.value.code == "STRATEGY_ILLEGAL_TRANSITION"
            assert _events(db, sid) == []


class TestCountAnchorsFiveWay:
    """计数锚五联动：len==11/B1==11/B2 非法==19/同态==6/网格==36
    ==11+19+6——常量与用例表谁错改谁。"""

    def test_five_way_anchor(self):
        grid = {(f, t) for f in STRATEGY_STATUSES for t in STRATEGY_STATUSES}
        same = {(s, s) for s in STRATEGY_STATUSES}
        illegal = grid - TRANSITION_MATRIX - same
        table_edges = {(f, t) for f, t, _, _ in _LEGAL_EDGE_TABLE}
        # ①矩阵 len==11
        assert len(TRANSITION_MATRIX) == 11
        # ②B1 合法正向用例==11 且边集与矩阵互锁（漏边/多边/重复都红）
        assert len(_LEGAL_EDGE_TABLE) == 11
        assert table_edges == TRANSITION_MATRIX
        # ③B2 异态非法==19（30−11）
        assert len(illegal) == 19
        # ④同态 not_migrated==6
        assert len(same) == 6
        # ⑤网格==36==11+19+6 完整性
        assert len(grid) == 36
        assert len(grid) == (len(TRANSITION_MATRIX) + len(illegal)
                             + len(same))


class TestTaskEndGuard:
    """paper→active 多任务守卫（fixture 两活跃任务场景）。"""

    def test_not_last_task_then_last_task(self, db):
        """A 运行 B 结束（非最后任务）→not_migrated('active_tasks_
        remain') 停留 paper；A 亦终态后（最后任务）→迁移 active。"""
        sid = "st_guard2"
        _add(db, sid)
        _set_status(db, sid, "paper")
        _add_task(db, "tk_a", sid, "running")      # 仍活跃
        _add_task(db, "tk_b", sid, "completed")    # 刚结束（本凭证）
        out = transit(db, sid, target="active", trigger="task_end",
                      evidence={"task_id": "tk_b",
                                "final_status": "completed"})
        assert out["migrated"] is False
        assert out["not_migrated_reason"] == "active_tasks_remain"
        assert _events(db, sid) == []

        from sqlalchemy import text
        with engine_for(db).begin() as conn:
            conn.execute(text("UPDATE pt_task SET status='completed'"
                              " WHERE task_id='tk_a'"))
        out2 = transit(db, sid, target="active", trigger="task_end",
                       evidence={"task_id": "tk_a",
                                 "final_status": "completed"})
        assert out2["migrated"] is True
        assert _events(db, sid) == [("paper", "active", "task_end")]


class TestReservedEdges:
    """B6：预留三边接口级可调+凭证占位断言。

    凭证真实性校验归 build-qualification-criteria（qualify 凭证签发）
    与 build-lineage-and-contracts（manual_promote 创建链）——先行窗
    只定义接口，不虚标真实性（占位实证：任意非空载荷过=占位语义）。"""

    def test_qualify_placeholder_credential_any_nonempty_dict(self, db):
        """qualify 占位：任意非空 dict 过（真实性校验不在本 change——
        占位语义如实断言，非虚标「已验真」）。"""
        sid = "st_rv_q"
        _add(db, sid)
        _set_status(db, sid, "paper")
        out = transit(db, sid, target="candidate", trigger="qualify",
                      evidence={"anything": True})
        assert out["migrated"] is True

    def test_qualify_missing_evidence_422(self, db):
        """占位不等于免凭证：必填 trigger 缺 evidence 仍 422。"""
        sid = "st_rv_q2"
        _add(db, sid)
        _set_status(db, sid, "paper")
        with pytest.raises(LifecycleError) as ei:
            transit(db, sid, target="candidate", trigger="qualify")
        assert ei.value.status == 422
        assert ei.value.detail["missing"] == ["evidence"]

    def test_manual_reserved_edges_callable_without_evidence(self, db):
        """manual_promote/retire 人工边可空载荷接口级可调（凭证=后续
        change 的创建链回执，先行窗接口定义）。"""
        sid = "st_rv_m"
        _add(db, sid)
        _set_status(db, sid, "candidate")
        out = transit(db, sid, target="live", trigger="manual_promote")
        assert out["migrated"] is True
        out2 = transit(db, sid, target="archived", trigger="retire")
        assert out2["migrated"] is True
        assert _events(db, sid) == [
            ("candidate", "live", "manual_promote"),
            ("live", "archived", "retire")]


class TestRealTriggerPaths:
    """自动触发边真实链路：经真实业务入口（研究链/REST 端点）触达
    transit 并落事件——非 transit 直调（3.1/3.2/3.3/3.4 接入实证）。"""

    @pytest.fixture
    def research_db(self, tmp_path: Path) -> Path:
        path = tmp_path / "real.db"
        init_db_ext(path)
        from core.analysis.executor import ensure_research_tables
        ensure_research_tables(path)
        return path

    def test_draft_to_active_via_research_promotion(self, research_db):
        """3.1 真实链路：假设 supported 定证→谱系 draft 转正（事件
        trigger=credential）。"""
        from core.analysis.hypothesis_service import (
            register_hypothesis, transition_hypothesis)
        from core.analysis.hypothesis_to_strategy import translate_hypothesis
        hid = register_hypothesis(
            research_db, conditions=_CONDS, statement="s",
            data_ranges=_RANGES)["hypothesis_id"]
        sid = translate_hypothesis(
            research_db, hypothesis_id=hid)["strategy_id"]
        rr.save_validation_with_log(
            research_db, record_id="rec_rl_pass", hypothesis_id=hid,
            strategy_id=sid, level="isolated_backtest", params="{}",
            result=None, verdict="pass", range_hash="rh_rl_pass")
        out = transition_hypothesis(
            research_db, hid, target_status="supported",
            evidence_ref="rec_rl_pass")
        assert out["promoted"] is True
        assert get_strategy(research_db, sid)["status"] == "active"
        assert _events(research_db, sid) == [("draft", "active", "credential")]

    def test_refutation_via_research_chain(self, research_db):
        """3.2 真实链路：假设 refuted 定证→谱系 draft 归档（事件
        trigger=refute）。"""
        from core.analysis.hypothesis_service import (
            register_hypothesis, transition_hypothesis)
        from core.analysis.hypothesis_to_strategy import translate_hypothesis
        hid = register_hypothesis(
            research_db, conditions=_CONDS, statement="s",
            data_ranges=_RANGES)["hypothesis_id"]
        sid = translate_hypothesis(
            research_db, hypothesis_id=hid)["strategy_id"]
        rr.save_validation_with_log(
            research_db, record_id="rec_rl_fail", hypothesis_id=hid,
            strategy_id=sid, level="isolated_backtest", params="{}",
            result=None, verdict="fail", range_hash="rh_rl_fail")
        transition_hypothesis(
            research_db, hid, target_status="refuted",
            evidence_ref="rec_rl_fail")
        assert get_strategy(research_db, sid)["status"] == "archived"
        assert _events(research_db, sid) == [("draft", "archived", "refute")]

    def test_manual_archive_via_delete_endpoint(self, db):
        """3.3 真实链路：DELETE 端点对非 draft 归档（事件
        trigger=manual_archive，行保留）。"""
        from api.routes.strategies.crud import delete_strategy_endpoint
        sid = "st_del"
        _add(db, sid)
        _set_status(db, sid, "active")
        delete_strategy_endpoint(sid, db_path=db)
        assert get_strategy(db, sid)["status"] == "archived"
        assert _events(db, sid) == [("active", "archived", "manual_archive")]

    def test_archived_to_draft_via_revalidate_endpoint(self, db):
        """3.4 真实链路：revalidate 端点 archived→draft（事件
        trigger=revalidate，evidence 含作废统计快照）。"""
        from api.routes.strategies.crud import revalidate_strategy_endpoint
        sid = "st_rev"
        _add(db, sid)
        _set_status(db, sid, "archived")
        resp = revalidate_strategy_endpoint(sid, db_path=db)
        assert resp.status == "draft"
        assert get_strategy(db, sid)["status"] == "draft"
        assert _events(db, sid) == [("archived", "draft", "revalidate")]
        from sqlalchemy import text
        with engine_for(db).connect() as conn:
            ev = conn.execute(text(
                "SELECT evidence_json FROM strategy_transition_event"
                " WHERE strategy_id=:i"), {"i": sid}).scalar_one()
        assert "invalidated_stats" in json.loads(ev)
