"""be-workbench-support 四端点契约测试（D1/D8/D9/D11）。

组 0 共享骨架沿 pending-events 先例四组形制：只读现算零持久化/
generated_at 时点快照/fail-open degraded/limit+1 探测。各端点专项：
D1 双态（mcp 探活 mock）/D8 过滤分页 422+templates 同源变异/D9 余量
披露与 degraded/D11 三分支基点+口径分解+单源结构。
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

pytestmark = [pytest.mark.integration]

from fastapi.testclient import TestClient


@pytest.fixture()
def client(tmp_path: Path):
    from core.persistence.db_ext import init_db_ext
    from api.app import create_app
    db = tmp_path / "q.db"
    init_db_ext(db)
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    app = create_app(db_path=db, data_dir=data_dir)
    with TestClient(app, raise_server_exceptions=False) as c:
        c._db = db  # type: ignore[attr-defined]
        c._data_dir = data_dir  # type: ignore[attr-defined]
        yield c


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _add_hypothesis(db):
    from core.analysis.hypothesis_service import register_hypothesis
    return register_hypothesis(
        db,
        conditions={"when_conditions": [
            {"subject": {"indicator": "RSI", "params": {"period": 14}},
             "action": "lt", "target": 35}],
            "then_conditions": [{"subject": "close", "action": "gt", "target": 0}]},
        statement="support 测试",
        strategy_params={"stop_loss": 0.05},
        data_ranges=[{"symbol": "BTCUSDT", "timeframe": "4h",
                      "start": "2026-01-01T00:00:00Z",
                      "end": "2026-02-01T00:00:00Z"}])["hypothesis_id"]


def _make_pt_task(db, *, status="running", created_at=None,
                  balance=None, initial_cash=100_000.0, task_id=None):
    from core.persistence.repository import paper as paper_repo
    tid = paper_repo.create_task(
        db, strategy_id=f"st_{task_id or 's1'}",
        dna_snapshot_json="{}", symbol="BTCUSDT", timeframe="4h",
        research_status_snapshot="active", initial_cash=initial_cash,
        task_id=task_id)
    if status != "pending":
        from sqlalchemy import update
        from core.persistence.repository.paper import (
            PtTask, _engine_for)
        eng = _engine_for(db)
        fields = {"status": status}
        if created_at is not None:
            fields["created_at"] = created_at
        if balance is not None:
            fields.update({"balance": balance, "position_margin": 0.0,
                           "unrealized_pnl": 0.0})
        with eng.begin() as conn:
            conn.execute(update(PtTask).where(
                PtTask.task_id == tid).values(**fields))
    return tid


def _append_equity(db, task_id, bar_time, equity):
    from core.persistence.repository.paper import PtEquity, _engine_for
    from sqlalchemy import insert
    eng = _engine_for(db)
    with eng.begin() as conn:
        conn.execute(insert(PtEquity).values(
            task_id=task_id, bar_time=bar_time, equity=equity,
            balance=equity, unrealized_pnl=0.0, cumulative_funding=0.0))


# ══════════ D1 process-status ══════════

class TestProcessStatus:
    def test_api_online_always_true_mcp_probe(self, client, monkeypatch):
        """api.online 恒 true（响应即存活）；mcp 探活走 socket——mock
        create_connection 成功/失败双态。"""
        import socket as sock_mod
        calls = {}

        class _FakeSock:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        real_create = sock_mod.create_connection

        def fake_ok(addr, timeout=None):
            calls["addr"] = addr
            calls["timeout"] = timeout
            return _FakeSock()

        monkeypatch.setattr(sock_mod, "create_connection", fake_ok)
        r = client.get("/api/workbench/process-status")
        assert r.status_code == 200
        body = r.json()
        assert body["api"] == {"online": True, "detail": None}
        assert body["mcp"]["online"] is True
        assert body["mcp"]["detail"] is None
        assert "generated_at" in body
        assert calls["addr"] == ("127.0.0.1", 8800)
        assert calls["timeout"] == 0.5
        monkeypatch.setattr(sock_mod, "create_connection", real_create)

    def test_mcp_offline_200_not_error(self, client, monkeypatch):
        """探活失败≠端点错误——200 返回离线事实+detail 摘要。"""
        import socket as sock_mod

        def fake_fail(addr, timeout=None):
            raise ConnectionRefusedError("conn refused")

        monkeypatch.setattr(sock_mod, "create_connection", fake_fail)
        r = client.get("/api/workbench/process-status")
        assert r.status_code == 200
        body = r.json()
        assert body["mcp"]["online"] is False
        assert "conn refused" in body["mcp"]["detail"]
        assert body["api"]["online"] is True

    def test_env_port_override(self, client, monkeypatch):
        import socket as sock_mod
        seen = {}

        class _FakeSock:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        monkeypatch.setenv("MYQUANT_MCP_PORT", "9999")
        monkeypatch.setattr(
            sock_mod, "create_connection",
            lambda addr, timeout=None: (seen.__setitem__("addr", addr), _FakeSock())[1])
        client.get("/api/workbench/process-status")
        assert seen["addr"] == ("127.0.0.1", 9999)


# ══════════ D8 analysis/tasks 列表 ══════════

class TestAnalysisTasksList:
    def _seed(self, db, n=3):
        from core.persistence.repository import research as rr
        ids = []
        for i in range(n):
            tid = f"at_list_{i}"
            rr.save_analysis_task(
                db, task_id=tid, template="condition_stats",
                params="{}")
            rr.update_analysis_task(
                db, task_id=tid, status="succeeded" if i % 2 else "failed",
                reason=None if i % 2 else "boom")
            ids.append(tid)
        return ids

    def test_list_desc_templates_and_fields(self, client):
        ids = self._seed(client._db)
        r = client.get("/api/research/analysis/tasks")
        assert r.status_code == 200
        body = r.json()
        assert body["total"] == 3
        assert body["has_more"] is False
        # templates 与 _TEMPLATES 同源（全集计数快照——ab_compare
        # 第 16 模板并入后 16；同源性行为级证据见 mutation_red 用例）
        from api.routes.research.analysis import _TEMPLATES
        assert body["templates"] == list(_TEMPLATES)
        assert len(body["templates"]) == 16
        # created_at desc + 七字段行（无 result/params）
        first = body["items"][0]
        assert set(first.keys()) == {
            "task_id", "template", "status", "created_at",
            "started_at", "finished_at", "error_summary"}
        assert first["task_id"] == ids[-1]

    def test_templates_same_source_mutation_red(self, client):
        """templates 同源变异锚：临时改 _TEMPLATES 元组必红（运行时
        patch ——同源性的行为级证据）。"""
        from api.routes.research import analysis as mod
        self._seed(client._db)
        r1 = client.get("/api/research/analysis/tasks").json()
        orig = mod._TEMPLATES
        try:
            mod._TEMPLATES = orig + ("fake_tpl",)
            r2 = client.get("/api/research/analysis/tasks").json()
            assert r2["templates"] == list(mod._TEMPLATES)
            assert "fake_tpl" in r2["templates"]
            assert r1["templates"] != r2["templates"]
        finally:
            mod._TEMPLATES = orig

    def test_filter_status(self, client):
        self._seed(client._db, n=2)
        r = client.get("/api/research/analysis/tasks",
                       params={"status": "succeeded"})
        body = r.json()
        assert body["total"] == 1
        assert all(i["status"] == "succeeded" for i in body["items"])

    def test_pagination_limit_plus_one(self, client):
        self._seed(client._db, n=3)
        r = client.get("/api/research/analysis/tasks",
                       params={"limit": 2})
        body = r.json()
        assert body["total"] == 3
        assert len(body["items"]) == 2
        assert body["has_more"] is True
        r2 = client.get("/api/research/analysis/tasks",
                        params={"limit": 2, "offset": 2})
        assert len(r2.json()["items"]) == 1

    def test_invalid_422(self, client):
        for params in ({"status": "bogus"}, {"template": "nope"},
                       {"limit": 0}, {"limit": 201}, {"offset": -1}):
            r = client.get("/api/research/analysis/tasks", params=params)
            assert r.status_code == 422, params
            assert r.json()["detail"]["code"] == "RESEARCH_PARAM_INVALID"

    def test_empty_200(self, client):
        r = client.get("/api/research/analysis/tasks")
        assert r.status_code == 200
        assert r.json()["items"] == []
        assert r.json()["total"] == 0


# ══════════ D9 hypothesis-union-remains ══════════

class TestHypothesisUnionRemains:
    def test_active_empty_200(self, client):
        r = client.get("/api/workbench/hypothesis-union-remains")
        assert r.status_code == 200
        body = r.json()
        assert body == {"items": [], "degraded": 0,
                        "generated_at": body["generated_at"]}

    def test_remains_disclosure_value(self, client):
        """余量全量披露（非 exhausted 只报 0——值本身在场）。
        双 patch 零网形制（pending-events mocked 先例同款——裸跑时
        load_data_bounds 对缺失 parquet 走远端降级，实测触发币安拉取
        〔IP 封禁风险〕——零外网红线禁裸跑）。"""
        from unittest.mock import patch
        _add_hypothesis(client._db)
        bounds = ("2026-01-01T00:00:00Z", "2026-02-01T00:00:00Z")
        with patch("core.analysis.validation_service.load_data_bounds",
                   return_value=bounds), \
             patch("core.analysis.validation_service.query_union_balance",
                   return_value={"remaining_bars": 7, "judged_bars": 13}):
            r = client.get("/api/workbench/hypothesis-union-remains")
        assert r.status_code == 200
        items = r.json()["items"]
        assert len(items) == 1
        it = items[0]
        assert it["hypothesis_id"].startswith("hyp_")
        assert it["symbol"] == "BTCUSDT" and it["timeframe"] == "4h"
        # 全量披露：余量数值本身在场（exhausted 扫描只报 0——本端点披露 7）
        assert it["remaining_bars"] == 7
        assert it["judged_bars"] == 13

    def test_fail_open_degraded(self, client):
        """单假设失败 degraded 不垮响应（双 patch——balance 先抛）。"""
        from unittest.mock import patch
        _add_hypothesis(client._db)

        def boom(*a, **k):
            raise RuntimeError("source down")

        with patch("core.analysis.validation_service.load_data_bounds",
                   side_effect=boom):
            r = client.get("/api/workbench/hypothesis-union-remains")
        assert r.status_code == 200
        body = r.json()
        assert body["degraded"] >= 1
        assert body["items"] == []


# ══════════ D11 fleet-summary ══════════

class TestFleetSummary:
    def test_empty_db_shape(self, client):
        r = client.get("/api/workbench/fleet-summary")
        assert r.status_code == 200
        body = r.json()
        assert body["fleet"]["active"] == {
            "equity": 0.0, "day_start_equity": 0.0,
            "day_delta": 0.0, "task_count": 0}
        assert body["fleet"]["all"]["task_count"] == 0
        assert body["tasks"] == []
        assert "generated_at" in body

    def test_active_day_delta_today_new_task(self, client):
        """分支一：当日新建且无当日快照→基点=initial_cash。"""
        tid = _make_pt_task(client._db, status="running",
                            balance=101_000.0, initial_cash=100_000.0)
        r = client.get("/api/workbench/fleet-summary")
        body = r.json()
        row = next(t for t in body["tasks"] if t["task_id"] == tid)
        assert row["day_delta"] == pytest.approx(1_000.0)
        assert row["no_bar_today"] is False
        assert body["fleet"]["active"]["task_count"] == 1

    def test_active_day_delta_cross_day_baseline(self, client):
        """分支二：跨日任务→基点=当日首条快照（昨日快照不作基点）。"""
        yesterday = (datetime.now(timezone.utc)
                     - timedelta(days=1)).isoformat()
        tid = _make_pt_task(client._db, status="running",
                            balance=102_000.0, created_at=yesterday)
        _append_equity(client._db, tid, yesterday, 100_000.0)
        _append_equity(client._db, tid, _now_iso(), 101_500.0)
        r = client.get("/api/workbench/fleet-summary")
        row = next(t for t in r.json()["tasks"] if t["task_id"] == tid)
        # 当日首条=101500（昨日 100000 不作基点）；equity=行内 balance 102000
        assert row["day_delta"] == pytest.approx(500.0)

    def test_no_bar_today_flag(self, client):
        """分支三：无当日快照非当日新建→no_bar_today+基点=最新快照。"""
        yesterday = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
        tid = _make_pt_task(client._db, status="running",
                            balance=103_000.0, created_at=yesterday)
        _append_equity(client._db, tid, yesterday, 100_000.0)
        r = client.get("/api/workbench/fleet-summary")
        row = next(t for t in r.json()["tasks"] if t["task_id"] == tid)
        assert row["no_bar_today"] is True
        # 基点=最新快照 100000（无推进语义——delta=equity-最新快照）
        assert row["day_delta"] == pytest.approx(3_000.0)

    def test_fleet_dual_decomposition(self, client):
        """口径双分解：active 主/all 副（终态任务计入 all 不计 active）。"""
        _make_pt_task(client._db, status="running", balance=100_000.0,
                      task_id="pt_run_1")
        _make_pt_task(client._db, status="completed", balance=50_000.0,
                      task_id="pt_done_1")
        r = client.get("/api/workbench/fleet-summary")
        f = r.json()["fleet"]
        assert f["active"]["task_count"] == 1
        assert f["all"]["task_count"] == 2
        assert f["all"]["equity"] == pytest.approx(150_000.0)

    def test_tasks_row_single_source_compat(self, client):
        """约束 a：tasks 行与 /trading 单查同源（_task_response_from 单点
        ——公共字段逐值一致兼容锚）。"""
        tid = _make_pt_task(client._db, status="running", balance=100_000.0)
        r_fleet = client.get("/api/workbench/fleet-summary")
        row = next(t for t in r_fleet.json()["tasks"] if t["task_id"] == tid)
        r_single = client.get(f"/api/paper-trading/tasks/{tid}")
        assert r_single.status_code == 200
        s = r_single.json()
        # 公共字段逐值（同源零重写）
        for k in ("task_id", "status", "strategy_id", "symbol",
                  "timeframe", "initial_cash", "leverage",
                  "balance", "total_trades", "created_at"):
            assert row[k] == s[k], k

    def test_funding_sum_sql_batch(self, client):
        """funding_sum 批量在场（SQL 聚合值；N+1 禁=仓储单查询实现）。"""
        from core.persistence.repository.paper import PtFunding, _engine_for
        from sqlalchemy import insert
        tid = _make_pt_task(client._db, status="running", balance=100_000.0)
        eng = _engine_for(client._db)
        with eng.begin() as conn:
            conn.execute(insert(PtFunding).values(
                task_id=tid, settle_time=_now_iso(), bar_time=_now_iso(),
                status="settled", rate=0.0001, amount=-1.5,
                position_quantity=0.01, settle_price=100_000.0))
        r = client.get("/api/workbench/fleet-summary")
        row = next(t for t in r.json()["tasks"] if t["task_id"] == tid)
        assert row["funding_sum"] == pytest.approx(-1.5)

    def test_recent_terminal_and_dims(self, client):
        """recent_terminal 行+dims 两维现算（pnl_positive/trades_enough）。"""
        tid = _make_pt_task(client._db, status="stopped", balance=120_000.0)
        r = client.get("/api/workbench/fleet-summary")
        body = r.json()
        assert any(t["task_id"] == tid for t in body["recent_terminal"])
        row = next(t for t in body["tasks"] if t["task_id"] == tid)
        assert set(row["dims"].keys()) == {
            "pnl_positive", "trades_enough", "first_met_at"}

    def test_recent_terminal_stopped_order(self, client):
        """TD-A 锚：recent_terminal 按 stopped_at desc（>10 时截最近停）。"""
        from core.persistence.repository.paper import PtTask, _engine_for
        from sqlalchemy import update
        # 造两任务：先创建的后停止（stopped_at 更晚）——排序应后者在前
        t1 = _make_pt_task(client._db, status="stopped", task_id="pt_ord_1")
        t2 = _make_pt_task(client._db, status="stopped", task_id="pt_ord_2")
        eng = _engine_for(client._db)
        early = "2026-01-01T00:00:00+00:00"
        late = "2026-02-01T00:00:00+00:00"
        with eng.begin() as conn:
            conn.execute(update(PtTask).where(PtTask.task_id == t1).values(
                created_at="2025-12-01T00:00:00+00:00", stopped_at=early))
            conn.execute(update(PtTask).where(PtTask.task_id == t2).values(
                created_at="2025-12-15T00:00:00+00:00", stopped_at=late))
        r = client.get("/api/workbench/fleet-summary")
        rt = r.json()["recent_terminal"]
        assert [x["task_id"] for x in rt] == [t2, t1]  # 停止晚者前

    def test_research_status_resolved_not_snapshot(self, client):
        """TD-B 锚：research_status 走 resolve 现算（与 /trading 同源）——
        造策略行 active+draft_to_active 事件，fleet 行应呈 active 非快照。"""
        from core.persistence.repository import paper as paper_repo
        from core.persistence.repository.paper import _engine_for
        from sqlalchemy import update
        tid = _make_pt_task(client._db, status="running", balance=100_000.0,
                            task_id="pt_rs_1")
        # 快照列写 draft（若走快照口径则 fleet 呈 draft——现算口径应随
        # strategy 行 active 而呈 active）
        eng = _engine_for(client._db)
        with eng.begin() as conn:
            conn.execute(update(PtTaskAnd := __import__(
                "core.persistence.repository.paper", fromlist=["PtTask"]
            ).PtTask).where(PtTaskAnd.task_id == tid).values(
                research_status_snapshot="draft"))
        # 造 strategy 行（active）
        from core.persistence.orm.models import Strategy as PStrategy
        from sqlalchemy import insert
        with eng.begin() as conn:
            conn.execute(insert(PStrategy).values(
                strategy_id="st_pt_rs_1", name="t", symbol="BTCUSDT",
                timeframe="4h", status="active", source="lab",
                created_at=_now_iso(), updated_at=_now_iso(),
                gene_signature="x", generation=0,
                dna_json="{}"))
        r = client.get("/api/workbench/fleet-summary")
        row = next(x for x in r.json()["tasks"] if x["task_id"] == tid)
        # TD-B 分歧面锚：resolve 三序现算（strategy.active+无事件+
        # qualified=None→unverified）——快照口径恒呈 draft（research_status
        # 值域=研究四态非 strategy.status，t3r6 TD-B 审查修正实录）
        assert row["research_status"] == "unverified"

    def test_fleet_aggregate_arithmetic(self, client):
        """fleet 聚合算术锚（t3r6 缺口补——原只断 row 级）。"""
        _make_pt_task(client._db, status="running", balance=100_000.0,
                      initial_cash=100_000.0, task_id="pt_agg_1")
        _make_pt_task(client._db, status="running", balance=105_000.0,
                      initial_cash=100_000.0, task_id="pt_agg_2")
        r = client.get("/api/workbench/fleet-summary")
        a = r.json()["fleet"]["active"]
        assert a["task_count"] == 2
        assert a["equity"] == pytest.approx(205_000.0)
        # 两任务当日新建无快照→基点各=initial_cash→day_start=200000
        assert a["day_start_equity"] == pytest.approx(200_000.0)
        assert a["day_delta"] == pytest.approx(5_000.0)

    def test_truncated_false_under_limit(self, client):
        """TD-C 锚：未达上限 truncated=False（披露字段在场）。"""
        _make_pt_task(client._db, status="running", balance=100_000.0)
        assert client.get("/api/workbench/fleet-summary").json()["truncated"] is False

    def test_no_persistence_side_effect(self, client):
        """组 0 骨架：只读现算零持久化——库文件 mtime 不因请求变化。"""
        _make_pt_task(client._db, status="running", balance=100_000.0)
        import time
        before = client._db.stat().st_mtime_ns
        client.get("/api/workbench/fleet-summary")
        client.get("/api/workbench/process-status")
        client.get("/api/workbench/hypothesis-union-remains")
        time.sleep(0.01)
        assert client._db.stat().st_mtime_ns == before
