"""fapi REST 兜底客户端测试（任务 2——全合成驱动、零真实网络）。

覆盖面（tasks.md 完成标准逐项）：
- 三函数签名兼容注入（构造帧/空帧/异常三分支）；
- 空区间返回空帧非 None；
- 网络异常上抛（调用方捕获的传播语义——本层不吞）；
- 帧 index（开盘时刻 UTC）+五列与 feed 内存序列同构；
- 代理语义沿 fetcher.py:108 先例（显式 proxies 传参+trust_env=False
  阻断环境代理泄漏）；
- 端点白名单常量可被触金扫描消费；
- FAPI_DATA_DIR / FAPI_MIN_NOTIONAL_* 覆写断言（fapi_env 解析面）。
"""
from __future__ import annotations

import requests

import pandas as pd
import pytest

from core.data.fapi.rest import (
    FAPI_ALLOWED_METHOD_FAMILIES,
    FAPI_REST_ENDPOINTS,
    FapiRestClient,
    fapi_env,
    fapi_min_notional_overrides,
)

# klines 行形制锚：[openTime, open, high, low, close, volume, closeTime, ...]
_T0 = 1774569600000  # 2026-03-27T00:00:00Z 的 ms（脚本基准）


def _kline_row(open_ms: int, *, price: float = 100.0) -> list:
    return [
        open_ms, str(price), str(price + 1), str(price - 1),
        str(price + 0.5), "42.5", open_ms + 59999, 10, "1", "0.5", "0",
    ]


class _ApiResponse:
    """新 SDK ApiResponse 替身（.data 惰性载荷直通）。"""

    def __init__(self, data):
        self.data = data


class _Dumpable:
    """新 SDK 建模响应替身（model_dump(by_alias=True) 回端点字段名）。"""

    def __init__(self, payload: dict):
        self._payload = payload

    def model_dump(self, by_alias: bool = False) -> dict:
        return dict(self._payload)


class FakeSDK:
    """UMFutures REST 替身（脚本化响应+调用记录+session 面）。"""

    def __init__(self, *, klines_pages=None, exchange_info=None,
                 funding_rows=None, raise_on=None):
        self.klines_pages = list(klines_pages or [])
        self.exchange_info_script = exchange_info
        self.funding_rows_script = list(funding_rows or [])
        self.raise_on = dict(raise_on or {})
        self.session = requests.Session()  # trust_env 断言面
        self.constructor_kwargs: dict = {}
        self.klines_calls: list[dict] = []

    def kline_candlestick_data(
            self, symbol, interval, start_time=None, end_time=None,
            limit=None):
        """新 SDK kline_candlestick_data 替身（ApiResponse.data 直通
        原始行列表——空响应模型直通形制源码级锚定）。"""
        if "klines" in self.raise_on:
            raise self.raise_on["klines"]
        self.klines_calls.append({
            "symbol": symbol, "interval": interval,
            "startTime": start_time, "endTime": end_time, "limit": limit})
        if not self.klines_pages:
            return _ApiResponse([])  # 页脚本耗尽=区间内无可得数据
        # 端点尊重 limit 截断（klines 契约）——窗口路径「至多 N 根」
        # 依赖此建模（CARD-02 边界用例 max_bars=1 首次暴露）
        return _ApiResponse(self.klines_pages.pop(0)[:limit])

    def exchange_information(self):
        """新 SDK exchange_information 替身（建模响应 dump 面）。"""
        if "exchange_info" in self.raise_on:
            raise self.raise_on["exchange_info"]
        return _ApiResponse(
            _Dumpable(self.exchange_info_script or {}))

    def get_funding_rate_history(
            self, symbol=None, start_time=None, end_time=None, limit=None):
        """新 SDK get_funding_rate_history 替身（建模行 dump 回端点
        字段名 dict——消费面读 fundingRate/fundingTime）。"""
        if "funding_rate" in self.raise_on:
            raise self.raise_on["funding_rate"]
        if not self.funding_rows_script:
            return _ApiResponse([])
        if len(self.funding_rows_script) > 1:
            rows = self.funding_rows_script.pop(0)
        else:
            rows = self.funding_rows_script[0]
        return _ApiResponse([_Dumpable(row) for row in rows])


def _make_client(sdk_kwargs: dict, **client_kwargs) -> tuple[FapiRestClient, FakeSDK]:
    sdk = FakeSDK(**sdk_kwargs)
    holder: dict = {}

    def factory(**kwargs):
        holder["kwargs"] = kwargs
        return sdk

    client = FapiRestClient(client_factory=factory, **client_kwargs)
    return client, sdk


# ── 签名注入与三分支 ───────────────────────────────────────

class TestThreeFunctions:
    def test_fetch_closed_bars_constructed_frame(self):
        """①④构造帧分支：帧形制与 feed 内存序列同构。"""
        client, _ = _make_client({"klines_pages": [[
            _kline_row(_T0), _kline_row(_T0 + 60_000)]]})
        frame = client.fetch_closed_bars(
            "BTCUSDT", "1m",
            pd.Timestamp("2026-03-27T00:00:00Z"),
            pd.Timestamp("2026-03-27T00:10:00Z"))
        assert len(frame) == 2
        assert frame.index.name == "timestamp"
        assert str(frame.index.tz) == "UTC"
        assert list(frame.columns) == ["open", "high", "low", "close", "volume"]
        assert frame["open"].tolist() == [100.0, 100.0]
        assert frame.index[0] == pd.Timestamp(_T0, unit="ms", tz="UTC")
        assert frame.index[1] == pd.Timestamp(
            _T0 + 60_000, unit="ms", tz="UTC")

    def test_fetch_closed_bars_empty_region_empty_frame(self):
        """②空区间返回空帧非 None（同构列结构）。"""
        client, _ = _make_client({"klines_pages": [[]]})
        frame = client.fetch_closed_bars(
            "ETHUSDT", "1h",
            pd.Timestamp("2026-03-27T00:00:00Z"),
            pd.Timestamp("2026-03-27T01:00:00Z"))
        assert frame is not None and frame.empty
        assert list(frame.columns) == ["open", "high", "low", "close", "volume"]

    def test_fetch_closed_bars_network_exception_propagates(self):
        """③网络异常上抛（调用方捕获语义——本层不吞）。"""
        client, _ = _make_client(
            {"raise_on": {"klines": ConnectionError("scripted: 断网")}})
        with pytest.raises(ConnectionError):
            client.fetch_closed_bars(
                "BTCUSDT", "1m",
                pd.Timestamp("2026-03-27T00:00:00Z"),
                pd.Timestamp("2026-03-27T00:10:00Z"))

    def test_fetch_exchange_info_dict_passthrough_and_raise(self):
        """①③exchangeInfo：dict 透传/异常上抛两分支。"""
        client, _ = _make_client({"exchange_info": {"symbols": ["BTCUSDT"]}})
        assert client.fetch_exchange_info() == {"symbols": ["BTCUSDT"]}
        client_fail, _ = _make_client(
            {"raise_on": {"exchange_info": TimeoutError("scripted")}})
        with pytest.raises(TimeoutError):
            client_fail.fetch_exchange_info()

    def test_fetch_funding_rate_frame_and_empty(self):
        """①②fundingRate：帧分支（8h 网格 index）与空帧分支。"""
        client, _ = _make_client({"funding_rows": [[
            {"symbol": "BTCUSDT", "fundingTime": _T0,
             "fundingRate": "0.0001", "markPrice": "100"},
        ]]})
        frame = client.fetch_funding_rate(
            "BTCUSDT",
            pd.Timestamp("2026-03-27T00:00:00Z"),
            pd.Timestamp("2026-03-27T08:00:00Z"))
        assert list(frame.columns) == ["funding_rate"]
        assert frame["funding_rate"].tolist() == [0.0001]
        assert frame.index.name == "timestamp"
        client_empty, _ = _make_client({"funding_rows": [[]]})
        assert client_empty.fetch_funding_rate(
            "BTCUSDT",
            pd.Timestamp("2026-03-27T00:00:00Z"),
            pd.Timestamp("2026-03-27T08:00:00Z")).empty

    def test_klines_pagination_multi_page(self):
        """分页补拉：多页脚本全收集+分页参数推进（startTime 步进）。"""
        page1 = [_kline_row(_T0 + i * 60_000) for i in range(3)]
        page2 = [_kline_row(_T0 + 3 * 60_000 + i * 60_000) for i in range(2)]
        client, sdk = _make_client({"klines_pages": [page1, page2]})
        # end=末根收盘时刻（五行全闭合）；末页后第三次空探测终止分页
        frame = client.fetch_closed_bars(
            "BTCUSDT", "1m",
            pd.Timestamp(_T0, unit="ms", tz="UTC"),
            pd.Timestamp(_T0 + 5 * 60_000, unit="ms", tz="UTC"))
        assert len(frame) == 5
        assert len(sdk.klines_calls) == 3  # 两页有数据+一次空探测终止
        # 第二页起点=page1 末根（第三根）开盘+1ms
        assert sdk.klines_calls[1]["startTime"] == _T0 + 2 * 60_000 + 1

    def test_klines_half_open_end_bound(self):
        """半开区间语义：end 恰为某根开盘时刻时该行被滤（本地兜底）。"""
        client, _ = _make_client({"klines_pages": [[
            _kline_row(_T0), _kline_row(_T0 + 60_000)]]})
        frame = client.fetch_closed_bars(
            "BTCUSDT", "1m",
            pd.Timestamp(_T0, unit="ms", tz="UTC"),
            pd.Timestamp(_T0 + 60_000, unit="ms", tz="UTC"))  # end=第二根开盘
        assert len(frame) == 1
        assert frame.index[0] == pd.Timestamp(_T0, unit="ms", tz="UTC")

    def test_klines_forming_tail_filtered(self):
        """闭合契约：未闭合尾行（openTime+tf>end）滤除——REST 只回闭合帧。"""
        client, _ = _make_client({"klines_pages": [[
            _kline_row(_T0),            # 00:00 根——00:01 已闭合 ✓
            _kline_row(_T0 + 60_000),   # 00:01 根——end=00:01:59.999 未闭合 ✗
        ]]})
        frame = client.fetch_closed_bars(
            "BTCUSDT", "1m",
            pd.Timestamp(_T0, unit="ms", tz="UTC"),
            pd.Timestamp(_T0 + 120_000 - 1, unit="ms", tz="UTC"))
        assert len(frame) == 1
        assert frame.index[0] == pd.Timestamp(_T0, unit="ms", tz="UTC")


# ── 代理语义（fetcher.py:108 先例）────────────────────────

class TestDirtyRowFiltering:
    """CARD-01 脏行单点过滤：klines/funding 行级脏数据丢弃不进帧。

    行级脏形制五类（klines）：OHLC/volume 非有限值（NaN/inf——
    is_valid_bar_values 判定）与字段不可解析（坏串 ValueError/None
    TypeError/缺字段 IndexError）——单点覆盖 reconcile/建库/重建/
    费率四消费路径（fetch 全走 _klines_rows_to_frame）。
    """

    def test_klines_mixed_dirty_rows_good_rows_kept(self):
        """混合六行（NaN/inf/坏串/None/缺字段各一+好行一）→恰好行。"""
        good = _kline_row(_T0)
        nan_row = _kline_row(_T0 + 60_000)
        nan_row[1] = "NaN"          # open 非有限
        inf_row = _kline_row(_T0 + 120_000)
        inf_row[2] = "Infinity"     # high 非有限
        bad_str_row = _kline_row(_T0 + 180_000)
        bad_str_row[4] = "abc"      # close 不可解析
        none_row = _kline_row(_T0 + 240_000)
        none_row[5] = None          # volume 不可解析
        short_row = [_T0 + 300_000]  # 缺字段（volume 位 IndexError）
        client, _ = _make_client({"klines_pages": [
            [good, nan_row, inf_row, bad_str_row, none_row, short_row]]})
        frame = client.fetch_closed_bars(
            "BTCUSDT", "1m",
            pd.Timestamp("2026-03-27T00:00:00Z"),
            pd.Timestamp("2026-03-27T00:10:00Z"))
        assert len(frame) == 1  # 五脏行全弃、好行恰保留
        assert frame.index[0] == pd.Timestamp(_T0, unit="ms", tz="UTC")
        assert frame["open"].tolist() == [100.0]

    def test_klines_all_dirty_rows_empty_frame(self):
        """全脏行→空帧（非 None——同构列结构保持）。"""
        nan_row = _kline_row(_T0)
        nan_row[3] = "NaN"  # low 非有限
        client, _ = _make_client({"klines_pages": [[nan_row]]})
        frame = client.fetch_closed_bars(
            "BTCUSDT", "1m",
            pd.Timestamp("2026-03-27T00:00:00Z"),
            pd.Timestamp("2026-03-27T00:10:00Z"))
        assert frame is not None and frame.empty
        assert list(frame.columns) == ["open", "high", "low", "close",
                                       "volume"]

    def test_funding_mixed_dirty_rows_good_rows_kept(self):
        """funding 混合五行（NaN/None/缺键/坏串各一+好行一）→恰好行。

        直转面同语义（CARD-01 修复第二面）：fundingRate 不可解析/
        非有限或 fundingTime 缺失的行丢弃。
        """
        good = {"symbol": "BTCUSDT", "fundingTime": _T0,
                "fundingRate": "0.0001", "markPrice": "100"}
        nan_rate = {"symbol": "BTCUSDT", "fundingTime": _T0 + 28_800_000,
                    "fundingRate": "NaN"}
        none_rate = {"symbol": "BTCUSDT", "fundingTime": _T0 + 57_600_000,
                     "fundingRate": None}
        missing_time = {"symbol": "BTCUSDT", "fundingRate": "0.0002"}
        bad_str = {"symbol": "BTCUSDT", "fundingTime": _T0 + 86_400_000,
                   "fundingRate": "abc"}
        client, _ = _make_client({"funding_rows": [
            [good, nan_rate, none_rate, missing_time, bad_str]]})
        frame = client.fetch_funding_rate(
            "BTCUSDT",
            pd.Timestamp("2026-03-27T00:00:00Z"),
            pd.Timestamp("2026-03-27T08:00:00Z"))
        assert frame["funding_rate"].tolist() == [0.0001]  # 四脏行全弃
        assert frame.index.tolist() == [
            pd.Timestamp(_T0, unit="ms", tz="UTC")]


class TestProxySemantics:
    def test_trust_env_disabled_blocks_env_proxy_leak(self):
        """⑤直连通道：session.trust_env=False（环境代理变量不生效）。"""
        client, sdk = _make_client({})
        client._sdk()  # noqa: SLF001——触发建连
        assert sdk.session.trust_env is False

    def test_explicit_proxies_passed_to_sdk(self):
        """⑤代理通道：FAPI_PROXY 显式配置直传 SDK 构造。"""
        proxies = {"https": "http://172.20.112.1:10809"}
        sdk = FakeSDK()
        holder: dict = {}

        def factory(**kwargs):
            holder.update(kwargs)
            return sdk

        client = FapiRestClient(
            client_factory=factory, proxies=proxies)
        client._sdk()  # noqa: SLF001
        assert holder["proxies"] is proxies
        assert sdk.session.trust_env is False  # 显式 proxies 不受影响

    def test_env_proxy_set_still_trust_env_false(self, monkeypatch):
        """VS-2.2 字面前置：环境变量 HTTP(S)_PROXY 置位+无显式代理
        ——建连后 trust_env 仍 False（环境代理通道被阻断而非依赖
        环境恰好未置位）。"""
        monkeypatch.setenv("HTTP_PROXY", "http://env-leak:1080")
        monkeypatch.setenv("HTTPS_PROXY", "http://env-leak:1080")
        client, sdk = _make_client({})
        client._sdk()  # noqa: SLF001——触发建连
        assert sdk.session.trust_env is False

    def test_default_factory_converts_legacy_proxy_to_sdk_form(self):
        """CARD-01：默认工厂把旧扁平 proxies 转 SDK 结构化 proxy 形制。

        SDK parse_proxies 只认 {protocol,host,port}——旧扁平直传拼出
        https://: 畸形代理，显式代理部署 REST 全断。构造纯本地零网络。
        """
        client = FapiRestClient._default_client_factory(
            base_url="https://fapi.binance.com",
            proxies={"https": "http://172.20.112.1:10809"}, timeout=30)
        assert client.configuration.proxy == {
            "protocol": "http", "host": "172.20.112.1", "port": 10809}

    def test_default_factory_proxy_none_and_empty_passthrough(self):
        """CARD-01：proxies=None 与空 dict 均直传 None（直连——
        空值不进转换分支）。"""
        client_none = FapiRestClient._default_client_factory(
            base_url="https://fapi.binance.com", proxies=None, timeout=30)
        assert client_none.configuration.proxy is None
        client_empty = FapiRestClient._default_client_factory(
            base_url="https://fapi.binance.com", proxies={}, timeout=30)
        assert client_empty.configuration.proxy is None

    def test_default_factory_http_channel_fallback(self):
        """CARD-01：无 https 键时 http 通道兜底（WS 面同型——
        https 优先、http 兜底）。"""
        client = FapiRestClient._default_client_factory(
            base_url="https://fapi.binance.com",
            proxies={"http": "http://10.0.0.8:8080"}, timeout=30)
        assert client.configuration.proxy == {
            "protocol": "http", "host": "10.0.0.8", "port": 8080}

    def test_real_client_session_trust_env_blocked_via_default_factory(self):
        """VS-2.2 真实工厂路径：默认工厂产物 session 探测命中
        （新 SDK 私有 _session 属性在场）且 trust_env 被置 False——
        替身面（公有 session 属性）从未证明真实属性名探测命中，
        SDK 改名时此面唯一拦截线。构造纯本地零网络。"""
        from requests import Session

        client = FapiRestClient()  # 无注入——默认工厂真实构造
        sdk = client._sdk()  # noqa: SLF001——触发建连+探测+阻断
        session = getattr(sdk, "session", None) or getattr(sdk, "_session", None)
        assert isinstance(session, Session)  # 探测命中真实属性
        assert session.trust_env is False  # 环境代理阻断落地


class TestLegacyProxyConversion:
    """legacy_proxy_to_sdk 转换单源直测（WS/REST 两消费面共用——
    CARD-01 修复后公开单源；分支边界面）。"""

    def test_https_url_without_port_defaults_443(self):
        """https URL 无端口→443（WS over TLS 缺省）。"""
        from core.data.fapi.stream import legacy_proxy_to_sdk

        assert legacy_proxy_to_sdk("https://proxy.example.com") == {
            "protocol": "https", "host": "proxy.example.com", "port": 443}

    def test_http_url_without_port_defaults_80(self):
        """http URL 无端口→80。"""
        from core.data.fapi.stream import legacy_proxy_to_sdk

        assert legacy_proxy_to_sdk("http://proxy.example.com") == {
            "protocol": "http", "host": "proxy.example.com", "port": 80}


# ── 白名单与环境变量面 ─────────────────────────────────────

class TestWhitelistAndEnv:
    def test_endpoint_whitelist_constants(self):
        """⑥端点/方法族白名单常量（触金扫描消费面）。"""
        assert set(FAPI_REST_ENDPOINTS) == {
            "klines", "exchangeInfo", "fundingRate"}
        assert set(FAPI_ALLOWED_METHOD_FAMILIES) == {
            "kline", "exchange_info", "funding_rate", "mark_price"}

    def test_fapi_env_defaults_and_overrides(self):
        """⑦FAPI_DATA_DIR 等四键缺省与覆写。"""
        defaults = fapi_env(env={})
        assert defaults["data_dir"].as_posix() == "data/market_fapi"
        assert defaults["proxy"] is None
        assert defaults["ws_base"] == "wss://fstream.binance.com"
        assert defaults["rest_base"] == "https://fapi.binance.com"
        overridden = fapi_env(env={
            "FAPI_DATA_DIR": "data/custom_fapi",
            "FAPI_PROXY": "http://proxy:1080",
            "FAPI_WS_BASE": "wss://test.example",
            "FAPI_REST_BASE": "https://test.example",
        })
        assert overridden["data_dir"].as_posix() == "data/custom_fapi"
        assert overridden["proxy"] == "http://proxy:1080"
        assert overridden["ws_base"] == "wss://test.example"
        assert overridden["rest_base"] == "https://test.example"

    def test_min_notional_overrides(self):
        """⑦FAPI_MIN_NOTIONAL_* 覆写解析（未设键不入返回）。"""
        assert fapi_min_notional_overrides(env={}) == {}
        overrides = fapi_min_notional_overrides(env={
            "FAPI_MIN_NOTIONAL_BTCUSDT": "100.0",
            "FAPI_MIN_NOTIONAL_ETHUSDT": "20.0",
        })
        assert overrides == {"BTCUSDT": 100.0, "ETHUSDT": 20.0}


class TestTsParsingAndPaginationGuard:
    """阶段四盲区消解：naive 时刻 UTC 假定/分页 cursor 自锁防御。"""

    def test_naive_timestamp_inputs_treated_as_utc(self):
        """naive pd.Timestamp 入参按 UTC 假定（tz_localize 分支）——
        与 aware 入参同帧结果（对账输入泛化语义）。"""
        naive_client, _ = _make_client(
            {"klines_pages": [[_kline_row(_T0)]]})
        aware_client, _ = _make_client(
            {"klines_pages": [[_kline_row(_T0)]]})
        frame_naive = naive_client.fetch_closed_bars(
            "BTCUSDT", "1m",
            pd.Timestamp("2026-03-27T00:00:00"),  # naive
            pd.Timestamp("2026-03-27T00:10:00"))
        frame_aware = aware_client.fetch_closed_bars(
            "BTCUSDT", "1m",
            pd.Timestamp("2026-03-27T00:00:00Z"),
            pd.Timestamp("2026-03-27T00:10:00Z"))
        pd.testing.assert_frame_equal(frame_naive, frame_aware)

    def test_klines_cursor_selflock_breaks_pagination(self):
        """端点异常自锁防御：页内 openTime 不前进（同根重复返回）——
        cursor 不前进即 break 终止分页（防死循环）。"""
        same_row = _kline_row(_T0)
        client, sdk = _make_client(
            {"klines_pages": [[same_row], [same_row]]})
        frame = client.fetch_closed_bars(
            "BTCUSDT", "1m",
            pd.Timestamp("2026-03-27T00:00:00Z"),
            pd.Timestamp("2026-03-27T01:00:00Z"))  # 大区间（多次分页企图）
        assert len(sdk.klines_calls) == 2  # 第二次同 openTime 即终止
        assert len(frame) == 1


# ── 运行时增量窗口参数（add-data-freshness-guardian 契约 3，VS-1.9）──

class TestFetchClosedBarsWindow:
    """max_bars 单轮窗口（start 锚定）与 None 分页语义回归。

    完成标准逐项：窗口调用从起点取至多 N 根（起点不裁剪+limit=
    min(N, KLINE_PAGE_LIMIT)+单页请求）；缺省调用行为与改前等价
    （分页推进形制不变）；窗口路径尾部 forming 被闭合过滤（实际
    返回可少于 N）。
    """

    def test_window_anchor_start_not_end(self):
        """start 锚定：请求起点=缺口起点（不被 end−N×周期 裁剪）。"""
        # 缺口 60 根（00:00→01:00），窗口 N=10：若 end 锚定会把起点
        # 推到 00:50——缺陷形制（深缺口中段空洞永久残留）；正确形制
        # 起点=缺口起点 00:00、单页 limit=10
        gap_start = pd.Timestamp(_T0, unit="ms", tz="UTC")
        gap_end = pd.Timestamp(_T0 + 60 * 60_000, unit="ms", tz="UTC")
        page = [_kline_row(_T0 + i * 60_000) for i in range(10)]
        client, sdk = _make_client({"klines_pages": [page]})
        frame = client.fetch_closed_bars(
            "BTCUSDT", "1m", gap_start, gap_end, max_bars=10)
        assert len(sdk.klines_calls) == 1  # 单页请求（不再分页拉区间）
        call = sdk.klines_calls[0]
        assert call["startTime"] == _T0  # 起点不裁剪（start 锚定）
        assert call["limit"] == 10
        assert len(frame) == 10
        assert frame.index[0] == gap_start  # 从起点起取
        assert frame.index[-1] == pd.Timestamp(
            _T0 + 9 * 60_000, unit="ms", tz="UTC")

    def test_window_limit_capped_by_page_limit(self):
        """limit=min(N, KLINE_PAGE_LIMIT)：N 超端点硬限时钳到 1500。"""
        gap_start = pd.Timestamp(_T0, unit="ms", tz="UTC")
        gap_end = pd.Timestamp(_T0 + 3_600_000, unit="ms", tz="UTC")
        client, sdk = _make_client({"klines_pages": [[]]})
        client.fetch_closed_bars(
            "BTCUSDT", "1m", gap_start, gap_end, max_bars=5000)
        assert sdk.klines_calls[0]["limit"] == 1500

    def test_window_forming_tail_filtered(self):
        """窗口路径闭合过滤：尾部 forming 行被滤——返回可少于 N。"""
        gap_start = pd.Timestamp(_T0, unit="ms", tz="UTC")
        gap_end = pd.Timestamp(_T0 + 120_000 - 1, unit="ms", tz="UTC")
        # 两根在脚本里：00:00 闭合、00:01 forming（end=00:01:59.999）
        page = [_kline_row(_T0), _kline_row(_T0 + 60_000)]
        client, _ = _make_client({"klines_pages": [page]})
        frame = client.fetch_closed_bars(
            "BTCUSDT", "1m", gap_start, gap_end, max_bars=10)
        assert len(frame) == 1  # 少于 N=10（尾部 forming 滤除）

    def test_window_empty_region_empty_frame(self):
        """窗口路径空区间（start>=end）零请求、空帧非 None。"""
        t = pd.Timestamp(_T0, unit="ms", tz="UTC")
        client, sdk = _make_client({"klines_pages": [[]]})
        frame = client.fetch_closed_bars(
            "BTCUSDT", "1m", t, t, max_bars=10)
        assert frame is not None and frame.empty
        assert len(sdk.klines_calls) == 0  # 区间空——不发请求

    def test_default_none_pagination_unchanged(self):
        """max_bars=None（含缺省）分页语义不变回归。"""
        page1 = [_kline_row(_T0 + i * 60_000) for i in range(3)]
        page2 = [_kline_row(_T0 + 3 * 60_000 + i * 60_000)
                 for i in range(2)]
        client, sdk = _make_client({"klines_pages": [page1, page2]})
        end = pd.Timestamp(_T0 + 5 * 60_000, unit="ms", tz="UTC")
        # 显式 None 与缺省同形制（两段对照——脚本重灌防耗尽）
        frame_default = client.fetch_closed_bars(
            "BTCUSDT", "1m",
            pd.Timestamp(_T0, unit="ms", tz="UTC"), end)
        sdk.klines_pages = [list(page1), list(page2)]
        frame_none = client.fetch_closed_bars(
            "BTCUSDT", "1m",
            pd.Timestamp(_T0, unit="ms", tz="UTC"), end, max_bars=None)
        assert len(frame_default) == len(frame_none) == 5
        assert len(sdk.klines_calls) == 6  # 各 3 次（两页+空探测）
        # 分页 limit 仍是端点硬限（窗口不参与分页路径）
        assert all(c["limit"] == 1500 for c in sdk.klines_calls)

    def test_runtime_backfill_max_bars_constant(self):
        """运行时增量单轮上限常量（R-03：单源 10——两消费方共用）。"""
        from core.data.fapi.rest import RUNTIME_BACKFILL_MAX_BARS

        assert RUNTIME_BACKFILL_MAX_BARS == 10

    @pytest.mark.parametrize("bad", [
        pytest.param(-10, id="负整数"),
        pytest.param(0, id="零"),
        pytest.param(float("nan"), id="NaN"),
        pytest.param(float("inf"), id="inf"),
        pytest.param(10.5, id="float"),
        pytest.param(True, id="bool-True"),
        pytest.param(False, id="bool-False"),
        pytest.param("10", id="str"),
    ])
    def test_max_bars_rejects_invalid_values(self, bad):
        """CARD-02：max_bars 畸形值入口快速失败——负/零/NaN/inf/
        float/bool（bool 是 int 子类显式排除）/str 一律 ValueError，
        零请求直达端点（消息锚定入口校验非其他来源）。"""
        client, sdk = _make_client({"klines_pages": [[]]})
        t0 = pd.Timestamp(_T0, unit="ms", tz="UTC")
        t1 = pd.Timestamp(_T0 + 60_000, unit="ms", tz="UTC")
        with pytest.raises(ValueError, match="max_bars 须为正整数"):
            client.fetch_closed_bars(
                "BTCUSDT", "1m", t0, t1, max_bars=bad)
        assert len(sdk.klines_calls) == 0  # 入口拒绝——不发请求

    def test_max_bars_minimum_positive_one_is_valid(self):
        """CARD-02 合法侧边界：max_bars=1（最小正整数）——单页
        limit=1、恰返回首根闭合 bar。"""
        page = [_kline_row(_T0 + i * 60_000) for i in range(3)]
        client, sdk = _make_client({"klines_pages": [page]})
        t0 = pd.Timestamp(_T0, unit="ms", tz="UTC")
        t1 = pd.Timestamp(_T0 + 3 * 60_000, unit="ms", tz="UTC")
        frame = client.fetch_closed_bars(
            "BTCUSDT", "1m", t0, t1, max_bars=1)
        assert len(frame) == 1  # 恰首根（窗口上限生效）
        assert frame.index[0] == t0
        assert sdk.klines_calls[0]["limit"] == 1
