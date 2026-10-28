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

    def klines(self, *, symbol, interval, startTime, endTime, limit):
        if "klines" in self.raise_on:
            raise self.raise_on["klines"]
        self.klines_calls.append({
            "symbol": symbol, "interval": interval,
            "startTime": startTime, "endTime": endTime, "limit": limit})
        if not self.klines_pages:
            return []  # 页脚本耗尽=区间内无可得数据（真实端点语义）
        return self.klines_pages.pop(0)

    def exchange_info(self):
        if "exchange_info" in self.raise_on:
            raise self.raise_on["exchange_info"]
        return self.exchange_info_script

    def funding_rate(self, *, symbol, startTime, endTime):
        if "funding_rate" in self.raise_on:
            raise self.raise_on["funding_rate"]
        if not self.funding_rows_script:
            return []
        if len(self.funding_rows_script) > 1:
            return self.funding_rows_script.pop(0)
        return self.funding_rows_script[0]


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
