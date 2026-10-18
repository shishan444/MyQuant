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
import threading
import time
from pathlib import Path
from unittest.mock import MagicMock

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


class TestDaemonAndDefensivePaths:
    """闸门盲区消解：守护线程/防御分支/异常吞咽定向锚。

    全部可控注入（fake 执行器/spy recover/异常注入），无 sleep 竞态
    依赖；守护循环用例以 poll_interval=0.05 缩短等待。
    """

    def test_default_data_dir_project_root(self, tmp_path):
        """不传 data_dir → registry 取项目根 data/market 缺省。"""
        db = tmp_path / "paper.db"
        init_db_ext(db)
        from core.paper_trading.runtime.scheduler import PaperScheduler

        s = PaperScheduler(db)
        assert str(s.registry.data_dir).endswith("data/market")

    def test_start_idempotent_same_thread(self, tmp_path):
        """重复 start 幂等：守护线程同一实例（不叠线程）。"""
        h = _SchedulerHarness(tmp_path)
        try:
            h.scheduler = type(h.scheduler)(
                h.db, registry=h.registry, poll_interval=0.05)
            h.scheduler.start()
            first = h.scheduler._thread
            h.scheduler.start()
            assert h.scheduler._thread is first
        finally:
            h.scheduler.stop()
            h.scheduler.join(timeout=5.0)

    def test_daemon_loop_survives_run_once_exception(self, tmp_path):
        """守护循环单轮异常续轮：run_once 首轮抛异常后守护线程继续。"""
        h = _SchedulerHarness(tmp_path)
        h.scheduler = type(h.scheduler)(
            h.db, registry=h.registry, poll_interval=0.05)
        calls: list[int] = []
        original = h.scheduler.run_once

        def flaky_run_once():
            calls.append(1)
            if len(calls) == 1:
                raise RuntimeError("首轮异常")
            original()

        h.scheduler.run_once = flaky_run_once
        try:
            h.scheduler.start()
            deadline = time.monotonic() + 5.0
            while time.monotonic() < deadline and len(calls) < 2:
                time.sleep(0.02)
            assert len(calls) >= 2, "守护线程须在首轮异常后续轮"
        finally:
            h.scheduler.stop()
            h.scheduler.join(timeout=5.0)

    def test_startup_recover_skips_known_executor(self, tmp_path):
        """startup_recover：本进程执行器在座（_executors 登记）→
        跳过该行不重复恢复。"""
        h = _SchedulerHarness(tmp_path)
        tid = h.create_task()
        paper.update_task(h.db, tid, status="running",
                          started_at="2030-01-01T00:00:00+00:00")
        h.scheduler._executors[tid] = object()
        result = h.scheduler.startup_recover()
        assert h.recover_calls == []
        assert result == {"recovered": 0, "untrusted": 0}

    def test_startup_recover_swallows_recover_exception(self, tmp_path):
        """startup_recover：恢复函数抛异常 → 记日志不崩、按不可信
        分桶计数（untrusted+1——恢复失败行的保守归类）。"""
        h = _SchedulerHarness(tmp_path)
        tid = h.create_task()
        paper.update_task(h.db, tid, status="running",
                          started_at="2030-01-01T00:00:00+00:00")

        def broken_recover(db_path, row, **kwargs):
            raise RuntimeError("恢复器自身异常")

        h.scheduler._recover_fn = broken_recover
        result = h.scheduler.startup_recover()
        assert result == {"recovered": 0, "untrusted": 1}

    def test_reap_finished_removes_dead_thread_entries(self, tmp_path):
        """_reap_finished：已结束线程的登记摘除（_threads/_executors）。"""
        h = _SchedulerHarness(tmp_path)
        tid = h.create_task()
        # 先迁终态防拾取段重新 launch 覆盖登记（合法两跳链——CAS
        # 守门拒 pending→completed 直迁）
        paper.update_task(h.db, tid, status="running",
                          started_at="2030-01-01T00:00:00+00:00")
        paper.update_task(h.db, tid, status="completed",
                          stopped_at="2030-01-01T00:00:00+00:00")
        dead = threading.Thread(target=lambda: None)
        dead.start()
        dead.join()
        h.scheduler._threads[tid] = dead
        h.scheduler._executors[tid] = object()
        h.scheduler.run_once()
        assert tid not in h.scheduler._threads
        assert tid not in h.scheduler._executors

    def test_malformed_heartbeat_skipped(self, tmp_path):
        """心跳字段坏值（不可解析串）→ 不判超时不移交不崩。"""
        import pandas as pd

        h = _SchedulerHarness(tmp_path)
        tid = h.create_task()
        paper.update_task(
            h.db, tid, status="running",
            started_at="2030-01-01T00:00:00+00:00",
            heartbeat_at="not-a-timestamp")
        h.scheduler._now_fn = lambda: pd.Timestamp("2030-02-01", tz="UTC")
        h.scheduler.run_once()
        assert h.recover_calls == []

    def test_stale_heartbeat_inseat_executor_cooperative_stop(
            self, tmp_path):
        """心跳超时+执行器在座：协作停（request_stop）不移交——防
        双执行器并发写库的守护分支（假死先协作停，下轮再验）。"""
        import pandas as pd

        h = _SchedulerHarness(tmp_path)
        tid = h.create_task()
        paper.update_task(
            h.db, tid, status="running",
            started_at="2030-01-01T00:00:00+00:00",
            heartbeat_at="2030-01-01T00:00:00+00:00")
        fake_exec = MagicMock()
        h.scheduler._executors[tid] = fake_exec
        h.scheduler._now_fn = lambda: pd.Timestamp("2030-02-01", tz="UTC")
        h.scheduler.run_once()
        fake_exec.controller.request_stop.assert_called_once()
        assert h.recover_calls == []

    def test_hand_over_dedupes_within_round(self, tmp_path):
        """_hand_over 同轮去重：同任务二次调用零恢复（handed 吸收）。"""
        h = _SchedulerHarness(tmp_path)
        tid = h.create_task()
        paper.update_task(h.db, tid, status="running",
                          started_at="2030-01-01T00:00:00+00:00")
        row = paper.get_task(h.db, tid)
        handed: set[str] = set()
        h.scheduler._hand_over(row, handed, reason="heartbeat_timeout")
        h.scheduler._hand_over(row, handed, reason="heartbeat_timeout")
        assert len(h.recover_calls) == 1

    def test_hand_over_starts_recovered_executor_thread(self, tmp_path):
        """移交成功路径：恢复器返回执行器 → 工作线程启动+登记在座。"""
        h = _SchedulerHarness(tmp_path)
        tid = h.create_task()
        paper.update_task(h.db, tid, status="running",
                          started_at="2030-01-01T00:00:00+00:00")
        started = threading.Event()
        fake_exec = MagicMock()

        def mark_started():
            started.set()

        fake_exec.run = mark_started
        h.scheduler._recover_fn = lambda *a, **k: fake_exec
        row = paper.get_task(h.db, tid)
        h.scheduler._hand_over(row, set(), reason="heartbeat_timeout")
        assert started.wait(timeout=5.0), "恢复线程须真启动"
        assert h.scheduler._executors[tid] is fake_exec

    def test_pickup_full_capacity_returns_early(self, tmp_path):
        """活跃数达上限：拾取段早退（新 pending 不拾不炸）。"""
        h = _SchedulerHarness(tmp_path, max_running=1)
        tid = h.create_task()
        h.scheduler._executors["occupied"] = object()  # 槽已占
        h.scheduler.run_once()
        assert paper.get_task(h.db, tid)["status"] == "pending"

    def test_launch_skips_registered_task(self, tmp_path):
        """拾取清单内任务已在座（并发登记）→ 不重复起线程。"""
        h = _SchedulerHarness(tmp_path)
        tid = h.create_task()
        h.scheduler._executors[tid] = object()
        h.scheduler.run_once()
        assert tid not in h.scheduler._threads, "在座任务不重复 launch"

    def test_backfill_exception_swallowed(self, tmp_path):
        """终态回补例程异常：单轮吞咽续轮（守护循环不崩）。"""
        h = _SchedulerHarness(tmp_path)
        h.create_task()

        def broken_backfill(db_path):
            raise RuntimeError("回补异常")

        h.scheduler._backfill_fn = broken_backfill
        h.scheduler.run_once()  # 不抛即通过

    def test_stop_task_inseat_running_returns_stopping(self, tmp_path):
        """scheduler 层：running+在座执行器 stop → request_stop+stopping
        （API 层 fake_exec 面之外的本层直驱锚）。"""
        h = _SchedulerHarness(tmp_path)
        tid = h.create_task()
        paper.update_task(h.db, tid, status="running",
                          started_at="2030-01-01T00:00:00+00:00")
        fake_exec = MagicMock()
        h.scheduler._executors[tid] = fake_exec
        assert h.scheduler.stop_task(tid) == "stopping"
        fake_exec.controller.request_stop.assert_called_once()

    def test_stop_pending_zero_hit_terminal_fallback(self, tmp_path,
                                                     monkeypatch):
        """pending 分支 CAS 零命中（并发方已直迁终态）→ 按现态分流
        terminal（吞没防线的收口分支）。"""
        import core.paper_trading.runtime.scheduler as sched_mod

        h = _SchedulerHarness(tmp_path)
        tid = h.create_task()
        paper.update_task(
            h.db, tid, status="stopped",
            stopped_at="2030-01-01T00:00:00+00:00",
            stop_reason="user_stop")
        # 行已终态而 stop_task 读到 pending 旧快照：patch get_task 首读
        # 返 pending 行、CAS 更新零命中（patch update_task hit=0）
        real_get = paper.get_task
        state = {"first": True}

        def stale_get(db, task_id, **kw):
            row = dict(real_get(db, task_id, **kw))
            if state["first"]:
                state["first"] = False
                row["status"] = "pending"  # 旧快照
            return row

        monkeypatch.setattr(sched_mod.paper_repo, "get_task", stale_get)
        monkeypatch.setattr(
            sched_mod.paper_repo, "update_task", lambda *a, **k: 0)
        assert h.scheduler.stop_task(tid) == "terminal"

    def test_parse_iso_naive_string_localizes_utc(self):
        """无时区时刻串 → 按 UTC 本地化（心跳判定输入归一）。"""
        from core.paper_trading.runtime.scheduler import _parse_iso

        ts = _parse_iso("2030-01-01T00:00:00")
        assert ts.tz is not None and "UTC" in str(ts.tz)

    def test_startup_recover_success_path_counts_recovered(self, tmp_path):
        """startup_recover 成功路径：恢复器返回执行器 → recovered=1
        +线程登记（_start_recovered 真启动）。"""
        h = _SchedulerHarness(tmp_path)
        tid = h.create_task()
        paper.update_task(h.db, tid, status="running",
                          started_at="2030-01-01T00:00:00+00:00")
        started = threading.Event()
        fake_exec = MagicMock()
        fake_exec.run = started.set
        h.scheduler._recover_fn = lambda *a, **k: fake_exec
        result = h.scheduler.startup_recover()
        assert result == {"recovered": 1, "untrusted": 0}
        assert started.wait(timeout=5.0)
        assert h.scheduler._executors[tid] is fake_exec

    def test_default_recover_untrusted_row_returns_none(self, tmp_path):
        """缺省恢复函数（延迟 import 真恢复器）：不可信残态 → None
        （恢复器已写 error 保留数据）。"""
        h = _SchedulerHarness(tmp_path)
        tid = h.create_task()
        # running 但 balance 从未落（恢复校验判据 2 命中）
        paper.update_task(h.db, tid, status="running",
                          started_at="2030-01-01T00:00:00+00:00")
        row = paper.get_task(h.db, tid)
        assert h.scheduler._default_recover(h.db, row) is None
        assert paper.get_task(h.db, tid)["status"] == "error"

    def test_refresh_feeds_skips_dead_feed_and_swallows_error(
            self, tmp_path):
        """_refresh_feeds 两防御分支：get 返 None 的键跳过+refresh
        抛异常吞咽续轮。"""
        h = _SchedulerHarness(tmp_path)
        broken_feed = MagicMock()
        broken_feed.subscriber_count = 1
        broken_feed.refresh = MagicMock(
            side_effect=RuntimeError("feed 刷新异常"))
        fake_registry = MagicMock()
        fake_registry.active_keys = [("GHOST", "4h"), ("DEAD", "1h")]
        fake_registry.get = lambda *k: (
            None if k[0] == "DEAD" else broken_feed)
        h.scheduler.registry = fake_registry
        h.scheduler.run_once()  # 两分支均不炸即通过
        broken_feed.refresh.assert_called_once()

    def test_executor_concluded_without_client_callback_noop(self, tmp_path):
        """终态回调清理：未挂 on_concluded 客户端回调 → 清理后静默
        （if None 分支）。"""
        h = _SchedulerHarness(tmp_path)
        bare = type(h.scheduler)(h.db, registry=h.registry)
        tid = h.create_task()
        bare._executors[tid] = object()
        bare._executor_concluded(tid, "completed")  # 不炸即通过
        assert tid not in bare._executors

    def test_stop_task_unknown_not_found(self, tmp_path):
        """scheduler 层：不存在任务 stop → "not_found"。"""
        h = _SchedulerHarness(tmp_path)
        assert h.scheduler.stop_task("ghost-task") == "not_found"

    def test_stop_pending_but_registered_cooperative(self, tmp_path):
        """pending 行+执行器已登记（拾取与置 running 间的窗口）→
        协作停 request_stop 返回 stopping（95c2eac 吞没防线分支）。"""
        h = _SchedulerHarness(tmp_path)
        tid = h.create_task()  # 行保持 pending
        fake_exec = MagicMock()
        h.scheduler._executors[tid] = fake_exec
        assert h.scheduler.stop_task(tid) == "stopping"
        fake_exec.controller.request_stop.assert_called_once()


# ── 策略回流（build-strategy-lifecycle 5.2：paper→active 接入） ──


_T_FLOW = "2030-01-02T00:00:00+00:00"


class _ReturnFlowHarness:
    """回流测试装配（轻量：无 feed/无守护线程——直调终态回调与重放步；
    沿 _SchedulerHarness 同库形制，策略行在场是回流前提）。"""

    def __init__(self, tmp_path: Path, name: str = "flow"):
        self.db = tmp_path / f"{name}.db"
        init_db_ext(self.db)
        self.scheduler = PaperScheduler(self.db, data_dir=tmp_path)

    def seed_paper_strategy(self, strategy_id: str, *,
                            status: str = "paper") -> None:
        """造 paper 态策略（合法入场 active 后直 SQL 构造轨迁 paper）。"""
        from sqlalchemy import text

        from core.persistence.repository.strategy import save_strategy

        save_strategy(self.db, strategy_id=strategy_id, dna_json="{}",
                      symbol="BTCUSDT", timeframe="4h",
                      gene_signature=f"sig-{strategy_id}")
        if status != "active":
            from core.persistence.orm.engine import engine_for

            with engine_for(self.db).begin() as conn:
                conn.execute(text("UPDATE strategy SET status=:s"
                                  " WHERE strategy_id=:i"),
                             {"s": status, "i": strategy_id})

    def seed_task(self, task_id: str, strategy_id: str, *,
                  status: str = "completed") -> None:
        """造终态任务（迁移链走合法路径：completed 两跳/stopped·error
        自 pending 单跳）。"""
        paper.create_task(
            self.db, task_id=task_id, strategy_id=strategy_id,
            dna_snapshot_json="{}", symbol="BTCUSDT", timeframe="4h",
            research_status_snapshot="unverified")
        if status == "completed":
            paper.update_task(self.db, task_id, status="running",
                              started_at=_T_FLOW)
            paper.update_task(self.db, task_id, status="completed",
                              stopped_at=_T_FLOW)
        else:
            paper.update_task(self.db, task_id, status=status,
                              started_at=_T_FLOW, stopped_at=_T_FLOW)

    def strategy_status(self, strategy_id: str) -> str:
        from sqlalchemy import text

        from core.persistence.orm.engine import engine_for

        with engine_for(self.db).connect() as conn:
            return conn.execute(text("SELECT status FROM strategy"
                                     " WHERE strategy_id=:i"),
                                {"i": strategy_id}).scalar_one()

    def events(self, strategy_id: str) -> list:
        from sqlalchemy import text

        from core.persistence.orm.engine import engine_for

        with engine_for(self.db).connect() as conn:
            return conn.execute(text(
                "SELECT from_status, to_status, trigger FROM"
                " strategy_transition_event WHERE strategy_id=:i"
                " ORDER BY created_at"), {"i": strategy_id}).all()


class TestStrategyReturnFlow:
    """5.2 paper→active 接入：终态回调触回流+多任务守卫+回调失败两
    权威分离+回补重放自愈。"""

    @pytest.mark.parametrize("final_status", ["completed", "stopped", "error"])
    def test_each_terminal_status_triggers_return(
            self, tmp_path, final_status):
        """三终态各自触迁移（最后任务）：paper→active+事件
        (task_end)+evidence 含 task_id 与终态。"""
        h = _ReturnFlowHarness(tmp_path)
        h.seed_paper_strategy("st-flow")
        h.seed_task("pt_flow00000001", "st-flow", status=final_status)
        h.scheduler._executor_concluded("pt_flow00000001", final_status)
        assert h.strategy_status("st-flow") == "active"
        assert h.events("st-flow") == [("paper", "active", "task_end")]

    def test_not_last_task_does_not_migrate(self, tmp_path, caplog):
        """非最后任务不迁移：A running B 结束→not_migrated
        (active_tasks_remain)+策略停留 paper+info 日志（正当不触发）。"""
        import logging

        h = _ReturnFlowHarness(tmp_path)
        # 坑位补偿：init_db_ext→alembic env.py fileConfig 会重置 root
        # handlers 摘掉 pytest caplog 挂的捕获 handler——补挂回来才能
        # 断言日志（工程无先例，根因实测锁定于 alembic/env.py:31）
        logging.getLogger().addHandler(caplog.handler)
        h.seed_paper_strategy("st-flow")
        h.seed_task("pt_flow_a0000001", "st-flow", status="running")
        h.seed_task("pt_flow_b0000001", "st-flow", status="completed")
        with caplog.at_level(logging.INFO, logger="paper_runtime"):
            h.scheduler._executor_concluded("pt_flow_b0000001", "completed")
        assert h.strategy_status("st-flow") == "paper"
        assert h.events("st-flow") == []
        assert "st-flow" in caplog.text  # info 日志（仍有活跃任务）
        assert "暂不回流" in caplog.text

    def test_callback_error_continues_conclusion(self, tmp_path, caplog):
        """回调异常→结题继续+error 日志：live 态策略（存量遗留非法
        前态）任务终态→transit 409→结构化 error 日志（code 可辨）→
        客户端回调仍透传（两权威分离）。注：draft 前态经 task_end 回流
        是矩阵合法边（draft→active），运行时校验面=矩阵+凭证两层，
        trigger-边错配不在校验面——本用例取 live 构造真 409。"""
        import logging

        concluded: list = []
        h = _ReturnFlowHarness(tmp_path)
        logging.getLogger().addHandler(caplog.handler)  # 同 fileConfig 坑位
        h.scheduler._on_concluded_client = (
            lambda tid, st: concluded.append((tid, st)))
        h.seed_paper_strategy("st-flow", status="live")
        h.seed_task("pt_flow00000001", "st-flow", status="completed")
        with caplog.at_level(logging.ERROR, logger="paper_runtime"):
            h.scheduler._executor_concluded("pt_flow00000001", "completed")
        assert h.strategy_status("st-flow") == "live"
        assert "STRATEGY_ILLEGAL_TRANSITION" in caplog.text
        assert "pt_flow00000001" in caplog.text
        assert concluded == [("pt_flow00000001", "completed")], \
            "结题流程继续（客户端回调不被迁移失败绑架）"

    def test_backfill_replay_migrates_missed_strategy(self, tmp_path):
        """回补重试成功：回调未触达（构造终态行不经回调）→run_once
        重放步扫描命中→回流 active+事件。"""
        h = _ReturnFlowHarness(tmp_path)
        h.seed_paper_strategy("st-flow")
        h.seed_task("pt_flow00000001", "st-flow", status="completed")
        h.scheduler._backfill_fn = lambda db_path: None  # 资金费步空转
        h.scheduler.run_once()
        assert h.strategy_status("st-flow") == "active"
        assert h.events("st-flow") == [("paper", "active", "task_end")]

    def test_backfill_replay_idempotent_no_second_event(self, tmp_path):
        """已迁移幂等无第二事件：回调成功回流（1 事件）→重放步两轮
        扫描域零命中（status 已 active）→事件仍 1。"""
        h = _ReturnFlowHarness(tmp_path)
        h.seed_paper_strategy("st-flow")
        h.seed_task("pt_flow00000001", "st-flow", status="completed")
        h.scheduler._executor_concluded("pt_flow00000001", "completed")
        h.scheduler._backfill_fn = lambda db_path: None
        h.scheduler.run_once()
        h.scheduler.run_once()
        assert h.strategy_status("st-flow") == "active"
        assert h.events("st-flow") == [("paper", "active", "task_end")]

    def test_backfill_replay_skips_active_tasks_remain(self, tmp_path):
        """扫描守卫：paper 策略仍有活跃任务（回调失败的活跃侧）→重放
        步不迁移（守卫正当跳过），终态任务在场不误迁。"""
        h = _ReturnFlowHarness(tmp_path)
        h.seed_paper_strategy("st-flow")
        h.seed_task("pt_flow_a0000001", "st-flow", status="running")
        h.seed_task("pt_flow_b0000001", "st-flow", status="completed")
        h.scheduler._backfill_fn = lambda db_path: None
        h.scheduler.run_once()
        assert h.strategy_status("st-flow") == "paper"
        assert h.events("st-flow") == []

    def test_guard_shared_single_point_callback_and_scan(
            self, tmp_path, monkeypatch):
        """守卫与扫描共享判定单点：回调路径（transit 内建守卫）与扫描
        路径（预筛）均触达 credential.has_no_active_tasks 同一函数。"""
        from core.strategy.lifecycle import credential as lifecycle_credential

        calls: list = []
        original = lifecycle_credential.has_no_active_tasks

        def _spy(conn, strategy_id):
            calls.append(strategy_id)
            return original(conn, strategy_id)

        monkeypatch.setattr(
            lifecycle_credential, "has_no_active_tasks", _spy)

        # 扫描路径：重放步预筛经单点（预筛+transit 内建守卫两次触达
        # 同一函数——次数是实现细节，落点单点才是断言面）
        h1 = _ReturnFlowHarness(tmp_path, name="flow_scan")
        h1.seed_paper_strategy("st-scan")
        h1.seed_task("pt_scan00000001", "st-scan", status="completed")
        h1.scheduler._replay_paper_strategies()
        assert calls and set(calls) == {"st-scan"}, "扫描路径经守卫单点"

        # 回调路径：transit 内建守卫经单点
        calls.clear()
        h2 = _ReturnFlowHarness(tmp_path, name="flow_cb")
        h2.seed_paper_strategy("st-cb")
        h2.seed_task("pt_cb000000001", "st-cb", status="completed")
        h2.scheduler._executor_concluded("pt_cb000000001", "completed")
        assert calls and set(calls) == {"st-cb"}, "回调路径经守卫单点"
