"""任务调度器测试（rebuild-paper-trading 任务 3.3）。

覆盖：状态机全迁移集+非法迁移拒绝面；并发上限（第 11 任务停留
pending、活跃数降后拾取）+环境变量覆盖；窗口到点自动 completed+
停止条件转 stopped（禁止变化断言：停止触发的 bar 上交易行为与无停止
条件时逐笔一致）；stop 三态分派（pending 直接 stopped/running 协作）；
心跳超时移交；runner-status 数据面；守护线程全链（scheduler→feed→
executor→落库——合成帧注入 fetcher，不经真实网络）。
"""
from __future__ import annotations

import importlib
import time
from pathlib import Path

import pytest

pytestmark = [pytest.mark.unit]

from core.data.storage import save_parquet
from core.features.indicators import compute_all_indicators
from core.paper_trading.runtime.scheduler import (
    PaperScheduler,
    can_transition,
)
from core.paper_trading.runtime.feed import FeedRegistry
from core.persistence.db_ext import init_db_ext
from core.persistence.repository import paper
from tests.helpers.data_factory import make_dna, make_ohlcv

# 合成帧用未来时间轴（>created_at 真实时钟——历史 bar 不被起始边界跳过）
_FRAME_START = "2030-01-01"


def _wait_task_status(db: Path, task_id: str, statuses: set[str],
                      timeout: float = 15.0) -> dict:
    """轮询等待任务到达目标状态（全链异步收口）。"""
    deadline = time.monotonic() + timeout
    row = paper.get_task(db, task_id)
    while time.monotonic() < deadline:
        row = paper.get_task(db, task_id)
        if row and row["status"] in statuses:
            return row
        time.sleep(0.1)
    pytest.fail(f"任务 {task_id} 未在 {timeout}s 内到达 {statuses}"
                f"（当前 {row and row['status']}）")


class _SchedulerHarness:
    """调度器测试装配（真实守护线程全链——合成 fetcher 注入）。"""

    def __init__(self, tmp_path: Path, *, max_running=None):
        self.data_dir = tmp_path / "market"
        self.data_dir.mkdir()
        self.df = make_ohlcv(n=200, freq="4h", seed=7, base_price=100.0,
                             start=_FRAME_START)
        save_parquet(self.df, self.data_dir / "BTCUSDT_4h.parquet")
        self.enhanced = compute_all_indicators(self.df)

        self.db = tmp_path / "paper.db"
        init_db_ext(self.db)
        self.registry = FeedRegistry(
            self.data_dir,
            fetcher=lambda **k: self.df,
            funding_refresher=lambda **k: self._empty_funding())
        self.recover_calls: list[dict] = []
        self.pushes: list[dict] = []
        self.concluded: list[tuple[str, str]] = []
        self.scheduler = PaperScheduler(
            self.db, registry=self.registry,
            push=lambda tid, p: self.pushes.append(p),
            on_concluded=lambda tid, st: self.concluded.append((tid, st)),
            recover_fn=self._recover_spy,
            poll_interval=0.2,
            max_running=max_running,
        )

    @staticmethod
    def _empty_funding():
        import pandas as pd

        return pd.DataFrame(
            {"funding_rate": []},
            index=pd.DatetimeIndex([], name="timestamp"))

    def _recover_spy(self, db_path, row, **kwargs):
        self.recover_calls.append({"task_id": row["task_id"]})
        return None  # 不可信转 error（专测另注）——避免起线程

    def create_task(self, *, window_bars=20, stop_loss_pct=None) -> str:
        return paper.create_task(
            self.db, strategy_id="strat-1",
            dna_snapshot_json=make_dna().to_json(),
            symbol="BTCUSDT", timeframe="4h",
            research_status_snapshot="unverified",
            initial_cash=10_000.0, leverage=1,
            window_bars=window_bars, stop_loss_pct=stop_loss_pct)

    def teardown(self) -> None:
        self.scheduler.stop()
        self.scheduler.join(timeout=5.0)


class TestStateMachine:
    """五态状态机迁移集（design §5.1）。"""

    @pytest.mark.parametrize(
        "from_status,to_status,expected",
        [
            ("pending", "running", True),    # 拾取
            ("pending", "stopped", True),    # 未拾取撤销
            ("pending", "error", True),      # 装载校验拒绝
            ("running", "completed", True),  # 窗口到点
            ("running", "stopped", True),    # 用户 stop/停止条件
            ("running", "error", True),      # 执行异常/恢复不可信
            ("completed", "running", False),  # 终态吸收
            ("stopped", "running", False),
            ("error", "running", False),
            ("completed", "stopped", False),
            ("stopped", "completed", False),
            ("error", "completed", False),
            ("running", "pending", False),   # 无回退迁移
            ("completed", "completed", False),
        ],
    )
    def test_transition_set(self, from_status, to_status, expected):
        assert can_transition(from_status, to_status) is expected


class TestConcurrencyCap:
    """并发上限与排队拾取。"""

    def test_cap_blocks_and_pickup_after_release(self, tmp_path):
        """上限 2：第 3 任务停留 pending；活跃降后按序拾取。"""
        h = _SchedulerHarness(tmp_path, max_running=2)
        try:
            ids = [h.create_task(window_bars=None) for _ in range(3)]
            h.scheduler.run_once()
            # 上限 2：前两 running，第 3 停留 pending
            _wait_task_status(h.db, ids[0], {"running"})
            _wait_task_status(h.db, ids[1], {"running"})
            assert paper.get_task(h.db, ids[2])["status"] == "pending"

            # 停掉 ids[0]（协作停——空闲等待期退出）→活跃槽释放
            assert h.scheduler.stop_task(ids[0]) == "stopping"
            _wait_task_status(h.db, ids[0], {"stopped"})

            # 槽释放后的守护轮拾取第 3 任务（created_at 顺序）
            h.scheduler.run_once()
            _wait_task_status(h.db, ids[2], {"running"})
            statuses = [paper.get_task(h.db, i)["status"] for i in ids]
            assert statuses[1] == "running"
        finally:
            h.teardown()

    def test_env_override_max_running(self, monkeypatch):
        """PAPER_MAX_RUNNING_TASKS 环境变量覆盖 constants 单源。"""
        from core.paper_trading import constants

        monkeypatch.setenv("PAPER_MAX_RUNNING_TASKS", "3")
        try:
            reloaded = importlib.reload(constants)
            assert reloaded.MAX_RUNNING_TASKS == 3
        finally:
            monkeypatch.delenv("PAPER_MAX_RUNNING_TASKS")
            importlib.reload(constants)  # 恢复缺省（防污染后续用例）


class TestConclusion:
    """窗口到点自动结题+停止条件零干预。"""

    def test_full_chain_window_autocompletes(self, tmp_path):
        """守护线程全链：拾取→feed 刷新→执行→窗口到点 completed。"""
        h = _SchedulerHarness(tmp_path)
        try:
            tid = h.create_task(window_bars=30)  # >RSI 预热+首信号位 i=20
            h.scheduler.start()
            task = _wait_task_status(h.db, tid, {"completed"})
            assert task["bars_processed"] == 30
            assert task["started_at"] is not None
            assert task["stopped_at"] is not None
            fills = paper.list_fills(h.db, tid, limit=1000)
            assert fills, "全链须产出成交"
            eqs = paper.list_equity_snapshots(h.db, tid, limit=1000)
            assert len(eqs) == 30  # 窗口恰 30 根快照
            assert any(p["type"] == "task_completed" for p in h.pushes)
            assert (tid, "completed") in h.concluded
        finally:
            h.teardown()

    def test_stop_condition_zero_intervention(self, tmp_path):
        """停止触发的 bar 上交易行为与无停止条件时逐笔一致。

        触发场景按正确权益语义（P0 修复后 equity=balance+margin+upnl）
        设定：make_dna 缺省（ps=0.5/lev=1/SL 5%）下每轮 SL 循环亏权益
        约 2.5%——stop_loss_pct=0.05（阈值 9500）两轮内破线可达；
        修复前缺陷公式（漏 margin 开仓假腰斩）下 0.5 线跌 10% 即触
        的旧场景在正确语义中不可达（名义需跌 100%）。
        """
        h = _SchedulerHarness(tmp_path)
        try:
            plain = h.create_task(window_bars=None)
            guarded = h.create_task(window_bars=None, stop_loss_pct=0.05)
            h.scheduler.start()
            stopped_row = _wait_task_status(
                h.db, guarded, {"stopped"}, timeout=30.0)
            assert stopped_row["stop_reason"] == "stop_condition"

            # 等 plain 追平停止点（两任务并发推进——guarded 停止时
            # plain 可能滞后，直接读库会截断出更短序列=偶发红）
            upto = stopped_row["last_bar_time"]
            deadline = time.monotonic() + 30.0
            while time.monotonic() < deadline:
                if paper.get_task(h.db, plain)["last_bar_time"] >= upto:
                    break
                time.sleep(0.1)
            else:
                pytest.fail("plain 任务未在 30s 内追平停止点")

            fills_plain = paper.list_fills(h.db, plain, limit=1000)
            fills_guarded = paper.list_fills(h.db, guarded, limit=1000)
            assert fills_guarded, "停止前须有交易"
            # 截断到停止任务感知范围逐笔对比（价格/数量/kind/seq 一致）
            upto = stopped_row["last_bar_time"]
            comparable = [f for f in fills_plain
                          if f["bar_time"] <= upto]
            assert len(comparable) == len(fills_guarded)
            for a, b in zip(comparable, fills_guarded):
                assert (a["bar_time"], a["seq"], a["kind"],
                        a["price"], a["quantity"]) == (
                    b["bar_time"], b["seq"], b["kind"],
                    b["price"], b["quantity"])
        finally:
            h.teardown()


class TestStopDispatch:
    """stop 三态分派（调度器写者面）。"""

    def test_pending_stop_direct_stopped_no_side_effects(self, tmp_path):
        """pending 未拾取撤销：直接 stopped、五表零明细行。"""
        h = _SchedulerHarness(tmp_path)
        try:
            tid = h.create_task()
            assert h.scheduler.stop_task(tid) == "stopped"
            row = paper.get_task(h.db, tid)
            assert row["status"] == "stopped"
            assert row["stop_reason"] == "user_stop"
            assert row["started_at"] is None  # 无任何运行副作用
            assert paper.list_fills(h.db, tid) == []
            assert paper.list_equity_snapshots(h.db, tid) == []
            assert paper.list_funding_records(h.db, tid) == []
        finally:
            h.teardown()

    def test_terminal_stop_rejected(self, tmp_path):
        """终态任务 stop 请求拒绝（terminal）。

        终态行构造走合法链 pending→running→completed（选项 i——
        CAS 时代测试直建通道不再非法直跳终态）。
        """
        h = _SchedulerHarness(tmp_path)
        try:
            tid = h.create_task()
            paper.update_task(
                h.db, tid, status="running",
                started_at="2030-01-01T00:00:00+00:00")
            paper.update_task(
                h.db, tid, status="completed",
                stopped_at="2030-01-01T00:00:01+00:00")
            assert h.scheduler.stop_task(tid) == "terminal"
        finally:
            h.teardown()


class TestStopRaceWindows:
    """stop×拾取/移交竞态窗口（确定性交错注入锚）。

    交错注入：patch get_task 在 stop_task 读行的间隙注入并发方完成
    的落库效果（每步均为对真实仓库函数的调用，仅手工重排顺序——
    TESTing 并发可控同步点形制）；执行器 run() 起点的 running 写按
    executor.py:398-400 等价重放。

    契约依据（spec R1）：stop 应答 "stopped" 即终局承诺（未拾取撤销
    语义+状态迁移唯一写者）——交错下任务终态 running 属 stop 请求
    吞没。【缺陷在案锚：修复前红，禁删断言或放宽语义迁就实现】
    """

    def test_stop_pending_swallowed_by_concurrent_pickup(self, tmp_path):
        """窗口①：stop 读 pending → 拾取在间隙置 running → stop 仍按
        旧快照写 stopped → 执行器写覆盖回 running——应答 stopped 而
        任务照跑（吞没）。"""
        import core.paper_trading.runtime.scheduler as sched_mod

        h = _SchedulerHarness(tmp_path)
        try:
            tid = h.create_task()
            real_get = paper.get_task
            injected = {"done": False}

            def racing_get(db, task_id, **kw):
                row = real_get(db, task_id, **kw)
                if (row and row["task_id"] == tid
                        and row["status"] == "pending"
                        and not injected["done"]):
                    injected["done"] = True
                    # t1：并发拾取线程在本间隙完成置 running
                    # （_launch→executor.run() 起点写，executor.py:398）
                    paper.update_task(
                        h.db, tid, status="running",
                        started_at="2030-01-01T00:00:00+00:00")
                return row

            original = sched_mod.paper_repo.get_task
            sched_mod.paper_repo.get_task = racing_get
            try:
                answer = h.scheduler.stop_task(tid)
            finally:
                sched_mod.paper_repo.get_task = original

            # t3：执行器 run() 起点写（prepare 已过，等价重放）
            paper.update_task(
                h.db, tid, status="running",
                started_at="2030-01-01T00:00:10+00:00")
            final = paper.get_task(h.db, tid)
            assert not (answer == "stopped"
                        and final["status"] == "running"), (
                f"stop 吞没：应答 {answer} 而终态 "
                f"{final['status']}——拾取竞态窗口在案")
        finally:
            h.teardown()

    def test_stop_running_during_handover_swallowed(self, tmp_path):
        """窗口②：心跳超时移交进行中（recover 未返回、_executors 未
        登记）REST 线程调 stop——查表无执行器直迁 stopped；随后移交
        完成起线程，run() 覆写回 running——应答 stopped 而任务照跑。"""
        import pandas as pd

        import core.paper_trading.runtime.scheduler as sched_mod

        h = _SchedulerHarness(tmp_path)
        try:
            tid = h.create_task()
            paper.update_task(
                h.db, tid, status="running",
                started_at="2030-01-01T00:00:00+00:00",
                heartbeat_at="2030-01-01T00:00:00+00:00")

            stop_answer: list[str] = []

            def recover_and_inject_stop(db_path, row, **kwargs):
                # 移交进行中（此刻 _executors 尚未登记）——并发 REST
                # 线程调真实 stop_task：running 分支查表无 → 直迁
                stop_answer.append(h.scheduler.stop_task(row["task_id"]))
                return None  # 返回 None 后移交不起线程——t3 重放

            h.scheduler._recover_fn = recover_and_inject_stop
            h.scheduler._now_fn = lambda: pd.Timestamp(
                "2030-02-01", tz="UTC")  # 4h bar→12h 超时；30 天恒超
            h.scheduler.run_once()

            # t3：移交线程起跑后 run() 起点写（executor.py:398 等价）
            paper.update_task(
                h.db, tid, status="running",
                started_at="2030-01-01T00:01:00+00:00")
            final = paper.get_task(h.db, tid)
            assert not (stop_answer == ["stopped"]
                        and final["status"] == "running"), (
                f"stop 吞没：应答 {stop_answer} 而终态 "
                f"{final['status']}——移交竞态窗口在案")
        finally:
            h.teardown()


class TestHeartbeatHandover:
    """心跳超时移交恢复器（>3×bar 周期）。"""

    def test_stale_running_task_handed_over(self, tmp_path):
        """running 而无本进程执行器+心跳超时→移交恢复器。"""
        import pandas as pd

        h = _SchedulerHarness(tmp_path)
        tid = h.create_task()
        paper.update_task(
            h.db, tid, status="running",
            started_at="2030-01-01T00:00:00+00:00",
            heartbeat_at="2030-01-01T00:00:00+00:00")
        h.scheduler._now_fn = lambda: pd.Timestamp(
            "2030-02-01", tz="UTC")  # 4h bar→12h 超时；30 天恒超
        h.scheduler.run_once()
        assert h.recover_calls == [{"task_id": tid}]

    def test_fresh_heartbeat_not_handed_over(self, tmp_path):
        """心跳未超时不移交。"""
        import pandas as pd

        h = _SchedulerHarness(tmp_path)
        tid = h.create_task()
        paper.update_task(
            h.db, tid, status="running",
            started_at="2030-01-01T00:00:00+00:00",
            heartbeat_at="2030-01-01T00:00:00+00:00")
        h.scheduler._now_fn = lambda: pd.Timestamp(
            "2030-01-01T01:00:00", tz="UTC")  # 1h < 12h 超时线
        h.scheduler.run_once()
        assert h.recover_calls == []

    def test_no_heartbeat_not_judged(self, tmp_path):
        """无 heartbeat（刚置 running 未处理 bar）不判超时。"""
        import pandas as pd

        h = _SchedulerHarness(tmp_path)
        tid = h.create_task()
        paper.update_task(
            h.db, tid, status="running",
            started_at="2030-01-01T00:00:00+00:00")
        h.scheduler._now_fn = lambda: pd.Timestamp(
            "2030-02-01", tz="UTC")
        h.scheduler.run_once()
        assert h.recover_calls == []


class TestRunnerStatus:
    """runner-status 数据面（组 5 端点消费）。"""

    def test_status_payload(self, tmp_path):
        h = _SchedulerHarness(tmp_path, max_running=4)
        try:
            tids = [h.create_task() for _ in range(2)]
            h.scheduler.run_once()
            _wait_task_status(h.db, tids[0], {"running"})
            _wait_task_status(h.db, tids[1], {"running"})
            status = h.scheduler.status()
            assert status["max_running_tasks"] == 4
            assert status["running_count"] == 2
            assert sorted(status["active_task_ids"]) == sorted(tids)
            assert status["pending_depth"] == 0
        finally:
            h.teardown()
