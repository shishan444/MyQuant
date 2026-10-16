"""品种数据作业测试（rebuild-paper-trading 任务 3.1）。

覆盖：per-(symbol,timeframe) 单例共享（同键两订阅一份拉取/不同键独立/
订阅归零回收）+forming bar 过滤（沿旧 runner 判据）+ATR 非默认周期补充
（取列优先+现算兜底）+funding 8h 网格装载（毫秒偏移容差）+节流刷新
+funding_arrived 广播（新覆盖点非空才广播、初次装载建基线不广播）+
结算点区间枚举（(open,close] 左开右闭语义）。
"""
from __future__ import annotations

import pandas as pd
import pytest

pytestmark = [pytest.mark.unit]

from core.paper_trading.runtime.feed import (
    FeedRegistry,
    FeedSubscription,
    settlement_points_between,
    snap_to_funding_grid,
)
from tests.helpers.data_factory import make_ohlcv


class FakeClock:
    """可推进时钟（forming 判据与 funding 节流的注入通道）。"""

    def __init__(self, start: str):
        self.now = pd.Timestamp(start, tz="UTC")

    def __call__(self) -> pd.Timestamp:
        return self.now

    def advance(self, **kwargs) -> None:
        self.now += pd.Timedelta(**kwargs)


def _make_fetcher(frames: list[pd.DataFrame]):
    """合成行情拉取（按调用序逐帧返回——末帧驻留；spy 计调用次数）。"""
    calls: list[tuple[str, str]] = []

    def fetch(*, symbol: str, interval: str, data_dir) -> pd.DataFrame:
        calls.append((symbol, interval))
        return frames[min(len(calls) - 1, len(frames) - 1)]

    return fetch, calls


def _make_funding_refresher(frames: list[pd.DataFrame]):
    """合成 funding 归档刷新（同上——frames 为全量帧序列）。"""
    calls: list[str] = []

    def refresh(*, symbol: str, data_dir) -> pd.DataFrame:
        calls.append(symbol)
        return frames[min(len(calls) - 1, len(frames) - 1)]

    return refresh, calls


def _funding_frame(times: list[str], rate: float = 0.0001) -> pd.DataFrame:
    """合成 funding parquet 形制（列 funding_rate+DatetimeIndex UTC）。"""
    idx = pd.DatetimeIndex(pd.to_datetime(times, utc=True), name="timestamp")
    return pd.DataFrame({"funding_rate": [rate] * len(idx)}, index=idx)


def _make_registry(tmp_path, frames, funding_frames=None, clock=None):
    """组装测试注册表（合成 fetcher+固定晚时钟——合成帧不触 forming）。"""
    fetcher, calls = _make_fetcher(frames)
    refresher, funding_calls = _make_funding_refresher(
        funding_frames or [_funding_frame(["2024-01-01 00:00:00"])])
    clock = clock or FakeClock("2030-01-01")
    registry = FeedRegistry(
        tmp_path, fetcher=fetcher, funding_refresher=refresher,
        now_fn=clock)
    return registry, {"fetch": calls, "funding": funding_calls}


class TestSharedFeed:
    """同键共享/异键独立/引用计数回收。"""

    def test_same_key_two_subscribers_share_one_fetch(self, tmp_path):
        """同键两任务共享一份拉取：一次 refresh 拉一次、两订阅各广播。"""
        frame = make_ohlcv(n=100, freq="4h")
        registry, spies = _make_registry(tmp_path, [frame])
        received: list[tuple[int, int]] = []

        sub_a = FeedSubscription(
            on_frames=lambda df, idx: received.append(("a", idx)))
        sub_b = FeedSubscription(
            on_frames=lambda df, idx: received.append(("b", idx)))
        feed_a = registry.acquire("BTCUSDT", "4h", sub_a)
        feed_b = registry.acquire("BTCUSDT", "4h", sub_b)

        assert feed_a is feed_b  # 同键同实例（共享单位）
        feed_a.refresh()
        assert spies["fetch"] == [("BTCUSDT", "4h")]  # 拉取只发生一份
        assert received == [("a", 0), ("b", 0)]  # 两订阅各收一次广播

    def test_different_keys_independent_feeds(self, tmp_path):
        """不同 (symbol,timeframe) 各自独立作业（两实例两拉取）。"""
        frame = make_ohlcv(n=100, freq="4h")
        registry, spies = _make_registry(tmp_path, [frame])

        sub_a = FeedSubscription(on_frames=lambda df, idx: None)
        sub_b = FeedSubscription(on_frames=lambda df, idx: None)
        feed_a = registry.acquire("BTCUSDT", "4h", sub_a)
        feed_b = registry.acquire("ETHUSDT", "1h", sub_b)

        assert feed_a is not feed_b
        feed_a.refresh()
        feed_b.refresh()
        assert spies["fetch"] == [
            ("BTCUSDT", "4h"), ("ETHUSDT", "1h")]

    def test_unref_to_zero_recycles_feed(self, tmp_path):
        """订阅归零→feed 摘除回收（再 acquire 得新实例）。"""
        frame = make_ohlcv(n=100, freq="4h")
        registry, _ = _make_registry(tmp_path, [frame])

        sub_a = FeedSubscription(on_frames=lambda df, idx: None)
        sub_b = FeedSubscription(on_frames=lambda df, idx: None)
        feed = registry.acquire("BTCUSDT", "4h", sub_a)
        registry.acquire("BTCUSDT", "4h", sub_b)
        assert feed.subscriber_count == 2

        registry.release("BTCUSDT", "4h", sub_a)
        assert feed.subscriber_count == 1
        assert registry.get("BTCUSDT", "4h") is feed  # 尚有订阅不回收

        registry.release("BTCUSDT", "4h", sub_b)
        assert registry.get("BTCUSDT", "4h") is None  # 归零回收
        assert registry.active_keys == []

        sub_new = FeedSubscription(on_frames=lambda df, idx: None)
        feed_new = registry.acquire("BTCUSDT", "4h", sub_new)
        assert feed_new is not feed  # 回收后重建（非复活）

    def test_no_new_bars_no_broadcast(self, tmp_path):
        """增量无新闭合 bar 不广播（长度不增静默返回）。"""
        frame = make_ohlcv(n=100, freq="4h")
        registry, _ = _make_registry(tmp_path, [frame])
        got: list[tuple] = []
        sub = FeedSubscription(on_frames=lambda df, idx: got.append(idx))
        feed = registry.acquire("BTCUSDT", "4h", sub)

        feed.refresh()
        assert got == [0]
        feed.refresh()  # 同长度帧（无增量）
        assert got == [0]
        assert feed.latest_df is not None


class TestFormingBarFilter:
    """forming bar 过滤（沿旧 runner 判据：末根+周期>now 截末行）。"""

    def test_forming_last_bar_dropped(self, tmp_path):
        """末根未闭合截掉（99 根闭合广播、forming 末根不进产物）。"""
        frame = make_ohlcv(n=100, freq="4h")  # 末根 2024-01-17 12:00
        clock = FakeClock("2024-01-17 14:30")  # 12:00+4h=16:00 > now → forming
        registry, _ = _make_registry(tmp_path, [frame], clock=clock)
        got: list[tuple[pd.DataFrame, int]] = []
        sub = FeedSubscription(on_frames=lambda df, idx: got.append((df, idx)))
        feed = registry.acquire("BTCUSDT", "4h", sub)

        feed.refresh()
        assert len(got) == 1  # 99 根闭合 bar 是首次产物——正常广播
        df, from_idx = got[0]
        assert from_idx == 0
        assert len(df) == 99  # forming 末根被截（12:00 未闭合）
        assert df.index[-1] == pd.Timestamp("2024-01-17 08:00", tz="UTC")

    def test_closed_last_bar_kept(self, tmp_path):
        clock = FakeClock("2024-01-17 16:30")  # 末根已闭合（16:00 ≤ now）
        frame = make_ohlcv(n=100, freq="4h")
        registry, _ = _make_registry(tmp_path, [frame], clock=clock)
        got: list[int] = []
        sub = FeedSubscription(on_frames=lambda df, idx: got.append(idx))
        feed = registry.acquire("BTCUSDT", "4h", sub)

        feed.refresh()
        assert got == [0]
        assert len(feed.latest_df) == 100  # 全闭合不截


class TestAtrSupplement:
    """ATR 非默认周期补充（取列优先+现算兜底）。"""

    def test_atr_non_default_period_column_added(self, tmp_path):
        """订阅方声明 atr_period=20 → refresh 后 atr_20 列在场。"""
        frame = make_ohlcv(n=100, freq="4h")
        registry, _ = _make_registry(tmp_path, [frame])
        sub = FeedSubscription(
            on_frames=lambda df, idx: None, atr_periods={20})
        feed = registry.acquire("BTCUSDT", "4h", sub)

        feed.refresh()
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
        frame = _funding_frame(
            ["2026-08-30 00:00:00.001", "2026-08-30 08:00:00.002"])
        registry, _ = _make_registry(tmp_path, [make_ohlcv(n=100)],
                                     funding_frames=[frame])
        sub = FeedSubscription(on_frames=lambda df, idx: None)
        feed = registry.acquire("BTCUSDT", "4h", sub)
        feed.refresh()

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
    """funding 节流刷新+funding_arrived 广播。"""

    def test_throttle_within_interval(self, tmp_path):
        """1h 内重复 refresh 不再拉 funding（节流）。"""
        frame = _funding_frame(["2026-08-30 00:00:00"])
        clock = FakeClock("2026-08-30 01:00:00")
        registry, spies = _make_registry(
            tmp_path, [make_ohlcv(n=100)], funding_frames=[frame],
            clock=clock)
        sub = FeedSubscription(on_frames=lambda df, idx: None)
        feed = registry.acquire("BTCUSDT", "4h", sub)

        feed.refresh()
        assert len(spies["funding"]) == 1
        clock.advance(minutes=30)  # 间隔内
        feed.refresh()
        assert len(spies["funding"]) == 1  # 节流命中
        clock.advance(minutes=31)  # 跨过 1h
        feed.refresh()
        assert len(spies["funding"]) == 2

    def test_newly_covered_points_broadcast(self, tmp_path):
        """索引扩展后新覆盖点非空→广播 funding_arrived（携带点列表）。"""
        base = _funding_frame(["2026-08-30 00:00:00", "2026-08-30 08:00:00"])
        extended = _funding_frame(
            ["2026-08-30 00:00:00", "2026-08-30 08:00:00",
             "2026-08-30 16:00:00"])
        clock = FakeClock("2026-08-30 01:00:00")
        registry, _ = _make_registry(
            tmp_path, [make_ohlcv(n=100)],
            funding_frames=[base, extended], clock=clock)
        arrived: list[list[pd.Timestamp]] = []
        sub = FeedSubscription(
            on_frames=lambda df, idx: None,
            on_funding_arrived=lambda pts: arrived.append(list(pts)))
        feed = registry.acquire("BTCUSDT", "4h", sub)

        feed.refresh()  # 初次装载建基线——不广播
        assert arrived == []
        clock.advance(hours=2)
        feed.refresh()  # 索引扩展至 16:00——广播新覆盖点
        assert arrived == [[pd.Timestamp("2026-08-30 16:00:00", tz="UTC")]]

    def test_initial_load_no_broadcast(self, tmp_path):
        """初次装载（全历史）不广播——基线建立非「挂起点到达」。"""
        base = _funding_frame(
            ["2026-08-28 00:00:00", "2026-08-30 08:00:00"])
        registry, _ = _make_registry(
            tmp_path, [make_ohlcv(n=100)], funding_frames=[base])
        arrived: list[list[pd.Timestamp]] = []
        sub = FeedSubscription(
            on_frames=lambda df, idx: None,
            on_funding_arrived=lambda pts: arrived.append(list(pts)))
        feed = registry.acquire("BTCUSDT", "4h", sub)

        feed.refresh()
        assert arrived == []
