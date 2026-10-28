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

import json
import threading
import time

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
        self._lock = threading.Lock()

    def on_closed_bar(self, event: ClosedBarEvent) -> None:
        with self._lock:
            self.closed.append(event)
        if self.raise_in_closed:
            raise RuntimeError("scripted: 消费方回调故障")

    def on_connection(self, event: ConnEvent) -> None:
        with self._lock:
            self.connections.append(event)


class _ClientScript:
    """客户端工厂脚本（按序产出指定行为的 FakeFapiClient）。"""

    def __init__(self, behaviors: list[str]):
        """behaviors 每项："ok"（连接成功）/"fail"（构造抛异常）。"""
        self.behaviors = list(behaviors)
        self.clients: list[FakeFapiClient] = []
        self.calls = 0  # 工厂调用次数（含构造失败——重试计数面）

    def __call__(self, **kwargs):
        index = self.calls
        self.calls += 1
        behavior = (
            self.behaviors[index] if index < len(self.behaviors)
            else self.behaviors[-1] if self.behaviors else "ok")

        class _Client(FakeFapiClient):
            pass

        _Client.raise_on_construct = behavior == "fail"
        _Client.construct_connects = True
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
            client.deliver_raw(
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
        client.deliver_raw(kline_ws_payload(event, closed=False))
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
        client.deliver_raw(kline_ws_payload(event))
        client.deliver_raw(kline_ws_payload(event))
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
        client.deliver_raw(kline_ws_payload(event))
        client.deliver_raw(kline_ws_payload(event))
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
        # 等第三次工厂调用（脚本第 3 项 ok）产生成功实例
        assert _wait_until(
            lambda: script.calls >= 3 and len(script.clients) >= 1)
        assert _wait_until(lambda: stream.connected)
        script.clients[0].die()  # 成功连接后断线（fail 调用不入实例列）
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
        client.deliver_raw(kline_ws_payload(event))  # 00:00 bar 闭合
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
        client.deliver_raw(json.dumps(
            {"result": None, "id": 1}))  # 订阅确认控制消息
        client.deliver_raw("not-a-json{")  # 坏 JSON
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
        client.deliver_raw(json.dumps(
            {"stream": "btcusdt@kline_1m", "data": [1, 2]}))
        client.deliver_raw(json.dumps(
            {"stream": "btcusdt@kline_1m", "data": "oops"}))
        # k 非 dict 两形态（list/str）+k 缺失
        client.deliver_raw(json.dumps(
            {"stream": "btcusdt@kline_1m",
             "data": {"e": "kline", "s": "BTCUSDT", "k": [1]}}))
        client.deliver_raw(json.dumps(
            {"stream": "btcusdt@kline_1m",
             "data": {"e": "kline", "s": "BTCUSDT", "k": "bad"}}))
        client.deliver_raw(json.dumps(
            {"stream": "btcusdt@kline_1m",
             "data": {"e": "kline", "s": "BTCUSDT"}}))

        assert recorder.closed == []  # 畸形消息零投递
        assert len(recorder.connections) == connected_events  # 无断线/重连
        assert stream.connected  # 连接存活（未伪断）

        # 对照：后续正常闭合消息仍恰一次投递（防线不误伤正常路径）
        event = make_closed_event()
        client.deliver_raw(kline_ws_payload(event))
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
        client.deliver_raw(kline_ws_payload(stray))  # 同步内联——返回即处理完
        assert recorder.closed == []  # 清单外键不投递
        assert stream.last_push(("BTCUSDT", "30m")) is None  # 不入健康面

        member = make_closed_event(timeframe="1m")  # 固定 12 键成员
        client.deliver_raw(kline_ws_payload(member))
        assert _wait_until(lambda: len(recorder.closed) == 1)
        assert stream.last_push(("BTCUSDT", "1m")) is not None
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
        client.deliver_raw(kline_ws_payload(event))
        assert _wait_until(lambda: len(recorder.closed) == 1)
        got = recorder.closed[0]
        assert got.symbol == "BTCUSDT" and got.timeframe == "1m"
        assert got.open_time == pd.Timestamp("2026-10-28T00:00:00Z")
        assert got.open == 100.0 and got.high == 110.0
        assert got.low == 95.0 and got.close == 105.0
        assert got.volume == 42.5
        assert got.closed is True
        stream.stop()
