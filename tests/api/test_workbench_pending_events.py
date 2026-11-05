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


class TestFailOpenPaths:
    """fail-open 防御分支族覆盖（阶段四闸门 missing 行收口）：
    单任务失败跳过/端点级兜底/源可用性异常/str 报告防御/无报告终态。"""

    def test_single_task_failure_skipped_others_continue(self, client):
        """qualify 单任务坏数据跳过+其余任务照常扫描（129-131）：
        一任务 evaluate 抛→degraded+1，另一任务事件照产。"""
        from unittest.mock import patch
        from core.paper_trading.qualification import evaluate_two_dimensions as orig
        db = client._db
        _upsert_running(db, "wb_f1", balance=110.0, trades=6)
        _upsert_running(db, "wb_f2", balance=120.0, trades=7,
                        strategy_id="st_f2")

        def flaky(equity, cash, trades):
            if trades == 6:
                raise RuntimeError("坏数据模拟")
            return orig(equity, cash, trades)

        with patch("core.paper_trading.qualification.evaluate_two_dimensions",
                   side_effect=flaky):
            r = client.get("/api/workbench/pending-events")
        body = r.json()
        assert body["degraded"] == 1                      # 仅坏任务跳过
        q_events = [e for e in body["events"]
                    if e["type"] == "qualify_pending"]
        assert [e["payload"]["task_id"] for e in q_events] == ["wb_f2"]

    def test_endpoint_failopen_on_source_crash(self, client):
        """端点级兜底三形态：源函数崩溃→[],1,0 不 500（186-187/194-195）；
        源可用性探测异常→False→unavailable 标注（220-221）。"""
        from unittest.mock import patch
        with patch("api.routes.workbench._qualify_pending_events",
                   side_effect=RuntimeError("qualify 源崩溃")):
            r1 = client.get("/api/workbench/pending-events")
        assert r1.status_code == 200
        assert r1.json()["degraded"] == 1
        with patch("api.routes.workbench._trigger_deviation_events",
                   side_effect=RuntimeError("trigger 源崩溃")):
            r2 = client.get("/api/workbench/pending-events")
        assert r2.status_code == 200
        assert r2.json()["degraded"] == 1
        with patch("core.persistence.repository.paper.list_tasks",
                   side_effect=RuntimeError("任务表不可读")):
            r3 = client.get("/api/workbench/pending-events")
        assert r3.status_code == 200
        assert r3.json()["availability"]["qualify_pending"] == "unavailable"

    def test_str_report_defense_reachable(self, client):
        """str 报告防御分支可达（157/246）：get_final_report 现形制恒
        dict（内部已 loads），str 形态为防御分支——mock 可达性证明
        两消费函数（事件扫描+源可用性）的 loads 路径正确。"""
        from unittest.mock import patch
        db = client._db
        from core.persistence.repository import paper as paper_repo
        from sqlalchemy import create_engine, update as sa_update
        from sqlalchemy.orm import sessionmaker
        from core.persistence.orm.models import PtTask
        paper_repo.create_task(
            db, strategy_id="st_f3", dna_snapshot_json="{}",
            symbol="BTCUSDT", timeframe="4h",
            research_status_snapshot="unverified", window_bars=10,
            task_id="wb_f3")
        S = sessionmaker(bind=create_engine(f"sqlite:///{db}"))
        with S() as sess:
            sess.execute(sa_update(PtTask).where(
                PtTask.task_id == "wb_f3").values(status="completed"))
            sess.commit()
        str_report = {"task_id": "wb_f3", "report_version": 1,
                      "generated_at": "t", "as_of": "t",
                      "report": json.dumps({"performance": {}, "qualification": {
                          "trigger_consistency": {"passed": False,
                                                  "deviation_ratio": 0.3}}})}
        with patch("core.paper_trading.reporting.get_final_report",
                   return_value=str_report):
            r = client.get("/api/workbench/pending-events")
        body = r.json()
        # 事件扫描走 str loads → trigger_deviation 事件产出
        t_events = [e for e in body["events"]
                    if e["type"] == "trigger_deviation"]
        assert [e["payload"]["task_id"] for e in t_events] == ["wb_f3"]
        # 源可用性走 str loads → qualification 键在场=不标降级
        assert body["availability"]["trigger_deviation"] is None

    def test_terminal_without_report_skipped(self, client):
        """终态任务无报告→continue 静默跳过（154/243）：无事件无 degraded，
        源可用性判定同样跳过该任务不误判。"""
        from core.persistence.repository import paper as paper_repo
        from sqlalchemy import create_engine, update as sa_update
        from sqlalchemy.orm import sessionmaker
        from core.persistence.orm.models import PtTask
        db = client._db
        paper_repo.create_task(
            db, strategy_id="st_f4", dna_snapshot_json="{}",
            symbol="BTCUSDT", timeframe="4h",
            research_status_snapshot="unverified", window_bars=10,
            task_id="wb_f4")
        S = sessionmaker(bind=create_engine(f"sqlite:///{db}"))
        with S() as sess:
            sess.execute(sa_update(PtTask).where(
                PtTask.task_id == "wb_f4").values(status="completed"))
            sess.commit()
        r = client.get("/api/workbench/pending-events")
        body = r.json()
        assert body["events"] == []
        assert body["degraded"] == 0
        # 有终态任务但无报告可读→无 qualification 键在场→全缺键 unavailable
        assert body["availability"]["trigger_deviation"] == "unavailable"


class TestTruncatedDisclosure:
    """CARD-07 truncated 披露：终态扫描页满=可能漏扫的显式告知
    （TERMINAL_SCAN_LIMIT 常量缩限构造——1000 任务页满态构造不经济）。"""

    def _seed_terminal(self, db, n, report):
        for i in range(n):
            _add_terminal_report(db, f"wb_tr{i}", "completed", report)

    def test_page_full_produces_truncated(self, client):
        """页满产：扫描数达上限 → truncated=True+scanned_tasks 披露。"""
        from unittest.mock import patch
        db = client._db
        self._seed_terminal(db, 2, {"performance": {}})
        with patch("api.routes.workbench.TERMINAL_SCAN_LIMIT", 2):
            r = client.get("/api/workbench/pending-events")
        body = r.json()
        assert body["scanned_tasks"] == 2
        assert body["truncated"] is True

    def test_below_limit_no_truncated(self, client):
        """不满不产：扫描数低于上限 → truncated=False+scanned 如实。"""
        from unittest.mock import patch
        db = client._db
        self._seed_terminal(db, 2, {"performance": {}})
        with patch("api.routes.workbench.TERMINAL_SCAN_LIMIT", 3):
            r = client.get("/api/workbench/pending-events")
        body = r.json()
        assert body["scanned_tasks"] == 2
        assert body["truncated"] is False

    def test_running_page_full_truncated(self, client):
        """qualify 半边（批次三补件）：running 面页满单面置位 truncated
        （OR 语义——终态面不满仍置位）+scanned_running_tasks 披露；
        页满不影响事件产出（扫描照常）。"""
        from unittest.mock import patch
        db = client._db
        # 两 running 任务：一达标（事件应产出）+一初启（balance None 跳过）
        _upsert_running(db, "wb_r1", balance=110.0, trades=6)
        _upsert_running(db, "wb_r2", balance=None, trades=0)
        with patch("api.routes.workbench.TERMINAL_SCAN_LIMIT", 2):
            r = client.get("/api/workbench/pending-events")
        body = r.json()
        assert body["scanned_running_tasks"] == 2  # 含 balance None 行
        assert body["scanned_tasks"] == 0          # 终态面空（不满）
        assert body["truncated"] is True           # running 单面页满 OR 置位
        types = {e["type"] for e in body["events"]}
        assert "qualify_pending" in types          # 页满披露不影响事件产出

    def test_truncated_coexists_with_degraded(self, client):
        """页满与单源失败并存：truncated=True 且 degraded>=1 同响应
        （两披露字段独立不互斥）。"""
        from unittest.mock import patch
        from sqlalchemy import create_engine
        from sqlalchemy.orm import sessionmaker
        from core.persistence.orm.models import PtReport
        db = client._db
        self._seed_terminal(db, 1, {"performance": {}})
        # 第二任务报告坏 JSON——json.loads raise → degraded 路径
        _add_terminal_report(db, "wb_bad", "stopped", {"performance": {}})
        S = sessionmaker(bind=create_engine(f"sqlite:///{db}"))
        with S() as sess:
            sess.query(PtReport).filter(
                PtReport.task_id == "wb_bad").update(
                {"report_json": "{not-json"})
            sess.commit()
        with patch("api.routes.workbench.TERMINAL_SCAN_LIMIT", 2):
            r = client.get("/api/workbench/pending-events")
        body = r.json()
        assert body["truncated"] is True
        assert body["degraded"] >= 1
        assert body["scanned_tasks"] == 2


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


class TestCorruptReportFailOpen:
    """repay-vr-review-debts TD-02：pt_report.report_json 坏值下
    workbench 两消费点（pending-events 的 trigger 扫描/process-status
    源可用性）仍观察面降级——外层 except Exception 吞错语义不变
    （ResearchError 不穿出不 500）。"""

    def _env(self, client):
        import sqlite3

        from core.persistence.repository import paper
        from core.paper_trading.reporting import finalize_report

        paper.create_task(
            client._db, task_id="pt_wbcorp01", strategy_id="st_wb",
            dna_snapshot_json='{"risk_genes": {"leverage": 2}}',
            symbol="BTCUSDT", timeframe="4h",
            research_status_snapshot="unverified")
        paper.update_task(client._db, "pt_wbcorp01", status="running",
                          started_at="2026-10-01T00:00:00+00:00")
        paper.update_task(client._db, "pt_wbcorp01", status="completed",
                          stopped_at="2026-10-02T00:00:00+00:00")
        finalize_report(
            client._db, client._data_dir, "pt_wbcorp01",
            generated_at="2026-10-02T00:00:00+00:00")
        conn = sqlite3.connect(client._db)
        conn.execute(
            "UPDATE pt_report SET report_json=? WHERE task_id=?",
            ('{"broken": ', "pt_wbcorp01"))
        conn.commit()
        conn.close()

    def test_pending_events_still_200_degraded(self, client):
        self._env(client)
        r = client.get("/api/workbench/pending-events")
        assert r.status_code == 200, r.text
        body = r.json()
        assert "events" in body
        # 降级标记实证（防漏扫假绿——tester 探针实测锚定）：trigger
        # 扫描逐任务 except 计数 degraded≥1 + 源可用性整体 except →
        # trigger_deviation=unavailable——证明坏值被降级路径实际处理
        # 而非任务漏扫（:167/:256 两消费点同一请求内都被穿过）
        assert body["degraded"] >= 1
        assert body["availability"]["trigger_deviation"] == "unavailable"
