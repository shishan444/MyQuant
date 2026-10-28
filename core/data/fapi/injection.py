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

FakeFapiClient 为 BinanceFapiStream 的客户端工厂替身（模拟 SDK 行
为面：构造即连、on_open 期内发订阅、on_message (manager, str) 签
名、断线回调、构造失败），首验记录的行为契约见 stream.py 模块头。
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

    def emit_disconnect_pause_reconnect(
        self, *,
        _delay: float = 0.0,
    ) -> None:
        """断线→（延迟）→重连脚本（延迟由调用方线程控制，缺省即刻）。"""
        self.emit_connection("disconnected", "scripted 断线")
        self.emit_connection("reconnected", "scripted 重连")

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
) -> str:
    """构造组合流 kline 消息 JSON（BinanceFapiStream._on_message 输入形制）。

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
    return json.dumps({"stream": stream, "data": data})


class FakeFapiClient:
    """BinanceFapiStream 的客户端工厂替身（模拟 SDK 行为面，零网络）。

    行为脚本经类属性/构造参数控制：
    - raise_on_construct=True：构造抛异常（端点不可达分支）；
    - construct_connects=True（缺省）：构造即触发 on_open（SDK 首验
      行为锚——订阅在 on_open 内经 manager.send_message 发出）；
    - deliver()/deliver_raw()：测试线程内推送消息（on_message 签名
      (manager, str)）；
    - die()：模拟断线（触发 on_error 与 on_close）；
    - stop()：记录关停（SDK stop=close+join 语义此处仅打标）。

    send_message 记录全部出站消息（订阅清单断言面）。
    """

    raise_on_construct: bool = False
    construct_connects: bool = True

    def __init__(self, *, stream_url=None, on_message=None, on_open=None,
                 on_close=None, on_error=None, on_ping=None, on_pong=None,
                 is_combined=False, proxies=None):
        if type(self).raise_on_construct:
            raise ConnectionError("scripted: 端点不可达")
        self.stream_url = stream_url
        self.is_combined = is_combined
        self.proxies = proxies
        self._on_message = on_message
        self._on_open = on_open
        self._on_close = on_close
        self._on_error = on_error
        self.sent_messages: list[str] = []
        self.stopped = False
        self._dead = threading.Event()
        if type(self).construct_connects and on_open is not None:
            on_open(self)  # SDK 行为锚：构造期内触发 on_open

    # ── SDK 面方法 ──────────────────────────────────────────

    def send_message(self, message: str) -> None:
        """出站消息记录（订阅断言面）。"""
        self.sent_messages.append(message)

    def stop(self) -> None:
        """关停（打标+触发断线回调唤醒泊等——SDK close 同路径）。"""
        self.stopped = True
        self._dead.set()
        if self._on_close is not None:
            self._on_close(self)

    # ── 测试驱动面 ──────────────────────────────────────────

    def deliver_raw(self, raw: str) -> None:
        """推送原始 JSON 字符串消息（on_message (manager, str) 形制）。"""
        if self._on_message is not None:
            self._on_message(self, raw)

    def die(self) -> None:
        """模拟断线（on_error → on_close——SDK 读循环异常退出路径）。"""
        if self._on_error is not None:
            self._on_error(self, ConnectionError("scripted: 断线"))
        self.stop()

    def wait_dead(self, timeout: float = 5.0) -> bool:
        """等断线（测试同步锚）。"""
        return self._dead.wait(timeout)

    @property
    def subscribed_streams(self) -> list[str]:
        """已发送订阅消息解析出的流名全集（A2 断言面）。"""
        streams: list[str] = []
        for message in self.sent_messages:
            try:
                payload = json.loads(message)
            except json.JSONDecodeError:
                continue
            if payload.get("method") == "SUBSCRIBE":
                streams.extend(payload.get("params") or [])
        return streams


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
        start_ts, end_ts,
    ) -> pd.DataFrame:
        """fetch_closed_bars 替身（记录调用+脚本响应）。"""
        self.closed_bars_calls.append({
            "symbol": symbol, "timeframe": timeframe,
            "start_ts": start_ts, "end_ts": end_ts})
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
