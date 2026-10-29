"""fapi 接入层测试替身（四类注入能力——契约存在性=架构级）。

职能：为 stream/rest 接入层与 feed 双通道提供全合成驱动的可控事件
源，替代真实 SDK 与网络。仅测试依赖（tests/ 与本模块自测 import），
生产代码（feed/executor/scheduler/app）MUST NOT import 本模块。

四类能力（openspec market-data-feed spec「SDK 依赖注入契约」）：
①可控闭合事件序列——ScriptedStream.emit_closed 按脚本投递（支持
  重复投递同一事件〔幂等消费是被测行为〕与乱序投递〔防御性测试面〕）；
②可控断线/重连——ScriptedStream.emit_connection 投递三态事件脚本；
③可控静默窗口——ScriptedStream.start 后可零投递（静默判定按真实
  时钟触发，now_fn 注入旁路——沿 feed.py now_fn 先例形制）；
④可控 REST 响应序列——ScriptedRestClient 三函数各按队列返回构造
  帧/空帧/异常分支。

FakeFapiClient 为 BinanceFapiStream 的客户端工厂替身（模拟
_NewSdkWsClient 同步包装面：构造不建连、connect 阻塞建连语义、
deliver 投递已解析 dict、断线经 on_close、连接失败在 connect 抛），
首验记录的行为契约见 stream.py 模块头。
"""
from __future__ import annotations

import json
import threading
from typing import Any, Callable, Optional

import pandas as pd

from core.data.fapi.stream import (
    ClosedBarEvent,
    ConnEvent,
    FapiStreamProtocol,
    FIXED_SUBSCRIBE_STREAMS,
)


class ScriptedStream:
    """流替身（能力①②③）：按脚本投递闭合事件与连接事件。

    不做任何过滤/校验（脏数据防线驻生产层——BinanceFapiStream 直测）；
    start 幂等与 stop 语义按协议实现，供消费方按 FapiStreamProtocol
    消费（isinstance 通过 runtime_checkable 协议）。
    """

    def __init__(
        self,
        on_closed_bar: Callable[[ClosedBarEvent], None],
        on_connection: Optional[Callable[[ConnEvent], None]] = None,
    ):
        self.on_closed_bar = on_closed_bar
        self.on_connection = on_connection or (lambda event: None)
        self._started = False
        self.start_calls = 0
        self.stop_calls = 0

    def start(self) -> None:
        """协议 start（幂等——记调用次数供断言）。"""
        self.start_calls += 1
        self._started = True

    def stop(self) -> None:
        """协议 stop。"""
        self.stop_calls += 1
        self._started = False

    @property
    def started(self) -> bool:
        return self._started

    # ── 能力①：可控闭合事件序列 ─────────────────────────────

    def emit_closed(self, event: ClosedBarEvent) -> None:
        """投递单条闭合事件（重复投递同一事件合法——幂等消费归被测方）。"""
        self.on_closed_bar(event)

    def emit_closed_batch(self, events: list[ClosedBarEvent]) -> None:
        """按序投递一批闭合事件（乱序脚本由调用方构造列表达成）。"""
        for event in events:
            self.emit_closed(event)

    # ── 能力②：可控断线/重连脚本 ────────────────────────────

    def emit_connection(self, state: str, detail: str = "scripted") -> None:
        """投递连接事件（三态：connected/disconnected/reconnected）。"""
        self.on_connection(ConnEvent(state=state, detail=detail))

    # ── 能力③：可控静默窗口 ─────────────────────────────────
    # start 后零投递即静默——能力在于「可以什么都不投」，静默判定由
    # 消费方按 now_fn 时钟推进触发（无额外接口面）。


def make_closed_event(
    symbol: str = "BTCUSDT",
    timeframe: str = "1m",
    open_time: str = "2026-10-28T00:00:00Z",
    *,
    open_: float = 100.0,
    high: float = 101.0,
    low: float = 99.0,
    close: float = 100.5,
    volume: float = 10.0,
) -> ClosedBarEvent:
    """闭合事件构造便捷函数（测试数据形制单点）。"""
    return ClosedBarEvent(
        symbol=symbol,
        timeframe=timeframe,
        open_time=pd.Timestamp(open_time),
        open=open_,
        high=high,
        low=low,
        close=close,
        volume=volume,
    )


def kline_ws_payload(
    event: ClosedBarEvent,
    *,
    closed: bool = True,
    open_override: Optional[str] = None,
) -> dict:
    """构造组合流 kline 消息（已解析 dict——新 SDK 回调形制，首验③：
    json.loads 归 SDK receive_loop，BinanceFapiStream._on_message 收
    组合流包装 dict）。

    open_override 注入脏值字符串（"NaN"/"Infinity"）驱动脏数据防线；
    closed=False 构造 forming 更新推送。
    """
    stream = f"{event.symbol.lower()}@kline_{event.timeframe}"
    data = {
        "e": "kline",
        "E": 1700000000000,
        "s": event.symbol,
        "k": {
            "t": int(event.open_time.timestamp() * 1000),
            "T": 1700000060000,
            "s": event.symbol,
            "i": event.timeframe,
            "o": open_override if open_override is not None else str(event.open),
            "c": str(event.close),
            "h": str(event.high),
            "l": str(event.low),
            "v": str(event.volume),
            "x": closed,
        },
    }
    return {"stream": stream, "data": data}


class FakeFapiClient:
    """BinanceFapiStream 的客户端工厂替身（模拟 _NewSdkWsClient 同步
    包装面，零网络）。

    行为锚（新 SDK 首验记录+包装器契约）：
    - 构造不建连（首验①）——参数面与包装器一致（on_message/on_close/
      proxies/loop）；
    - fail_on_connect=True：connect() 抛异常（端点不可达——新 SDK
      构造纯本地，网络失败面在 connect 阶段）；
    - connect()：记录调用（连接成功——订阅/回调注册归包装器内部
      职责，替身经 subscribed_streams 静态返回固定订阅全集）；
    - deliver(payload)：投递已解析 dict（首验③——直接调 on_message，
      分发线程语境的直调等价简化）；
    - die()：模拟断线（on_close 回调——首验④ close 事件路径）；
    - stop()：关停打标+泊等唤醒（幂等）。
    """

    fail_on_connect: bool = False

    def __init__(self, *, stream_url=None, on_message=None, on_close=None,
                 proxies=None, loop=None):
        self.stream_url = stream_url
        self.proxies = proxies
        self._on_message = on_message
        self._on_close_cb = on_close
        self.connect_calls = 0
        self.stopped = False
        self._dead = threading.Event()

    # ── 包装器同步面 ────────────────────────────────────────

    def connect(self, timeout: float = 30.0) -> None:
        """阻塞建连语义（fail_on_connect 时抛异常——端点不可达分支）。"""
        if type(self).fail_on_connect:
            raise ConnectionError("scripted: 端点不可达")
        self.connect_calls += 1

    def stop(self) -> None:
        """关停（幂等——打标+触发断线回调唤醒泊等）。"""
        if self.stopped:
            return
        self.stopped = True
        self._dead.set()
        if self._on_close_cb is not None:
            self._on_close_cb()

    # ── 测试驱动面 ──────────────────────────────────────────

    def deliver(self, payload: dict) -> None:
        """投递已解析 dict 消息（on_message 单参形制——首验③）。"""
        if self._on_message is not None:
            self._on_message(payload)

    def die(self) -> None:
        """模拟断线（close 事件路径——on_close 唤醒泊等）。"""
        self._dead.set()
        if self._on_close_cb is not None:
            self._on_close_cb()

    def wait_dead(self, timeout: float = 5.0) -> bool:
        """等断线（测试同步锚）。"""
        return self._dead.wait(timeout)

    @property
    def subscribed_streams(self) -> list[str]:
        """固定订阅全集（包装器 connect 内订阅 12 路——A2 断言面）。"""
        return list(FIXED_SUBSCRIBE_STREAMS)


class ScriptedRestClient:
    """REST 替身（能力④）：三函数按队列脚本化响应（帧/空帧/异常）。

    队列形制：每个函数一条响应队列，pop 依序返回；队列耗尽重复返回
    末项（长跑测试不需显式铺满）。脚本项为 Exception 实例时抛出
    （异常传播语义测试）；None 视为空帧。签名与 rest.py 三函数对齐
    （duck typing——feed/reconcile 经参数注入消费，不 import rest）。
    """

    def __init__(
        self,
        closed_bars: Optional[list[Any]] = None,
        exchange_info: Optional[list[Any]] = None,
        funding_rates: Optional[list[Any]] = None,
    ):
        self.closed_bars_script = list(closed_bars or [])
        self.exchange_info_script = list(exchange_info or [])
        self.funding_rates_script = list(funding_rates or [])
        self.closed_bars_calls: list[dict] = []
        self.exchange_info_calls = 0
        self.funding_rate_calls: list[dict] = []

    def _pop(self, queue: list[Any], default: Any) -> Any:
        if len(queue) > 1:
            return queue.pop(0)
        if queue:
            return queue[0]  # 末项重复返回
        return default

    def fetch_closed_bars(
        self, symbol: str, timeframe: str,
        start_ts, end_ts, *,
        max_bars: Optional[int] = None,
    ) -> pd.DataFrame:
        """fetch_closed_bars 替身（记录调用含 max_bars+脚本响应）。"""
        self.closed_bars_calls.append({
            "symbol": symbol, "timeframe": timeframe,
            "start_ts": start_ts, "end_ts": end_ts,
            "max_bars": max_bars})
        item = self._pop(self.closed_bars_script, pd.DataFrame())
        if isinstance(item, Exception):
            raise item
        return item if item is not None else pd.DataFrame()

    def fetch_exchange_info(self) -> dict:
        """fetch_exchange_info 替身。"""
        self.exchange_info_calls += 1
        item = self._pop(self.exchange_info_script, {})
        if isinstance(item, Exception):
            raise item
        return item if item is not None else {}

    def fetch_funding_rate(self, symbol: str, start_ts, end_ts) -> pd.DataFrame:
        """fetch_funding_rate 替身。"""
        self.funding_rate_calls.append({
            "symbol": symbol, "start_ts": start_ts, "end_ts": end_ts})
        item = self._pop(self.funding_rates_script, pd.DataFrame())
        if isinstance(item, Exception):
            raise item
        return item if item is not None else pd.DataFrame()
