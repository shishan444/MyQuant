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
    from tests.helpers.vr_factory import insert_hypothesis
    return insert_hypothesis(
        db,
        conditions={"when_conditions": [
            {"subject": {"indicator": "RSI", "params": {"period": 14}},
             "action": "lt", "target": 35}],
            "then_conditions": [{"subject": "close", "action": "gt", "target": 0}]},
        statement="support 测试",
        strategy_params={"stop_loss": 0.05},
        data_ranges=[{"symbol": "BTCUSDT", "timeframe": "4h",
                      "start": "2026-01-01T00:00:00Z",
                      "end": "2026-02-01T00:00:00Z"}])


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
