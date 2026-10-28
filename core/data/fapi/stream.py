"""fapi USDT-M 永续行情流接入层（WS 组合流——binance SDK 唯一 WS 触点）。

职能：把 binance-futures-connector 的 UMFutures 组合流包装为固定订阅、
自动重连的闭合 K 线事件源——闭合事件是纸盘决策推进的唯一触发源，
forming bar 不投递（闭合判定驻本层）。服务对象：app 装配层的事件
路由（feed 实例按需消费）与调度器对账触发；数据作业与执行器永不
import 本模块依赖的 SDK（依赖方向硬约束：binance.um_futures /
binance.websocket 子树全工程 import 点仅 stream.py 与 rest.py）。

固定订阅终态（用户裁决 2026-10-28）：连接建立即全量订阅 12 路常量
清单，不随任务增退变化——无动态订阅面（数据面恒定/决策面按需的
路由丢弃归装配层）。

实施期首验记录（2026-10-28，binance-futures-connector 4.2.0）：
组合流订阅经 is_combined=True + subscribe(list) 达成；构造即阻塞
建连并起读线程，断线无自动重连（退避重连由本层自持）、on_message
回调签名 (manager, data_str) 且 data 为未解析 JSON 字符串、
on_open 在构造期内触发（订阅经 manager 原始 send_message 面发送）。
"""
from __future__ import annotations

import json
import logging
import math
import threading
import time
from dataclasses import dataclass
from typing import Callable, Optional, Protocol, runtime_checkable

import pandas as pd

logger = logging.getLogger(__name__)

# ── 固定订阅清单（终审 Q1 固定制；清单常量单源——挂载校验、扫描
#    断言、启动建库均引用此处，不另写第二份）─────────────────────
FAPI_SYMBOLS: tuple[str, ...] = ("BTCUSDT", "ETHUSDT")
FAPI_TIMEFRAMES: tuple[str, ...] = ("1m", "5m", "15m", "1h", "4h", "1d")
FIXED_SUBSCRIBE_KEYS: frozenset[tuple[str, str]] = frozenset(
    (symbol, timeframe)
    for symbol in FAPI_SYMBOLS
    for timeframe in FAPI_TIMEFRAMES
)  # 12 路 = 2 币种 × 6 周期；超集周期（30m/3d 等）不订阅

# 静默窗口基数（秒）——窗口 = bar 时长 × 2 + 此基数
SILENT_WINDOW_BASE_SECONDS = 60.0


def subscribe_stream_name(symbol: str, timeframe: str) -> str:
    """组合流订阅名（<symbol>@kline_<timeframe> 小写形制）。"""
    return f"{symbol.lower()}@kline_{timeframe}"


FIXED_SUBSCRIBE_STREAMS: list[str] = sorted(
    subscribe_stream_name(symbol, timeframe)
    for symbol, timeframe in FIXED_SUBSCRIBE_KEYS
)


def silent_window_seconds(bar_seconds: int) -> float:
    """静默判定窗口 = 该周期 bar 时长 × 2 + 60s（spec 锁语义不锁数值）。

    bar 秒数由调用方按自身周期表传入（fapi 模块不依赖 data.timeframes
    ——接入层零业务周期知识，参数化保持单向依赖）。
    """
    return bar_seconds * 2 + SILENT_WINDOW_BASE_SECONDS


# ── 重连参数（模块 constants 单源；spec 锁语义不锁数值）─────────
RECONNECT_BACKOFF_INITIAL_SECONDS = 1.0
RECONNECT_BACKOFF_CAP_SECONDS = 60.0


@dataclass(frozen=True)
class ClosedBarEvent:
    """闭合 K 线事件（forming 不投递——closed 恒 True）。

    Attributes:
        symbol: 交易对（如 "BTCUSDT"）。
        timeframe: K 线周期（FIXED_SUBSCRIBE_KEYS 值域）。
        open_time: bar 开盘时刻（行索引锚，UTC）。
        open/high/low/close: OHLC。
        volume: 成交量。
        closed: 恒 True——本回调只投递闭合事件。
    """

    symbol: str
    timeframe: str
    open_time: pd.Timestamp
    open: float
    high: float
    low: float
    close: float
    volume: float
    closed: bool = True


@dataclass(frozen=True)
class ConnEvent:
    """连接状态事件（接入层健康面输入）。

    Attributes:
        state: "connected" | "disconnected" | "reconnected"。
        detail: 诊断文本（脱敏——不含凭据）。
    """

    state: str
    detail: str


def is_valid_bar_values(*values: float) -> bool:
    """OHLC/volume 有效性判定单点（NaN/inf 任一命中即脏数据丢弃）。"""
    return all(math.isfinite(v) for v in values)


@runtime_checkable
class FapiStreamProtocol(Protocol):
    """行情流协议（生产/测试双实现——feed/调度器/装配依赖此抽象）。

    消费方经本协议消费事件流与连接事件，永不触碰 SDK 类型；测试以
    injection.py 替身实现同一协议达成合成驱动。
    """

    on_closed_bar: Callable[[ClosedBarEvent], None]
    on_connection: Callable[[ConnEvent], None]

    def start(self) -> None:
        """建立连接并订阅 FIXED_SUBSCRIBE_KEYS 全集。幂等。"""
        ...

    def stop(self) -> None:
        """协作关闭。"""
        ...


class BinanceFapiStream:
    """UMFutures 组合流生产实现（单连接、固定订阅、自持重连）。

    线程模型：start() 起单守护线程跑生命周期循环（构造客户端〔SDK
    构造即阻塞建连并在构造期内触发 on_open——固定订阅在该回调经
    manager 原始 send_message 面发送〕→ 泊等断线 → 指数退避
    1s/2s/4s/…封顶 60s 无限重试 → 重连）。连接成功存活后退避重置；
    首连投 connected、重连投 reconnected、断线投 disconnected。

    错误语义：本层吞一切网络异常（记日志+转事件），永不向
    on_closed_bar 抛异常；回调内异常由本层捕获记日志（单订阅方
    故障不拖垮流）。脏数据（OHLC/volume 任一 NaN/inf）丢弃不投递；
    重复闭合事件照常投递（幂等消费归 feed 判重兜底）。

    健康面：last_push 按键记录最近推送时刻（含 forming 推送——
    静默=无推送非无闭合），silent_seconds 供 runner-status 披露与
    调度器静默判定消费。
    """

    def __init__(
        self,
        on_closed_bar: Callable[[ClosedBarEvent], None],
        on_connection: Optional[Callable[[ConnEvent], None]] = None,
        *,
        ws_base_url: str = "wss://fstream.binance.com",
        proxies: Optional[dict] = None,
        now_fn: Optional[Callable[[], pd.Timestamp]] = None,
        client_factory: Optional[Callable[..., object]] = None,
        backoff_wait_fn: Optional[Callable[[float], None]] = None,
    ):
        """初始化接入层。

        Args:
            on_closed_bar: 闭合事件回调（异常被本层捕获记日志）。
            on_connection: 连接状态回调（None=不关心）。
            ws_base_url: WS 端点基址（环境变量 FAPI_WS_BASE 由装配层
                解析后传入；缺省官方端点）。
            proxies: 显式代理配置（None=直连——SDK 构造显式收参，
                不读环境代理变量，沿 fetcher.py 直连通道先例）。
            now_fn: 当前时刻函数（缺省真实时钟；测试注入固定时刻）。
            client_factory: WS 客户端工厂（缺省真实
                UMFuturesWebsocketClient——延迟 import；测试注入
                假客户端驱动连接生命周期）。
            backoff_wait_fn: 重连退避等待（缺省经 stop 事件可中断的
                wait；测试注入记录序列同步驱动）。
        """
        self.on_closed_bar = on_closed_bar
        self.on_connection = on_connection or (lambda event: None)
        self._ws_base_url = ws_base_url
        self._proxies = proxies
        self._now_fn = now_fn or (lambda: pd.Timestamp.now(tz="UTC"))
        self._client_factory = client_factory or self._default_client_factory
        self._backoff_wait_fn = backoff_wait_fn
        self._started = False
        self._ever_connected = False
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._client: Optional[object] = None
        self._last_push: dict[tuple[str, str], pd.Timestamp] = {}
        self._connected = False
        self._state_lock = threading.Lock()

    # ── 生命周期 ────────────────────────────────────────────

    @staticmethod
    def _default_client_factory(**kwargs):
        """真实 SDK 客户端工厂（延迟 import——模块顶层保持零 SDK 依赖面）。"""
        from binance.websocket.um_futures.websocket_client import (
            UMFuturesWebsocketClient,
        )

        return UMFuturesWebsocketClient(**kwargs)

    def start(self) -> None:
        """启动生命周期线程（幂等——重复调用 no-op）。"""
        if self._started:
            return
        self._started = True
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._run, name="fapi-stream", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        """协作关闭：置停 + 断 socket（若在连）+ 等线程退出。"""
        self._stop_event.set()
        with self._state_lock:
            client = self._client
            self._client = None
        if client is not None:
            try:
                client.stop()
            except Exception:
                logger.exception("fapi 流关停异常（忽略——已置停）")
        thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=10.0)

    # ── 生命周期循环 ─────────────────────────────────────────

    def _run(self) -> None:
        """连接生命周期循环：连 → 订阅 → 泊等断线 → 退避重试。"""
        backoff = RECONNECT_BACKOFF_INITIAL_SECONDS
        while not self._stop_event.is_set():
            established = False
            try:
                established = self._connect_and_park()
            except Exception as exc:  # 吞一切网络异常（错误语义单点）
                logger.error("fapi 流连接失败：%s", exc)
            if self._stop_event.is_set():
                break
            with self._state_lock:
                self._connected = False
            self._emit_connection(
                "disconnected", "读循环退出" if established else "连接建立失败")
            if established:
                backoff = RECONNECT_BACKOFF_INITIAL_SECONDS  # 存活过即重置
            self._wait_backoff(backoff)
            backoff = min(backoff * 2, RECONNECT_BACKOFF_CAP_SECONDS)
        logger.info("fapi 流生命周期线程退出")

    def _connect_and_park(self) -> bool:
        """单轮连接：构造客户端（建连+固定订阅）并泊等至断线。

        Returns:
            连接是否成功建立过（True=on_open 曾触发——退避重置依据）。

        SDK 形制锚（首验记录）：构造即建连并在构造期内触发 on_open
        （订阅须在该回调里发——self._client 尚未赋值，经 manager 的
        send_message 原始面）；构造抛异常（端点不可达）上抛由 _run
        捕获。on_close/on_error 触发 closed 事件唤醒泊等（stop() 的
        client.stop() 同路径）。
        """
        ready = threading.Event()
        closed = threading.Event()

        def _on_open(manager) -> None:
            try:
                manager.send_message(json.dumps({
                    "method": "SUBSCRIBE",
                    "params": FIXED_SUBSCRIBE_STREAMS,
                    "id": int(time.time() * 1000),
                }))
            except Exception:
                logger.exception("fapi 流订阅发送失败（连接仍存活，等对账兜底）")
            with self._state_lock:
                self._connected = True
                first = not self._ever_connected
                self._ever_connected = True
            self._emit_connection(
                "connected" if first else "reconnected",
                f"固定订阅 {len(FIXED_SUBSCRIBE_STREAMS)} 路")
            ready.set()

        def _on_close(_manager) -> None:
            closed.set()

        def _on_error(_manager, _error) -> None:
            closed.set()

        client = self._client_factory(
            stream_url=self._ws_base_url,
            on_message=self._on_message,
            on_open=_on_open,
            on_close=_on_close,
            on_error=_on_error,
            is_combined=True,
            proxies=self._proxies,
        )
        with self._state_lock:
            if self._stop_event.is_set():
                client.stop()
                return False
            self._client = client
        if not ready.wait(timeout=30.0):
            # 构造返回但 on_open 未达（握手挂起）——按建立失败收尾
            try:
                client.stop()
            except Exception:
                logger.exception("fapi 流握手超时清理异常（忽略）")
            with self._state_lock:
                self._client = None
            return False
        closed.wait()  # 泊等断线（SDK 读线程死亡回调 / stop 关闭）
        with self._state_lock:
            self._client = None
        return True

    def _wait_backoff(self, seconds: float) -> None:
        """退避等待（可被 stop 中断；测试注入旁路真实等待）。"""
        if self._backoff_wait_fn is not None:
            self._backoff_wait_fn(seconds)
            return
        self._stop_event.wait(seconds)

    # ── 消息面 ──────────────────────────────────────────────

    def _on_message(self, _manager, data) -> None:
        """SDK 消息回调（(manager, str) 签名锚；本层解析不外抛）。

        组合流消息形制 {"stream": "...", "data": {...}}；kline 事件
        的 k.x 判闭合——forming 更新推送只刷新 last_push 不投递。
        """
        try:
            payload = json.loads(data) if isinstance(data, str) else data
        except (json.JSONDecodeError, TypeError):
            logger.warning("fapi 流消息解析失败（丢弃）：%r", data)
            return
        if not isinstance(payload, dict) or "data" not in payload:
            return  # 订阅确认等控制消息——非行情推送
        # 形态校验（批次三 CARD-02）：data/k 非 dict（list/str/标量）
        # 时 .get 链 AttributeError 逃逸本层「解析不外抛」约束——SDK
        # 会把回调异常经 on_error 链当连接级故障处理，一条畸形消息
        # 伪断整条连接触发无谓重连；两处改 isinstance 显式丢弃记日志
        event = payload.get("data")
        if not isinstance(event, dict):
            logger.warning("fapi 流消息 data 非对象形（丢弃）：%r", event)
            return
        if event.get("e") != "kline":
            return
        self._handle_kline_event(event)

    def _handle_kline_event(self, event: dict) -> None:
        """kline 事件处理：last_push 刷新 + 闭合判定 + 脏数据丢弃 + 投递。"""
        kline = event.get("k")
        if not isinstance(kline, dict):
            logger.warning(
                "fapi 流 kline 事件 k 字段非对象形（丢弃）：symbol=%s",
                event.get("s"))
            return
        symbol = event.get("s")
        timeframe = kline.get("i")
        if not symbol or not timeframe:
            return
        # 键域校验（批次三 CARD-04）：订阅面固定 12 键——清单外键的
        # 推送（端点异常加推/配置漂移）不入 last_push（健康面静默面
        # 不被未知键稀释）也不投递（下游 registry 按键路由不认识）
        if (symbol, timeframe) not in FIXED_SUBSCRIBE_KEYS:
            logger.warning(
                "fapi 流清单外键推送（丢弃）：symbol=%s tf=%s",
                symbol, timeframe)
            return
        with self._state_lock:
            self._last_push[(symbol, timeframe)] = self._now_fn()
        if not kline.get("x"):  # forming 更新推送——不投递
            return
        try:
            bar = ClosedBarEvent(
                symbol=symbol,
                timeframe=timeframe,
                open_time=pd.Timestamp(
                    int(kline["t"]), unit="ms", tz="UTC"),
                open=float(kline["o"]),
                high=float(kline["h"]),
                low=float(kline["l"]),
                close=float(kline["c"]),
                volume=float(kline["v"]),
            )
        except (KeyError, TypeError, ValueError):
            logger.warning(
                "fapi 流 kline 字段异常（丢弃）：symbol=%s tf=%s",
                symbol, timeframe)
            return
        if not is_valid_bar_values(
                bar.open, bar.high, bar.low, bar.close, bar.volume):
            logger.warning(
                "fapi 流脏数据丢弃（OHLC/volume 非有限值）：%s %s @%s",
                symbol, timeframe, bar.open_time)
            return
        try:
            self.on_closed_bar(bar)
        except Exception:  # 回调内异常不外抛（单订阅方故障不拖垮流）
            logger.exception(
                "fapi 流闭合事件回调异常（吞掉）：%s %s", symbol, timeframe)

    # ── 连接事件与健康面 ────────────────────────────────────

    def _emit_connection(self, state: str, detail: str) -> None:
        try:
            self.on_connection(ConnEvent(state=state, detail=detail))
        except Exception:
            logger.exception("fapi 流连接事件回调异常（吞掉）")

    @property
    def connected(self) -> bool:
        """当前连接态（未启动/断线 False）。"""
        with self._state_lock:
            return self._connected

    def last_push(self, key: tuple[str, str]) -> Optional[pd.Timestamp]:
        """该键最近推送时刻（含 forming 推送；从未推送 None）。"""
        with self._state_lock:
            return self._last_push.get(key)

    def silent_seconds(self, key: tuple[str, str]) -> Optional[float]:
        """该键距最近推送的秒数（从未推送 None——调用方按未启动披露）。"""
        pushed = self.last_push(key)
        if pushed is None:
            return None
        return (self._now_fn() - pushed).total_seconds()
