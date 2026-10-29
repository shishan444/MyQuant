"""fapi USDT-M 永续行情流接入层（WS 组合流——SDK 唯一 WS 触点）。

职能：把 SDK 组合流包装为固定订阅、自动重连的闭合 K 线事件源——
闭合事件是纸盘决策推进的唯一触发源，forming bar 不投递（闭合判定
驻本层）。服务对象：app 装配层的事件路由（feed 实例按需消费）与
调度器对账触发；数据作业与执行器永不 import 本模块依赖的 SDK
（依赖方向硬约束：SDK import 点仅 stream.py 与 rest.py）。

SDK 迁移（unify-fapi-sdk）：现役 SDK 为
binance-sdk-derivatives-trading-usds-futures（全 async 模型——经
_NewSdkWsClient 同步包装器桥接为线程模型，见类 docstring）；旧
binance-futures-connector 处于过渡服役期（卸载锚 change 3）。

固定订阅终态（用户裁决 2026-10-28）：连接建立即全量订阅 12 路常量
清单，不随任务增退变化——无动态订阅面（数据面恒定/决策面按需的
路由丢弃归装配层）。

实施期首验记录（2026-10-28，binance-futures-connector 4.2.0）：
组合流订阅经 is_combined=True + subscribe(list) 达成；构造即阻塞
建连并起读线程，断线无自动重连（退避重连由本层自持）、on_message
回调签名 (manager, data_str) 且 data 为未解析 JSON 字符串、
on_open 在构造期内触发（订阅经 manager 原始 send_message 面发送）。

新 SDK 首验记录（2026-10-29，binance-sdk-derivatives-trading-usds-
futures 18.1.0——unify-fapi-sdk 契约 2 四要素；①②为运行实测、
③④为源码级实证：实测窗口内本机代理出口 IP 遭币安限流〔REST 418/
WS 订阅确认后零推送〕，消息样例与断线场景的运行级验证降档源码级，
证据留档 /tmp/sdk_first_probe2.md，IP 解封后可补运行级样例）：
①构造与连通：ConfigurationWebSocketStreams(stream_url=...) 构造不
  建连；await connect(url, cfg) 阻塞至握手完成（实测约 1-2s）返回
  client 自身——**全 async 模型**（connect/subscribe/close/reconnect
  均协程，需事件循环线程承载——与旧 SDK 同步线程骨架的根本差异）。
②订阅面：await subscribe(["btcusdt@kline_1m"]) 发送
  {"method":"SUBSCRIBE","params":[...],"id":uuid}（服务端回
  {"result":null} 确认实测）；回调注册 on("message", cb,
  stream=<流名>)——**必须在 subscribe 之后**（先于订阅时流名不在
  连接映射、注册被丢弃，实测 warning 锁定该时序约束）。
③回调签名（源码级，receive_loop 分发链）：消息 json.loads 后按
  data["stream"] 键查回调映射分发；无 response_model 时回调收整包
  dict（stream+data 两键的组合流包装形制——与旧 SDK 未解析 str
  不同，适配层需取 data["data"] 内层）。
④断线行为（源码级）：**内建自动重连存在**（connect 内双任务：
  schedule_reconnect 兜底间隔 AUTO_RECONNECT_INTERVAL_SECONDS=82800s
  +receive_loop 断开检测触发 schedule_reconnect〔Configuration
  reconnect_delay=5000ms 固定间隔 × reconnect_attempts=3 有限次〕
  +serverShutdown 事件 5s 重连）——固定间隔×有限次数形制，与现自
  持指数退避（1s/2s/4s…封顶 60s 无限重试）不等价；重连归属与
  R-16 语义等价迁移的适配裁决见 design 契约 2。
"""
from __future__ import annotations

import asyncio
import logging
import math
import queue
import threading
from dataclasses import dataclass
from typing import Callable, Optional, Protocol, runtime_checkable
from urllib.parse import urlsplit

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
    """组合流生产实现（单连接、固定订阅、自持重连）。

    线程模型：start() 起单守护线程跑生命周期循环（包装器 connect
    〔阻塞建连+固定订阅+回调注册，见 _NewSdkWsClient〕→ 泊等断线 →
    指数退避 1s/2s/4s/…封顶 60s 无限重试 → 重连）。连接成功存活后
    退避重置；首连投 connected、重连投 reconnected、断线投
    disconnected。

    重连归属（unify-fapi-sdk 首验④裁决）：新 SDK 内建重连仅两条触
    发路径（82800s 兜底/serverShutdown 5s）且断线不自动触发——
    自持指数退避循环维持无双轨竞态；每轮重连弃旧 client 重建（内
    建随连接关闭终止）。

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
                解析后传入；缺省官方端点——组合流 /stream 路径由
                包装器拼）。
            proxies: 显式代理配置（None=直连——SDK 构造显式收参，
                不读环境代理变量，沿 fetcher.py 直连通道先例）。
            now_fn: 当前时刻函数（缺省真实时钟；测试注入固定时刻）。
            client_factory: WS 客户端工厂（缺省 _NewSdkWsClient——
                延迟 import 新 SDK；测试注入假客户端驱动连接生命
                周期）。
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
        # 新 SDK async 桥接（事件循环线程——start() 起 stop() 停，
        # 跨重连轮次单例复用）
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._loop_thread: Optional[threading.Thread] = None

    # ── 事件循环管理（新 SDK async 桥接基座）─────────────────

    def _ensure_loop(self) -> asyncio.AbstractEventLoop:
        """起专用事件循环线程（幂等——跨重连轮次单例）。"""
        if self._loop is None:
            self._loop = asyncio.new_event_loop()
            self._loop_thread = threading.Thread(
                target=self._loop.run_forever, name="fapi-ws-loop",
                daemon=True)
            self._loop_thread.start()
        return self._loop

    def _shutdown_loop(self) -> None:
        """停事件循环线程（stop() 收尾——client 已先行关闭）。"""
        loop, thread = self._loop, self._loop_thread
        self._loop = None
        self._loop_thread = None
        if loop is not None and thread is not None:
            loop.call_soon_threadsafe(loop.stop)
            thread.join(timeout=5.0)
            loop.close()

    # ── 生命周期 ────────────────────────────────────────────

    @staticmethod
    def _default_client_factory(**kwargs):
        """真实 SDK 客户端工厂（延迟 import——模块顶层保持零 SDK 依赖面）。"""
        return _NewSdkWsClient(**kwargs)

    def start(self) -> None:
        """启动生命周期线程（幂等——重复调用 no-op）。"""
        if self._started:
            return
        self._started = True
        self._stop_event.clear()
        self._ensure_loop()
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
        self._shutdown_loop()

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
        """单轮连接：包装器建连（含固定订阅+回调注册）并泊等至断线。

        Returns:
            连接是否成功建立过（True=connect 完成——退避重置依据）。

        新 SDK 时序锚（首验记录）：connect 阻塞至握手；订阅与回调
        注册在包装器 connect 内完成（on 后于 subscribe 的时序约束）；
        断线经 close 连接事件唤醒泊等（stop() 的 client.stop() 同
        路径）；构造/连接异常上抛由 _run 捕获记日志续轮。
        """
        closed = threading.Event()

        def _on_open() -> None:
            with self._state_lock:
                self._connected = True
                first = not self._ever_connected
                self._ever_connected = True
            self._emit_connection(
                "connected" if first else "reconnected",
                f"固定订阅 {len(FIXED_SUBSCRIBE_STREAMS)} 路")

        def _on_close() -> None:
            closed.set()

        client = self._client_factory(
            stream_url=self._ws_base_url,
            on_message=self._on_message,
            on_close=_on_close,
            proxies=self._proxies,
            loop=self._ensure_loop(),
        )
        with self._state_lock:
            if self._stop_event.is_set():
                client.stop()
                return False
            self._client = client
        client.connect()  # 阻塞建连+订阅+注册（异常上抛归 _run 捕获）
        _on_open()
        with self._state_lock:
            if self._stop_event.is_set():
                client.stop()
                self._client = None
                return False
        closed.wait()  # 泊等断线（close 事件 / stop 关闭）
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

    def _on_message(self, payload) -> None:
        """SDK 消息回调（已解析 dict——首验③：新 SDK 回调收组合流
        包装 {"stream": "...", "data": {...}}，json.loads 归 SDK
        receive_loop；本层解析不外抛）。

        kline 事件的 k.x 判闭合——forming 更新推送只刷新 last_push
        不投递。
        """
        if not isinstance(payload, dict) or "data" not in payload:
            return  # 订阅确认等控制消息——非行情推送
        # 形态校验（批次三 CARD-02）：data/k 非 dict（list/str/标量）
        # 时 .get 链 AttributeError 逃逸本层「解析不外抛」约束——
        # 回调异常会被 SDK 记错误事件，一条畸形消息伪断整条连接触发
        # 无谓重连；两处 isinstance 显式丢弃记日志
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


# ── 新 SDK 同步包装器（unify-fapi-sdk 契约 2——async→线程桥接）────

_DISPATCH_SENTINEL = object()


def legacy_proxy_to_sdk(proxy_url: str) -> dict:
    """旧扁平代理 URL 转新 SDK 结构化 proxy 形制（转换单源）。

    旧形制（装配层/环境不变）：{"http": url, "https": url}；
    新 SDK 形制：{"protocol": scheme, "host": ..., "port": ...}。

    单源共引：WS 面（_NewSdkWsClient）与 REST 面（rest.py
    _default_client_factory）两侧消费同一实现，不两写。
    """
    parts = urlsplit(proxy_url)
    return {
        "protocol": parts.scheme or "https",
        "host": parts.hostname or "",
        "port": parts.port if parts.port else (
            443 if (parts.scheme or "https") == "https" else 80),
    }


class _NewSdkWsClient:
    """新 SDK WS 客户端同步包装器（BinanceFapiStream 线程模型消费面）。

    新 SDK 全 async（首验①）：本包装器把 connect/subscribe/close 桥
    接为同步阻塞调用（协程经 run_coroutine_threadsafe 提交专用事件
    循环线程——loop 由 BinanceFapiStream 生命周期管理传入）；消息回
    调经分发队列转投独立分发线程（SDK 回调在事件循环线程执行，消费
    方耗时处理若直调会阻塞循环卡死全部流推送——首验③链路的线程语
    境约束）。

    时序契约（首验②）：connect() 顺序=建连 → 注册 close 连接事件 →
    固定订阅 → 逐流 on("message") 注册（on 后于 subscribe——先于订
    阅时流名不在连接映射、注册被丢弃）→ 起分发线程。on_open 语义由
    调用方（BinanceFapiStream）在 connect() 返回后自行触发。
    """

    def __init__(self, *, stream_url, on_message, on_close, proxies=None,
                 loop=None, _sdk_client_factory=None):
        from binance_common.configuration import (
            ConfigurationWebSocketStreams,
        )
        from binance_sdk_derivatives_trading_usds_futures.websocket_streams import (  # noqa: E501
            DerivativesTradingUsdsFuturesWebSocketStreams,
        )

        # 组合流端点：基址拼 /stream 路径（旧 SDK is_combined 等价）
        self._url = stream_url.rstrip("/") + "/stream"
        sdk_proxy = None
        if proxies:
            # 旧扁平形制取 https 通道（WS over TLS）；转结构化形制
            raw = proxies.get("https") or proxies.get("http")
            if raw:
                sdk_proxy = legacy_proxy_to_sdk(str(raw))
        self._cfg = ConfigurationWebSocketStreams(
            stream_url=self._url, proxy=sdk_proxy,
            reconnect_delay=5000, reconnect_attempts=3)
        if _sdk_client_factory is not None:
            self._client = _sdk_client_factory(self._cfg)
        else:
            self._client = DerivativesTradingUsdsFuturesWebSocketStreams(
                self._cfg)
        self._loop = loop
        self._on_message = on_message
        self._on_close_cb = on_close
        self._dispatch_queue: queue.SimpleQueue = queue.SimpleQueue()
        self._dispatch_thread: Optional[threading.Thread] = None
        self._stopped = False

    # ── 同步桥接面 ──────────────────────────────────────────

    def connect(self, timeout: float = 30.0) -> None:
        """阻塞建连+固定订阅+回调注册（异常上抛归调用方捕获）。"""
        if self._loop is None:
            raise RuntimeError("包装器需要事件循环（loop=None——工厂"
                               "须经 BinanceFapiStream 传入）")
        fut = asyncio.run_coroutine_threadsafe(
            self._client.connect(self._url, self._cfg), self._loop)
        fut.result(timeout)
        # 断线感知：close 连接事件（receive_loop 断开经 _emit_close_
        # event 触发——首验④：断线不自动重连，仅事件通知）
        self._client.on_connection("close", self._handle_close)
        # 固定订阅（12 路）——await 语义桥接（订阅消息实际发送）
        fut = asyncio.run_coroutine_threadsafe(
            self._client.subscribe(FIXED_SUBSCRIBE_STREAMS), self._loop)
        fut.result(timeout)
        # 逐流消息回调注册（on 后于 subscribe——首验②时序约束）
        for stream_name in FIXED_SUBSCRIBE_STREAMS:
            self._client.on(
                "message", self._enqueue_message, stream=stream_name)
        # 分发线程（loop 线程仅入队——回调耗时不上事件循环）
        self._dispatch_thread = threading.Thread(
            target=self._dispatch_loop, name="fapi-ws-dispatch",
            daemon=True)
        self._dispatch_thread.start()

    def stop(self) -> None:
        """关闭连接+停分发线程（幂等）。"""
        if self._stopped:
            return
        self._stopped = True
        if self._loop is not None:
            try:
                fut = asyncio.run_coroutine_threadsafe(
                    self._client.close_connection(), self._loop)
                fut.result(timeout=10.0)
            except Exception:
                logger.exception("fapi 流包装器关停异常（忽略——已置停）")
        if self._dispatch_thread is not None:
            self._dispatch_queue.put(_DISPATCH_SENTINEL)
            self._dispatch_thread.join(timeout=5.0)

    # ── 内部（loop 线程/分发线程语境）───────────────────────

    def _enqueue_message(self, payload, **_kwargs) -> None:
        """SDK 消息回调（事件循环线程）——仅入队不处理。"""
        self._dispatch_queue.put(payload)

    def _dispatch_loop(self) -> None:
        """分发线程：队列消费调消息处理（异常吞掉续行）。"""
        while True:
            item = self._dispatch_queue.get()
            if item is _DISPATCH_SENTINEL:
                return
            try:
                self._on_message(item)
            except Exception:
                logger.exception("fapi 流消息分发异常（吞掉续行）")

    def _handle_close(self, *_args, **_kwargs) -> None:
        """close 连接事件（loop 线程）——置断线事件唤醒泊等。"""
        if self._on_close_cb is not None:
            self._on_close_cb()
