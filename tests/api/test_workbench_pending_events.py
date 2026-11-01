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


def _add_hypothesis_variant(db, i):
    """then target 变体假设（同 conditions 注册幂等返回同 id——变体保证
    互异；period 变体不可行：RSI.period 参数域 [2,50] 上限 49 个不够；
    CARD-01 全量扫/CARD-02 缓存用例的批量种子）。"""
    from core.analysis.hypothesis_service import register_hypothesis
    return register_hypothesis(
        db,
        conditions={"when_conditions": [
            {"subject": {"indicator": "RSI", "params": {"period": 14}},
             "action": "lt", "target": 35}],
            "then_conditions": [{"subject": "close", "action": "gt", "target": i}]},
        statement=f"wb 变体 {i}", strategy_params={"stop_loss": 0.05},
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
        双 patch（CARD-02 后装载序：load_data_bounds 先于
        query_union_balance——单 patch balance 时真装载先 raise 不可达）。
        """
        from unittest.mock import patch
        db = client._db
        hid = _add_hypothesis(db)
        bounds = ("2026-01-01T00:00:00Z", "2026-02-01T00:00:00Z")
        with patch("core.analysis.validation_service.load_data_bounds",
                   return_value=bounds), \
             patch("core.analysis.validation_service.query_union_balance",
                   return_value={"remaining_bars": 0}) as qb:
            r = client.get("/api/workbench/pending-events")
        assert r.status_code == 200
        events = [e for e in r.json()["events"] if e["type"] == "union_exhausted"]
        assert len(events) == 1
        p = events[0]["payload"]
        assert p["hypothesis_id"] == hid
        assert p["symbol"] == "BTCUSDT" and p["timeframe"] == "4h"
        assert p["remaining_bars"] == 0
        # CARD-02 传参形制：预装载 bounds 直传 query_union_balance
        assert qb.call_args.kwargs["data_bounds"] == bounds

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
        """判据边界：remaining_bars==1 不产（严格 ==0）。

        双 patch（CARD-02 后单 patch balance 不可达——真装载先 raise
        拦截，patch 从未被调用=恒绿未触达；变异自查：单 patch 形制下
        把返回值改为 0 用例仍绿，证 mock 不可达）。
        """
        db = client._db
        hid = _add_hypothesis(db)
        from unittest.mock import patch
        with patch("core.analysis.validation_service.load_data_bounds",
                   return_value=("2026-01-01T00:00:00Z", "2026-02-01T00:00:00Z")), \
             patch("core.analysis.validation_service.query_union_balance",
                   return_value={"remaining_bars": 1}) as qb:
            r = client.get("/api/workbench/pending-events")
        assert qb.call_count == 1  # patch 真被调用（触达证明）
        body = r.json()
        assert all(e["type"] != "union_exhausted" for e in body["events"])

    def test_full_scan_beyond_first_page(self, client):
        """CARD-01 全量扫：active 假设超单页（200）时第 201+ 不漏扫。

        修复前 list_hypotheses 缺省 limit=50 单页截断（>50 后半静默
        漏扫）；iter_all_pages 翻页后跨页全量。同 conditions 注册幂等
        （探针实测同 id），period 变体保证 201 个互异假设。
        """
        from unittest.mock import patch
        db = client._db
        n = 201  # 第二页首条=第 201——翻页边界的最小超页规模
        for i in range(n):
            _add_hypothesis_variant(db, i)
        with patch("core.analysis.validation_service.load_data_bounds",
                   return_value=("s", "e")), \
             patch("core.analysis.validation_service.query_union_balance",
                   return_value={"remaining_bars": 0}):
            r = client.get("/api/workbench/pending-events")
        events = [e for e in r.json()["events"] if e["type"] == "union_exhausted"]
        assert len(events) == n  # 一假设一事件——第 201 个不漏

    def test_bounds_cache_single_load_per_key(self, client):
        """CARD-02 请求内缓存：同 (symbol,timeframe) 多假设只装载一次
        bounds；query_union_balance 收到预装载传参（每假设各判定一次）。"""
        from unittest.mock import patch
        db = client._db
        _add_hypothesis_variant(db, 0)
        _add_hypothesis_variant(db, 1)  # 同 key (BTCUSDT, 4h)
        bounds = ("2026-01-01T00:00:00Z", "2026-02-01T00:00:00Z")
        with patch("core.analysis.validation_service.load_data_bounds",
                   return_value=bounds) as lb, \
             patch("core.analysis.validation_service.query_union_balance",
                   return_value={"remaining_bars": 0}) as qb:
            r = client.get("/api/workbench/pending-events")
        assert lb.call_count == 1  # 同 key 一次装载（缓存命中）
        assert qb.call_count == 2  # 两假设各自判定
        assert all(c.kwargs.get("data_bounds") == bounds
                   for c in qb.call_args_list)

    def test_failed_bounds_load_not_cached(self, client):
        """CARD-02 失败不缓存：装载 raise 不入缓存——同 key 两假设
        各自失败 degraded=2（失败若被缓存伪跳过则 degraded=1）。"""
        db = client._db
        _add_hypothesis_variant(db, 0)
        _add_hypothesis_variant(db, 1)  # 同 key 无 parquet 真装载两次 raise
        r = client.get("/api/workbench/pending-events")
        assert r.status_code == 200
        assert r.json()["degraded"] == 2


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


def _add_terminal_report(db, task_id, status, report):
    """终态任务+单报告直插（CARD-05 三态种子——TestTriggerDeviation
    同款构造形制：create_task+status 直更+PtReport version=1）。"""
    from sqlalchemy import create_engine, update as sa_update
    from sqlalchemy.orm import sessionmaker
    from core.persistence.orm.models import PtReport, PtTask
    from core.persistence.repository import paper as paper_repo
    paper_repo.create_task(
        db, strategy_id="st_wb5", dna_snapshot_json="{}",
        symbol="BTCUSDT", timeframe="4h",
        research_status_snapshot="unverified", window_bars=10, task_id=task_id)
    S = sessionmaker(bind=create_engine(f"sqlite:///{db}"))
    with S() as sess:
        sess.execute(sa_update(PtTask).where(PtTask.task_id == task_id).values(
            status=status))
        sess.add(PtReport(task_id=task_id, report_version=1,
                          generated_at="2026-11-01T00:00:00",
                          as_of="2026-11-01T00:00:00",
                          report_json=json.dumps(report)))
        sess.commit()


class TestTriggerAvailability:
    """CARD-05 trigger 半边源可用性三态（spec Scenario 源未建区分的
    trigger 半边）：无终态任务=空态合法不标；全部最新报告缺
    qualification 键=unavailable；任一含键（混合库）=源已建不标。"""

    def test_no_terminal_tasks_empty_state_not_marked(self, client):
        """空态：无终态任务不标降级（无从判源≠源未建）。"""
        r = client.get("/api/workbench/pending-events")
        assert r.status_code == 200
        assert r.json()["availability"]["trigger_deviation"] is None

    def test_all_reports_missing_key_unavailable(self, client):
        """全缺键：终态报告无 qualification 键（C5 前旧报告）→
        unavailable+reason 披露 trigger 半边；端点 200。"""
        db = client._db
        _add_terminal_report(db, "wb_a1", "completed",
                             {"performance": {}})  # 无 qualification 键
        _add_terminal_report(db, "wb_a2", "stopped",
                             {"performance": {}, "other": {}})
        r = client.get("/api/workbench/pending-events")
        assert r.status_code == 200
        avail = r.json()["availability"]
        assert avail["trigger_deviation"] == "unavailable"
        assert "trigger 回看源未建" in avail["reason"]

    def test_mixed_library_any_key_present_available(self, client):
        """混合库：缺键旧报告与含键新报告并存 → 任一含键即源已建
        不标（同构 qualify 半边「任一行含列」语义）。"""
        db = client._db
        _add_terminal_report(db, "wb_a3", "completed",
                             {"performance": {}})  # C5 前旧形态
        _add_terminal_report(db, "wb_a4", "stopped",
                             {"performance": {}, "qualification": {
                                 "trigger_consistency": {"passed": True}}})
        r = client.get("/api/workbench/pending-events")
        assert r.status_code == 200
        assert r.json()["availability"]["trigger_deviation"] is None

    def test_dual_unavailable_reason_concatenated(self, client):
        """两半同时 unavailable：reason 单字段拼接两半说明（非分源
        双 reason——architect 卡面形制），「；」连接。"""
        from unittest.mock import patch
        db = client._db
        _add_terminal_report(db, "wb_a5", "completed", {"performance": {}})
        with patch("api.routes.workbench._qualify_source_available",
                   return_value=False):
            r = client.get("/api/workbench/pending-events")
        avail = r.json()["availability"]
        assert avail["qualify_pending"] == "unavailable"
        assert avail["trigger_deviation"] == "unavailable"
        reason = avail["reason"]
        assert "C5 判定器源未建" in reason      # qualify 半边说明在
        assert "trigger 回看源未建" in reason   # trigger 半边说明在
        assert "；" in reason                    # 单字段拼接连接符


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
