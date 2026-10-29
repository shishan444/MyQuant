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

import logging
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable, Optional

import pandas as pd

from core.data.storage import load_parquet, merge_closed_rows
from core.data.timeframes import DEFAULT_TF_FALLBACK_SECONDS, TF_SECONDS

logger = logging.getLogger(__name__)

# funding 归档刷新节流间隔（秒）——design §6.3「每小时节流刷新」
FUNDING_REFRESH_INTERVAL_SECONDS = 3600.0

# 8h 结算网格（Binance 永续资金费结算节奏：UTC 00/08/16 点）
_FUNDING_GRID = "8h"

# OHLCV 基础列（WS 事件行与 REST 补拉帧的共同锚；指标列为派生面）
OHLCV_COLUMNS = ["open", "high", "low", "close", "volume"]

# ── 分级落盘参数（design §2.3 受控频率合并重写+分级触发）─────
# 低频周期：闭合即写；高频周期缓冲按间隔 flush（spec 锁语义不锁数值）
LOW_FREQ_TIMEFRAMES = frozenset({"1h", "4h", "1d"})
HIGH_FREQ_FLUSH_INTERVAL_SECONDS = 60.0
# 崩溃损坏重建的回溯窗口（与启动建库 history_years 同源语义）
REBUILD_HISTORY_YEARS = 2


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


def _default_rest_fetcher(
    symbol, timeframe, start_ts, end_ts, *,
    max_bars: Optional[int] = None,
) -> pd.DataFrame:
    """缺省 REST 兜底（fapi 接入层——延迟构造，feed 永不触碰 SDK 类型）。

    max_bars 透传（R-03）：对账路径由调用方（_reconcile_round）显式
    传窗口上限；建库/损坏重建（backfill_key/_rebuild_from_rest）不传
    ——None=分页全区间（豁免面：启动建库的全量回补不受运行时上限
    约束）。
    """
    from core.data.fapi.rest import FapiRestClient

    return FapiRestClient().fetch_closed_bars(
        symbol, timeframe, start_ts, end_ts, max_bars=max_bars)


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
        self._pending_rows: Optional[pd.DataFrame] = None  # 高频缓冲未落行
        self._last_flush: Optional[pd.Timestamp] = None
        self._reconcile_last_run: Optional[pd.Timestamp] = None
        self._reconcile_last_result: Optional[str] = None
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
        try:
            frame = load_parquet(self._parquet_path)
        except Exception:
            # 崩溃损坏窗口=重写瞬间：footer 缺失/半写文件加载失败——
            # 删除该文件并 REST 全量重建（design §2.3 恢复路径；重建
            # 失败转无产物由启动建库线程兜底）
            logger.warning(
                "feed parquet 损坏——删除并 REST 重建：%s", self._parquet_path)
            self._parquet_path.unlink(missing_ok=True)
            frame = self._rebuild_from_rest()
            if frame is not None and not frame.empty:
                from core.data.storage import save_parquet

                save_parquet(frame, self._parquet_path)
        if frame is None or frame.empty:
            return
        enhanced = self._enhance(frame)  # 锁外（ATR 补充内部取锁）
        with self._lock:
            self._df = enhanced
            subscribers = list(self._subscriptions)
        for sub in subscribers:
            sub.on_frames(enhanced, 0)  # 初始帧到达（from_idx=0 全量）

    def _rebuild_from_rest(self) -> Optional[pd.DataFrame]:
        """REST 全量重建（损坏自愈——回溯窗口与建库同源语义）。"""
        now = self._now_fn()
        start = now - pd.DateOffset(years=REBUILD_HISTORY_YEARS)
        try:
            return self._rest_fetcher(self.symbol, self.timeframe, start, now)
        except Exception:
            logger.exception(
                "feed REST 重建失败（键转无产物——建库线程兜底）：%s_%s",
                self.symbol, self.timeframe)
            return None

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
        缺口（末根+2×周期 > now）不广播不落盘。REST 异常记 failed 后
        上抛（调度器记日志续轮——对账三触发之守护循环节拍）。
        """
        try:
            outcome = self._reconcile_round()
        except Exception:
            with self._lock:
                self._reconcile_last_run = self._now_fn()
                self._reconcile_last_result = "failed"
            raise
        with self._lock:
            self._reconcile_last_run = self._now_fn()
            self._reconcile_last_result = outcome

    def _reconcile_round(self) -> str:
        """对账主体（返回 last_result 三值：ok | gap_filled:N | failed）。"""
        self._refresh_funding_if_due()
        self.ensure_loaded()
        with self._lock:
            base = self._df
        if base is None or base.empty:
            return "ok"  # 无基线键——等启动建库线程建立
        interval = TF_SECONDS.get(self.timeframe, DEFAULT_TF_FALLBACK_SECONDS)
        last_closed = base.index[-1]
        now = self._now_fn()
        if now < last_closed + pd.Timedelta(seconds=2 * interval):
            return "ok"  # 无缺口——下一根尚在形成（不广播不落盘）
        gap_start = last_closed + pd.Timedelta(seconds=interval)
        # 运行时增量窗口（R-03）：单轮从缺口起点取至多 N 根（start
        # 锚定——多轮由对账节拍推进收敛）；建库/重建路径不经此处
        from core.data.fapi.rest import RUNTIME_BACKFILL_MAX_BARS

        frame = self._rest_fetcher(
            self.symbol, self.timeframe, gap_start, now,
            max_bars=RUNTIME_BACKFILL_MAX_BARS)
        if frame is None or frame.empty:
            return "ok"  # 对账无所得（端点暂无新数据）
        # forming 防御滤（REST 契约已闭合——本滤为落盘不变式的纵深：
        # 未闭合行不入内存不入盘）
        frame = frame[
            frame.index + pd.Timedelta(seconds=interval) <= now]
        merged, from_idx, new_rows = self._merge_ohlcv(base, frame)
        if merged is None:
            return "ok"  # 补拉段全为重复（WS 已先到）——不广播
        self._publish(merged, from_idx, new_rows)
        return f"gap_filled:{len(new_rows)}"

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
        # 死分支收敛（批次三 CARD-14）：keep="first" 下 base 行全保留
        # → len(merged)>=from_idx 恒成立，三元 else 不可达；len 相等时
        # iloc[from_idx:] 即空帧
        new_rows = merged.iloc[from_idx:]
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
        """分级落盘（受控频率合并重写+分级触发——design §2.3）。

        低频周期（1h/4h/1d）闭合即写；高频周期（1m/5m/15m）缓冲行
        累积、按 60s 检查点 flush（检查点=每次落盘请求时刻——高频
        事件到达与守护循环对账节拍天然构成检查源）。三不变式（forming
        不落库/已落行不改写/去重不重行）由上游闭合源+本层 keep-first
        合并守护。
        """
        if self.timeframe in LOW_FREQ_TIMEFRAMES:
            self._write_closed_rows(new_rows)
            return
        self._buffer_rows(new_rows)
        self._maybe_flush()

    def _buffer_rows(self, new_rows: pd.DataFrame) -> None:
        """高频缓冲累积（先到为准去重——缓冲内不重行）。"""
        with self._lock:
            if self._pending_rows is None:
                self._pending_rows = new_rows
            else:
                combined = pd.concat([self._pending_rows, new_rows])
                combined = combined[
                    ~combined.index.duplicated(keep="first")]
                self._pending_rows = combined.sort_index()

    def _maybe_flush(self) -> None:
        """60s 检查点判定（now_fn 注入驱动——测试旁路真实等待）。"""
        now = self._now_fn()
        last = self._last_flush
        due = (
            last is None
            or (now - last).total_seconds()
            >= HIGH_FREQ_FLUSH_INTERVAL_SECONDS)
        if due:
            self.flush_pending()

    def flush_pending(self) -> None:
        """清空缓冲落盘（停机路径/SIGTERM 关停序/60s 检查点；幂等）。"""
        with self._lock:
            pending = self._pending_rows
            self._pending_rows = None
            self._last_flush = self._now_fn()
        if pending is not None and not pending.empty:
            self._write_closed_rows(pending)

    def _write_closed_rows(self, new_rows: pd.DataFrame) -> None:
        """闭合行合并落盘（既有行优先——已落行内容永不改写+去重不重行）。"""
        merge_closed_rows(self._parquet_path, new_rows)

    # ── 健康面（runner-status 聚合输入）─────────────────────

    @property
    def pending_rows_count(self) -> int:
        """高频缓冲未落行数（flush_buffer 组输入；无缓冲 0）。"""
        with self._lock:
            return 0 if self._pending_rows is None else len(self._pending_rows)

    @property
    def reconcile_state(self) -> tuple[Optional[pd.Timestamp], Optional[str]]:
        """最近一轮对账状态（last_run 时刻, last_result 三值）。"""
        with self._lock:
            return self._reconcile_last_run, self._reconcile_last_result

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
            # 无基线显式空（外层三元承载「首次装载不广播」结构语义
            # ——生成器内只判覆盖点；双重判空收敛为单层，CARD-14）
            newly_covered = sorted(
                p for p in new_index if p > old_cover
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
        # 健康聚合挂点（装配层注入——scheduler.status 经此读 stream 与
        # 建库状态；缺省 None=该面未启动的 K2 缺省语义）
        self.fapi_stream: Optional[object] = None
        self.backfill: Optional["FixedKeyBackfill"] = None
        # 保鲜守护器挂点（add-data-freshness-guardian 契约 6：属性
        # 引用非模块依赖——registry 不 import guardian 类型，scheduler
        # 经 getattr 聚合健康面）
        self.guardian: Optional[object] = None

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

    def flush_all(self) -> None:
        """全部活跃 feed 清空缓冲落盘（进程正常退出/SIGTERM 关停序——
        装配层关停时调用；feed 侧幂等）。"""
        for key in self.active_keys:
            feed = self.get(*key)
            if feed is None:
                continue
            try:
                feed.flush_pending()
            except Exception:
                logger.exception("feed 缓冲清空失败（关停续行）：%s", key)


class FixedKeyBackfill:
    """固定订阅 12 键初始建库（app lifespan 后台线程）。

    逐键执行：无文件→REST 全量回补（回溯窗口与重建同源语义 2 年）
    落盘；有文件→尾戳对账补齐（幂等——按尾戳增量续传，崩溃重启
    续跑）。失败键记 failed 并在下一轮重试；全键 done 后线程退出。
    状态面（runner-status backfill 组）：{"SYMBOL_TF": "running"|
    "done"|"failed"}——未启动键不在字典。

    异步不阻塞 app 启动；装载层 no_data 拒绝语义零改动（12 路内键
    建库完成后数据必然就绪，窗口期约束=首次部署经健康披露确认建库
    完成后再开始挂载——操作细节归后续部署文档）。
    """

    POLL_INTERVAL_SECONDS = 60.0

    def __init__(
        self,
        data_dir: Path,
        *,
        rest_fetcher: Optional[Callable[..., pd.DataFrame]] = None,
        now_fn: Optional[Callable[[], pd.Timestamp]] = None,
    ):
        from core.data.fapi.stream import FIXED_SUBSCRIBE_KEYS

        self.data_dir = Path(data_dir)
        self._rest = rest_fetcher or _default_rest_fetcher
        self._now_fn = now_fn or (lambda: pd.Timestamp.now(tz="UTC"))
        self._keys = sorted(FIXED_SUBSCRIBE_KEYS)
        self._status: dict[str, str] = {}
        self._lock = threading.Lock()
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None

    # ── 状态面 ──────────────────────────────────────────────

    def status(self) -> dict[str, str]:
        """建库状态快照（backfill 健康组输入）。"""
        with self._lock:
            return dict(self._status)

    # ── 执行面 ──────────────────────────────────────────────

    def run_once(self) -> None:
        """单轮全键扫描（done 跳过/failed 重试/未跑开跑）。"""
        for symbol, timeframe in self._keys:
            key = f"{symbol}_{timeframe}"
            with self._lock:
                if self._status.get(key) == "done":
                    continue
                self._status[key] = "running"
            try:
                backfill_key(
                    self.data_dir, symbol, timeframe,
                    rest_fetcher=self._rest, now_fn=self._now_fn)
            except Exception:
                logger.exception(
                    "建库失败（下一轮重试）：%s", key)
                with self._lock:
                    self._status[key] = "failed"
            else:
                with self._lock:
                    self._status[key] = "done"

    def start_background(self) -> None:
        """起后台建库线程（轮询至全键 done 或 stop）。"""
        if self._thread is not None:
            return
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._background_loop, name="fapi-backfill", daemon=True)
        self._thread.start()

    def _background_loop(self) -> None:
        """轮询循环：单轮扫描→未全 done 等待重试→全 done 退出。"""
        while not self._stop_event.is_set():
            self.run_once()
            with self._lock:
                all_done = all(
                    value == "done" for value in self._status.values())
            if all_done:
                logger.info("fapi 建库全键完成（线程退出）")
                return
            self._stop_event.wait(self.POLL_INTERVAL_SECONDS)

    def stop(self) -> None:
        """协作停止（app 关停序——超时未退记 warning 监测面）。

        join 超时后线程仍存活（REST 调用阻塞超脱 stop_event 协作
        语义）只记日志不强杀——彻底消除（可中断的 REST 装载面）归
        批次三 CARD-08 不修档。
        """
        self._stop_event.set()
        thread = self._thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=10.0)
            if thread.is_alive():
                logger.warning(
                    "fapi 建库线程 stop 超时未退（REST 阻塞超脱协作"
                    "语义——监测面留痕，不强制回收）")


def backfill_key(
    data_dir: Path,
    symbol: str,
    timeframe: str,
    *,
    rest_fetcher: Callable[..., pd.DataFrame],
    now_fn: Callable[[], pd.Timestamp],
    history_years: int = REBUILD_HISTORY_YEARS,
) -> None:
    """单键建库（无文件全量回补/有文件尾戳补齐——merge_closed_rows 落盘）。

    done=「数据就绪」单义（批次三 CARD-03 a 案）：非空文件+尾戳新鲜，
    或增量成功落盘。REST 异常与空响应/空文件/增量空帧三空路径一律
    上抛（调用方 run_once 记 failed 下轮重试——空态视为暂时性：
    固定 12 键=白名单两币种×6 周期，两年窗必有数据，「端点确认无
    数据」在现行键集不可达；若 FIXED_SUBSCRIBE_KEYS 扩容（新币种
    接入属另立需求），扩容 change 须重估本空态语义）。
    """
    path = Path(data_dir) / f"{symbol}_{timeframe}.parquet"
    now = now_fn()
    if not path.exists():
        frame = rest_fetcher(
            symbol, timeframe,
            now - pd.DateOffset(years=history_years), now)
        if frame is None or frame.empty:
            raise RuntimeError(
                f"建库未就绪（无文件且端点空响应）：{symbol}_{timeframe}"
                "——视为暂时性空响应，记 failed 下轮重试")
        from core.data.storage import save_parquet

        save_parquet(frame, path)
        return
    existing = load_parquet(path)
    if existing is None or existing.empty:
        raise RuntimeError(
            f"建库未就绪（文件空）：{symbol}_{timeframe}"
            "——空文件非数据就绪，记 failed 下轮重试")
    interval = TF_SECONDS.get(timeframe, DEFAULT_TF_FALLBACK_SECONDS)
    last = existing.index[-1]
    if now < last + pd.Timedelta(seconds=2 * interval):
        return  # 尾戳已新鲜——数据就绪
    frame = rest_fetcher(symbol, timeframe,
                         last + pd.Timedelta(seconds=interval), now)
    if frame is None or frame.empty:
        # 单义推论：缺口成立（now>=last+2×周期）而增量空响应——既非
        # 尾戳新鲜亦非增量落盘，数据未就绪，与无文件空响应同语义
        raise RuntimeError(
            f"建库未就绪（缺口区间空响应）：{symbol}_{timeframe}"
            "——记 failed 下轮重试")
    merge_closed_rows(path, frame)
