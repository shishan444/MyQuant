"""事件驱动并发与帧注入性特征网（paper-trading spec「多任务并发与
数据共享」MODIFIED S2/S3——change-1 修复期特征测试，从实现行为记录）。

S2 同键批量唤醒并发消化：
- 广播扇出面：同键 100 订阅在一根闭合 bar 广播时同步扇出全达
  （spec Scenario 字面规模锚——_publish 锁内快照订阅列表逐一同步
  调用，无错峰机制面）；
- 执行器并发面：同键 12 任务（真线程 executor）经真实广播链并发
  消化，各任务权益曲线与成交明细和单独运行逐位一致（无相互等待
  的结果面证据）。

S3 执行器帧注入性保持：直喂 process_frames 全帧单次 vs 经真实广播链
（装载广播+逐根 on_closed_bar 增量）——成交明细/权益曲线/终态逐位
一致（「测试基建结构性前提」的对照锚：两条驱动通道到达八步序的
(df, from_idx) 序列等价）。

规模披露：scheduler MAX_RUNNING_TASKS 缺省 10（拾取面上限）——
「100 个仓位」的执行器级并发经直构 executor 线程承载（不经拾取面，
cap 属拾取职责不在本 Scenario 范围）；100 规模由广播扇出面（100
订阅者）字面承载。秒级时延为性能面承诺，本网只记录墙钟不断言
（功能测试职责边界——TESTING_V3）。
"""
from __future__ import annotations

import threading
import time
from pathlib import Path

import pandas as pd
import pytest

pytestmark = [pytest.mark.unit]

from core.data.fapi.stream import ClosedBarEvent
from core.data.storage import save_parquet
from core.features.indicators import compute_all_indicators
from core.paper_trading.runtime.executor import PaperTaskExecutor
from core.paper_trading.runtime.feed import FeedRegistry, FeedSubscription
from core.persistence.db_ext import init_db_ext
from core.persistence.repository import paper
from tests.helpers.data_factory import make_dna, make_ohlcv

# 合成数据基线（沿 test_controlled_equivalence 同源：seed=11 的 140 根
# 4h 数据在 warmup=20 起的对照段有 4 笔开平——信号分布已证）
_N_BARS = 140
_SPLIT = 20
_SEED = 11
_INIT_CASH = 10_000.0
_N_FANOUT = 100        # S2 Scenario 字面规模（广播扇出面）
_N_CONCURRENT = 12     # 执行器并发任务数（>MAX_RUNNING_TASKS 缺省 10）


def _funding_frame(times=()) -> pd.DataFrame:
    """空 funding 索引（等价前提：两侧结算点全挂起不扣款）。"""
    idx = pd.DatetimeIndex(pd.to_datetime(list(times), utc=True),
                           name="timestamp")
    return pd.DataFrame({"funding_rate": [0.0001] * len(idx)}, index=idx)


def _b0_dna():
    """B0 基线 DNA（沿 test_controlled_equivalence——对照段 4 笔开平）。"""
    return make_dna(entry_value=45, exit_value=55, stop_loss=0.19,
                    take_profit=0.20, position_size=0.5, leverage=1)


def _event_from_row(df: pd.DataFrame, i: int) -> ClosedBarEvent:
    """df 第 i 行 → 闭合事件（广播链驱动形制）。"""
    row = df.iloc[i]
    return ClosedBarEvent(
        symbol="BTCUSDT", timeframe="4h", open_time=df.index[i],
        open=float(row["open"]), high=float(row["high"]),
        low=float(row["low"]), close=float(row["close"]),
        volume=float(row["volume"]))


def _wait(predicate, timeout: float = 15.0, interval: float = 0.02) -> bool:
    """轮询等待（deadline 内谓词为真返回 True——调用方 assert）。"""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return False


class _ChainHarness:
    """广播链/直喂双通道装配：前 _SPLIT 根落 parquet（装载面）、
    其余经 on_closed_bar 增量（广播面）——start_after 切感知段。"""

    def __init__(self, root: Path, *, dna=None, n_tasks: int = 1):
        self.dna = dna or _b0_dna()
        self.data_dir = root / "market"
        self.data_dir.mkdir(parents=True)
        self.df = make_ohlcv(n=_N_BARS, freq="4h", seed=_SEED)
        save_parquet(self.df.iloc[:_SPLIT],
                     self.data_dir / "BTCUSDT_4h.parquet")
        self.enhanced = compute_all_indicators(self.df)
        self.db = root / "paper.db"
        init_db_ext(self.db)
        self.registry = FeedRegistry(
            self.data_dir,
            rest_fetcher=lambda *a, **k: pd.DataFrame(),
            funding_refresher=lambda **k: _funding_frame())
        self.start_after = self.df.index[_SPLIT].isoformat()
        self.task_ids: list[str] = []
        for _ in range(n_tasks):
            self.task_ids.append(paper.create_task(
                self.db, strategy_id="strat-edc",
                dna_snapshot_json=self.dna.to_json(),
                symbol="BTCUSDT", timeframe="4h",
                research_status_snapshot="unverified",
                initial_cash=_INIT_CASH, leverage=1,
                fee=0.0, slippage=0.0))

    def make_executor(self, task_id: str) -> PaperTaskExecutor:
        row = paper.get_task(self.db, task_id)
        return PaperTaskExecutor(
            self.db, row, registry=self.registry,
            push=lambda tid, p: None,
            on_concluded=lambda tid, st: None,
            start_after=self.start_after)

    @property
    def feed(self):
        return self.registry.get("BTCUSDT", "4h")

    def main_subs_attached(self, executors) -> bool:
        """主订阅挂入且装载完成判定（投帧安全锚）。

        三个窗口都要关闭（任一未关都会丢 bar）：
        ①subscriber_count 不可用——prepare 期临时订阅也计数；
        ②executor._main_sub is not None 不可用——赋值在 registry.
          acquire 之前，赋值到挂入之间投帧丢失；
        ③仅挂载不可用——acquire 先注册实例后锁外 ensure_loaded，
          装载未完成时投帧触发冷启动建序列，随后的装载覆盖使该
          bar 从内存序列消失（实现边界行为——生产由对账兜底）。
        可靠锚=主订阅对象在 feed 订阅列表内且 latest_df 非 None。
        """
        if any(ex._main_sub is None for ex in executors):  # noqa: SLF001
            return False
        feed = self.feed
        if feed is None:
            return False  # prepare 临时订阅释放后瞬时回收——中间态
        with feed._lock:  # noqa: SLF001——观察订阅列表
            subs = list(feed._subscriptions)  # noqa: SLF001
        return (all(ex._main_sub in subs for ex in executors)  # noqa: SLF001
                and feed.latest_df is not None)

    # ── 观察面 ──────────────────────────────────────────────

    def equity(self, task_id: str) -> list[tuple[str, float]]:
        return [(e["bar_time"], float(e["equity"])) for e in
                paper.list_equity_snapshots(self.db, task_id, limit=100_000)]

    def fills(self, task_id: str) -> list[tuple]:
        return [(f["kind"], f["bar_time"], float(f["price"]),
                 float(f["quantity"]), f["reason"]) for f in
                paper.list_fills(self.db, task_id, limit=100_000)]


class TestBroadcastFanout:
    """S2 扇出面：单根闭合 bar 广播同步到达同键全部订阅者。"""

    def test_single_bar_reaches_all_hundred_subscribers(self, tmp_path):
        """100 订阅（spec 字面规模）单根闭合 bar 广播全达：帧与
        from_idx 逐订阅一致（同步扇出——广播调用返回即全部到达）。"""
        h = _ChainHarness(tmp_path)
        seen: list[dict] = []

        def _counter(i):
            def on_frames(df, idx):
                seen.append({"i": i, "len": len(df), "from_idx": idx})
            return on_frames

        for i in range(_N_FANOUT):
            h.registry.acquire("BTCUSDT", "4h", FeedSubscription(
                on_frames=_counter(i)))
        seen.clear()  # 首订阅装载广播（from_idx=0）——聚焦增量帧

        t0 = time.monotonic()
        h.feed.on_closed_bar(_event_from_row(h.df, _SPLIT))
        wall = time.monotonic() - t0  # 墙钟记录（性能面不断言）

        assert len(seen) == _N_FANOUT  # 全部 100 订阅各达一次
        assert {s["from_idx"] for s in seen} == {_SPLIT}  # 同增量起点
        assert {s["len"] for s in seen} == {_SPLIT + 1}   # 同帧（共享单次计算）
        assert wall < 60.0  # 防挂死宽松上界（非性能断言）


class TestSameKeyConcurrentDigest:
    """S2 执行器面：同键多任务经广播链并发消化，结果与单独运行一致。"""

    @staticmethod
    def _run_chain(h: _ChainHarness, n_bars: int):
        """广播链驱动 n_bars 根增量：起线程→等订阅→投帧→等消化→协作停。

        返回 (executors, threads, 消化根数)。
        """
        executors = [h.make_executor(tid) for tid in h.task_ids]
        threads = [threading.Thread(target=ex.run, name=f"edc-{k}",
                                    daemon=True)
                   for k, ex in enumerate(executors)]
        for t in threads:
            t.start()
        # 主订阅挂载锚：主订阅对象真正挂入 feed 订阅列表（见
        # main_subs_attached 的窗口说明——提前投帧会丢失）
        assert _wait(lambda: h.main_subs_attached(executors)), \
            "全部任务主订阅挂载超时"
        for i in range(_SPLIT, _SPLIT + n_bars):
            h.feed.on_closed_bar(_event_from_row(h.df, i))
        for tid in h.task_ids:
            assert _wait(lambda t=tid: len(h.equity(t)) >= n_bars), \
                f"任务 {tid} 消化超时"
        for ex in executors:
            ex.controller.request_stop()
        for t in threads:
            t.join(timeout=15.0)
        return executors, threads, n_bars

    def test_concurrent_digest_matches_solo_run(self, tmp_path):
        """12 任务同键并发消化 40 根：每任务权益曲线与成交明细和单独
        运行逐位一致（同时唤醒并发消化、无相互等待的结果面证据）。"""
        n_bars = 40
        solo = _ChainHarness(tmp_path / "solo", n_tasks=1)
        self._run_chain(solo, n_bars)
        solo_task = solo.task_ids[0]
        solo_equity = solo.equity(solo_task)
        solo_fills = solo.fills(solo_task)
        assert len(solo_equity) == n_bars  # solo 消化全量（基线前提）
        assert solo_fills, "段内须有成交（一致性断言区分度前提）"

        conc = _ChainHarness(tmp_path / "conc", n_tasks=_N_CONCURRENT)
        t0 = time.monotonic()
        self._run_chain(conc, n_bars)
        wall = time.monotonic() - t0  # 墙钟记录（秒级承诺不断言）

        for tid in conc.task_ids:
            task = paper.get_task(conc.db, tid)
            assert task["status"] == "stopped"
            equity = conc.equity(tid)
            # 全部任务消化全部 bar（无漏唤醒）且无重复（bar_time 唯一）
            assert len(equity) == n_bars
            assert len({bar for bar, _ in equity}) == n_bars
            # 与单独运行逐位一致（bar_time 与权益数值）
            assert [bar for bar, _ in equity] == \
                [bar for bar, _ in solo_equity]
            for (_, e_conc), (_, e_solo) in zip(equity, solo_equity):
                assert e_conc == pytest.approx(e_solo, rel=1e-9, abs=1e-6)
            # 成交明细一致（kind/bar_time/price/quantity/reason）
            assert conc.fills(tid) == solo_fills
        assert wall < 60.0  # 防挂死宽松上界（非性能断言）


class TestFrameInjectionEquivalence:
    """S3 帧注入性：直喂全帧 vs 经真实广播链——逐位一致。"""

    def test_direct_feed_equals_broadcast_chain(self, tmp_path):
        """同数据同参数：A=process_frames 全帧单次直喂；B=run() 真线程
        经装载广播+120 根逐帧增量——成交明细/权益曲线/终态逐位一致。"""
        # A 侧：直喂通道（测试注入通道——模拟拾取后单帧全量）
        ha = _ChainHarness(tmp_path / "direct")
        tid_a = ha.task_ids[0]
        ex_a = ha.make_executor(tid_a)
        assert ex_a.prepare() is True
        paper.update_task(ha.db, tid_a, status="running",
                          started_at="2026-10-28T00:00:00+00:00")
        ex_a.process_frames(ha.enhanced, 0)
        paper.update_task(ha.db, tid_a, status="stopped",
                          stopped_at="2026-10-28T01:00:00+00:00",
                          stop_reason="user_stop")

        # B 侧：真实广播链（装载广播 20 根帧+逐根 on_closed_bar 120 帧）
        hb = _ChainHarness(tmp_path / "chain")
        tid_b = hb.task_ids[0]
        ex_b = hb.make_executor(tid_b)
        t_b = threading.Thread(target=ex_b.run, daemon=True)
        t_b.start()
        assert _wait(lambda: hb.main_subs_attached([ex_b])), \
            "主订阅挂载超时"  # 挂载锚（同 _run_chain）
        for i in range(_SPLIT, _N_BARS):
            hb.feed.on_closed_bar(_event_from_row(hb.df, i))
        assert _wait(lambda: len(hb.equity(tid_b)) >= _N_BARS - _SPLIT), \
            "广播链消化超时"
        ex_b.controller.request_stop()
        t_b.join(timeout=15.0)

        task_b = paper.get_task(hb.db, tid_b)
        assert task_b["status"] == "stopped"

        # 逐位一致：成交明细（含价格/数量）
        assert hb.fills(tid_b) == ha.fills(tid_a)
        # 逐位一致：权益曲线（bar 集对齐+逐点数值）
        eq_a = ha.equity(tid_a)
        eq_b = hb.equity(tid_b)
        assert len(eq_a) == len(eq_b) == _N_BARS - _SPLIT
        assert [bar for bar, _ in eq_b] == [bar for bar, _ in eq_a]
        for (_, v_b), (_, v_a) in zip(eq_b, eq_a):
            assert v_b == pytest.approx(v_a, rel=1e-9, abs=1e-6)
        # 段内有成交（等价断言区分度前提——对照段 4 笔开平已证）
        assert ha.fills(tid_a), "对照段须有成交"
