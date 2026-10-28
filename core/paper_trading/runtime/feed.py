"""品种数据作业（SymbolFeed——per-(symbol,timeframe) 共享单例，design §5.2）。

同 (symbol, timeframe) 的任务共享一份行情与指标计算（产物 DataFrame
只读引用整体替换——无锁读：消费方持旧快照完成当前 bar 后取新引用）；
不同键各自独立作业；订阅归零回收（经 FeedRegistry 摘除）。

双通道职责链（change-1 refresh 单点分解）：
- on_closed_bar（WS 路径）：闭合事件追加内存 → 全帧指标（共享单次）
  → 广播 (new_df, from_idx=追加前长度)；重复事件先到为准不重行。
- reconcile（对账路径，调度器守护循环驱动）：读内存尾戳 → 判缺口
  （末根+2×周期 ≤ now 即应有新闭合 bar）→ REST 补拉缺口段 → 时间戳
  去重合并（内存先到为准）→ 指标 → 广播补齐段 → 落盘对齐；无缺口
  不广播不落盘。无基线键（无内存且无文件）跳过——初始建库归启动
  建库线程（app lifespan）。
- 内存闭合序列=决策权威（latest_df 实时可读），parquet=持久副本。

feed 实例创建（首个订阅者 acquire）=从 parquet 装载历史建内存序列+
指标（ensure_loaded 幂等——装载产物广播 from_idx=0 供初始消费）。

funding 面（Q-2=a 资金费挂起补记，tasks 3.5）：{symbol}_funding.parquet
装载为 8h 网格索引（snap_to_funding_grid 容差吸收归档时间戳毫秒偏移，
如 16:00:00.001→16:00 网格）；每小时节流刷新（update_funding_data 增
量）；新覆盖挂起点非空时广播 funding_arrived（初次装载建基线不算
「到达」，不广播）。data_dir 随装配切换（FAPI_DATA_DIR）。
"""
from __future__ import annotations

import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable, Optional

import pandas as pd

from core.data.storage import load_parquet, merge_parquet
from core.data.timeframes import TF_SECONDS

# funding 归档刷新节流间隔（秒）——design §6.3「每小时节流刷新」
FUNDING_REFRESH_INTERVAL_SECONDS = 3600.0

# 8h 结算网格（Binance 永续资金费结算节奏：UTC 00/08/16 点）
_FUNDING_GRID = "8h"

# 未知周期的 bar 秒数兜底（沿旧 runner _BAR_SECONDS.get(tf, 14400) 形制）
_DEFAULT_BAR_SECONDS = 14400

# OHLCV 基础列（WS 事件行与 REST 补拉帧的共同锚；指标列为派生面）
OHLCV_COLUMNS = ["open", "high", "low", "close", "volume"]


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


def _default_rest_fetcher(symbol, timeframe, start_ts, end_ts) -> pd.DataFrame:
    """缺省 REST 兜底（fapi 接入层——延迟构造，feed 永不触碰 SDK 类型）。"""
    from core.data.fapi.rest import FapiRestClient

    return FapiRestClient().fetch_closed_bars(
        symbol, timeframe, start_ts, end_ts)


@dataclass(frozen=True)
class FeedSubscription:
    """feed 订阅句柄（执行器持引用，退订/回收的协作面）。

    Attributes:
        on_frames: 闭合 bar 广播回调（new_df 只读引用, from_idx=新增
            闭合 bar 起始位置——消费方从 from_idx 逐根推进；from_idx
            是提示非承诺，消费端按自身游标判重兜底）。
        on_funding_arrived: funding 新覆盖挂起点广播（结算点列表）；
            None=不关心。
        atr_periods: 该订阅方按 DNA 声明的 ATR 周期需求集（feed 汇总
            全订阅者并集补充计算 atr_{period} 列）。
    """

    on_frames: Callable[[pd.DataFrame, int], None]
    on_funding_arrived: Optional[Callable[[list[pd.Timestamp]], None]] = None
    atr_periods: frozenset = frozenset()


class SymbolFeed:
    """单 (symbol, timeframe) 的行情/指标/funding 共享作业（双通道）。

    线程模型：被动对象（不自起线程）——on_closed_bar 由接入层事件
    路由调用（app 装配的回调线程），reconcile 由调度器守护循环或测试
    直接驱动；广播回调在调用线程同步执行（订阅方回调应仅入队+置事件，
    重活在自有工作线程做）。内部状态以锁保护（订阅表/产物引用/funding
    索引三面）。
    """

    def __init__(
        self,
        symbol: str,
        timeframe: str,
        data_dir: Path,
        *,
        rest_fetcher: Optional[Callable[..., pd.DataFrame]] = None,
        funding_refresher: Optional[Callable[..., pd.DataFrame]] = None,
        now_fn: Optional[Callable[[], pd.Timestamp]] = None,
    ):
        """初始化作业。

        Args:
            symbol: 交易对。
            timeframe: K 线周期（TF_SECONDS 键集）。
            data_dir: Parquet 数据目录。
            rest_fetcher: REST 缺口补拉（缺省 fapi FapiRestClient——
                fetch_closed_bars 同形；测试注入合成帧）。
            funding_refresher: funding 归档增量刷新（缺省
                update_funding_data）。
            now_fn: 当前时刻函数（缺省真实时钟——缺口判定与 funding
                节流用；测试注入固定时刻旁路真实时钟）。
        """
        self.symbol = symbol
        self.timeframe = timeframe
        self.data_dir = Path(data_dir)
        self._parquet_path = self.data_dir / f"{symbol}_{timeframe}.parquet"
        self._rest_fetcher = rest_fetcher or _default_rest_fetcher
        self._funding_refresher = funding_refresher or _default_funding_refresh
        self._now_fn = now_fn or (lambda: pd.Timestamp.now(tz="UTC"))
        self._lock = threading.Lock()
        self._df: Optional[pd.DataFrame] = None  # 最新产物（只读广播）
        self._loaded = False  # ensure_loaded 幂等锚
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

    # ── 装载面 ──────────────────────────────────────────────

    def ensure_loaded(self) -> None:
        """从 parquet 装载历史建内存序列（幂等；首装广播 from_idx=0）。

        无文件/空文件：保持无产物（等启动建库线程或首事件冷启动）——
        无基线键不判缺口。装载失败（文件损坏）异常上抛（调用方=
        registry.acquire→executor.prepare 装载校验转 error；崩溃删
        文件重建路径归落盘层）。
        """
        with self._lock:
            if self._loaded:
                return
            self._loaded = True  # 先置幂等锚（装载期并发调用早退）
            path_exists = self._parquet_path.exists()
        if not path_exists:
            return
        frame = load_parquet(self._parquet_path)
        if frame is None or frame.empty:
            return
        enhanced = self._enhance(frame)  # 锁外（ATR 补充内部取锁）
        with self._lock:
            self._df = enhanced
            subscribers = list(self._subscriptions)
        for sub in subscribers:
            sub.on_frames(enhanced, 0)  # 初始帧到达（from_idx=0 全量）

    # ── WS 路径：闭合事件追加 ───────────────────────────────

    def on_closed_bar(self, event) -> None:
        """闭合 K 线事件追加（WS 路径——接入层回调线程调用）。

        追加行进内存序列 → 全帧指标（compute_all_indicators 共享单次）
        → 广播 (new_df, from_idx=追加前长度)。重复事件（open_time 已
        在内存）先到为准：忽略不广播（幂等消费锚）。内存无基线（冷
        启动键）以本事件建序列。
        """
        row = pd.DataFrame(
            {column: [getattr(event, column)]
             for column in OHLCV_COLUMNS},
            index=pd.DatetimeIndex([event.open_time], name="timestamp"))
        with self._lock:
            base = self._df
            self._loaded = True  # 事件路径建立基线（冷启动）
        merged, from_idx, new_rows = self._merge_ohlcv(base, row)
        if merged is None:
            return  # 重复事件（先到为准）——不广播
        self._publish(merged, from_idx, new_rows)

    # ── 对账路径：缺口补拉 ──────────────────────────────────

    def reconcile(self) -> None:
        """对账一轮（调度器守护循环驱动）：funding 节流 → 缺口判定 →
        REST 补拉 → 去重合并 → 广播 → 落盘对齐。

        无基线（内存与文件俱无）跳过——初始建库归启动建库线程；无
        缺口（末根+2×周期 > now）不广播不落盘。REST 异常上抛（调度
        器记日志续轮——对账三触发之守护循环节拍）。
        """
        self._refresh_funding_if_due()
        self.ensure_loaded()
        with self._lock:
            base = self._df
        if base is None or base.empty:
            return  # 无基线键——等启动建库线程建立
        interval = TF_SECONDS.get(self.timeframe, _DEFAULT_BAR_SECONDS)
        last_closed = base.index[-1]
        now = self._now_fn()
        if now < last_closed + pd.Timedelta(seconds=2 * interval):
            return  # 无缺口——下一根尚在形成（不广播不落盘）
        gap_start = last_closed + pd.Timedelta(seconds=interval)
        frame = self._rest_fetcher(
            self.symbol, self.timeframe, gap_start, now)
        if frame is None or frame.empty:
            return  # 对账无所得（端点暂无新数据）
        merged, from_idx, new_rows = self._merge_ohlcv(base, frame)
        if merged is None:
            return  # 补拉段全为重复（WS 已先到）——不广播
        self._publish(merged, from_idx, new_rows)

    # ── 内部：合并/指标/广播/落盘 ───────────────────────────

    def _merge_ohlcv(
        self, base: Optional[pd.DataFrame], incoming: pd.DataFrame,
    ) -> tuple[Optional[pd.DataFrame], int, pd.DataFrame]:
        """OHLCV 去重合并（内存先到为准）。

        Returns:
            (合并 OHLCV 帧, from_idx=追加前长度, 新行帧)；全为重复
            时 (None, 0, 空帧)——调用方据此不广播。
        """
        base_ohlcv = (
            base[OHLCV_COLUMNS] if base is not None and not base.empty
            else None)
        if base_ohlcv is None:
            merged = incoming.sort_index()
            from_idx = 0
        else:
            from_idx = len(base_ohlcv)
            combined = pd.concat([base_ohlcv, incoming])
            # 先到为准：同时间戳保留内存既有行（keep="first"——base 在前）
            combined = combined[~combined.index.duplicated(keep="first")]
            merged = combined.sort_index()
        new_rows = merged.iloc[from_idx:] if len(merged) > from_idx else \
            merged.iloc[0:0]
        if new_rows.empty:
            return None, 0, new_rows
        return merged, from_idx, new_rows

    def _publish(
        self, merged_ohlcv: pd.DataFrame,
        from_idx: int, new_rows: pd.DataFrame,
    ) -> None:
        """全帧指标 → 锁内替换引用 → 锁外广播 → 落盘对齐。"""
        enhanced = self._enhance(merged_ohlcv)
        with self._lock:
            self._df = enhanced  # 引用整体替换（旧快照消费方不受影响）
            subscribers = list(self._subscriptions)
        for sub in subscribers:
            sub.on_frames(enhanced, from_idx)
        self._persist(new_rows)

    def _persist(self, new_rows: pd.DataFrame) -> None:
        """落盘对齐（受控频率合并重写；分级触发面归落盘层扩展）。"""
        merge_parquet(new_rows, self._parquet_path)

    def _enhance(self, ohlcv: pd.DataFrame) -> pd.DataFrame:
        """全帧指标计算+ATR 并集补充（共享单次语义保持）。"""
        from core.features.indicators import compute_all_indicators

        enhanced = compute_all_indicators(ohlcv)
        return self._ensure_atr_columns(enhanced)

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

    def _ensure_atr_columns(self, df: pd.DataFrame) -> pd.DataFrame:
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
            computed = _compute_indicator(df, "ATR", {"period": period})
            if col in computed:
                df[col] = computed[col]
        return df

    # ── funding 索引面 ──────────────────────────────────────

    def funding_rates(
        self, points: Iterable[pd.Timestamp],
    ) -> dict[pd.Timestamp, Optional[float]]:
        """按 8h 网格点查费率（未达/不存在→None——挂起判定输入）。"""
        with self._lock:
            return {p: self._funding_index.get(p) for p in points}


def _default_funding_refresh(*, symbol, data_dir) -> pd.DataFrame:
    """缺省 funding 归档刷新（延迟 import——updater 形制签名兼容）。"""
    from core.data.updater import update_funding_data

    return update_funding_data(symbol=symbol, data_dir=data_dir)


class FeedRegistry:
    """per-(symbol, timeframe) feed 单例注册表（调度器/执行器的获取面）。

    acquire 建/取同键 feed 并挂订阅（新建后 ensure_loaded 从 parquet
    装载历史）；release 退订后订阅归零即摘除（停止拉取回收——无订阅
    者的数据作业不存活）。构造参数原样注入每个新建 feed（生产只传
    data_dir；测试注入合成 rest_fetcher/fixed 时钟）。
    """

    def __init__(self, data_dir: Path, **feed_kwargs):
        """初始化注册表。

        Args:
            data_dir: Parquet 数据目录。
            **feed_kwargs: SymbolFeed 构造注入面（rest_fetcher/
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
        """取同键 feed（无则建+装载历史）并挂订阅（同键任务共享一份）。"""
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
        feed.ensure_loaded()  # 锁外装载（读盘+指标计算不持注册表锁）
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
