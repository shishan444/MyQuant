"""fapi REST 兜底（binance SDK 唯一 REST 触点）。

职能：纸盘行情面的缺口补拉（klines）、规则拉取（exchangeInfo——本
模块仅建函数，MIN_NOTIONAL 装载消费归 change-4）与费率对账面
（fundingRate）。REST MUST NOT 承担主通道（market-data-feed spec
——闭合事件流是决策推进唯一触发源，本模块只做兜底三触发：缺口
补拉、启动对账、规则拉取）。服务对象：feed 对账例程（reconcile）、
app 启动建库线程、change-4 规则装载。

端点白名单（spec 写死单源）：klines / exchangeInfo / fundingRate
三族——触金扫描消费方法族白名单（kline/exchange_info/funding_rate/
mark_price）。环境变量面：FAPI_DATA_DIR / FAPI_PROXY / FAPI_WS_BASE /
FAPI_REST_BASE / FAPI_MIN_NOTIONAL_*（装配与消费点经 fapi_env 取值，
本模块不读环境变量——环境解析单点供测试注入旁路）。

代理语义沿 fetcher.py:108 先例：SDK 内部 requests.Session 构造后
置 trust_env=False（阻断 HTTP(S)_PROXY 环境变量泄漏）；显式 proxies
（FAPI_PROXY 配置时）不受 trust_env 影响——直连/代理双通道语义独立。
"""
from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any, Callable, Optional

import pandas as pd

from core.data.fapi.stream import is_valid_bar_values
from core.data.timeframes import TF_SECONDS

logger = logging.getLogger(__name__)

# ── 端点与方法族白名单（spec 写死；触金扫描常驻测试消费）────────
FAPI_REST_ENDPOINTS: tuple[str, ...] = ("klines", "exchangeInfo", "fundingRate")
FAPI_ALLOWED_METHOD_FAMILIES: tuple[str, ...] = (
    "kline", "exchange_info", "funding_rate", "mark_price",
)

# ── 环境变量面（部署操作面；装配/消费点经 fapi_env 单点解析）────
ENV_FAPI_DATA_DIR = "FAPI_DATA_DIR"
ENV_FAPI_PROXY = "FAPI_PROXY"
ENV_FAPI_WS_BASE = "FAPI_WS_BASE"
ENV_FAPI_REST_BASE = "FAPI_REST_BASE"

FAPI_REST_BASE_DEFAULT = "https://fapi.binance.com"
FAPI_WS_BASE_DEFAULT = "wss://fstream.binance.com"
FAPI_DATA_DIR_DEFAULT = "data/market_fapi"

# fapi klines 单页上限（端点硬限；分页补拉的步进参数）
KLINE_PAGE_LIMIT = 1500

# 运行时增量补拉单轮上限（R-03——feed 对账与数据保鲜守护器两消费方
# import 同一常量不两写）：单轮从缺口区间起点取至多 N 根，调用方逐轮
# 推进向尾部收敛；启动建库/损坏重建的全量回补不受此限（豁免面）。
RUNTIME_BACKFILL_MAX_BARS = 10

# REST 超时（秒）——兜底通道可容忍慢，不可容忍无限挂起
REST_TIMEOUT_SECONDS = 30


def fapi_env(env: Optional[dict[str, str]] = None) -> dict[str, Any]:
    """FAPI_* 环境变量面解析单点（缺省值与覆写语义的测试注入面）。

    Args:
        env: 环境变量字典（缺省 os.environ——测试注入构造字典旁路）。

    Returns:
        四键：data_dir（Path，缺省 data/market_fapi）、proxy（str|None，
        缺省 None=直连）、ws_base（str）、rest_base（str）。
    """
    source = os.environ if env is None else env
    data_dir = Path(source.get(ENV_FAPI_DATA_DIR, FAPI_DATA_DIR_DEFAULT))
    proxy = source.get(ENV_FAPI_PROXY) or None
    return {
        "data_dir": data_dir,
        "proxy": proxy,
        "ws_base": source.get(ENV_FAPI_WS_BASE, FAPI_WS_BASE_DEFAULT),
        "rest_base": source.get(ENV_FAPI_REST_BASE, FAPI_REST_BASE_DEFAULT),
    }


def fapi_min_notional_overrides(
    env: Optional[dict[str, str]] = None,
) -> dict[str, float]:
    """FAPI_MIN_NOTIONAL_<SYMBOL> 覆写解析（change-4 装载的 env 源）。

    覆写值>内置常量>端点拉取的优先级裁定归 change-4 装载链；本函数
    只解析显式覆写面（未设置/空串的键不入返回——缺省语义由消费方
    回退链承载）。畸形值（不可解析/NaN/inf/≤0）warning 丢弃该键
    （C4-01：回退链兜底——env 是部署侧人工输入面，直穿将使覆写
    静默失效〔NaN 比较 False 枯竭兜底失效〕或装载期崩溃〔abc 值
    ValueError 崩启动〕，丢弃回退优先于带病装载）。
    """
    import math

    source = os.environ if env is None else env
    from core.data.fapi.stream import FAPI_SYMBOLS

    overrides: dict[str, float] = {}
    for symbol in FAPI_SYMBOLS:
        raw = source.get(f"FAPI_MIN_NOTIONAL_{symbol}")
        if not raw:
            continue
        try:
            value = float(raw)
        except ValueError:
            logger.warning(
                "FAPI_MIN_NOTIONAL_%s=%r 不可解析——丢弃该覆写回退"
                "装载链下一来源", symbol, raw)
            continue
        if not math.isfinite(value) or value <= 0:
            logger.warning(
                "FAPI_MIN_NOTIONAL_%s=%r 非有限正值——丢弃该覆写回退"
                "装载链下一来源", symbol, raw)
            continue
        overrides[symbol] = value
    return overrides


def _ts_to_ms(value) -> int:
    """时刻转毫秒时间戳（pd.Timestamp/ISO 串兼容；feed 对账输入泛化）。"""
    ts = value if isinstance(value, pd.Timestamp) else pd.Timestamp(value)
    if ts.tz is None:
        ts = ts.tz_localize("UTC")
    return int(ts.timestamp() * 1000)


def _empty_ohlcv_frame() -> pd.DataFrame:
    """空 OHLCV 帧（与 feed 内存序列同构——空区间返回空帧非 None）。"""
    return pd.DataFrame(
        columns=["open", "high", "low", "close", "volume"],
        index=pd.DatetimeIndex([], name="timestamp", tz="UTC"),
    )


def _klines_rows_to_frame(rows: list) -> pd.DataFrame:
    """klines 端点行列表转帧（index=开盘时刻 UTC；五列浮点）。

    行形制锚（fapi klines 端点）：[openTime, open, high, low, close,
    volume, closeTime, ...]——前六位消费，其余（成交笔数/报价量等）
    不入决策面。

    脏行单点过滤（批次三 CARD-01）：OHLC/volume 任一非有限值
    （NaN/inf）或字段不可解析的行丢弃——与 WS 通道 is_valid_
    bar_values 同源同语义（两入口脏数据一套判定），不经判定的
    float 直转会把关行静默入帧，污染对账/建库/重建/费率四消费
    路径（直转面同修 fetch_funding_rate）。
    """
    if not rows:
        return _empty_ohlcv_frame()
    parsed: list[tuple[int, tuple[float, ...]]] = []
    for row in rows:
        try:
            values = (float(row[1]), float(row[2]), float(row[3]),
                      float(row[4]), float(row[5]))
        except (TypeError, ValueError, IndexError):
            logger.warning(
                "fapi REST klines 行字段异常（丢弃）：openTime=%r",
                row[0] if row else None)
            continue
        if not is_valid_bar_values(*values):
            logger.warning(
                "fapi REST 脏数据丢弃（OHLC/volume 非有限值）："
                "openTime=%r", row[0])
            continue
        parsed.append((int(row[0]), values))
    if not parsed:
        # 全脏滤空与 rows 空早退同构（含 index tz=UTC——naive 空索引
        # 会在 fetch_closed_bars 的 tz-aware 边界比较上 TypeError）
        return _empty_ohlcv_frame()
    frame = pd.DataFrame(
        {
            "open": [v[0] for _, v in parsed],
            "high": [v[1] for _, v in parsed],
            "low": [v[2] for _, v in parsed],
            "close": [v[3] for _, v in parsed],
            "volume": [v[4] for _, v in parsed],
        },
        index=pd.DatetimeIndex(
            [pd.Timestamp(ms, unit="ms", tz="UTC") for ms, _ in parsed],
            name="timestamp"),
    )
    # 端点按 openTime 升序返回——排序去重是端点契约外的本地不变式
    frame = frame[~frame.index.duplicated(keep="last")]
    return frame.sort_index()


class FapiRestClient:
    """fapi REST 兜底客户端（三函数+显式代理语义+注入友好）。

    三函数错误语义：网络异常上抛（调用方=feed 对账例程捕获记日志
    续轮——本层不吞）；空区间返回空帧非 None。全签名注入友好——
    构造注入 client_factory 替身（ScriptedRestClient 同形鸭子面）。
    """

    def __init__(
        self,
        *,
        base_url: Optional[str] = None,
        proxies: Optional[dict] = None,
        timeout: int = REST_TIMEOUT_SECONDS,
        client_factory: Optional[Callable[..., Any]] = None,
    ):
        """初始化客户端。

        Args:
            base_url: REST 端点基址（缺省 FAPI_REST_BASE_DEFAULT——
                装配层经 fapi_env 解析后传入）。
            proxies: 显式代理（{"https": "..."} 形制；None=直连）。
            timeout: 请求超时秒数。
            client_factory: SDK 客户端工厂（缺省真实 UMFutures——
                延迟 import 保持模块顶层零 SDK 依赖面；测试注入替身）。
        """
        self._base_url = base_url or FAPI_REST_BASE_DEFAULT
        self._proxies = proxies
        self._timeout = timeout
        self._client_factory = client_factory or self._default_client_factory
        self._client: Any = None

    @staticmethod
    def _default_client_factory(**kwargs):
        """真实 SDK REST 客户端工厂（延迟 import——import 白名单锚）。"""
        from binance.um_futures import UMFutures

        return UMFutures(**kwargs)

    def _sdk(self) -> Any:
        """SDK 客户端惰性单例（首调用建连）。"""
        if self._client is None:
            self._client = self._client_factory(
                base_url=self._base_url,
                proxies=self._proxies,
                timeout=self._timeout,
            )
            session = getattr(self._client, "session", None)
            if session is not None:
                # 直连通道阻断环境代理泄漏（fetcher.py:108 先例）；
                # 显式 proxies 不受 trust_env 影响——双通道独立
                session.trust_env = False
        return self._client

    def fetch_closed_bars(
        self,
        symbol: str,
        timeframe: str,
        start_ts,
        end_ts,
        *,
        max_bars: Optional[int] = None,
    ) -> pd.DataFrame:
        """按时刻区间补拉闭合 K 线（klines；端点白名单面）。

        Args:
            symbol: 交易对（白名单两币种）。
            timeframe: K 线周期。
            start_ts: 区间起点（pd.Timestamp/ISO 串兼容，含）。
            end_ts: 区间终点（不含——半开区间语义与数据目录一致，
                本地过滤兜底端点含端边界）。
            max_bars: 运行时增量窗口上限（None=分页补拉全区间——
                建库/损坏重建语义；N=单轮窗口，**start 锚定**——请求
                区间起点不变〔缺口起点〕、limit=min(N, KLINE_PAGE_
                LIMIT)、从起点起取至多 N 根，调用方逐轮推进向尾部
                收敛。禁用 end 锚定〔起点裁剪为 end−N×周期的形制
                会使深缺口首轮补尾后新鲜判定直接通过、中段空洞永久
                残留——缺口尾部暂缺不阻塞已补齐前缀段的消费〕。
                缺省 None 保持既有分页行为不变）。

        Returns:
            闭合 bar 帧（index=开盘时刻 UTC、五列），与 feed 内存
            序列同构；空区间空帧非 None。窗口路径实际返回可少于 N
            （尾部 forming bar 被闭合过滤）。

        Raises:
            ValueError: max_bars 非 None 时须为正整数（int；bool 是
                int 子类显式排除）——负/零/NaN/inf/float 一律入口拒绝，
                不使畸形值直达端点或在 int() 处非受控抛出。
            网络异常原样上抛（调用方捕获记日志续轮）。
        """
        if max_bars is not None and (
            isinstance(max_bars, bool)
            or not isinstance(max_bars, int)
            or max_bars <= 0
        ):
            raise ValueError(
                f"max_bars 须为正整数（int、非 bool），当前: {max_bars!r}")
        start_ms = _ts_to_ms(start_ts)
        end_ms = _ts_to_ms(end_ts)
        collected: list = []
        cursor = start_ms
        if max_bars is not None:
            # 单轮窗口（start 锚定）：单页请求，起点即缺口起点，至多
            # N 根——多轮由调用方推进（feed 对账 20s 主通道/守护器
            # 300s 慢通道双通道收敛）
            if cursor >= end_ms:
                frame = _klines_rows_to_frame([])
            else:
                rows = self._sdk().klines(
                    symbol=symbol,
                    interval=timeframe,
                    startTime=cursor,
                    endTime=end_ms - 1,
                    limit=min(int(max_bars), KLINE_PAGE_LIMIT),
                )
                frame = _klines_rows_to_frame(rows)
        else:
            while cursor < end_ms:
                rows = self._sdk().klines(
                    symbol=symbol,
                    interval=timeframe,
                    startTime=cursor,
                    endTime=end_ms - 1,
                    limit=KLINE_PAGE_LIMIT,
                )
                if not rows:
                    break  # 区间内无可得数据（尾部推进或端点缺档）
                collected.extend(rows)
                next_cursor = int(rows[-1][0]) + 1
                if next_cursor <= cursor:
                    break  # 端点异常自锁防御（cursor 不前进即终止）
                cursor = next_cursor
            frame = _klines_rows_to_frame(collected)
        end_bound = pd.Timestamp(end_ms, unit="ms", tz="UTC")
        # 闭合过滤蕴含半开边界（openTime+tf<=end 蕴含 openTime<end）：
        # 端点 klines 含当前未闭合 bar（无 x 字段），本地按周期时长判闭合
        tf_seconds = TF_SECONDS.get(timeframe, 60)
        return frame[
            frame.index + pd.Timedelta(seconds=tf_seconds) <= end_bound]

    def fetch_exchange_info(self) -> dict:
        """拉取交易规则（exchangeInfo——filters 提取归 change-4 消费）。

        Raises:
            网络异常原样上抛（调用方回退内置常量）。
        """
        return self._sdk().exchange_info()

    def fetch_funding_rate(self, symbol: str, start_ts, end_ts) -> pd.DataFrame:
        """按区间拉取资金费率历史（fundingRate 端点——对账披露面）。

        funding 结算主链仍走 S3 归档（update_funding_data）；本函数
        只承担 fapi 面对账（验收 1）。

        Returns:
            帧 index=结算时刻（UTC 8h 网格）、列 funding_rate——与
            {symbol}_funding.parquet 归档同构；空区间空帧。
        """
        rows = self._sdk().funding_rate(
            symbol=symbol,
            startTime=_ts_to_ms(start_ts),
            endTime=_ts_to_ms(end_ts),
        )
        if not rows:
            return pd.DataFrame(
                {"funding_rate": pd.Series(dtype=float)},
                index=pd.DatetimeIndex([], name="timestamp", tz="UTC"),
            )
        # 脏行过滤（CARD-01 直转面同修）：费率值不可解析或非有限
        # （NaN/inf）的行丢弃——与 klines 行同语义（对账面不吞坏点）
        parsed: list[tuple[int, float]] = []
        for row in rows:
            try:
                rate = float(row["fundingRate"])
                ms = int(row["fundingTime"])
            except (KeyError, TypeError, ValueError):
                logger.warning(
                    "fapi REST fundingRate 行字段异常（丢弃）：%r", row)
                continue
            if not is_valid_bar_values(rate):
                logger.warning(
                    "fapi REST 脏数据丢弃（fundingRate 非有限值）："
                    "fundingTime=%r", row.get("fundingTime"))
                continue
            parsed.append((ms, rate))
        return pd.DataFrame(
            {"funding_rate": [rate for _, rate in parsed]},
            index=pd.DatetimeIndex(
                [pd.Timestamp(ms, unit="ms", tz="UTC") for ms, _ in parsed],
                name="timestamp"),
        )
