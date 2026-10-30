"""fapi 流协议与注入面测试（任务 1——全合成驱动、零真实网络）。

覆盖面（tasks.md 完成标准逐项）：
- 四类注入能力各自成立（①闭合序列含重复/②断线重连/③静默窗口/
  ④REST 响应序列——ScriptedRestClient 契约预验）；
- 脏数据（OHLC/volume 任一 NaN/inf）丢弃不投递；
- 回调内异常被接入层捕获不外抛（后续投递不受影响）；
- 重复闭合事件投递不炸（幂等消费归 feed，接入层照常投递）；
- A1 start 幂等 / A2 固定清单订阅（12 路全集、清单外键不受理）/
  A6 重连退避（单调不降+60s 封顶+无限重试+存活后重置）/ A7 静默
  窗口语义（窗口公式+last_push/silent_seconds）。
"""
from __future__ import annotations

import asyncio
import json
import threading
import time
from pathlib import Path
from unittest import mock

import pandas as pd
import pytest

from core.data.fapi import (
    FIXED_SUBSCRIBE_KEYS,
    FIXED_SUBSCRIBE_STREAMS,
    BinanceFapiStream,
    ClosedBarEvent,
    ConnEvent,
    FapiStreamProtocol,
    silent_window_seconds,
)
from core.data.fapi.injection import (
    FakeFapiClient,
    ScriptedRestClient,
    ScriptedStream,
    kline_ws_payload,
    make_closed_event,
)


# ── 测试基建 ────────────────────────────────────────────────

class _Recorder:
    """回调记录器（线程安全——投递可来自 stream 生命周期线程）。"""

    def __init__(self):
        self.closed: list[ClosedBarEvent] = []
        self.connections: list[ConnEvent] = []
        self.raise_in_closed = False
        self.raise_in_connection = False
        self._lock = threading.Lock()

    def on_closed_bar(self, event: ClosedBarEvent) -> None:
        with self._lock:
            self.closed.append(event)
        if self.raise_in_closed:
            raise RuntimeError("scripted: 消费方回调故障")

    def on_connection(self, event: ConnEvent) -> None:
        with self._lock:
            self.connections.append(event)
        if self.raise_in_connection:
            raise RuntimeError("scripted: 连接事件回调故障")


class _ClientScript:
    """客户端工厂脚本（按序产出指定行为的 FakeFapiClient）。"""

    def __init__(self, behaviors: list[str]):
        """behaviors 每项："ok"（连接成功）/"fail"（connect 抛异常——
        新 SDK 构造纯本地，网络失败面在 connect 阶段）。"""
        self.behaviors = list(behaviors)
        self.clients: list[FakeFapiClient] = []
        self.calls = 0  # 工厂调用次数（含连接失败——重试计数面）

    def __call__(self, **kwargs):
        index = self.calls
        self.calls += 1
        behavior = (
            self.behaviors[index] if index < len(self.behaviors)
            else self.behaviors[-1] if self.behaviors else "ok")

        class _Client(FakeFapiClient):
            pass

        _Client.fail_on_connect = behavior == "fail"
        client = _Client(**kwargs)
        self.clients.append(client)
        return client


def _wait_until(predicate, timeout: float = 5.0, interval: float = 0.01):
    """轮询等待条件成立（合成驱动的同步锚；超时失败）。"""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return False


def _make_stream(recorder: _Recorder, script: _ClientScript,
                 **kwargs) -> BinanceFapiStream:
    waits: list[float] = []
    lock = threading.Lock()

    def _wait(seconds: float) -> None:
        with lock:
            waits.append(seconds)

    stream = BinanceFapiStream(
        recorder.on_closed_bar, recorder.on_connection,
        client_factory=script, backoff_wait_fn=_wait, **kwargs)
    stream._test_waits = waits  # noqa: SLF001——测试观察锚
    stream._test_wait_lock = lock  # noqa: SLF001
    return stream


# ── 注入能力①②③：ScriptedStream 形制 ─────────────────────

class TestScriptedStream:
    """替身自身的协议符合性与三类能力存在性。"""

    def test_implements_protocol(self):
        recorder = _Recorder()
        stream = ScriptedStream(
            recorder.on_closed_bar, recorder.on_connection)
        assert isinstance(stream, FapiStreamProtocol)

    def test_capability_closed_sequence_with_duplicates(self):
        """能力①：可控闭合序列——重复投递同一事件合法（被测方判重）。"""
        recorder = _Recorder()
        stream = ScriptedStream(recorder.on_closed_bar)
        event = make_closed_event()
        stream.emit_closed(event)
        stream.emit_closed(event)  # 重复投递
        stream.emit_closed(make_closed_event(open_time="2026-10-28T00:01:00Z"))
        assert len(recorder.closed) == 3

    def test_capability_disconnect_reconnect_script(self):
        """能力②：断线→重连脚本事件。"""
        recorder = _Recorder()
        stream = ScriptedStream(recorder.on_closed_bar, recorder.on_connection)
        stream.emit_connection("disconnected", "脚本断线")
        stream.emit_connection("reconnected", "脚本重连")
        assert [e.state for e in recorder.connections] == [
            "disconnected", "reconnected"]

    def test_capability_silent_window_zero_delivery(self):
        """能力③：start 后零投递（静默判定由消费方按 now_fn 时钟触发）。"""
        recorder = _Recorder()
        stream = ScriptedStream(recorder.on_closed_bar, recorder.on_connection)
        stream.start()
        assert stream.started and not recorder.closed
        stream.stop()
        assert not stream.started


# ── 注入能力④：ScriptedRestClient 契约 ─────────────────────

class TestScriptedRestClient:
    """REST 替身三函数的帧/空帧/异常分支（rest.py 契约预验）。"""

    def _frame(self) -> pd.DataFrame:
        return pd.DataFrame(
            {"open": [1.0], "high": [2.0], "low": [0.5],
             "close": [1.5], "volume": [10.0]},
            index=pd.DatetimeIndex(
                [pd.Timestamp("2026-10-28T00:00:00Z")], name="timestamp"))

    def test_closed_bars_frame_branch(self):
        rest = ScriptedRestClient(closed_bars=[self._frame()])
        out = rest.fetch_closed_bars("BTCUSDT", "1m", 0, 1)
        assert len(out) == 1 and rest.closed_bars_calls[0]["symbol"] == "BTCUSDT"

    def test_closed_bars_empty_branch(self):
        rest = ScriptedRestClient()
        out = rest.fetch_closed_bars("ETHUSDT", "1h", 0, 1)
        assert out is not None and out.empty  # 空帧非 None

    def test_closed_bars_exception_branch(self):
        rest = ScriptedRestClient(
            closed_bars=[ConnectionError("scripted: 网络异常")])
        with pytest.raises(ConnectionError):
            rest.fetch_closed_bars("BTCUSDT", "1m", 0, 1)

    def test_exchange_info_and_funding_branches(self):
        rest = ScriptedRestClient(
            exchange_info=[{"symbols": []}],
            funding_rates=[None, self._frame()])
        assert rest.fetch_exchange_info() == {"symbols": []}
        assert rest.fetch_funding_rate("BTCUSDT", 0, 1).empty  # None→空帧
        assert len(rest.fetch_funding_rate("BTCUSDT", 0, 1)) == 1


# ── BinanceFapiStream：生产编排（FakeFapiClient 驱动）──────

class TestBinanceFapiStreamProduction:
    """生产实现的行为面（经客户端工厂替身，零真实网络）。"""

    def test_a1_start_idempotent(self):
        """A1：start 幂等——重复调用不起新线程。"""
        recorder = _Recorder()
        script = _ClientScript(["ok"])
        stream = _make_stream(recorder, script)
        stream.start()
        thread = stream._thread  # noqa: SLF001
        stream.start()
        assert stream._thread is thread  # noqa: SLF001
        assert thread is not None and thread.is_alive()
        stream.stop()
        assert not thread.is_alive()

    def test_a2_fixed_subscription_full_set(self):
        """A2：start 后固定订阅 12 路全集；清单外键（30m/3d）不受理。"""
        recorder = _Recorder()
        script = _ClientScript(["ok"])
        stream = _make_stream(recorder, script)
        stream.start()
        assert _wait_until(lambda: stream.connected)
        client = script.clients[0]
        subscribed = client.subscribed_streams
        assert subscribed == FIXED_SUBSCRIBE_STREAMS  # 12 路全集
        assert len(subscribed) == 12 == len(FIXED_SUBSCRIBE_KEYS)
        # 清单外周期不在订阅面（30m/3d/1w 等）
        assert not any("30m" in s or "3d" in s or "1w" in s for s in subscribed)
        # 首连事件=connected
        assert recorder.connections[0].state == "connected"
        stream.stop()

    def test_dirty_data_dropped(self):
        """③脏数据丢弃：OHLC/volume 任一 NaN/inf 不投递（推送已到达）。"""
        recorder = _Recorder()
        script = _ClientScript(["ok"])
        stream = _make_stream(recorder, script)
        stream.start()
        assert _wait_until(lambda: stream.connected)
        client = script.clients[0]
        event = make_closed_event()
        for dirty in ("NaN", "Infinity", "-Infinity"):
            client.deliver(
                kline_ws_payload(event, open_override=dirty))
        assert recorder.closed == []  # 全部丢弃不投递
        stream.stop()

    def test_forming_update_not_delivered_but_touches_last_push(self):
        """forming 推送不投递，但 last_push 照常刷新（静默=无推送）。"""
        recorder = _Recorder()
        script = _ClientScript(["ok"])
        stream = _make_stream(recorder, script)
        stream.start()
        assert _wait_until(lambda: stream.connected)
        client = script.clients[0]
        event = make_closed_event(symbol="ETHUSDT", timeframe="5m")
        client.deliver(kline_ws_payload(event, closed=False))
        assert recorder.closed == []
        assert stream.last_push(("ETHUSDT", "5m")) is not None
        stream.stop()

    def test_callback_exception_swallowed(self):
        """④回调内异常捕获：投递不中断，后续事件照常到达。"""
        recorder = _Recorder()
        recorder.raise_in_closed = True  # 消费方回调故障
        script = _ClientScript(["ok"])
        stream = _make_stream(recorder, script)
        stream.start()
        assert _wait_until(lambda: stream.connected)
        client = script.clients[0]
        event = make_closed_event()
        client.deliver(kline_ws_payload(event))
        client.deliver(kline_ws_payload(event))
        assert len(recorder.closed) == 2  # 两次均到达（异常被吞不外抛）
        stream.stop()

    def test_duplicate_closed_delivery_not_crash(self):
        """⑤重复闭合事件投递不炸（接入层照常投递，判重归 feed）。"""
        recorder = _Recorder()
        script = _ClientScript(["ok"])
        stream = _make_stream(recorder, script)
        stream.start()
        assert _wait_until(lambda: stream.connected)
        client = script.clients[0]
        event = make_closed_event()
        client.deliver(kline_ws_payload(event))
        client.deliver(kline_ws_payload(event))
        assert len(recorder.closed) == 2
        assert recorder.closed[0] == recorder.closed[1]
        stream.stop()

    def test_reconnect_event_and_resubscribe(self):
        """断线→重连投递三态事件，重连后再次订阅固定清单。"""
        recorder = _Recorder()
        script = _ClientScript(["ok", "ok"])
        stream = _make_stream(recorder, script)
        stream.start()
        assert _wait_until(lambda: stream.connected)
        script.clients[0].die()
        assert _wait_until(
            lambda: len(script.clients) >= 2 and script.clients[1].subscribed_streams)
        states = [e.state for e in recorder.connections]
        assert "disconnected" in states
        assert states[-1] == "reconnected"  # 第二次连接=重连
        assert script.clients[1].subscribed_streams == FIXED_SUBSCRIBE_STREAMS
        stream.stop()

    def test_a6_backoff_monotonic_capped_unlimited(self):
        """A6：退避单调不降+60s 封顶+无限重试（持续失败场景）。"""
        recorder = _Recorder()
        script = _ClientScript(["fail"])  # 全失败
        stream = _make_stream(recorder, script)
        stream.start()
        # 等序列越过封顶点（1+2+…+32 共 7 次后进 60 段）
        assert _wait_until(
            lambda: len(stream._test_waits) >= 10)  # noqa: SLF001
        stream.stop()
        waits = list(stream._test_waits)  # noqa: SLF001
        assert waits[0] == 1.0  # 初值
        assert all(b >= a for a, b in zip(waits, waits[1:]))  # 单调不降
        assert max(waits) == 60.0  # 封顶
        assert waits.count(60.0) >= 2  # 封顶后持续重试（无限）
        assert script.calls > 8  # 重试次数远超封顶点——无限重试

    def test_a6_backoff_reset_after_established(self):
        """A6：连接成功存活后退避重置（中途恢复不累积惩罚）。"""
        recorder = _Recorder()
        script = _ClientScript(["fail", "fail", "ok", "fail"])
        stream = _make_stream(recorder, script)
        stream.start()
        # 新模型 fail 在 connect 阶段抛——实例全部进 clients（成功
        # 实例=第 3 个：clients[2]）
        assert _wait_until(lambda: len(script.clients) >= 3)
        assert _wait_until(lambda: stream.connected)
        script.clients[2].die()  # 成功连接后断线
        # 序列应含重置点：fail(1),fail(2) 后 ok 断线重置回 1
        assert _wait_until(lambda: len(stream._test_waits) >= 4)  # noqa: SLF001
        stream.stop()
        waits = list(stream._test_waits)  # noqa: SLF001
        assert waits[:2] == [1.0, 2.0]  # 两次失败
        assert waits[2] == 1.0  # 成功存活后重置
        assert waits[3] == 2.0  # 重置后继续倍增


# ── A7：静默窗口语义 ───────────────────────────────────────

class TestSilentWindowSemantics:
    """窗口公式与 last_push/silent_seconds 健康面语义。"""

    def test_window_formula(self):
        """窗口=bar 时长×2+60s（1m→180s、1d→172860s）。"""
        assert silent_window_seconds(60) == 180.0
        assert silent_window_seconds(3600) == 7260.0
        assert silent_window_seconds(86400) == 172860.0

    def test_silent_seconds_grows_with_clock(self):
        """silent_seconds 随注入时钟推进增长；未推送键 None。"""
        clock = {"now": pd.Timestamp("2026-10-28T00:10:00Z")}
        recorder = _Recorder()
        script = _ClientScript(["ok"])
        stream = BinanceFapiStream(
            recorder.on_closed_bar, recorder.on_connection,
            client_factory=script,
            now_fn=lambda: clock["now"])
        stream.start()
        assert _wait_until(lambda: stream.connected)
        client = script.clients[0]
        event = make_closed_event(timeframe="1m")
        client.deliver(kline_ws_payload(event))  # 00:00 bar 闭合
        key = ("BTCUSDT", "1m")
        assert stream.last_push(key) is not None
        assert stream.silent_seconds(key) == 0.0 or stream.silent_seconds(key) >= 0
        clock["now"] = pd.Timestamp("2026-10-28T00:12:00Z")
        assert stream.silent_seconds(key) == pytest.approx(120.0, abs=5.0)
        # 从未推送的键 None（调用方按未启动披露）
        assert stream.last_push(("ETHUSDT", "1d")) is None
        assert stream.silent_seconds(("ETHUSDT", "1d")) is None
        stream.stop()


# ── 消息面细节 ─────────────────────────────────────────────

class TestMessageFace:
    """消息解析边界（控制消息/坏 JSON 不投递不崩溃）。"""

    def test_control_message_ignored(self):
        recorder = _Recorder()
        script = _ClientScript(["ok"])
        stream = _make_stream(recorder, script)
        stream.start()
        assert _wait_until(lambda: stream.connected)
        client = script.clients[0]
        client.deliver({"result": None, "id": 1})  # 订阅确认控制消息
        client.deliver("not-a-payload")  # 非 dict 载荷（json.loads 归
        # SDK 侧——坏 JSON 到不了本层，本层防线=非 dict 丢弃）
        assert recorder.closed == []
        assert stream.connected  # 未崩
        stream.stop()

    def test_malformed_data_and_k_no_escape_no_disconnect(self):
        """CARD-02 畸形消息（data/k 非 dict）无异常逃逸+不触发断线：
        若 .get 链 AttributeError 逃逸，SDK 会把回调异常经 on_error 链
        当连接级故障——一条畸形消息伪断整条连接。"""
        recorder = _Recorder()
        script = _ClientScript(["ok"])
        stream = _make_stream(recorder, script)
        stream.start()
        assert _wait_until(lambda: stream.connected)
        client = script.clients[0]
        connected_events = len(recorder.connections)  # 起连事件基线

        # data 非 dict 两形态（list/str）
        client.deliver(
            {"stream": "btcusdt@kline_1m", "data": [1, 2]})
        client.deliver(
            {"stream": "btcusdt@kline_1m", "data": "oops"})
        # k 非 dict 两形态（list/str）+k 缺失
        client.deliver(
            {"stream": "btcusdt@kline_1m",
             "data": {"e": "kline", "s": "BTCUSDT", "k": [1]}})
        client.deliver(
            {"stream": "btcusdt@kline_1m",
             "data": {"e": "kline", "s": "BTCUSDT", "k": "bad"}})
        client.deliver(
            {"stream": "btcusdt@kline_1m",
             "data": {"e": "kline", "s": "BTCUSDT"}})

        assert recorder.closed == []  # 畸形消息零投递
        assert len(recorder.connections) == connected_events  # 无断线/重连
        assert stream.connected  # 连接存活（未伪断）

        # 对照：后续正常闭合消息仍恰一次投递（防线不误伤正常路径）
        event = make_closed_event()
        client.deliver(kline_ws_payload(event))
        assert _wait_until(lambda: len(recorder.closed) == 1)
        assert len(recorder.connections) == connected_events
        stream.stop()

    def test_out_of_fixed_keys_push_dropped(self):
        """CARD-04 键域校验：清单外键（30m）推送丢弃——不入 last_push
        健康面（未知键不稀释静默披露面）不投递下游；成员键正常。"""
        recorder = _Recorder()
        script = _ClientScript(["ok"])
        stream = _make_stream(recorder, script)
        stream.start()
        assert _wait_until(lambda: stream.connected)
        client = script.clients[0]

        stray = make_closed_event(timeframe="30m")  # 清单外周期
        client.deliver(kline_ws_payload(stray))  # 同步内联——返回即处理完
        assert recorder.closed == []  # 清单外键不投递
        assert stream.last_push(("BTCUSDT", "30m")) is None  # 不入健康面

        member = make_closed_event(timeframe="1m")  # 固定 12 键成员
        client.deliver(kline_ws_payload(member))
        assert _wait_until(lambda: len(recorder.closed) == 1)
        assert stream.last_push(("BTCUSDT", "1m")) is not None
        stream.stop()

    def test_missing_symbol_or_timeframe_dropped(self):
        """s/i 缺失的 kline 消息静默丢弃不投递（不炸不投）。"""
        recorder = _Recorder()
        script = _ClientScript(["ok"])
        stream = _make_stream(recorder, script)
        stream.start()
        assert _wait_until(lambda: stream.connected)
        client = script.clients[0]
        client.deliver(
            {"stream": "btcusdt@bookTicker",
             "data": {"e": "bookTicker", "b": "1", "a": "2"}})  # 非 kline 事件
        client.deliver(
            {"stream": "btcusdt@kline_1m",
             "data": {"e": "kline", "k": {"i": "1m", "x": True}}})  # 缺 s
        client.deliver(
            {"stream": "btcusdt@kline_1m",
             "data": {"e": "kline", "s": "BTCUSDT",
                      "k": {"x": True}}})  # k 缺 i
        assert recorder.closed == []
        assert stream.connected
        stream.stop()

    def test_kline_field_error_dropped(self):
        """k dict 缺 openTime（t）等字段不可构造事件——warning 丢弃
        不投递不炸。"""
        recorder = _Recorder()
        script = _ClientScript(["ok"])
        stream = _make_stream(recorder, script)
        stream.start()
        assert _wait_until(lambda: stream.connected)
        client = script.clients[0]
        client.deliver(
            {"stream": "btcusdt@kline_1m",
             "data": {"e": "kline", "s": "BTCUSDT",
                      "k": {"i": "1m", "x": True, "o": "1", "h": "2",
                            "l": "0.5", "c": "1.5", "v": "10"}}})  # 缺 t
        assert recorder.closed == []
        assert stream.connected
        stream.stop()

    def test_connection_callback_exception_swallowed(self):
        """on_connection 回调抛异常被吞——流连接不受单订阅方故障影响。"""
        recorder = _Recorder()
        recorder.raise_in_connection = True
        script = _ClientScript(["ok"])
        stream = _make_stream(recorder, script)
        stream.start()
        # 等待锚=工厂完成：on_open 在 FakeFapiClient 构造内同步触发并先
        # 置 _connected，connected 锚存在「标志已置而 append 未落」窗口
        assert _wait_until(lambda: script.clients and stream.connected)
        event = make_closed_event()
        script.clients[0].deliver(kline_ws_payload(event))
        assert _wait_until(lambda: len(recorder.closed) == 1)  # 投递照常
        stream.stop()

    def test_event_field_mapping(self):
        """闭合事件字段映射（开盘时刻 ms→UTC Timestamp、OHLCV 浮点）。"""
        recorder = _Recorder()
        script = _ClientScript(["ok"])
        stream = _make_stream(recorder, script)
        stream.start()
        assert _wait_until(lambda: stream.connected)
        client = script.clients[0]
        event = make_closed_event(
            open_time="2026-10-28T00:00:00Z",
            open_=100.0, high=110.0, low=95.0, close=105.0, volume=42.5)
        client.deliver(kline_ws_payload(event))
        assert _wait_until(lambda: len(recorder.closed) == 1)
        got = recorder.closed[0]
        assert got.symbol == "BTCUSDT" and got.timeframe == "1m"
        assert got.open_time == pd.Timestamp("2026-10-28T00:00:00Z")
        assert got.open == 100.0 and got.high == 110.0
        assert got.low == 95.0 and got.close == 105.0
        assert got.volume == 42.5
        assert got.closed is True
        stream.stop()


# ── 生命周期防御分支（阶段四盲区消解）──────────────────────

class TestLifecycleDefensiveBranches:
    """stop/订阅发送/退避等待的防御分支（吞异常不断流语义）。"""

    def test_stop_client_exception_swallowed(self):
        """stop 时 client.stop 抛异常被吞（已置停——关停不外抛）。"""
        recorder = _Recorder()
        script = _ClientScript(["ok"])
        stream = _make_stream(recorder, script)
        stream.start()
        assert _wait_until(lambda: stream.connected)
        client = script.clients[0]

        client_ref = script.clients[0]

        def broken_stop():
            # 包装器 stop 语义：先关连接（close 事件）再抛（关停崩模拟）
            client_ref._on_close_cb()  # noqa: SLF001——测试注入面
            raise RuntimeError("scripted: SDK 关停崩")

        client.stop = broken_stop  # 实例覆写
        stream.stop()  # 不抛即通过（异常被吞）
        assert not stream._thread.is_alive()  # noqa: SLF001——线程干净退出

    def test_subscribe_send_failure_swallowed_connected_kept(self):
        """连接建立失败（connect 抛）被生命周期循环吞掉续轮——不外抛
        不停线程（对账兜底语义：连接/订阅失败走退避重试，恢复后闭合
        事件照常投递）。"""
        recorder = _Recorder()
        script = _ClientScript(["fail", "ok"])
        stream = _make_stream(recorder, script)
        stream.start()
        assert _wait_until(lambda: stream.connected)  # 第二轮恢复
        event = make_closed_event()
        script.clients[1].deliver(kline_ws_payload(event))
        assert _wait_until(lambda: len(recorder.closed) == 1)
        stream.stop()

    def test_real_backoff_wait_interrupted_by_stop(self):
        """无 backoff 注入（生产形制）的真实 Event.wait 分支：fail 脚本
        断线后退避等待被 stop 打断——线程及时退出（不打满退避时长）。

        等待锚=disconnected 事件已发+调度缓冲：stop 若在 _run 线程到达
        _wait_backoff 前完成，「打断 wait」名存实亡（异常捕获后置停
        检查直接 break——阶段四覆盖核对实证过该漂移）。"""
        import time as _time

        recorder = _Recorder()
        script = _ClientScript(["fail", "fail"])  # 构造即失败→断线退避
        stream = BinanceFapiStream(  # 无 backoff_wait_fn——真实等待分支
            recorder.on_closed_bar, recorder.on_connection,
            client_factory=script)
        t0 = _time.monotonic()
        stream.start()
        # 事件锚：断线事件已发=异常处理完成；缓冲窗=线程进入
        # _stop_event.wait（emit 与 wait 间无阻塞，0.2s 足够）
        assert _wait_until(lambda: recorder.connections)
        _time.sleep(0.2)
        stream.stop()  # 退避 wait 期间 stop——立即打断
        stream._thread.join(timeout=5.0)  # noqa: SLF001——退出观察锚
        assert not stream._thread.is_alive()  # noqa: SLF001
        assert _time.monotonic() - t0 < 10.0  # 未打满退避（及时退出）

    def test_stop_during_connect_window_cleaned_up(self):
        """构造返回与挂载之间的 stop 置停窗口：客户端清理返回 False
        （不进入泊等）。"""
        recorder = _Recorder()
        inner = _ClientScript(["ok"])
        gate = threading.Event()
        stream_holder: dict = {}

        class _WindowFactory:
            """构造返回后立即置停（stop 与挂载之间的窗口注入）。"""

            def __call__(self, **kwargs):
                client = inner(**kwargs)
                stream_holder["stream"].stop()
                gate.set()
                return client

        stream = _make_stream(recorder, _WindowFactory())
        stream_holder["stream"] = stream
        stream.start()
        assert gate.wait(timeout=5.0), "窗口注入未触达"
        stream._thread.join(timeout=5.0)  # noqa: SLF001
        assert not stream._thread.is_alive()  # noqa: SLF001——干净退出

    def test_connect_fail_cleans_up_abandoned_client(self):
        """CARD-03：connect 失败的弃 client 被 stop 清理（修复前无人
        stop——半建连连接协程泄漏）且挂载解除，重连挂载新 client。"""
        script = _ClientScript(["fail", "ok"])
        recorder = _Recorder()
        stream = _make_stream(recorder, script)
        stream.start()
        assert _wait_until(lambda: script.calls >= 2 and stream.connected)
        with stream._state_lock:  # noqa: SLF001——挂载面观察锚
            mounted = stream._client  # noqa: SLF001
        stream.stop()
        assert script.clients[0].stopped  # 弃 client 清理
        assert mounted is script.clients[1]  # 挂载的是新 client 非死对象

    def test_connect_fail_during_user_stop_keeps_unmounted(self):
        """CARD-03 覆写竞态防御：connect 抛异常时用户 stop() 已解除
        挂载（_client=None）——except 清理不把死 client 覆写回挂载面。"""
        script = _ClientScript(["fail"])
        recorder = _Recorder()
        holder: dict = {}

        class _StopInsideConnectFactory:
            """connect 内先触发用户 stop（置停+解除挂载）再放行原
            connect（fail 抛）——注入 except 路径的竞态窗口。"""

            def __call__(self, **kwargs):
                client = script(**kwargs)
                original_connect = client.connect

                def _connect(timeout: float = 30.0):
                    holder["stream"].stop()  # 置停+置 _client=None
                    original_connect(timeout)  # fail_on_connect 抛

                client.connect = _connect
                return client

        stream = _make_stream(recorder, _StopInsideConnectFactory())
        holder["stream"] = stream
        stream.start()
        stream._thread.join(timeout=5.0)  # noqa: SLF001
        if stream._thread.is_alive():  # noqa: SLF001——防御收尾
            stream.stop()  # 置停失败时防狂转线程泄漏（daemon 收尾）
            stream._thread.join(timeout=5.0)  # noqa: SLF001
        assert not stream._thread.is_alive()  # stop 置停→线程退出
        with stream._state_lock:  # noqa: SLF001
            mounted = stream._client  # noqa: SLF001
        assert mounted is None  # 未被死 client 覆写回
        assert script.clients[0].stopped  # 清理仍执行（幂等 stop）

    def test_scripted_stream_batch_and_rest_raise_branches(self):
        """替身面消解：emit_closed_batch 批量投递/ScriptedRestClient
        exchange_info 与 funding 的异常脚本项/wait_dead 超时 False/
        subscribed_streams 坏 JSON 行跳过。"""
        from core.data.fapi.injection import ScriptedRestClient

        recorder = _Recorder()
        scripted = ScriptedStream(
            on_closed_bar=recorder.on_closed_bar,
            on_connection=recorder.on_connection)
        batch = [make_closed_event(open_time="2026-10-28T00:00:00Z"),
                 make_closed_event(open_time="2026-10-28T00:01:00Z"),
                 make_closed_event(open_time="2026-10-28T00:00:00Z")]  # 重复
        scripted.emit_closed_batch(batch)
        assert len(recorder.closed) == 3  # 逐条投递（重复合法——幂等归被测方）

        rest = ScriptedRestClient(
            exchange_info=[ValueError("scripted: 规则拉取崩")],
            funding_rates=[ConnectionError("scripted: 费率断网")])
        with pytest.raises(ValueError):
            rest.fetch_exchange_info()
        with pytest.raises(ConnectionError):
            rest.fetch_funding_rate("BTCUSDT", None, None)

        client = FakeFapiClient()
        assert client.wait_dead(timeout=0.0) is False  # 未断线超时 False
        # 新模型：固定订阅全集静态面（订阅归包装器 connect 内部，
        # 替身无出站消息面——A2 断言锚=全集）
        from core.data.fapi.stream import FIXED_SUBSCRIBE_STREAMS

        assert client.subscribed_streams == FIXED_SUBSCRIBE_STREAMS


# ── CARD-02：静默判死防御（畸形帧杀 receive_loop 的行为结果面）──


class TestSilenceDeathGuard:
    """CARD-02 活性判死：SDK receive_loop 死亡（零回调且不发 close
    事件——畸形帧协程死亡的行为结果）不再永久泊等——周期复查静默
    超阈值弃 client 按断线重连。参数缩至毫秒级驱动（模块常量运行时
    查表，patch 生效）。"""

    _PATCH = (
        "core.data.fapi.stream.SILENCE_DEATH_CHECK_INTERVAL_SECONDS", 0.05)

    def test_silence_death_triggers_reconnect(self):
        """判死重连：零投递静默超阈值→弃 client（幂等 stop 清理）→
        disconnected+退避+重建第二连接（事件三态序列）。"""
        recorder = _Recorder()
        script = _ClientScript(["ok", "ok"])
        stream = _make_stream(recorder, script)
        with mock.patch(self._PATCH[0], self._PATCH[1]), \
                mock.patch(
                    "core.data.fapi.stream.SILENCE_DEATH_SECONDS", 0.3):
            stream.start()
            assert _wait_until(lambda: script.calls >= 2), "未判死重连"
            assert _wait_until(lambda: stream.connected)
        stream.stop()
        assert script.clients[0].stopped  # 死连接被清理
        # 前缀断言：缩参数下第二连接同样零投递，stop 前可能再判死
        # 产生后续事件——首三态序列才是本用例契约面
        states = [e.state for e in recorder.connections]
        assert states[:3] == ["connected", "disconnected", "reconnected"]

    def test_activity_refresh_prevents_false_death(self):
        """持续推送（含控制帧——订阅确认等非行情消息）不误判死：
        回调到达即活性刷新，连接保持不重建；控制帧不投递下游。"""
        recorder = _Recorder()
        script = _ClientScript(["ok"])
        stream = _make_stream(recorder, script)
        with mock.patch(self._PATCH[0], self._PATCH[1]), \
                mock.patch(
                    "core.data.fapi.stream.SILENCE_DEATH_SECONDS", 0.3):
            stream.start()
            assert _wait_until(lambda: stream.connected)
            deadline = time.monotonic() + 1.0  # 3 倍阈值窗口
            while time.monotonic() < deadline:
                script.clients[0].deliver({"stream": "x", "data": {}})
                time.sleep(0.02)
        stream.stop()
        assert script.calls == 1  # 未判死重建
        assert recorder.closed == []  # 控制帧不投递（附带锁定丢弃面）

    def test_none_activity_conservative_skip(self):
        """异常态防御：last_activity=None（未初始化）保守跳过不误杀
        ——泊等保持至 stop（判死检查永不因缺数据而触发）。"""
        recorder = _Recorder()
        script = _ClientScript(["ok"])
        stream = _make_stream(recorder, script)
        with mock.patch(self._PATCH[0], self._PATCH[1]), \
                mock.patch(
                    "core.data.fapi.stream.SILENCE_DEATH_SECONDS", 0.2):
            stream.start()
            assert _wait_until(lambda: stream.connected)
            script.clients[0]._last_activity = None  # noqa: SLF001——模拟异常态
            time.sleep(0.5)  # 多个判死周期（若误判死则 calls 增）
            assert script.calls == 1
            assert stream.connected  # 泊等保持
        stream.stop()

    def test_parking_wakes_on_stop_event_without_close(self):
        """泊等循环置停防御分支：stop_event 置位而 close 事件未发
        （stop() 与泊等线程的竞态窗口）——醒来发现置停即退出泊等，
        线程干净收尾（不停留至判死周期）。注：置停 break 路径不清
        _connected（现实现——记录在案的健康面观察，见闸门报告）。"""
        recorder = _Recorder()
        script = _ClientScript(["ok"])
        stream = _make_stream(recorder, script)
        with mock.patch(self._PATCH[0], self._PATCH[1]):
            stream.start()
            assert _wait_until(lambda: stream.connected)
            states_before = len(recorder.connections)
            stream._stop_event.set()  # noqa: SLF001——直接置停（绕过
            # stop() 的 client.stop 路径——注入竞态窗口：close 不发）
            stream._thread.join(timeout=5.0)  # noqa: SLF001
            assert not stream._thread.is_alive()  # noqa: SLF001
        # 置停退出：不发判死/断线事件（区别于判死与正常断线路径）
        assert len(recorder.connections) == states_before
        assert script.calls == 1  # 未判死重建


def test_fake_client_none_callback_defensive_sides():
    """闸门盲区消解（阶段四）：FakeFapiClient 全回调缺省（None）构造
    的防御侧——stop/deliver_raw/die 无回调不炸（no-op 面），die 仍打
    stopped 与 _dead 标（断线同步锚保持）。"""
    client = FakeFapiClient()  # 五回调全 None（on_open None 构造不触发）
    client.stop()              # on_close None 侧——打标不炸
    client.deliver("{}")   # on_message None 侧——no-op
    client.die()               # on_error None 侧——仅 stop 打标
    assert client.stopped is True
    assert client.wait_dead(timeout=0.1) is True  # die→stop 已置 _dead


# ── VS-2.4/VS-2.5：包装器订阅发送面+首验留档（覆盖核对补齐）──


class _FakeAsyncSdk:
    """新 SDK async 客户端替身（协程/注册调用全记录——订阅时序断言面）。

    VS-2.4 订阅发送面驻 _NewSdkWsClient.connect 内部（BinanceFapiStream
    测试族经 FakeFapiClient 时被整体绕过——subscribed_streams 常量直返
    是恒真面，不能证明订阅实际发送）；本替身经 _sdk_client_factory 注入
    包装器，按首验记录①②③的行为锚记录调用序列。
    """

    def __init__(self):
        self.calls: list[tuple] = []
        self.message_callbacks: dict[str, object] = {}
        self.connection_handlers: dict[str, object] = {}
        self.connect_cfg = None
        self.close_connection_calls = 0
        self.close_connection_raises = False

    async def connect(self, url, cfg):
        self.calls.append(("connect", url))
        self.connect_cfg = cfg

    def on_connection(self, event, handler):
        self.calls.append(("on_connection", event))
        self.connection_handlers[event] = handler

    async def subscribe(self, streams):
        self.calls.append(("subscribe", tuple(streams)))

    def on(self, event, callback, stream=None):
        self.calls.append(("on", event, stream))
        self.message_callbacks[stream] = callback

    async def close_connection(self):
        self.close_connection_calls += 1
        if self.close_connection_raises:
            raise RuntimeError("scripted: close 崩")


def _loop_and_thread() -> tuple[asyncio.AbstractEventLoop, threading.Thread]:
    """起专用事件循环线程（async 桥接基座——conftest AF_UNIX 放行面）。"""
    loop = asyncio.new_event_loop()
    thread = threading.Thread(
        target=loop.run_forever, name="test-ws-loop", daemon=True)
    thread.start()
    return loop, thread


def _stop_loop(loop: asyncio.AbstractEventLoop,
               thread: threading.Thread) -> None:
    loop.call_soon_threadsafe(loop.stop)
    thread.join(timeout=5.0)
    loop.close()


class TestNewSdkWsClientSubscribeFace:
    """VS-2.4 订阅发送面：连接建立即全量订阅 12 路+构造期订阅时序
    （首验②——on 后于 subscribe，先注册时流名不在连接映射被丢弃）；
    VS-2.3 等价族的分发桥面（loop 线程仅入队→分发线程转投消费方）。"""

    @staticmethod
    def _make_wrapper(fake, on_message, on_close=lambda: None,
                      proxies=None):
        from core.data.fapi.stream import _NewSdkWsClient  # noqa: SLF001

        loop, thread = _loop_and_thread()

        def _teardown():
            _stop_loop(loop, thread)

        client = _NewSdkWsClient(
            stream_url="wss://fstream.example.com",
            on_message=on_message, on_close=on_close,
            proxies=proxies,
            loop=loop, _sdk_client_factory=lambda cfg: fake)
        return client, _teardown

    def test_connect_subscribes_full_set_in_sequence(self):
        """连接建立即全量订阅：connect→close 注册→subscribe（恰一次、
        12 路全集）→逐流 on 注册（12 次全部后于 subscribe——首验②）。"""
        fake = _FakeAsyncSdk()
        client, teardown = self._make_wrapper(fake, lambda payload: None)
        try:
            client.connect(timeout=5.0)
            kinds = [c[0] for c in fake.calls]
            # 建连先于订阅（首验①：connect 阻塞至握手完成）
            assert kinds.index("connect") < kinds.index("subscribe")
            # 订阅恰一次、参数=固定 12 路全集
            assert kinds.count("subscribe") == 1
            subscribed = next(
                c for c in fake.calls if c[0] == "subscribe")[1]
            assert list(subscribed) == FIXED_SUBSCRIBE_STREAMS
            # 断线感知面：close 连接事件注册（首验④——断线仅事件通知）
            assert any(c[0] == "on_connection" and c[1] == "close"
                       for c in fake.calls)
            # 逐流消息回调注册：12 次、流名=全集、全部后于 subscribe
            on_indices = [i for i, c in enumerate(fake.calls)
                          if c[0] == "on"]
            assert len(on_indices) == 12
            assert all(i > kinds.index("subscribe") for i in on_indices)
            assert {c[2] for c in fake.calls
                    if c[0] == "on"} == set(FIXED_SUBSCRIBE_STREAMS)
        finally:
            client.stop()
            teardown()

    def test_message_dispatched_via_dedicated_thread(self):
        """消息分发桥（首验③线程语境）：SDK 推送经注册回调仅入队，
        分发线程转投消费方 on_message——回调异常被吞不中断后续分发。"""
        received: list[dict] = []
        fail_first = {"yes": True}

        def _on_message(payload):
            received.append(payload)
            if fail_first["yes"]:
                fail_first["yes"] = False
                raise RuntimeError("scripted: 消费方故障")

        fake = _FakeAsyncSdk()
        client, teardown = self._make_wrapper(fake, _on_message)
        try:
            client.connect(timeout=5.0)
            payload1 = {"stream": "btcusdt@kline_1m", "data": {"e": "kline"}}
            payload2 = {"stream": "ethusdt@kline_1m", "data": {"e": "kline"}}
            fake.message_callbacks["btcusdt@kline_1m"](payload1)
            fake.message_callbacks["ethusdt@kline_1m"](payload2)
            # 首条回调异常被分发线程吞掉，第二条照常到达
            assert _wait_until(lambda: len(received) >= 2)
            assert received == [payload1, payload2]
        finally:
            client.stop()
            teardown()

    def test_stop_closes_connection_once_and_joins_dispatch(self):
        """stop：close_connection 恰一次（幂等——重复 stop 不再发）+
        分发线程随 sentinel 退出（stop 返回即已收尾）。"""
        fake = _FakeAsyncSdk()
        client, teardown = self._make_wrapper(fake, lambda payload: None)
        client.connect(timeout=5.0)
        client.stop()
        client.stop()  # 幂等侧
        assert fake.close_connection_calls == 1
        dispatch = client._dispatch_thread  # noqa: SLF001——退出观察锚
        assert dispatch is not None and not dispatch.is_alive()
        teardown()

    def test_connect_without_loop_raises(self):
        """loop=None 防御面：connect 拒绝（工厂须经 BinanceFapiStream
        传入事件循环——桥接基座缺失快速失败）。"""
        from core.data.fapi.stream import _NewSdkWsClient  # noqa: SLF001

        client = _NewSdkWsClient(
            stream_url="wss://fstream.example.com",
            on_message=lambda payload: None, on_close=lambda: None,
            loop=None, _sdk_client_factory=lambda cfg: _FakeAsyncSdk())
        with pytest.raises(RuntimeError, match="事件循环"):
            client.connect(timeout=1.0)

    def test_ws_proxy_converted_to_structured_form(self):
        """CARD-01 同族 WS 面：包装器构造把旧扁平 proxies 转结构化
        传入 Configuration（rest 面之外的另一转换消费点——单源
        legacy_proxy_to_sdk 的 WS 侧行为锁定）。"""
        fake = _FakeAsyncSdk()
        client, teardown = self._make_wrapper(
            fake, lambda p: None,
            proxies={"https": "http://172.20.112.1:10809"})
        try:
            client.connect(timeout=5.0)
            assert fake.connect_cfg.proxy == {
                "protocol": "http",
                "host": "172.20.112.1", "port": 10809}
        finally:
            client.stop()
            teardown()

    def test_ws_blank_channel_value_no_proxy(self):
        """WS 面 blank 通道值：https 键显式 None（raw 假值）不进
        转换——cfg.proxy=None 直连（与 rest 面 blank 值例同语义）。"""
        fake = _FakeAsyncSdk()
        client, teardown = self._make_wrapper(
            fake, lambda p: None, proxies={"https": None})
        try:
            client.connect(timeout=5.0)
            assert fake.connect_cfg.proxy is None
        finally:
            client.stop()
            teardown()

    def test_stop_without_loop_only_joins_dispatch(self):
        """loop=None 构造态的 stop：跳过 close 协程只收尾分发线程
        （不炸——桥接基座缺失时的关停防御分支）。"""
        from core.data.fapi.stream import _NewSdkWsClient  # noqa: SLF001

        client = _NewSdkWsClient(
            stream_url="wss://fstream.example.com",
            on_message=lambda p: None, on_close=lambda: None,
            loop=None, _sdk_client_factory=lambda cfg: _FakeAsyncSdk())
        client.stop()  # 不抛即通过（loop None 分支）

    def test_stop_during_connect_success_window_returns_false(self):
        """connect 成功后置停窗口：connect 期间用户 stop()——连接
        建立即弃（_on_open 已发事件，置停检查 client.stop+解除挂载
        +return False 不进泊等；_run 置停 break 不发 disconnected）。
        确定性驱动（此前为竞态偶发触达——覆盖波动源）。"""
        recorder = _Recorder()
        script = _ClientScript(["ok"])
        holder: dict = {}

        class _StopInsideConnectOkFactory:
            """connect 成功前触发用户 stop（成功版置停窗口注入）。"""

            def __call__(self, **kwargs):
                client = script(**kwargs)
                original_connect = client.connect

                def _connect(timeout: float = 30.0):
                    holder["stream"].stop()  # 置停+解除挂载
                    original_connect(timeout)  # 成功返回

                client.connect = _connect
                return client

        stream = _make_stream(recorder, _StopInsideConnectOkFactory())
        holder["stream"] = stream
        stream.start()
        stream._thread.join(timeout=5.0)  # noqa: SLF001
        if stream._thread.is_alive():  # noqa: SLF001——防御收尾
            holder["stream"].stop()
            stream._thread.join(timeout=5.0)  # noqa: SLF001
        assert not stream._thread.is_alive()  # noqa: SLF001
        assert script.clients[0].stopped  # 置停窗口内连接被弃（清理）
        states = [e.state for e in recorder.connections]
        assert states == ["connected"]  # 置停 break 不发 disconnected

    def test_last_activity_none_before_connect_and_set_after(self):
        """CARD-02 真实包装器活性锚：构造后 None（未建连）→connect
        尾初始化非 None（替身 property 是等价面，生产 property 此处
        直测）。"""
        fake = _FakeAsyncSdk()
        client, teardown = self._make_wrapper(fake, lambda p: None)
        try:
            assert client.last_activity is None  # 构造后未建连
            client.connect(timeout=5.0)
            assert client.last_activity is not None  # 建连锚点已置
        finally:
            client.stop()
            teardown()

    def test_close_connection_event_wakes_parking(self):
        """首验④断线路径：SDK close 连接事件→_handle_close→on_close
        回调（泊等唤醒链——替身经注册的 handler 直接触发）。"""
        woke: list[bool] = []
        fake = _FakeAsyncSdk()
        client, teardown = self._make_wrapper(
            fake, lambda p: None, on_close=lambda: woke.append(True))
        try:
            client.connect(timeout=5.0)
            assert "close" in fake.connection_handlers
            fake.connection_handlers["close"]()  # SDK 断线事件
            assert woke == [True]
        finally:
            client.stop()
            teardown()

    def test_stop_swallows_close_connection_exception(self):
        """stop 防御面：close_connection 抛异常被吞（已置停——关停
        不外抛），分发线程仍收尾。"""
        fake = _FakeAsyncSdk()
        fake.close_connection_raises = True
        client, teardown = self._make_wrapper(fake, lambda p: None)
        client.connect(timeout=5.0)
        client.stop()  # 不抛即通过
        assert fake.close_connection_calls == 1
        dispatch = client._dispatch_thread  # noqa: SLF001——收尾观察锚
        assert dispatch is not None and not dispatch.is_alive()
        teardown()

    def test_default_factory_constructs_real_client_locally(self):
        """真实 SDK 构造分支（默认工厂+包装器无注入路径）：构造纯
        本地不建连（首验①），类型名锚定——测试注入替身时被绕过的
        两行的行为面。类型名断言（非 isinstance）：测试文件不 import
        SDK 顶层——全工程 import 白名单（tests 亦在扫描面）约束。"""
        from core.data.fapi.stream import _NewSdkWsClient  # noqa: SLF001

        wrapper = _NewSdkWsClient(
            stream_url="wss://fstream.example.com",
            on_message=lambda p: None, on_close=lambda: None)
        assert type(wrapper._client).__name__ == (  # noqa: SLF001——构造面
            "DerivativesTradingUsdsFuturesWebSocketStreams")
        # BinanceFapiStream 默认工厂直调（注入替身时被绕过的分派行）
        made = BinanceFapiStream._default_client_factory(
            stream_url="wss://fstream.example.com",
            on_message=lambda p: None, on_close=lambda: None)
        assert isinstance(made, _NewSdkWsClient)
        assert made.last_activity is None  # 未 connect——构造不建连


class TestFirstProbeRecordAnchors:
    """VS-2.5 首验留档核验（文档核验面）：stream.py 模块头四要素首验
    记录存在性文本锚——防记录被误删（第四要素是退避循环归属本层自持
    的裁决依据，第二要素时序是订阅面断言的契约源）。"""

    def test_module_header_four_elements_present(self):
        stream_path = (
            Path(__file__).resolve().parents[3]
            / "core" / "data" / "fapi" / "stream.py")
        header = stream_path.read_text(encoding="utf-8").split('"""')[1]
        assert "新 SDK 首验记录" in header  # 记录在场
        for anchor in ("构造与连通", "订阅面", "回调签名", "断线行为"):
            assert anchor in header, f"首验四要素缺失：{anchor}"
        # 要素④裁决锚：内建自动重连判定（退避归属维持本层自持）
        assert "内建自动重连存在" in header
        # 要素②时序锚：回调注册须后于订阅（先注册被连接映射丢弃）
        assert "subscribe 之后" in header
