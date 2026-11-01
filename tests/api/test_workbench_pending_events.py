"""GET /api/workbench/pending-events 契约测试（workbench-observation 组 B2）。

spec workbench-observation 4 Scenario：三类事件命中/判据边界/fail-open
degraded/availability 降级/空态。
"""
from __future__ import annotations

import json
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


def _add_hypothesis(db):
    from core.analysis.hypothesis_service import register_hypothesis
    return register_hypothesis(
        db,
        conditions={"when_conditions": [
            {"subject": {"indicator": "RSI", "params": {"period": 14}},
             "action": "lt", "target": 35}],
            "then_conditions": [{"subject": "close", "action": "gt", "target": 0}]},
        statement="wb 事件测试",
        strategy_params={"stop_loss": 0.05},
        data_ranges=[{"symbol": "BTCUSDT", "timeframe": "4h",
                      "start": "2026-01-01T00:00:00Z",
                      "end": "2026-02-01T00:00:00Z"}])["hypothesis_id"]



def _upsert_running(db, task_id, *, balance, trades, first_met=None, cash=100.0, strategy_id="st_wb"):
    """运行态任务 ORM 直插（update_task 白名单不含 balance 运行态组——测试种子专用）。"""
    from sqlalchemy import create_engine, update
    from sqlalchemy.orm import sessionmaker
    from core.persistence.orm.models import PtTask
    from core.persistence.repository import paper as paper_repo
    paper_repo.create_task(
        db, strategy_id=strategy_id, dna_snapshot_json="{}",
        symbol="BTCUSDT", timeframe="4h",
        research_status_snapshot="unverified", window_bars=100, task_id=task_id)
    S = sessionmaker(bind=create_engine(f"sqlite:///{db}"))
    with S() as sess:
        sess.execute(update(PtTask).where(PtTask.task_id == task_id).values(
            status="running", balance=balance, total_trades=trades,
            unrealized_pnl=0.0, initial_cash=cash, first_met_at=first_met))
        sess.commit()

class TestEmptyState:
    def test_empty_events_legal(self, client):
        """Scenario 空态：events=[] 合法（无待裁决事件）。"""
        r = client.get("/api/workbench/pending-events")
        assert r.status_code == 200
        body = r.json()
        assert body["events"] == []
        assert body["degraded"] == 0
        assert body["availability"]["qualify_pending"] is None


class TestUnionExhausted:
    def test_remaining_bars_zero_produces_event_mocked(self, client):
        """Scenario 并集耗尽正向：remaining_bars==0 → 事件产出+payload 锚。

        与 remaining_bars==1 边界用例成对（0 产/1 不产）。原
        test_remaining_bars_zero_produces_event 名义测正向、实际只证
        fail-open degraded 路径——正向命中分支由本用例 monkeypatch 补。
        """
        from unittest.mock import patch
        db = client._db
        hid = _add_hypothesis(db)
        with patch("core.analysis.validation_service.query_union_balance",
                   return_value={"remaining_bars": 0}):
            r = client.get("/api/workbench/pending-events")
        assert r.status_code == 200
        events = [e for e in r.json()["events"] if e["type"] == "union_exhausted"]
        assert len(events) == 1
        p = events[0]["payload"]
        assert p["hypothesis_id"] == hid
        assert p["symbol"] == "BTCUSDT" and p["timeframe"] == "4h"
        assert p["remaining_bars"] == 0

    def test_remaining_bars_zero_produces_event(self, client, tmp_path):
        """判据=remaining_bars==0（§7.3 单字段）——数据范围全判即耗尽。

        构造：假设 active+judged 覆盖全数据段（直接写 judged_ranges——
        query_union_balance 读账本）。简化实证：无数据文件时
        query_union_balance 抛（数据不足）→fail-open degraded 计数路径
        同步验证。
        """
        db = client._db
        hid = _add_hypothesis(db)
        # 无 parquet → 单源失败跳过+degraded（fail-open 不整体 500）
        r = client.get("/api/workbench/pending-events")
        assert r.status_code == 200
        body = r.json()
        types = {e["type"] for e in body["events"]}
        assert "union_exhausted" not in types  # 数据不足≠耗尽
        assert body["degraded"] >= 1  # fail-open 计数

    def test_remaining_bars_one_no_event(self, client, tmp_path):
        """判据边界：remaining_bars==1 不产（严格 ==0）。"""
        db = client._db
        hid = _add_hypothesis(db)
        # 用 monkeypatch 层面实证判据边界：patch query_union_balance 返回 1
        import api.routes.workbench as wb
        orig = wb.query_union_balance if hasattr(wb, "query_union_balance") else None
        from unittest.mock import patch
        with patch("core.analysis.validation_service.query_union_balance",
                   return_value={"remaining_bars": 1}):
            r = client.get("/api/workbench/pending-events")
        body = r.json()
        assert all(e["type"] != "union_exhausted" for e in body["events"])


class TestQualifyPending:
    def test_two_dims_both_met_event_and_boundary(self, client):
        """两维同时满足产事件+判据边界（仅一维不产）。"""
        db = client._db
        _upsert_running(db, "wb_q1", balance=110.0, trades=6,
                        first_met="2026-11-01T00:00:00+00:00")
        _upsert_running(db, "wb_q2", balance=110.0, trades=3, strategy_id="st_wb2")
        r = client.get("/api/workbench/pending-events")
        events = [e for e in r.json()["events"] if e["type"] == "qualify_pending"]
        assert len(events) == 1
        assert events[0]["payload"]["task_id"] == "wb_q1"
        assert events[0]["payload"]["first_met_at"] == "2026-11-01T00:00:00+00:00"

    @pytest.mark.parametrize(
        ("balance", "trades", "expect_event"),
        [
            # spec 判据「收益>0」严格大于：equity==initial_cash 恰 0 不满足
            pytest.param(100.0, 5, False, id="收益恰0不产（严格大于侧）"),
            # spec 判据「笔数≥5」含等：恰好 5 笔+微利 → 产
            pytest.param(100.01, 5, True, id="恰好5笔含等侧产事件"),
        ],
    )
    def test_two_dims_threshold_sides(self, client, balance, trades, expect_event):
        """两维阈值两侧边界（§qualify 判据边界的另一侧对称补全）。"""
        db = client._db
        _upsert_running(db, "wb_qb", balance=balance, trades=trades)
        r = client.get("/api/workbench/pending-events")
        events = [e for e in r.json()["events"]
                  if e["type"] == "qualify_pending"]
        assert bool(events) is expect_event


class TestAvailability:
    def test_qualify_source_unavailable_annotation(self, client):
        """Scenario 源未建与无事件的区分：availability 标注
        qualify_pending=unavailable——200 正常返回+结构完整，不以空
        events 伪装源可用。"""
        from unittest.mock import patch
        with patch("api.routes.workbench._qualify_source_available",
                   return_value=False):
            r = client.get("/api/workbench/pending-events")
        assert r.status_code == 200
        body = r.json()
        assert body["availability"]["qualify_pending"] == "unavailable"
        assert body["availability"]["reason"]
        assert "events" in body  # 响应结构完整（事件面如实呈现）


class TestTriggerDeviation:
    def test_passed_false_event_and_null_no_event(self, client):
        """passed==false 产事件+passed=None（对照重放不可执行）不产。"""
        from core.persistence.repository import paper as paper_repo
        from core.persistence.orm.models import PtReport
        from sqlalchemy import create_engine
        from sqlalchemy.orm import sessionmaker
        db = client._db
        Session = sessionmaker(bind=create_engine(f"sqlite:///{db}"))
        report_false = json.dumps({"performance": {}, "qualification": {
            "trigger_consistency": {"passed": False, "deviation_ratio": 0.25}}})
        report_null = json.dumps({"performance": {}, "qualification": {
            "trigger_consistency": {"passed": None}}})
        from sqlalchemy import update as sa_update
        from core.persistence.orm.models import PtTask as PtTaskM
        PtTask = PtTaskM
        for tid, status, rep in (("wb_t1", "completed", report_false),
                                 ("wb_t2", "stopped", report_null)):
            paper_repo.create_task(
                db, strategy_id="st_wb3", dna_snapshot_json="{}",
                symbol="BTCUSDT", timeframe="4h",
                research_status_snapshot="unverified", window_bars=10, task_id=tid)
            with Session() as sess:
                sess.execute(sa_update(PtTask).where(PtTask.task_id == tid).values(
                    status=status))
                sess.commit()
        with Session() as sess:
            for tid, rep in (("wb_t1", report_false), ("wb_t2", report_null)):
                sess.add(PtReport(
                    task_id=tid, report_version=1, generated_at="2026-11-01T00:00:00",
                    as_of="2026-11-01T00:00:00", report_json=rep))
            sess.commit()
        r = client.get("/api/workbench/pending-events")
        events = [e for e in r.json()["events"] if e["type"] == "trigger_deviation"]
        assert len(events) == 1
        assert events[0]["payload"]["task_id"] == "wb_t1"
        assert events[0]["payload"]["deviation_ratio"] == 0.25
