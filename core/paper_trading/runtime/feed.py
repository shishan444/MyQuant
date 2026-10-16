"""品种数据作业（SymbolFeed——per-(symbol,timeframe) 共享单例，design §5.2）。

同 (symbol, timeframe) 的任务共享一份行情拉取与指标计算（产物 DataFrame
只读引用整体替换——无锁读：消费方持旧快照完成当前 bar 后取新引用）；
不同键各自独立作业；订阅归零停止拉取回收（经 FeedRegistry 摘除）。

职责链（refresh 单点）：
update_market_data 增量拉取 → compute_all_indicators 指标计算（stop_check
可接 TaskController）→ ATR 非默认周期补充（取列优先+现算兜底，沿
core/backtest/engine.py:84 _load_atr_column 形制）→ forming bar 过滤
（沿旧 runner 判据：末根 bar 时间+周期间隔>now 即未闭合，截掉末行）
→ 闭合 bar 广播给订阅者。

funding 面（Q-2=a 资金费挂起补记，tasks 3.5）：{symbol}_funding.parquet
装载为 8h 网格索引（snap_to_funding_grid 容差吸收归档时间戳毫秒偏移，
如 16:00:00.001→16:00 网格）；每小时节流刷新（update_funding_data 增量
——feed 是其首个生产调用方）；新覆盖挂起点非空时广播 funding_arrived
（初次装载建基线不算「到达」，不广播）。
"""
from __future__ import annotations

import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable, Optional

import pandas as pd

from core.data.timeframes import TF_SECONDS
from core.data.updater import update_funding_data, update_market_data

# funding 归档刷新节流间隔（秒）——design §6.3「每小时节流刷新」
FUNDING_REFRESH_INTERVAL_SECONDS = 3600.0

# 8h 结算网格（Binance 永续资金费结算节奏：UTC 00/08/16 点）
_FUNDING_GRID = "8h"

# 未知周期的 bar 秒数兜底（沿旧 runner _BAR_SECONDS.get(tf, 14400) 形制）
_DEFAULT_BAR_SECONDS = 14400


def snap_to_funding_grid(ts: pd.Timestamp) -> pd.Timestamp:
    """结算时刻吸附 8h 网格（容差吸收归档时间戳毫秒偏移）。

    round 取最近网格点：毫秒级偏移（如 16:00:00.001）恒吸附回本格
    （偏移远小于 4h 半格）；design §1.3 funding parquet 实数据核验依据。
    """
    return ts.round(_FUNDING_GRID)


def settlement_points_between(
    open_t: pd.Timestamp, close_t: pd.Timestamp,
) -> list[pd.Timestamp]:
    """bar 区间 (open_t, close_t] 跨过的 8h 结算点（升序）。

    左开右闭——开盘时刻恰落网格点时该点属上一根 bar（其区间以该时刻
    为右端）；收盘时刻的网格点属本 bar。1d bar 跨 3 点、8h bar 跨 1 点、
    4h bar 每 2 根跨 1 点。
    """
    if close_t <= open_t:
        return []
    step = pd.Timedelta(_FUNDING_GRID)
    point = open_t.floor(_FUNDING_GRID) + step  # 严格大于 open_t 的首格
    points: list[pd.Timestamp] = []
    while point <= close_t:
        points.append(point)
        point += step
    return points


@dataclass(frozen=True)
class FeedSubscription:
    """feed 订阅句柄（执行器持引用，退订/回收的协作面）。

    Attributes:
        on_frames: 闭合 bar 广播回调（new_df 只读引用, from_idx=新增
            闭合 bar 起始位置——消费方从 from_idx 逐根推进）。
        on_funding_arrived: funding 新覆盖挂起点广播（结算点列表）；
            None=不关心。
        atr_periods: 该订阅方按 DNA 声明的 ATR 周期需求集（feed 汇总
            全订阅者并集补充计算 atr_{period} 列）。
    """

    on_frames: Callable[[pd.DataFrame, int], None]
    on_funding_arrived: Optional[Callable[[list[pd.Timestamp]], None]] = None
    atr_periods: frozenset = frozenset()


class SymbolFeed:
    """单 (symbol, timeframe) 的行情/指标/funding 共享作业。

    线程模型：被动对象（不自起线程）——refresh 由调度器守护循环或
    测试直接驱动；广播回调在 refresh 调用线程同步执行（订阅方回调
    应仅入队+置事件，重活在自有工作线程做）。内部状态以锁保护
    （订阅表/产物引用/funding 索引三面）。
    """

    def __init__(
        self,
        symbol: str,
        timeframe: str,
        data_dir: Path,
        *,
        fetcher: Optional[Callable[..., pd.DataFrame]] = None,
        funding_refresher: Optional[Callable[..., pd.DataFrame]] = None,
        now_fn: Optional[Callable[[], pd.Timestamp]] = None,
    ):
        """初始化作业。

        Args:
            symbol: 交易对。
            timeframe: K 线周期（TF_SECONDS 键集）。
            data_dir: Parquet 数据目录。
            fetcher: 行情增量拉取函数（缺省 update_market_data；签名
                兼容其 kwargs——测试注入合成帧/spy）。
            funding_refresher: funding 归档增量刷新（缺省
                update_funding_data）。
            now_fn: 当前时刻函数（缺省真实时钟——forming 过滤判据与
                funding 节流用；测试注入固定时刻旁路真实时钟）。
        """
        self.symbol = symbol
        self.timeframe = timeframe
        self.data_dir = Path(data_dir)
        self._fetcher = fetcher or update_market_data
        self._funding_refresher = funding_refresher or update_funding_data
        self._now_fn = now_fn or (lambda: pd.Timestamp.now(tz="UTC"))
        self._lock = threading.Lock()
        self._df: Optional[pd.DataFrame] = None  # 最新产物（只读广播）
        self._subscriptions: list[FeedSubscription] = []
        self._funding_index: dict[pd.Timestamp, float] = {}
        self._funding_cover: Optional[pd.Timestamp] = None  # 索引覆盖终点
        self._last_funding_refresh: Optional[pd.Timestamp] = None

    # ── 订阅面 ──────────────────────────────────────────────

    @property
    def subscriber_count(self) -> int:
        """当前活跃订阅数（引用计数——归零即回收依据）。"""
        with self._lock:
            return len(self._subscriptions)

    @property
    def latest_df(self) -> Optional[pd.DataFrame]:
        """最新闭合 bar 产物引用（无产物 None；消费方只读）。"""
        with self._lock:
            return self._df

    def subscribe(
        self,
        on_frames: Callable[[pd.DataFrame, int], None],
        *,
        on_funding_arrived: Optional[
            Callable[[list[pd.Timestamp]], None]] = None,
        atr_periods: Iterable[int] = (),
    ) -> FeedSubscription:
        """订阅闭合 bar 广播（同键任务共享同一 feed 实例的拉取）。"""
        sub = FeedSubscription(
            on_frames=on_frames,
            on_funding_arrived=on_funding_arrived,
            atr_periods=frozenset(atr_periods),
        )
        with self._lock:
            self._subscriptions.append(sub)
        return sub

    def unsubscribe(self, subscription: FeedSubscription) -> None:
        """退订（幂等——未订阅句柄 no-op）。"""
        with self._lock:
            if subscription in self._subscriptions:
                self._subscriptions.remove(subscription)

    # ── funding 索引面 ──────────────────────────────────────

    def funding_rates(
        self, points: Iterable[pd.Timestamp],
    ) -> dict[pd.Timestamp, Optional[float]]:
        """按 8h 网格点查费率（未达/不存在→None——挂起判定输入）。"""
        with self._lock:
            return {p: self._funding_index.get(p) for p in points}

    # ── 刷新单点 ────────────────────────────────────────────

    def refresh(self, stop_check: Optional[Callable[[], None]] = None) -> None:
        """一轮刷新：funding 节流刷新 → 行情增量拉取 → 指标 → ATR 补充
        → forming 过滤 → 广播新闭合 bar。

        无新闭合 bar（拉取空/长度不增/增量全为 forming）时静默返回
        （轮询继续）；拉取异常向上传播（调度器守护循环记日志续轮）。
        """
        self._refresh_funding_if_due()
        raw = self._fetcher(
            symbol=self.symbol, interval=self.timeframe,
            data_dir=self.data_dir,
        )
        if raw is None or raw.empty:
            return
        with self._lock:
            prev = self._df
        prev_len = 0 if prev is None else len(prev)
        if prev is not None and len(raw) <= prev_len:
            return  # 无增量（拉取返回全量帧——长度不增即无新数据）
        if stop_check is not None:
            stop_check()
        from core.features.indicators import compute_all_indicators

        enhanced = compute_all_indicators(raw, stop_check=stop_check)
        enhanced = self._ensure_atr_columns(enhanced, stop_check)
        closed = self._filter_forming_bar(enhanced)
        if prev is not None and len(closed) <= prev_len:
            return  # 增量全为 forming bar（未闭合不广播）
        with self._lock:
            self._df = closed  # 引用整体替换（旧快照消费方不受影响）
            subscribers = list(self._subscriptions)
        for sub in subscribers:
            sub.on_frames(closed, prev_len)

    def _refresh_funding_if_due(self) -> None:
        """funding 归档节流刷新（首次装载建基线；新覆盖点非空时广播）。

        初次装载（无覆盖终点）不广播——全部历史点属基线建立而非
        「挂起点的到达」，此时订阅方尚无 pending 行，广播无消费者。
        """
        now = self._now_fn()
        last = self._last_funding_refresh
        if last is not None and (
                now - last).total_seconds() < FUNDING_REFRESH_INTERVAL_SECONDS:
            return
        self._last_funding_refresh = now
        frame = self._funding_refresher(
            symbol=self.symbol, data_dir=self.data_dir)
        if frame is None or frame.empty:
            return
        new_index = {
            snap_to_funding_grid(ts): float(rate)
            for ts, rate in frame["funding_rate"].items()
        }
        with self._lock:
            old_cover = self._funding_cover
            newly_covered = sorted(
                p for p in new_index
                if old_cover is not None and p > old_cover
            ) if old_cover is not None else []
            self._funding_index = new_index
            if new_index:
                self._funding_cover = max(new_index)
            subscribers = list(self._subscriptions)
        if newly_covered:
            for sub in subscribers:
                if sub.on_funding_arrived is not None:
                    sub.on_funding_arrived(newly_covered)

    def _ensure_atr_columns(
        self, df: pd.DataFrame,
        stop_check: Optional[Callable[[], None]],
    ) -> pd.DataFrame:
        """ATR 非默认周期补充（取列优先+现算兜底）。

        默认指标集仅 ATR(14) 档；订阅方 DNA 的 atr_period≠14 时按全
        订阅者并集补算 atr_{period} 列。现算不可得（数据不足）不补
        ——装载校验层前置拦截 atr_missing，运行中缺列按该 bar 无 ATR
        走 kernel 守卫（None 注入）。
        """
        with self._lock:
            periods = sorted(
                {p for sub in self._subscriptions for p in sub.atr_periods})
        if not periods:
            return df
        from core.features.indicators import _compute_indicator

        for period in periods:
            col = f"atr_{period}"
            if col in df.columns:
                continue  # 取列优先（compute_all_indicators 已产出）
            if stop_check is not None:
                stop_check()
            computed = _compute_indicator(df, "ATR", {"period": period})
            if col in computed:
                df[col] = computed[col]
        return df

    def _filter_forming_bar(self, df: pd.DataFrame) -> pd.DataFrame:
        """剔除未闭合（forming）bar——沿旧 runner 判据。

        末根 bar 时间+周期间隔仍晚于当前时刻=该 bar 尚在形成（high/low
        未定），截掉末行；决策只消费已闭合 bar。
        """
        if df.empty:
            return df
        interval = TF_SECONDS.get(self.timeframe, _DEFAULT_BAR_SECONDS)
        now = self._now_fn()
        last_bar_time = df.index[-1]
        if last_bar_time.tz is None:
            last_bar_time = last_bar_time.tz_localize("UTC")
        if last_bar_time + pd.Timedelta(seconds=interval) > now:
            return df.iloc[:-1]
        return df


class FeedRegistry:
    """per-(symbol, timeframe) feed 单例注册表（调度器/执行器的获取面）。

    acquire 建/取同键 feed 并挂订阅；release 退订后订阅归零即摘除
    （停止拉取回收——无订阅者的数据作业不存活）。构造参数原样注入
    每个新建 feed（生产只传 data_dir；测试注入合成 fetcher/fixed 时钟）。
    """

    def __init__(self, data_dir: Path, **feed_kwargs):
        """初始化注册表。

        Args:
            data_dir: Parquet 数据目录。
            **feed_kwargs: SymbolFeed 构造注入面（fetcher/
                funding_refresher/now_fn——测试通道）。
        """
        self.data_dir = Path(data_dir)
        self._feed_kwargs = feed_kwargs
        self._feeds: dict[tuple[str, str], SymbolFeed] = {}
        self._lock = threading.Lock()

    def acquire(
        self, symbol: str, timeframe: str,
        subscription: FeedSubscription,
    ) -> SymbolFeed:
        """取同键 feed（无则建）并挂订阅（同键任务共享一份拉取）。"""
        key = (symbol, timeframe)
        with self._lock:
            feed = self._feeds.get(key)
            if feed is None:
                feed = SymbolFeed(
                    symbol, timeframe, self.data_dir,
                    **self._feed_kwargs)
                self._feeds[key] = feed
            with feed._lock:
                feed._subscriptions.append(subscription)
        return feed

    def release(
        self, symbol: str, timeframe: str,
        subscription: FeedSubscription,
    ) -> None:
        """退订；订阅归零摘除 feed（停止拉取回收）。"""
        key = (symbol, timeframe)
        with self._lock:
            feed = self._feeds.get(key)
            if feed is None:
                return
            with feed._lock:
                if subscription in feed._subscriptions:
                    feed._subscriptions.remove(subscription)
                remaining = len(feed._subscriptions)
            if remaining == 0:
                del self._feeds[key]

    def get(self, symbol: str, timeframe: str) -> Optional[SymbolFeed]:
        """取同键现存 feed（无/已回收 None——测试观察面）。"""
        with self._lock:
            return self._feeds.get((symbol, timeframe))

    @property
    def active_keys(self) -> list[tuple[str, str]]:
        """现存键集（测试观察面）。"""
        with self._lock:
            return sorted(self._feeds)
