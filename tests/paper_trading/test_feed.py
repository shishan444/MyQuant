"""品种数据作业测试（change-1 双通道：on_closed_bar+reconcile）。

覆盖：per-(symbol,timeframe) 单例共享（同键两订阅一份对账拉取/不同键
独立/订阅归零回收）+装载广播（acquire 即从 parquet 装载，from_idx=0）
+WS 闭合事件追加（1 根/多根/重复先到为准/冷启动建序列）+reconcile
缺口三态（无缺口/恰一根/多根）+重叠段去重（WS 与 REST 先到为准）+
消费端游标判重兜底+ATR 非默认周期并集补充+funding 8h 网格装载与
节流刷新+结算点区间枚举（(open,close] 左开右闭语义）。

广播契约不变量：(closed_df, from_idx) 签名语义沿旧形——from_idx 是
提示非承诺，消费端按自身游标判重兜底（TestConsumerCursorOverlap）。
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

pytestmark = [pytest.mark.unit]

from core.data.fapi.stream import ClosedBarEvent
from core.data.storage import load_parquet, save_parquet
from core.data.timeframes import TF_SECONDS
from core.paper_trading.runtime.feed import (
    FeedRegistry,
    FeedSubscription,
    settlement_points_between,
    snap_to_funding_grid,
)
from tests.helpers.data_factory import make_ohlcv


class FakeClock:
    """可推进时钟（缺口判定与 funding 节流的注入通道）。"""

    def __init__(self, start):
        self.now = pd.Timestamp(start, tz="UTC") if isinstance(
            start, str) else start

    def __call__(self) -> pd.Timestamp:
        return self.now

    def advance(self, **kwargs) -> None:
        self.now += pd.Timedelta(**kwargs)


def _funding_frame(times: list[str], rate: float = 0.0001) -> pd.DataFrame:
    """合成 funding parquet 形制（列 funding_rate+DatetimeIndex UTC）。"""
    idx = pd.DatetimeIndex(pd.to_datetime(times, utc=True), name="timestamp")
    return pd.DataFrame({"funding_rate": [rate] * len(idx)}, index=idx)


def _to_utc(t) -> pd.Timestamp:
    """时刻归一 UTC（naive 本地化、aware 转换）。"""
    ts = pd.Timestamp(t)
    return ts.tz_localize("UTC") if ts.tz is None else ts.tz_convert("UTC")


def _ohlcv_at(times, price: float = 100.0) -> pd.DataFrame:
    """按时刻列表构造 OHLCV 帧（五列；闭合段构造便捷函数）。"""
    if not isinstance(times, pd.DatetimeIndex):
        times = pd.DatetimeIndex(
            [_to_utc(t) for t in times], name="timestamp")
    return pd.DataFrame(
        {"open": [price] * len(times), "high": [price + 1] * len(times),
         "low": [price - 1] * len(times), "close": [price + 0.5] * len(times),
         "volume": [10.0] * len(times)},
        index=times)


def _event_at(open_time, *, symbol="BTCUSDT", timeframe="4h",
              price=100.0) -> ClosedBarEvent:
    """闭合事件构造（open_time 锚）。"""
    return ClosedBarEvent(
        symbol=symbol, timeframe=timeframe,
        open_time=pd.Timestamp(open_time, tz="UTC")
        if isinstance(open_time, str) else open_time,
        open=price, high=price + 1, low=price - 1,
        close=price + 0.5, volume=10.0)


class FeedHarness:
    """双通道测试装配（预写 parquet+脚本化 REST+可变时钟）。"""

    def __init__(self, tmp_path, *, frame=None, rest_frames=None,
                 funding_frames=None, clock=None, write_parquet=True,
                 timeframe="4h"):
        self.data_dir = tmp_path / "market"
        self.timeframe = timeframe
        self.frame = frame if frame is not None else make_ohlcv(
            n=100, freq=timeframe)
        if write_parquet:
            save_parquet(
                self.frame,
                self.data_dir / f"BTCUSDT_{timeframe}.parquet")
        self.last = self.frame.index[-1]
        self.tf = pd.Timedelta(seconds=TF_SECONDS[timeframe])
        self.clock = clock or FakeClock(self.last + self.tf)
        self._rest_frames = list(rest_frames or [])
        self.rest_calls: list[dict] = []
        funding = funding_frames or [_funding_frame(["2024-01-01 00:00:00"])]
        self._funding_frames = funding
        self.funding_calls: list[str] = []
        self.registry = FeedRegistry(
            self.data_dir,
            rest_fetcher=self._rest,
            funding_refresher=self._funding_refresher,
            now_fn=self.clock)

    def _rest(self, symbol, timeframe, start_ts, end_ts) -> pd.DataFrame:
        self.rest_calls.append(
            {"symbol": symbol, "timeframe": timeframe,
             "start": start_ts, "end": end_ts})
        if not self._rest_frames:
            return pd.DataFrame()
        if len(self._rest_frames) > 1:
            return self._rest_frames.pop(0)
        return self._rest_frames[0]

    def _funding_refresher(self, *, symbol, data_dir) -> pd.DataFrame:
        self.funding_calls.append(symbol)
        if len(self._funding_frames) > 1:
            return self._funding_frames.pop(0)
        return self._funding_frames[0] if self._funding_frames else \
            pd.DataFrame()

    def acquire(self, sub=None) -> tuple:
        sub = sub or FeedSubscription(on_frames=lambda df, idx: None)
        feed = self.registry.acquire("BTCUSDT", self.timeframe, sub)
        return feed, sub

    def event_after(self, steps: int = 1, **kwargs) -> ClosedBarEvent:
        """末根后第 steps 根的闭合事件。"""
        kwargs.setdefault("timeframe", self.timeframe)
        return _event_at(self.last + self.tf * steps, **kwargs)

    def parquet(self) -> pd.DataFrame:
        return load_parquet(
            self.data_dir / f"BTCUSDT_{self.timeframe}.parquet")


class TestSharedFeed:
    """同键共享/异键独立/引用计数回收/装载广播。"""

    def test_same_key_two_subscribers_share_one_reconcile(self, tmp_path):
        """同键两任务共享一份对账拉取：装载零 REST、每轮 reconcile
        恰一次 REST、增量广播两订阅各达一次。"""
        h = FeedHarness(tmp_path)
        received: list[tuple[str, int]] = []
        sub_a = FeedSubscription(
            on_frames=lambda df, idx: received.append(("a", idx)))
        sub_b = FeedSubscription(
            on_frames=lambda df, idx: received.append(("b", idx)))
        feed_a = h.registry.acquire("BTCUSDT", "4h", sub_a)
        feed_b = h.registry.acquire("BTCUSDT", "4h", sub_b)
        assert feed_a is feed_b  # 同键同实例（共享单位）
        # 装载广播达首订阅（from_idx=0 全量初始帧）
        assert ("a", 0) in received
        # 装载读 parquet 零 REST（REST 归对账/重建路径——计数归属核对）
        assert h.rest_calls == []
        # 缺口成立（末根+10h > 2×4h）
        h.clock.advance(hours=10)
        # 首次对账：缺口判定成立→REST 发起拉到空响应（对账无所得的
        # 中间态——不广播不落盘）
        broadcast_before = len(received)
        feed_a.reconcile()
        assert len(h.rest_calls) == 1  # 首次对账恰一次 REST
        assert len(received) == broadcast_before  # 空响应不广播
        # 第二次对账：REST 段有帧→合并广播，两订阅各恰达一次
        h._rest_frames = [_ohlcv_at([h.last + h.tf])]
        feed_a.reconcile()
        assert len(h.rest_calls) == 2  # 两轮对账两次 REST（精确计数）
        increments = [r for r in received if r[1] > 0]
        assert len(increments) == 2  # 两订阅各恰一次（无重复广播）
        assert ("a", 100) in increments and ("b", 100) in increments

    def test_different_keys_independent_feeds(self, tmp_path):
        """不同 (symbol,timeframe) 各自独立作业（两实例两装载）。"""
        h = FeedHarness(tmp_path)
        save_parquet(make_ohlcv(n=50, freq="1h"),
                     h.data_dir / "ETHUSDT_1h.parquet")
        sub_a = FeedSubscription(on_frames=lambda df, idx: None)
        sub_b = FeedSubscription(on_frames=lambda df, idx: None)
        feed_a = h.registry.acquire("BTCUSDT", "4h", sub_a)
        feed_b = h.registry.acquire("ETHUSDT", "1h", sub_b)
        assert feed_a is not feed_b
        assert feed_a.latest_df is not None and len(feed_a.latest_df) == 100
        assert feed_b.latest_df is not None and len(feed_b.latest_df) == 50

    def test_acquire_loads_parquet_and_broadcasts_initial(self, tmp_path):
        """装载广播：acquire 从 parquet 装载历史+指标+ATR 并集，
        初始帧广播 from_idx=0。"""
        h = FeedHarness(tmp_path)
        got: list[tuple[pd.DataFrame, int]] = []
        sub = FeedSubscription(
            on_frames=lambda df, idx: got.append((df, idx)), atr_periods={20})
        feed = h.registry.acquire("BTCUSDT", "4h", sub)
        assert feed.latest_df is not None and len(feed.latest_df) == 100
        assert "atr_14" in feed.latest_df.columns  # 默认指标集
        assert "atr_20" in feed.latest_df.columns  # 订阅并集补充
        assert len(got) == 1 and got[0][1] == 0  # 初始帧 from_idx=0

    def test_acquire_without_parquet_no_product(self, tmp_path):
        """无文件键：acquire 装载无产物（latest_df None——等建库线程）。"""
        h = FeedHarness(tmp_path, write_parquet=False)
        feed, _ = h.acquire()
        assert feed.latest_df is None

    def test_unref_to_zero_recycles_feed(self, tmp_path):
        """订阅归零→feed 摘除回收（再 acquire 得新实例）。"""
        h = FeedHarness(tmp_path)
        sub_a = FeedSubscription(on_frames=lambda df, idx: None)
        sub_b = FeedSubscription(on_frames=lambda df, idx: None)
        feed = h.registry.acquire("BTCUSDT", "4h", sub_a)
        h.registry.acquire("BTCUSDT", "4h", sub_b)
        assert feed.subscriber_count == 2

        h.registry.release("BTCUSDT", "4h", sub_a)
        assert feed.subscriber_count == 1
        assert h.registry.get("BTCUSDT", "4h") is feed  # 尚有订阅不回收

        h.registry.release("BTCUSDT", "4h", sub_b)
        assert h.registry.get("BTCUSDT", "4h") is None  # 归零回收
        assert h.registry.active_keys == []

        sub_new = FeedSubscription(on_frames=lambda df, idx: None)
        feed_new = h.registry.acquire("BTCUSDT", "4h", sub_new)
        assert feed_new is not feed  # 回收后再建是新实例


class TestOnClosedBar:
    """WS 路径：闭合事件追加（直调注入——独立于接入层测试）。"""

    def test_single_event_appends_and_broadcasts(self, tmp_path):
        """1 根追加：广播 from_idx=追加前长度、末行=事件行、latest_df
        即时可读（内存=决策权威）。"""
        h = FeedHarness(tmp_path)
        got: list[tuple[pd.DataFrame, int]] = []
        sub = FeedSubscription(on_frames=lambda df, idx: got.append((df, idx)))
        feed, _ = h.acquire(sub)
        got.clear()  # 装载广播已收——聚焦增量

        feed.on_closed_bar(h.event_after(1))
        assert len(got) == 1
        df, from_idx = got[0]
        assert from_idx == 100  # 追加前长度
        assert len(df) == 101
        assert df.index[-1] == h.last + h.tf
        assert feed.latest_df.index[-1] == h.last + h.tf  # 内存权威即时

    def test_multi_events_incremental(self, tmp_path):
        """多根连续：两次广播 from_idx 递增（100→101）。"""
        h = FeedHarness(tmp_path)
        got: list[int] = []
        feed, _ = h.acquire(
            FeedSubscription(on_frames=lambda df, idx: got.append(idx)))
        got.clear()
        feed.on_closed_bar(h.event_after(1))
        feed.on_closed_bar(h.event_after(2))
        assert got == [100, 101]
        assert len(feed.latest_df) == 102

    def test_duplicate_event_first_wins(self, tmp_path):
        """重复事件先到为准：第二次投递不广播、行数不增、parquet 不重行。"""
        h = FeedHarness(tmp_path)
        got: list[int] = []
        feed, _ = h.acquire(
            FeedSubscription(on_frames=lambda df, idx: got.append(idx)))
        got.clear()
        event = h.event_after(1)
        feed.on_closed_bar(event)
        feed.on_closed_bar(event)  # 重复投递（幂等消费锚）
        assert got == [100]
        assert len(feed.latest_df) == 101
        persisted = load_parquet(h.data_dir / "BTCUSDT_4h.parquet")
        assert len(persisted) == 101  # 去重不重行
        assert persisted.index[-1] == h.last + h.tf  # 已落新行

    def test_cold_start_without_parquet(self, tmp_path):
        """冷启动：无 parquet 键首事件建序列（from_idx=0）。"""
        h = FeedHarness(tmp_path, write_parquet=False)
        got: list[int] = []
        feed, _ = h.acquire(
            FeedSubscription(on_frames=lambda df, idx: got.append(idx)))
        feed.on_closed_bar(_event_at("2026-10-28T00:00:00Z"))
        assert got == [0]
        assert len(feed.latest_df) == 1

    def test_out_of_order_event_sorted_merge_no_crash(self, tmp_path):
        """R4 乱序防御面：open_time 早于末根的不重复事件——sort 合并
        不炸、序列单调、无重复行（组合流按序投递下的异常形态纵深；
        spec「乱序投递作为防御性测试面保留，不作 SDK 行为声称」——
        仅记录防御行为不声称语义）。

        实现形制披露：晚到的早时刻行经 sort 并入序列中部，from_idx=
        追加前长度切片使该行对增量消费与落盘不可见（new_rows 只取
        iloc[from_idx:] 末段）——内存序列含该行、下游增量面不见。
        """
        h = FeedHarness(tmp_path)
        feed, _ = h.acquire(FeedSubscription(
            on_frames=lambda df, idx: None))
        feed.on_closed_bar(h.event_after(2))   # 先到末根+2
        feed.on_closed_bar(h.event_after(1))   # 晚到末根+1（乱序）
        df = feed.latest_df
        assert df.index.is_monotonic_increasing  # 序列单调
        assert len(df) == 102                    # 两行都在内存
        assert not df.index.duplicated().any()   # 无重复行

    def test_row_lands_in_parquet(self, tmp_path):
        """事件行落盘对齐（merge 语义——既有行不改写）。"""
        h = FeedHarness(tmp_path)
        feed, _ = h.acquire(FeedSubscription(
            on_frames=lambda df, idx: None))
        before = load_parquet(h.data_dir / "BTCUSDT_4h.parquet")
        feed.on_closed_bar(h.event_after(1))
        after = load_parquet(h.data_dir / "BTCUSDT_4h.parquet")
        assert len(after) == len(before) + 1
        pd.testing.assert_frame_equal(
            before, after.iloc[:len(before)], check_freq=False)  # 既有行不改写


class TestReconcile:
    """对账路径：缺口三态+重叠段去重+异常传播。"""

    def test_no_gap_no_fetch_no_broadcast(self, tmp_path):
        """无缺口（末根+2×周期>now）：不调 REST、不广播、不落盘。"""
        h = FeedHarness(tmp_path)  # clock=last+1tf（无缺口：需 ≥2tf）
        got: list[int] = []
        feed, _ = h.acquire(
            FeedSubscription(on_frames=lambda df, idx: got.append(idx)))
        got.clear()
        feed.reconcile()
        assert h.rest_calls == []  # 不拉
        assert got == []  # 不广播
        assert len(load_parquet(h.data_dir / "BTCUSDT_4h.parquet")) == 100

    def test_gap_exactly_one_bar(self, tmp_path):
        """恰一根缺口：末根+2×周期时刻拉 [last+tf, now] 补一根。"""
        h = FeedHarness(tmp_path)
        new_bar = h.last + h.tf
        h._rest_frames = [_ohlcv_at([new_bar])]
        got: list[tuple[pd.DataFrame, int]] = []
        feed, _ = h.acquire(
            FeedSubscription(on_frames=lambda df, idx: got.append((df, idx))))
        got.clear()
        h.clock.now = h.last + 2 * h.tf  # 恰闭合边界
        feed.reconcile()
        assert len(h.rest_calls) == 1
        call = h.rest_calls[0]
        assert call["start"] == new_bar  # 缺口起点=末根+周期
        assert len(got) == 1 and got[0][1] == 100
        assert len(got[0][0]) == 101
        assert len(load_parquet(h.data_dir / "BTCUSDT_4h.parquet")) == 101

    def test_gap_multiple_bars(self, tmp_path):
        """多根缺口：补拉段一次合并广播（from_idx=合并前长度）。"""
        h = FeedHarness(tmp_path)
        segment = _ohlcv_at(
            [h.last + h.tf * i for i in (1, 2, 3)])
        h._rest_frames = [segment]
        got: list[tuple[pd.DataFrame, int]] = []
        feed, _ = h.acquire(
            FeedSubscription(on_frames=lambda df, idx: got.append((df, idx))))
        got.clear()
        h.clock.now = h.last + 6 * h.tf  # 三根已闭合
        feed.reconcile()
        assert len(got) == 1 and got[0][1] == 100 and len(got[0][0]) == 103

    def test_overlapping_segment_first_wins_no_duplicate(self, tmp_path):
        """重叠段先到为准：WS 已到的行 REST 段重含——合并不重行、
        广播 from_idx 不回退（消费端无重复视图）。"""
        h = FeedHarness(tmp_path)
        got: list[tuple[pd.DataFrame, int]] = []
        feed, _ = h.acquire(
            FeedSubscription(on_frames=lambda df, idx: got.append((df, idx))))
        # WS 先到两根
        feed.on_closed_bar(h.event_after(1))
        feed.on_closed_bar(h.event_after(2))
        ws_len = len(feed.latest_df)  # 102
        # REST 补拉段重含第 2 根+新第 3 根（重叠）
        h._rest_frames = [_ohlcv_at([h.last + h.tf * 2, h.last + h.tf * 3])]
        h.clock.now = h.last + 5 * h.tf
        got.clear()
        feed.reconcile()
        assert len(h.rest_calls) == 1
        assert len(got) == 1
        df, from_idx = got[0]
        assert from_idx == ws_len  # 不回退（重叠行不重复广播）
        assert len(df) == ws_len + 1  # 只新增第 3 根
        assert not df.index.duplicated().any()  # 无重复行

    def test_rest_empty_frame_no_broadcast(self, tmp_path):
        """REST 空帧（端点暂无新数据）：不广播。"""
        h = FeedHarness(tmp_path)
        got: list[int] = []
        feed, _ = h.acquire(
            FeedSubscription(on_frames=lambda df, idx: got.append(idx)))
        got.clear()
        h.clock.now = h.last + 5 * h.tf
        feed.reconcile()  # rest 缺省返回空帧
        assert h.rest_calls and got == []

    def test_rest_exception_propagates(self, tmp_path):
        """REST 异常上抛（调度器捕获记日志续轮——本层不吞）。"""
        h = FeedHarness(tmp_path)

        def boom(*args, **kwargs):
            raise ConnectionError("scripted: 断网")

        h.registry = FeedRegistry(
            h.data_dir, rest_fetcher=boom,
            funding_refresher=h._funding_refresher, now_fn=h.clock)
        feed, _ = h.acquire()
        h.clock.now = h.last + 5 * h.tf
        with pytest.raises(ConnectionError):
            feed.reconcile()

    def test_no_baseline_skips(self, tmp_path):
        """无基线键（无内存无文件）跳过对账——建库线程职责。"""
        h = FeedHarness(tmp_path, write_parquet=False)
        feed, _ = h.acquire()
        feed.reconcile()
        assert h.rest_calls == []


class TestConsumerCursorOverlap:
    """消费端游标判重兜底：from_idx 提示非承诺语义。"""

    def test_cursor_consumer_never_duplicates(self, tmp_path):
        """模拟消费端持游标：重叠广播段（from_idx<cursor）不重复消化
        ——总消化行数=最终行数（WS+REST 混合驱动）。"""
        h = FeedHarness(tmp_path)
        digested: list[pd.Timestamp] = []

        def consumer(df: pd.DataFrame, from_idx: int) -> None:
            nonlocal cursor
            for i in range(max(cursor, from_idx), len(df)):
                digested.append(df.index[i])
            cursor = len(df)

        cursor = 0
        feed, _ = h.acquire(
            FeedSubscription(on_frames=consumer))
        digested.clear()  # 装载帧全量消化合法——聚焦后续增量段
        feed.on_closed_bar(h.event_after(1))
        feed.on_closed_bar(h.event_after(2))
        # 重叠补拉段（含已消化行）——消费端按游标判重不重复
        h._rest_frames = [_ohlcv_at(
            [h.last + h.tf * 2, h.last + h.tf * 3])]
        h.clock.now = h.last + 5 * h.tf
        feed.reconcile()
        assert len(digested) == 3  # 三根新 bar 恰好各消化一次
        assert len(set(digested)) == 3  # 无重复
        assert len(feed.latest_df) == 103


class TestTieredPersistence:
    """分级落盘三不变式+停机清缓冲+崩溃重建（任务 4）。"""

    def test_forming_rows_never_persisted(self, tmp_path):
        """①forming 不落库：REST 返回段含未闭合行（防御纵深）——
        合并前滤除，内存与 parquet 均不含。"""
        h = FeedHarness(tmp_path)
        closed_row = h.last + h.tf          # 已闭合（index+4h<=now）
        forming_row = h.last + 2 * h.tf     # 未闭合（index+4h>now）
        h._rest_frames = [_ohlcv_at([closed_row, forming_row])]
        got: list[int] = []
        feed, _ = h.acquire(
            FeedSubscription(on_frames=lambda df, idx: got.append(idx)))
        got.clear()
        h.clock.now = h.last + 2 * h.tf    # 恰闭合边界
        feed.reconcile()
        assert len(feed.latest_df) == 101  # 只进闭合行
        persisted = h.parquet()
        assert len(persisted) == 101
        assert persisted.index[-1] == closed_row  # forming 行不在盘

    def test_written_rows_never_rewritten_low_freq(self, tmp_path):
        """②已落行不改写（低频 4h 闭合即写）：两次落盘后既有段逐值比对。"""
        h = FeedHarness(tmp_path)
        feed, _ = h.acquire()
        feed.on_closed_bar(h.event_after(1))
        before = h.parquet()
        feed.on_closed_bar(h.event_after(2))
        after = h.parquet()
        assert len(after) == len(before) + 1
        pd.testing.assert_frame_equal(
            before, after.iloc[:len(before)], check_freq=False)

    def test_written_rows_never_rewritten_after_flush(self, tmp_path):
        """②已落行不改写（高频缓冲 flush 路径）：flush 后既有段逐值比对。"""
        h = FeedHarness(tmp_path, timeframe="1m",
                        clock=FakeClock("2030-01-01"))
        feed, _ = h.acquire()
        feed.on_closed_bar(h.event_after(1))
        feed.flush_pending()
        before = h.parquet()
        assert len(before) == 101
        feed.on_closed_bar(h.event_after(2))
        feed.flush_pending()
        after = h.parquet()
        pd.testing.assert_frame_equal(
            before, after.iloc[:len(before)], check_freq=False)

    def test_duplicate_rows_deduped_across_buffer(self, tmp_path):
        """③去重不重行：缓冲内重复+跨 flush 重叠——行数=去重数。"""
        h = FeedHarness(tmp_path, timeframe="1m",
                        clock=FakeClock("2030-01-01"))
        feed, _ = h.acquire()
        event = h.event_after(1)
        feed.on_closed_bar(event)
        feed.on_closed_bar(event)      # 重复投递（缓冲内去重）
        feed.on_closed_bar(h.event_after(2))
        feed.flush_pending()
        assert len(h.parquet()) == 102  # 100+2（去重后）

    def test_high_freq_buffer_flush_by_clock(self, tmp_path):
        """④⑥高频 60s 缓冲：窗口内不落盘（内存=决策权威）→时钟跨
        间隔后检查点 flush（now_fn 注入驱动，零真实等待）。"""
        start = pd.Timestamp("2026-10-28T00:00:00Z")
        h = FeedHarness(tmp_path, timeframe="1m", clock=FakeClock(start))
        feed, _ = h.acquire()
        feed._last_flush = start  # noqa: SLF001——检查点相位对齐注入
        feed.on_closed_bar(h.event_after(1))
        # 60s 窗口内：内存有新行（决策权威）、parquet 无（缓冲中）
        assert len(feed.latest_df) == 101
        assert len(h.parquet()) == 100
        h.clock.advance(seconds=61)  # 跨间隔
        feed.on_closed_bar(h.event_after(2))  # 落盘请求=检查点
        assert len(h.parquet()) == 102

    def test_flush_pending_idempotent_on_shutdown(self, tmp_path):
        """⑤停机路径缓冲清空落盘：flush_pending 显式清空+幂等。"""
        h = FeedHarness(tmp_path, timeframe="1m",
                        clock=FakeClock("2030-01-01"))
        feed, _ = h.acquire()
        feed.on_closed_bar(h.event_after(1))
        assert len(h.parquet()) == 101  # 首落盘（last_flush=None 即刻）
        feed.on_closed_bar(h.event_after(2))
        assert len(h.parquet()) == 101  # 60s 窗口内——第二根进缓冲
        feed.flush_pending()
        assert len(h.parquet()) == 102  # 清空落盘
        feed.flush_pending()            # 幂等（空缓冲 no-op）
        assert len(h.parquet()) == 102

    def test_registry_flush_all(self, tmp_path):
        """⑤聚合面：registry.flush_all 遍历活跃 feed 清缓冲。"""
        h = FeedHarness(tmp_path, timeframe="1m",
                        clock=FakeClock("2030-01-01"))
        feed, _ = h.acquire()
        feed.on_closed_bar(h.event_after(1))
        h.registry.flush_all()
        assert len(h.parquet()) == 101

    def test_corrupted_parquet_deleted_and_rebuilt(self, tmp_path):
        """⑥崩溃损坏→删文件 REST 重建：垃圾字节文件→装载检测→删除→
        注入 REST 全量重建→落盘→装载成功。"""
        h = FeedHarness(tmp_path)
        rebuild_frame = make_ohlcv(n=80, freq="4h", seed=9)
        h._rest_frames = [rebuild_frame]  # 重建回拉帧
        # 覆写为垃圾字节（损坏 parquet——footer 缺失/半写）
        (h.data_dir / "BTCUSDT_4h.parquet").write_bytes(b"garbage\xff\xfe")
        got: list[int] = []
        feed, _ = h.acquire(
            FeedSubscription(on_frames=lambda df, idx: got.append(idx)))
        assert feed.latest_df is not None
        assert len(feed.latest_df) == 80  # 重建帧装载
        rebuilt = h.parquet()
        assert len(rebuilt) == 80  # 重建落盘
        assert not rebuilt.index.duplicated().any()
        assert got == [0]  # 重建帧初始广播

    def test_corrupted_parquet_rebuild_failure_falls_back(self, tmp_path):
        """⑥重建失败兜底：REST 重建抛异常→键转无产物（建库线程域）。"""
        h = FeedHarness(tmp_path)

        def boom(*args, **kwargs):
            raise ConnectionError("scripted: 重建失败")

        h.registry = FeedRegistry(
            h.data_dir, rest_fetcher=boom,
            funding_refresher=h._funding_refresher, now_fn=h.clock)
        (h.data_dir / "BTCUSDT_4h.parquet").write_bytes(b"garbage")
        feed, _ = h.acquire()
        assert feed.latest_df is None  # 无产物（异常不上抛）
        assert not (h.data_dir / "BTCUSDT_4h.parquet").exists()  # 损坏文件已删


class TestAtrSupplement:
    """ATR 非默认周期补充（取列优先+现算兜底——装载路径）。"""

    def test_atr_non_default_period_column_added(self, tmp_path):
        """订阅方声明 atr_period=20 → 装载后 atr_20 列在场。"""
        h = FeedHarness(tmp_path)
        feed, _ = h.acquire(FeedSubscription(
            on_frames=lambda df, idx: None, atr_periods={20}))
        df = feed.latest_df
        assert "atr_14" in df.columns  # 默认指标集 ATR(14) 档
        assert "atr_20" in df.columns  # 现算兜底补充
        assert df["atr_20"].notna().sum() > 0


class TestFundingGrid:
    """funding 8h 网格装载与结算点枚举。"""

    def test_millisecond_offset_snapped_to_grid(self):
        """16:00:00.001 → 16:00 网格命中（容差吸收毫秒偏移）。"""
        assert snap_to_funding_grid(
            pd.Timestamp("2026-08-30 16:00:00.001", tz="UTC")
        ) == pd.Timestamp("2026-08-30 16:00:00", tz="UTC")

    @pytest.mark.parametrize(
        "open_t,close_t,expected",
        [
            # 4h bar 12:00→16:00：跨 16:00 一点（12:00 非网格点）
            ("2026-08-30 12:00", "2026-08-30 16:00",
             ["2026-08-30 16:00"]),
            # 8h bar 开盘恰在网格点：08:00 属上一根（左开），跨 16:00
            ("2026-08-30 08:00", "2026-08-30 16:00",
             ["2026-08-30 16:00"]),
            # 1d bar 跨 3 点（08:00/16:00/次日 00:00）
            ("2026-08-30 00:00", "2026-08-31 00:00",
             ["2026-08-30 08:00", "2026-08-30 16:00",
              "2026-08-31 00:00"]),
            # 4h bar 00:00→04:00：区间内无网格点（08:00 未到）
            ("2026-08-30 00:00", "2026-08-30 04:00", []),
        ],
    )
    def test_settlement_points_half_open_interval(self, open_t, close_t,
                                                  expected):
        """(open, close] 左开右闭——开盘点归上根、收盘点归本根。"""
        points = settlement_points_between(
            pd.Timestamp(open_t, tz="UTC"), pd.Timestamp(close_t, tz="UTC"))
        assert points == [pd.Timestamp(e, tz="UTC") for e in expected]

    def test_funding_index_built_and_queried(self, tmp_path):
        """网格索引建成：毫秒偏移点可按整点查询（未达点 None）。"""
        h = FeedHarness(tmp_path, funding_frames=[_funding_frame(
            ["2026-08-30 00:00:00.001", "2026-08-30 08:00:00.002"])])
        feed, _ = h.acquire()
        feed.reconcile()  # funding 面经对账节流装载
        rates = feed.funding_rates([
            pd.Timestamp("2026-08-30 00:00:00", tz="UTC"),
            pd.Timestamp("2026-08-30 08:00:00", tz="UTC"),
            pd.Timestamp("2026-08-30 16:00:00", tz="UTC"),  # 未达
        ])
        assert rates == {
            pd.Timestamp("2026-08-30 00:00:00", tz="UTC"): 0.0001,
            pd.Timestamp("2026-08-30 08:00:00", tz="UTC"): 0.0001,
            pd.Timestamp("2026-08-30 16:00:00", tz="UTC"): None,
        }


class TestFundingRefreshThrottle:
    """funding 节流刷新+funding_arrived 广播（reconcile 驱动）。"""

    def _harness(self, tmp_path, funding_frames=None):
        clock = FakeClock("2026-08-30 01:00:00")
        return FeedHarness(
            tmp_path, funding_frames=funding_frames, clock=clock), clock

    def test_throttle_within_interval(self, tmp_path):
        """1h 内重复对账不再拉 funding（节流）。"""
        h, clock = self._harness(
            tmp_path, funding_frames=[_funding_frame(["2026-08-30 00:00:00"])])
        feed, _ = h.acquire()
        feed.reconcile()
        assert len(h.funding_calls) == 1
        clock.advance(minutes=30)  # 间隔内
        feed.reconcile()
        assert len(h.funding_calls) == 1  # 节流命中
        clock.advance(minutes=31)  # 跨过 1h
        feed.reconcile()
        assert len(h.funding_calls) == 2

    def test_newly_covered_points_broadcast(self, tmp_path):
        """索引扩展后新覆盖点非空→广播 funding_arrived（携带点列表）。"""
        h, clock = self._harness(tmp_path, funding_frames=[
            _funding_frame(["2026-08-30 00:00:00", "2026-08-30 08:00:00"]),
            _funding_frame(["2026-08-30 00:00:00", "2026-08-30 08:00:00",
                            "2026-08-30 16:00:00"]),
        ])
        arrived: list[list[pd.Timestamp]] = []
        feed, _ = h.acquire(FeedSubscription(
            on_frames=lambda df, idx: None,
            on_funding_arrived=lambda pts: arrived.append(list(pts))))
        feed.reconcile()  # 初次装载建基线——不广播
        assert arrived == []
        clock.advance(hours=2)
        feed.reconcile()  # 索引扩展至 16:00——广播新覆盖点
        assert arrived == [[pd.Timestamp("2026-08-30 16:00:00", tz="UTC")]]

    def test_initial_load_no_broadcast(self, tmp_path):
        """初次装载（全历史）不广播——基线建立非「挂起点到达」。"""
        h, _ = self._harness(tmp_path, funding_frames=[_funding_frame(
            ["2026-08-28 00:00:00", "2026-08-30 08:00:00"])])
        arrived: list[list[pd.Timestamp]] = []
        h.acquire(FeedSubscription(
            on_frames=lambda df, idx: None,
            on_funding_arrived=lambda pts: arrived.append(list(pts))))
        h.registry.get("BTCUSDT", "4h").reconcile()
        assert arrived == []


class TestFeedDirectSubscribeApi:
    """Feed 直连订阅 API 与散点防御分支。"""

    def test_settlement_close_equals_open_returns_empty(self):
        """结算点枚举边界：close==open（零长区间）→ 空（左开右闭下
        不含任何点）。"""
        ts = pd.Timestamp("2026-08-30 16:00", tz="UTC")
        assert settlement_points_between(ts, ts) == []

    def test_direct_subscribe_unsubscribe_and_idempotent(self, tmp_path):
        """Feed 直连 API：subscribe 登记计数+unsubscribe 摘除+幂等
        （未订阅句柄 no-op）。"""
        h = FeedHarness(tmp_path)
        sub_holder = FeedSubscription(on_frames=lambda df, idx: None)
        feed = h.registry.acquire("BTCUSDT", "4h", sub_holder)
        assert feed.subscriber_count == 1

        got: list[int] = []
        direct = feed.subscribe(lambda df, idx: got.append(idx))
        assert feed.subscriber_count == 2
        feed.on_closed_bar(h.event_after(1))  # 增量广播达直连订阅
        assert got == [100]
        feed.unsubscribe(direct)
        assert feed.subscriber_count == 1
        feed.unsubscribe(direct)  # 幂等（未订阅 no-op）
        assert feed.subscriber_count == 1

    def test_registry_release_unknown_key_noop(self, tmp_path):
        """release 对不存在键 no-op（get None 早退分支）。"""
        h = FeedHarness(tmp_path)
        sub = FeedSubscription(on_frames=lambda df, idx: None)
        h.registry.release("GHOST", "4h", sub)  # 不炸即通过

    def test_funding_face_uses_data_dir(self, tmp_path):
        """funding 面随 data_dir 装载（装配切换 FAPI_DATA_DIR 后同链）。"""
        h = FeedHarness(tmp_path, funding_frames=[_funding_frame(
            ["2026-08-30 00:00:00"])])
        seen: dict = {}
        original = h._funding_refresher

        def spy(*, symbol, data_dir):
            seen["data_dir"] = Path(data_dir)
            return original(symbol=symbol, data_dir=data_dir)

        h.registry = FeedRegistry(
            h.data_dir, rest_fetcher=h._rest, funding_refresher=spy,
            now_fn=h.clock)
        feed, _ = h.acquire()
        feed.reconcile()
        assert seen["data_dir"] == h.data_dir



class TestReconcileAllDuplicateAndDefaults:
    """阶段四盲区消解：全重复补拉段/缺省 funding refresher/flush_all
    关停防御。"""

    def test_reconcile_all_duplicate_segment_returns_ok(self, tmp_path):
        """补拉段全为重复（WS 已先到）——merged None 短路 return "ok"
        （不广播不落盘，对账状态仍 ok）。"""
        h = FeedHarness(tmp_path)
        got: list[int] = []
        feed, _ = h.acquire(
            FeedSubscription(on_frames=lambda df, idx: got.append(idx)))
        feed.on_closed_bar(h.event_after(1))  # WS 先到一根
        got.clear()
        # REST 补拉段重含同一根（全重复——无新行）
        h._rest_frames = [_ohlcv_at([h.last + h.tf])]
        h.clock.now = h.last + 3 * h.tf
        assert feed.reconcile() is None  # 不外抛
        last_run, last_result = feed.reconcile_state
        assert last_result == "ok"  # 全重复=对账无所得（非 gap_filled）
        assert got == []  # 不广播

    def test_default_funding_refresher_forwards_updater(self, tmp_path,
                                                        monkeypatch):
        """缺省 funding refresher（生产装配面）：转发 update_funding_data
        （symbol+data_dir 同参）。"""
        data_dir = tmp_path / "market"
        data_dir.mkdir()
        calls: list[dict] = []
        frame = _funding_frame(["2024-01-01 00:00:00"])

        def fake_update(*, symbol, data_dir):
            calls.append({"symbol": symbol, "data_dir": data_dir})
            return frame

        monkeypatch.setattr(
            "core.data.updater.update_funding_data", fake_update)
        from core.paper_trading.runtime.feed import (
            _default_funding_refresh)  # noqa: SLF001——缺省 refresher 路径

        assert _default_funding_refresh(
            symbol="BTCUSDT", data_dir=data_dir) is frame
        assert calls == [{"symbol": "BTCUSDT", "data_dir": data_dir}]

    def test_flush_all_swallows_feed_failure_continues(self, tmp_path,
                                                       monkeypatch):
        """flush_all 关停防御：单 feed flush 抛异常被吞续行（后续 feed
        照常清缓冲——关停序不被单点故障绑架）。"""
        h = FeedHarness(tmp_path, timeframe="1m",
                        clock=FakeClock("2030-01-01"))
        from core.data.storage import save_parquet

        save_parquet(make_ohlcv(n=30, freq="1h", seed=4),
                     h.data_dir / "ETHUSDT_1h.parquet")
        feed_1m, _ = h.acquire()
        feed_1h = h.registry.acquire(
            "ETHUSDT", "1h", FeedSubscription(on_frames=lambda df, i: None))
        feed_1m.on_closed_bar(h.event_after(1))  # 首根即刻 flush
        feed_1m.on_closed_bar(h.event_after(2))  # 第二根进缓冲
        feed_1h.on_closed_bar(_event_at(
            feed_1h.latest_df.index[-1] + pd.Timedelta(hours=1),
            timeframe="1h"))

        def broken_flush():
            raise RuntimeError("scripted: 清缓冲崩")

        monkeypatch.setattr(feed_1m, "flush_pending", broken_flush)
        h.registry.flush_all()  # 1m 键抛异常被吞——1h 键续行清缓冲
        assert len(load_parquet(
            h.data_dir / "ETHUSDT_1h.parquet")) == 31  # 1h 已清

    def test_mixed_duplicate_outoforder_sequence_invariants(self, tmp_path):
        """R4 防御面属性补强：固定 seed 随机混合序列（乱序+重复）投递
        ——内存序列保持单调递增、无重复行、行数=唯一 open_time 数
        （sort 合并+keep-first 的不变量，任意闭合序列下成立）。"""
        import random

        rng = random.Random(20261028)
        h = FeedHarness(tmp_path)
        feed, _ = h.acquire()
        # 60 根候选：正常序+随机重放（重复）+随机乱序插入的混合
        steps = [rng.choice((1, 2, 3, -1, 0)) for _ in range(60)]
        unique_times = {h.last + h.tf * (s if s > 0 else 1)
                        for s in steps if s != 0}
        for step in steps:
            if step == 0:
                continue  # 噪声步（跳过）
            feed.on_closed_bar(h.event_after(step))
        df = feed.latest_df
        assert df.index.is_monotonic_increasing  # 不变量①：单调
        assert not df.index.duplicated().any()   # 不变量②：无重复
        assert len(df) == 100 + len(unique_times)  # 不变量③：行数=装载+唯一

    def test_flush_all_continues_when_feed_recycled_midway(self, tmp_path,
                                                           monkeypatch):
        """flush_all 竞态防御：active_keys 快照后 feed 被回收（get
        None）——跳过该键不炸不外抛。"""
        h = FeedHarness(tmp_path, timeframe="1m",
                        clock=FakeClock("2030-01-01"))
        h.acquire()  # feed 在场（active_keys 非空）
        monkeypatch.setattr(
            h.registry, "get", lambda *a, **k: None)  # 回收竞态窗口
        h.registry.flush_all()  # 不炸即通过（continue 分支）
