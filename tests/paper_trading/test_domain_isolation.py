"""调度域隔离负向用例（fix-replay-task-lifecycle 任务 3.8）。

回放与纸盘实时共表 pt_task 后，纸盘调度器对 origin=replay 行的零处置
边界：拾取/启动恢复/心跳移交三运行域入口+策略回流/资金费回补两终态
域入口+stop 域拒绝六件（spec paper-trading MODIFIED 各 Scenario 的
负向面）。每件均带 origin=paper 同库对照行——证明过滤是域选择性
（replay 行零处置）而非扫描面整体失效（paper 行照常处置）。

装配沿 test_scheduler._SchedulerHarness 与 test_funding_backfill.
_FundingHarness 形制（真实 registry+recover spy 注入；run_once/
startup_recover 单轮同步测试通道）。
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

pytestmark = [pytest.mark.unit]

from core.data.storage import save_parquet
from core.features.indicators import compute_all_indicators
from core.paper_trading.runtime.recovery import backfill_terminal_funding
from core.paper_trading.runtime.scheduler import PaperScheduler
from core.paper_trading.runtime.feed import FeedRegistry
from core.persistence.db_ext import init_db_ext
from core.persistence.repository import paper
from tests.helpers.data_factory import make_dna, make_ohlcv

# 合成帧用未来时间轴（>created_at 真实时钟——历史 bar 不被起始边界跳过）
_FRAME_START = "2030-01-01"


class _DomainHarness:
    """调度域隔离测试装配（六面共用：库+registry+调度器 spy）。"""

    def __init__(self, tmp_path: Path, *, funding_times=()):
        self.data_dir = tmp_path / "market"
        self.data_dir.mkdir()
        self.df = make_ohlcv(n=200, freq="4h", seed=7, base_price=100.0,
                             start=_FRAME_START)
        save_parquet(self.df, self.data_dir / "BTCUSDT_4h.parquet")
        if funding_times:
            save_parquet(self._funding_frame(funding_times),
                         self.data_dir / "BTCUSDT_funding.parquet")
        self.enhanced = compute_all_indicators(self.df)

        self.db = tmp_path / "domain.db"
        init_db_ext(self.db)
        self.recover_calls: list[dict] = []
        self.registry = FeedRegistry(
            self.data_dir,
            rest_fetcher=lambda *a, **k: pd.DataFrame(),
            funding_refresher=lambda **k: pd.DataFrame())
        self.scheduler = PaperScheduler(
            self.db, registry=self.registry,
            recover_fn=self._recover_spy)

    @staticmethod
    def _funding_frame(times) -> pd.DataFrame:
        idx = pd.DatetimeIndex(pd.to_datetime(list(times), utc=True),
                               name="timestamp")
        return pd.DataFrame({"funding_rate": [0.0001] * len(idx)},
                            index=idx)

    def _recover_spy(self, db_path, row, **kwargs):
        self.recover_calls.append({"task_id": row["task_id"]})
        return None  # 不可信转 error（专测另注）——避免起线程

    def create_task(self, *, origin: str = "paper") -> str:
        """直建任务行（origin 域可控——测试通道）。"""
        return paper.create_task(
            self.db, strategy_id="strat-1",
            dna_snapshot_json=make_dna().to_json(),
            symbol="BTCUSDT", timeframe="4h",
            research_status_snapshot="unverified",
            initial_cash=10_000.0, leverage=1,
            window_bars=10_000, origin=origin)

    def teardown(self) -> None:
        self.scheduler.stop()
        self.scheduler.join(timeout=5.0)


def _force_terminal(db: Path, task_id: str, *, status: str = "completed",
                    started_at: str = "2030-01-01T00:00:00+00:00") -> None:
    """沿合法迁移链推到终态（pending→running→completed/stopped——
    update_task 前态门走真实状态机，不直 SQL 旁路）。"""
    paper.update_task(db, task_id, status="running", started_at=started_at)
    hit = paper.update_task(db, task_id, status=status)
    assert hit == 1, f"终态迁移未命中: {task_id} -> {status}"


class TestPickupDomainIsolation:
    """拾取面：replay pending 行不进拾取集（spec Scenario
    「回放 pending 行不被纸盘调度器拾取」）。"""

    def test_replay_pending_not_picked_up_while_paper_picked(self, tmp_path):
        import time

        h = _DomainHarness(tmp_path)
        try:
            replay_tid = h.create_task(origin="replay")
            paper_tid = h.create_task(origin="paper")
            h.scheduler.run_once()

            # 同步判据：拾取登记面（_launch 起线程前登记——run_once
            # 返回时已定）：paper 行在座（对照拾取发生）、replay 行
            # 零登记（零 launch）
            with h.scheduler._lock:  # noqa: SLF001——登记面测试通道
                picked = set(h.scheduler._executors)
            assert paper_tid in picked, "对照 paper 行未被拾取——过滤误伤纸盘域"
            assert replay_tid not in picked, "replay pending 行被拾取 launch"
            # 行为判据：等待窗口后 replay 行仍 pending（无拾取副作用）
            deadline = time.monotonic() + 10.0
            paper_status = None
            while time.monotonic() < deadline:
                paper_status = paper.get_task(h.db, paper_tid)["status"]
                if paper_status in {"running", "error"}:
                    break
                time.sleep(0.05)
            assert paper_status in {"running", "error"}, (
                f"对照 paper 行未进入执行（{paper_status}）")
            assert paper.get_task(h.db, replay_tid)["status"] == "pending"
            assert h.recover_calls == []  # 拾取面不经恢复器（旁证零 launch）
        finally:
            h.teardown()


class TestLaunchGuardDomainIsolation:
    """_launch 直调旁路守卫（3.4——防不经 _pickup_pending 过滤查询的
    直调面：非 paper 域行拒绝 launch，拾取过滤之外的独立第二层）。"""

    def test_direct_launch_replay_row_rejected(self, tmp_path):
        h = _DomainHarness(tmp_path)
        replay_tid = h.create_task(origin="replay")

        h.scheduler._launch(paper.get_task(h.db, replay_tid))

        with h.scheduler._lock:  # noqa: SLF001——登记面测试通道
            assert replay_tid not in h.scheduler._executors
        assert paper.get_task(h.db, replay_tid)["status"] == "pending"


class TestStartupRecoverDomainIsolation:
    """启动恢复面：origin=replay running 残行不被接管（spec Scenario
    「回放 running 残态不被启动恢复接管」——不恢复执行、不转 error、
    不改写任何字段）。"""

    def test_replay_running_residual_skipped_untouched(self, tmp_path):
        h = _DomainHarness(tmp_path)
        replay_tid = h.create_task(origin="replay")
        paper_tid = h.create_task(origin="paper")
        residual_fields = {
            "status": "running",
            "started_at": "2030-01-01T00:00:00+00:00",
            "heartbeat_at": "2030-01-01T00:00:00+00:00"}
        for tid in (replay_tid, paper_tid):
            paper.update_task(h.db, tid, **residual_fields)
        before = paper.get_task(h.db, replay_tid)

        counts = h.scheduler.startup_recover()

        # replay 行：零恢复零转态零字段改写（逐字段对账）
        after = paper.get_task(h.db, replay_tid)
        for key, value in residual_fields.items():
            assert after[key] == value, f"replay 残行字段被改写: {key}"
        assert after["error_detail"] is None  # 不转 error（保留 running 残态）
        assert after["updated_at"] == before["updated_at"]
        # 对照：paper 残行经恢复器处置（spy 记录——恢复调用发生）
        assert {"task_id": paper_tid} in h.recover_calls
        assert {"task_id": replay_tid} not in h.recover_calls
        # 计数面：replay 行既不计 recovered 也不计 untrusted
        assert counts == {"recovered": 0, "untrusted": 1}


class TestHeartbeatDomainIsolation:
    """心跳移交面：replay running 行不进移交集（spec Scenario
    「回放行不参与心跳移交」）。"""

    def test_replay_stale_heartbeat_not_handed_over(self, tmp_path):
        h = _DomainHarness(tmp_path)
        replay_tid = h.create_task(origin="replay")
        paper_tid = h.create_task(origin="paper")
        for tid in (replay_tid, paper_tid):
            paper.update_task(
                h.db, tid, status="running",
                started_at="2030-01-01T00:00:00+00:00",
                heartbeat_at="2030-01-01T00:00:00+00:00")
        # 4h bar→12h 超时；2030-02-01 距 2030-01-01 恒超时
        h.scheduler._now_fn = lambda: pd.Timestamp("2030-02-01", tz="UTC")
        h.scheduler.run_once()

        assert {"task_id": paper_tid} in h.recover_calls  # 对照：移交发生
        assert {"task_id": replay_tid} not in h.recover_calls
        assert paper.get_task(h.db, replay_tid)["status"] == "running"


class TestFlowbackDomainIsolation:
    """策略回流面：replay 终态行不参与「策略在纸盘域的最近验证结束」
    判定（spec Scenario「回放终态行不触发策略回流」——origin 过滤前置
    在每策略最新终态行的查询域内：replay 行更新也不覆盖策略的纸盘域
    最新行）。"""

    def test_flowback_uses_paper_latest_despite_newer_replay_row(
            self, tmp_path):
        from core.persistence.repository.strategy import (
            get_strategy,
            save_strategy,
        )
        from core.strategy.lifecycle import transit

        h = _DomainHarness(tmp_path)
        save_strategy(
            h.db, strategy_id="strat-1",
            dna_json=make_dna().to_json(),
            symbol="BTCUSDT", timeframe="4h", status="active")
        # 挂盘迁移（初始态白名单只允许 draft/active——paper 态经
        # mount trigger 进入，沿 REST 挂盘同通道）
        transit(h.db, "strat-1", target="paper", trigger="mount",
                evidence={"task_id": "mount-for-test"})
        # 先建 paper 终态行（created_at 早）再建 replay 终态行（晚）——
        # 后置滤域的错误实现会把策略的「最新终态行」错判为 replay 行
        # 而整段跳过回流；前置 SQL 下推的正确实现回流用 paper 行
        paper_tid = h.create_task(origin="paper")
        _force_terminal(h.db, paper_tid)
        replay_tid = h.create_task(origin="replay")
        _force_terminal(h.db, replay_tid)
        assert (paper.get_task(h.db, replay_tid)["created_at"]
                >= paper.get_task(h.db, paper_tid)["created_at"])

        h.scheduler._replay_paper_strategies()

        strategy = get_strategy(h.db, "strat-1")
        assert strategy["status"] == "active", (
            "策略未回流——replay 行覆盖了纸盘域最新终态判定（过滤未前置）")
        # 回流凭证=纸盘域最新终态行（replay task_id 不注册为回流证据）
        from core.persistence.orm.engine import engine_for
        from core.persistence.orm.models import StrategyTransitionEvent
        from sqlalchemy import select

        engine = engine_for(h.db)
        with engine.connect() as conn:
            events = conn.execute(
                select(StrategyTransitionEvent).where(
                    StrategyTransitionEvent.strategy_id == "strat-1",
                    StrategyTransitionEvent.trigger == "task_end"),
            ).mappings().all()
        assert events, "task_end 事件行缺失"
        import json as _json

        evidence = _json.loads(events[-1]["evidence_json"])
        assert evidence["task_id"] == paper_tid
        assert evidence["task_id"] != replay_tid


class TestFundingBackfillDomainIsolation:
    """资金费回补面：replay 行 pending funding 结算点不被实时费率回补
    （spec Scenario「回放行资金费不被实时回补」——历史归档缺口如实留
    pending 披露，不触发报告修订回调错域）。"""

    @staticmethod
    def _seed_pending_funding(db: Path, task_id: str, settle_time: str,
                              bar_time: str) -> None:
        """直插 pending funding 行（测试通道——唯一键 (task_id,
        settle_time) 幂等）。"""
        from sqlalchemy import insert

        from core.persistence.orm.engine import engine_for
        from core.persistence.orm.models import PtFunding

        engine = engine_for(db)
        with engine.begin() as conn:
            conn.execute(insert(PtFunding).values(
                task_id=task_id, settle_time=settle_time,
                bar_time=bar_time, status="pending",
                position_quantity=1.0, settle_price=100.0))

    def test_replay_pending_funding_left_untouched(self, tmp_path):
        # settle_time 落库形制=网格点 isoformat（executor 写入通道同源）
        settle = "2030-01-01T16:00:00+00:00"
        bar = "2030-01-01T20:00:00+00:00"
        h = _DomainHarness(
            tmp_path, funding_times=["2030-01-01 16:00:00"])
        replay_tid = h.create_task(origin="replay")
        paper_tid = h.create_task(origin="paper")
        _force_terminal(h.db, replay_tid)
        _force_terminal(h.db, paper_tid)
        self._seed_pending_funding(h.db, replay_tid, settle, bar)
        self._seed_pending_funding(h.db, paper_tid, settle, bar)

        revisions: list[str] = []
        result = backfill_terminal_funding(
            h.db, data_dir=h.data_dir,
            on_revision=lambda tid: revisions.append(tid))

        # replay 行：pending 原样（如实披露）+不触发修订回调
        replay_pendings = [r for r in paper.list_funding_records(
            h.db, replay_tid, limit=100) if r["status"] == "pending"]
        assert len(replay_pendings) == 1, "replay 行 pending 结算点被实时回补"
        assert replay_tid not in revisions
        # 对照：paper 行同点位正常回补+修订回调触发
        assert result["tasks_backfilled"] == 1
        assert revisions == [paper_tid]
        assert paper.list_funding_records(
            h.db, paper_tid, limit=100,
            status="settled_disclosure_only"), "对照 paper 行未回补"


class TestStopDomainRejection:
    """stop 域拒绝：origin=replay running 行 409（spec Scenario「回放
    运行中行拒绝 stop」——同步调用驱动无干预通道，外部 stop 只会制造
    终态写入竞态）。"""

    def test_scheduler_dispatch_domain_forbidden(self, tmp_path):
        h = _DomainHarness(tmp_path)
        replay_tid = h.create_task(origin="replay")
        paper.update_task(
            h.db, replay_tid, status="running",
            started_at="2030-01-01T00:00:00+00:00")

        assert h.scheduler.stop_task(replay_tid) == "domain_forbidden"
        # 行零触碰（无终态写入竞态）
        row = paper.get_task(h.db, replay_tid)
        assert row["status"] == "running"
        assert row["stopped_at"] is None

    def test_pending_replay_stop_rejected_untouched(self, tmp_path):
        """spec Scenario「回放 pending 行拒绝 stop」（CARD-01 补锚）：
        建行后装载/指标计算窗口（回放线程尚未置 running）——不进
        未拾取撤销分支（origin=paper 专属），行零触碰（status/
        stopped_at/stop_reason 逐字段），调度器面 domain_forbidden，
        REST 出站 409 域不符。
        """
        h = _DomainHarness(tmp_path)
        replay_tid = h.create_task(origin="replay")

        assert h.scheduler.stop_task(replay_tid) == "domain_forbidden"
        row = paper.get_task(h.db, replay_tid)
        assert row["status"] == "pending", "replay pending 行被改态"
        assert row["stopped_at"] is None
        assert row["stop_reason"] is None
        assert h.recover_calls == []

        # REST 出站面：409 域不符（pending 装载窗口同拒）
        from fastapi.testclient import TestClient

        from api.app import create_app

        app = create_app(db_path=h.db, data_dir=h.data_dir)
        app.state.db_path = h.db
        app.state.data_dir = h.data_dir
        app.state.paper_scheduler = h.scheduler
        resp = TestClient(app).post(
            f"/api/paper-trading/tasks/{replay_tid}/stop")
        assert resp.status_code == 409
        assert "replay" in resp.json()["detail"]
        after = paper.get_task(h.db, replay_tid)
        assert after["status"] == "pending"
        assert after["stopped_at"] is None
        assert after["stop_reason"] is None

    def test_rest_stop_replay_running_409(self, tmp_path):
        from fastapi.testclient import TestClient

        from api.app import create_app

        db = tmp_path / "stop_api.db"
        data_dir = tmp_path / "market"
        data_dir.mkdir()
        init_db_ext(db)
        app = create_app(db_path=db, data_dir=data_dir)
        app.state.db_path = db
        app.state.data_dir = data_dir
        app.state.paper_scheduler = PaperScheduler(db_path=db,
                                                   data_dir=data_dir)
        tid = paper.create_task(
            db, strategy_id="strat-1",
            dna_snapshot_json=make_dna().to_json(),
            symbol="BTCUSDT", timeframe="4h",
            research_status_snapshot="unverified", origin="replay")
        paper.update_task(
            db, tid, status="running",
            started_at="2030-01-01T00:00:00+00:00")

        client = TestClient(app)
        resp = client.post(f"/api/paper-trading/tasks/{tid}/stop")
        assert resp.status_code == 409
        assert "replay" in resp.json()["detail"]
        # 对照：paper running 行 stop 走既有协作/直迁通道（200）
        paper_tid = paper.create_task(
            db, strategy_id="strat-1",
            dna_snapshot_json=make_dna().to_json(),
            symbol="BTCUSDT", timeframe="4h",
            research_status_snapshot="unverified")
        paper.update_task(
            db, paper_tid, status="running",
            started_at="2030-01-01T00:00:00+00:00")
        resp_paper = client.post(
            f"/api/paper-trading/tasks/{paper_tid}/stop")
        assert resp_paper.status_code == 200
        assert resp_paper.json()["status"] == "stopped"
