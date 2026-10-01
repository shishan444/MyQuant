"""fetcher 特征测试网（fix/data-signals-review 修复期安全网）。

现状记录语义：期望值从当前实现行为推导钉住。建网时缺口：
_parse_start_ts 各解析分支 / fetch_klines 翻页与停滞停止 /
_http_get 无代理直抛 / _download_archive_csv 三类失败 /
fetch_open_interest_history 全空窗口。
"""

import io
import zipfile
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest
import requests

pytestmark = [pytest.mark.unit]

from core.data.fetcher import (
    _build_session,
    _direct_session,
    _download_archive_csv,
    _http_get,
    _parse_start_ts,
    fetch_klines,
    fetch_open_interest_history,
)


def _kline_row(ts: int, price: float = 40000.0) -> list:
    return [
        ts, str(price), str(price + 50), str(price - 50), str(price + 25),
        "1234.5", ts + 3599_999, "50000.0", 500, "600.0", "24000.0", "0",
    ]


def _resp_with(payload) -> MagicMock:
    resp = MagicMock()
    resp.json.return_value = payload
    resp.raise_for_status = MagicMock()
    return resp


class TestParseStartTs:
    """start_str 解析分支：毫秒直传 / ISO(Z) / N 天前 / N 年前 / 兜底。"""

    def test_millisecond_passthrough(self) -> None:
        assert _parse_start_ts("1700000000000") == 1700000000000

    def test_iso_with_z_suffix(self) -> None:
        # "Z"→"+00:00" 后 fromisoformat 精确换算（aware，无时区歧义）
        assert _parse_start_ts("2024-01-01T00:00:00Z") == 1704067200000

    @pytest.mark.parametrize(
        ("expr", "n_days"),
        [
            pytest.param("1 day ago", 1, id="单数天"),
            pytest.param("30 days ago", 30, id="复数天"),
        ],
    )
    def test_days_ago(self, expr: str, n_days: int) -> None:
        before = datetime.now(timezone.utc)
        ts = _parse_start_ts(expr)
        after = datetime.now(timezone.utc)
        lo = before.timestamp() * 1000 - n_days * 86400_000 - 60_000
        hi = after.timestamp() * 1000 - n_days * 86400_000 + 60_000
        assert lo <= ts <= hi

    @pytest.mark.parametrize(
        ("expr", "years"),
        [
            pytest.param("1 year ago UTC", 1, id="单数年"),
            pytest.param("2 years ago UTC", 2, id="复数年（fetch_klines 默认值）"),
            pytest.param("3 years ago", 3, id="无 UTC 后缀"),
        ],
    )
    def test_years_ago(self, expr: str, years: int) -> None:
        before = datetime.now(timezone.utc)
        ts = _parse_start_ts(expr)
        after = datetime.now(timezone.utc)
        # 年锚定在 [365, 366] 天区间（足以区分相邻年数，不复制 replace 语义）
        lo = before.timestamp() * 1000 - years * 366 * 86400_000 - 60_000
        hi = after.timestamp() * 1000 - years * 365 * 86400_000 + 60_000
        assert lo <= ts <= hi

    def test_unparseable_falls_back_two_years(self) -> None:
        before = datetime.now(timezone.utc)
        ts = _parse_start_ts("not a date")
        after = datetime.now(timezone.utc)
        lo = before.timestamp() * 1000 - 2 * 366 * 86400_000 - 60_000
        hi = after.timestamp() * 1000 - 2 * 365 * 86400_000 + 60_000
        assert lo <= ts <= hi


class TestYearsAgoLeapDay:
    """闰日 2/29 年度回退（CARD-04）：目标年无 2/29 时取 2/28。

    修复前 dt.replace(year=y-n) 在闰日抛 ValueError，从 _parse_start_ts
    逃逸导致全量首拉静默失败；修复后回退 2/28（年度起点近似下界，
    不引入前视）。期望值独立于实现手工核算。
    """

    @staticmethod
    def _frozen(moment):
        class _Frozen(datetime):
            @classmethod
            def now(cls, tz=None):
                return moment

        return _Frozen

    @pytest.mark.parametrize(
        ("frozen", "expr", "expected"),
        [
            pytest.param(
                datetime(2024, 2, 29, 12, 0, tzinfo=timezone.utc),
                "2 year ago UTC",
                datetime(2022, 2, 28, 12, 0, tzinfo=timezone.utc),
                id="闰日退 2 年→非闰年回退 2/28",
            ),
            pytest.param(
                datetime(2024, 2, 29, 0, 0, tzinfo=timezone.utc),
                "1 year ago",
                datetime(2023, 2, 28, 0, 0, tzinfo=timezone.utc),
                id="闰日退 1 年→非闰年回退 2/28",
            ),
            pytest.param(
                datetime(2023, 3, 15, 8, 30, tzinfo=timezone.utc),
                "1 year ago UTC",
                datetime(2022, 3, 15, 8, 30, tzinfo=timezone.utc),
                id="非闰日直通不变",
            ),
            pytest.param(
                datetime(2025, 3, 1, 0, 0, tzinfo=timezone.utc),
                "4 years ago",
                datetime(2021, 3, 1, 0, 0, tzinfo=timezone.utc),
                id="退 4 年直通",
            ),
        ],
    )
    def test_year_rollback(self, frozen, expr, expected) -> None:
        with patch("core.data.fetcher.datetime", self._frozen(frozen)):
            ts = _parse_start_ts(expr)
        assert ts == int(expected.timestamp() * 1000)

    def test_fallback_two_years_leap_day(self) -> None:
        """非法串 fallback 2 年前同样走闰日回退。"""
        frozen = datetime(2024, 2, 29, 23, 59, 59, tzinfo=timezone.utc)
        with patch("core.data.fetcher.datetime", self._frozen(frozen)):
            ts = _parse_start_ts("not a date")
        expected = datetime(2022, 2, 28, 23, 59, 59, tzinfo=timezone.utc)
        assert ts == int(expected.timestamp() * 1000)


def _patch_session_for(session):
    """Patch _session_for 恒返同一 session（CARD-10 后 _http_get 的协作者）。

    工厂 patch 在 thread-local 缓存下失效（同线程先行用例已缓存 session
    则工厂不再被触达）；side_effect 闭包保证翻页多次取用同一实例。
    """
    return patch("core.data.fetcher._session_for", side_effect=lambda kind: session)


class TestFetchKlinesPagination:
    """分页累积、翻页起点推进与停滞保护。"""

    def test_multi_page_accumulates(self) -> None:
        page1 = [_kline_row(1700000000000 + i * 3600_000) for i in range(1000)]
        last_ts = page1[-1][0]
        page2 = [_kline_row(last_ts + 1 + i * 3600_000) for i in range(10)]
        session = MagicMock()
        session.get.side_effect = [_resp_with(page1), _resp_with(page2)]

        with _patch_session_for(session):
            df = fetch_klines(
                symbol="BTCUSDT", interval="1h",
                start_str="1700000000000", end_str="9999999999999",
            )

        assert len(df) == 1010
        # 翻页起点 = 上页最后一根 open_time + 1
        second_params = session.get.call_args_list[1].kwargs["params"]
        assert second_params["startTime"] == last_ts + 1
        assert df.index.is_monotonic_increasing

    def test_empty_response_returns_empty_frame(self) -> None:
        session = MagicMock()
        session.get.return_value = _resp_with([])

        with _patch_session_for(session):
            df = fetch_klines(
                symbol="BTCUSDT", interval="1h", start_str="1700000000000"
            )

        assert df.empty
        assert list(df.columns) == ["open", "high", "low", "close", "volume", "trades"]

    def test_non_advancing_response_stops(self) -> None:
        """响应最后一根 open_time 未前进时立即停止（停滞保护）。"""
        stale = [_kline_row(1704067200000)]  # 2024-01-01，早于请求起点
        session = MagicMock()
        session.get.return_value = _resp_with(stale)

        with _patch_session_for(session):
            df = fetch_klines(
                symbol="BTCUSDT", interval="1h", start_str="1704163200000"
            )

        session.get.assert_called_once()  # 未停滞循环
        assert len(df) == 1  # 停滞行仍被收进结果（现状记录）


class TestHttpGetNoProxy:
    """直连失败且未配置代理时异常直抛，不回退。"""

    @patch("core.data.fetcher._DEFAULT_PROXY", "")
    def test_direct_failure_without_proxy_raises(self) -> None:
        direct = MagicMock()
        direct.get.side_effect = requests.RequestException("unreachable")
        requested_kinds = []

        def _fake_session_for(kind):
            requested_kinds.append(kind)
            return direct

        with patch("core.data.fetcher._session_for", side_effect=_fake_session_for):
            with pytest.raises(requests.RequestException):
                _http_get("https://example.test")
        # 仅触达直连通道，代理通道未被取用
        assert requested_kinds == ["direct"]


class TestSessionForThreadLocal:
    """_session_for thread-local 语义锚（CARD-10）。

    requests.Session 非线程安全且双通道配置互斥（trust_env=False vs
    显式 proxies）——每线程每通道各持一个实例复用连接池。
    """

    @pytest.fixture(autouse=True)
    def _reset_thread_local(self):
        from core.data.fetcher import _thread_local

        yield
        if hasattr(_thread_local, "sessions"):
            del _thread_local.sessions

    def test_same_thread_same_kind_reuses_instance(self) -> None:
        from core.data.fetcher import _session_for

        a = _session_for("direct")
        b = _session_for("direct")
        assert a is b  # 同线程同通道复用（连接池热复用）
        assert isinstance(a, requests.Session)

    def test_direct_and_proxy_channels_independent(self) -> None:
        from core.data.fetcher import _session_for

        with patch("core.data.fetcher._DEFAULT_PROXY", "http://pinned:1"):
            direct = _session_for("direct")
            proxy = _session_for("proxy")
        assert direct is not proxy  # 双通道实例独立
        assert direct.trust_env is False  # 直连通道 distrust 环境
        assert proxy.proxies == {"http": "http://pinned:1", "https": "http://pinned:1"}

    def test_cross_thread_isolated(self) -> None:
        """跨线程不共享：子线程取用得到独立实例。"""
        import threading as _threading

        from core.data.fetcher import _session_for

        main_session = _session_for("direct")
        other = []
        t = _threading.Thread(
            target=lambda: other.append(_session_for("direct"))
        )
        t.start()
        t.join()
        assert other[0] is not main_session


class TestDirectSessionTrustEnv:
    """_direct_session 显式 distrust 环境（CARD-05 双行为面）。

    受限地区部署机常带全局 HTTP(S)_PROXY——直连通道若合并环境代理，
    vision 域名的直连会被环境代理劫持。语义先例：
    mcpserver/api_client.py 的 httpx trust_env=False。
    """

    ENV = {
        "http_proxy": "http://envproxy:1",
        "https_proxy": "http://envproxy:1",
        "HTTP_PROXY": "http://envproxy:1",
        "HTTPS_PROXY": "http://envproxy:1",
        "no_proxy": "",
    }

    def test_direct_session_ignores_env_proxies(self) -> None:
        """行为面 1：环境代理注入后合并设置为空（直连不被劫持）。"""
        import os

        with patch.dict(os.environ, self.ENV):
            session = _direct_session()
            settings = session.merge_environment_settings(
                "https://data-api.binance.vision", {}, None, None, None
            )
            assert settings["proxies"] == {}

    def test_plain_session_would_merge_env_proxies(self) -> None:
        """断言有效性对照：原生 Session（trust_env 默认开）确实合并环境代理——
        证明上一断言非恒真，能识别 trust_env 回退丢失。"""
        import os

        with patch.dict(os.environ, self.ENV):
            session = requests.Session()
            settings = session.merge_environment_settings(
                "https://data-api.binance.vision", {}, None, None, None
            )
            assert "envproxy" in str(settings["proxies"])

    def test_proxy_fallback_pins_explicit_proxies(self) -> None:
        """行为面 2：_build_session 显式 proxies 不受环境/ trust_env 影响。"""
        import os

        pinned = "http://pinned-proxy:1080"
        with patch.dict(os.environ, self.ENV):
            with patch("core.data.fetcher._DEFAULT_PROXY", pinned):
                session = _build_session()
        assert session.proxies == {"http": pinned, "https": pinned}


class TestDownloadArchiveCsv:
    """归档下载：404 / 网络失败 / 坏 zip 均返回 None，正常 zip 解析。"""

    @patch("core.data.fetcher._http_get")
    def test_404_returns_none(self, mock_get: MagicMock) -> None:
        resp = MagicMock()
        resp.status_code = 404
        mock_get.return_value = resp
        assert _download_archive_csv("monthly/fundingRate", "BTCUSDT", "stem") is None

    @patch("core.data.fetcher._http_get")
    def test_network_error_returns_none(self, mock_get: MagicMock) -> None:
        mock_get.side_effect = requests.RequestException("net down")
        assert _download_archive_csv("monthly/fundingRate", "BTCUSDT", "stem") is None

    @patch("core.data.fetcher._http_get")
    def test_bad_zip_returns_none(self, mock_get: MagicMock) -> None:
        resp = MagicMock()
        resp.status_code = 200
        resp.content = b"definitely not a zip"
        mock_get.return_value = resp
        assert _download_archive_csv("monthly/fundingRate", "BTCUSDT", "stem") is None

    @patch("core.data.fetcher._http_get")
    def test_valid_zip_parsed(self, mock_get: MagicMock) -> None:
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr(
                "data.csv",
                "calc_time,last_funding_rate\n1704067200000,0.0001\n",
            )
        resp = MagicMock()
        resp.status_code = 200
        resp.content = buf.getvalue()
        mock_get.return_value = resp

        df = _download_archive_csv("monthly/fundingRate", "BTCUSDT", "stem")

        assert df is not None
        assert int(df["calc_time"].iloc[0]) == 1704067200000
        assert float(df["last_funding_rate"].iloc[0]) == pytest.approx(0.0001)


class TestFetchOpenInterestHistoryNoData:
    """窗口内全部归档缺失时返回空帧（列契约保持）。"""

    @patch("core.data.fetcher._download_archive_csv", return_value=None)
    def test_all_days_missing_returns_empty(self, mock_dl: MagicMock) -> None:
        df = fetch_open_interest_history(
            symbol="BTCUSDT", start="2024-01-01", end="2024-01-03"
        )
        assert df.empty
        assert list(df.columns) == ["open_interest"]
