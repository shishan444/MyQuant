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


class TestFetchKlinesPagination:
    """分页累积、翻页起点推进与停滞保护。"""

    @patch("core.data.fetcher._direct_session")
    def test_multi_page_accumulates(self, mock_direct: MagicMock) -> None:
        page1 = [_kline_row(1700000000000 + i * 3600_000) for i in range(1000)]
        last_ts = page1[-1][0]
        page2 = [_kline_row(last_ts + 1 + i * 3600_000) for i in range(10)]
        session = MagicMock()
        session.get.side_effect = [_resp_with(page1), _resp_with(page2)]
        mock_direct.return_value = session

        df = fetch_klines(
            symbol="BTCUSDT", interval="1h",
            start_str="1700000000000", end_str="9999999999999",
        )

        assert len(df) == 1010
        # 翻页起点 = 上页最后一根 open_time + 1
        second_params = session.get.call_args_list[1].kwargs["params"]
        assert second_params["startTime"] == last_ts + 1
        assert df.index.is_monotonic_increasing

    @patch("core.data.fetcher._direct_session")
    def test_empty_response_returns_empty_frame(self, mock_direct: MagicMock) -> None:
        session = MagicMock()
        session.get.return_value = _resp_with([])
        mock_direct.return_value = session

        df = fetch_klines(
            symbol="BTCUSDT", interval="1h", start_str="1700000000000"
        )

        assert df.empty
        assert list(df.columns) == ["open", "high", "low", "close", "volume", "trades"]

    @patch("core.data.fetcher._direct_session")
    def test_non_advancing_response_stops(self, mock_direct: MagicMock) -> None:
        """响应最后一根 open_time 未前进时立即停止（停滞保护）。"""
        stale = [_kline_row(1704067200000)]  # 2024-01-01，早于请求起点
        session = MagicMock()
        session.get.return_value = _resp_with(stale)
        mock_direct.return_value = session

        df = fetch_klines(
            symbol="BTCUSDT", interval="1h", start_str="1704163200000"
        )

        session.get.assert_called_once()  # 未停滞循环
        assert len(df) == 1  # 停滞行仍被收进结果（现状记录）


class TestHttpGetNoProxy:
    """直连失败且未配置代理时异常直抛，不回退。"""

    @patch("core.data.fetcher._build_session")
    @patch("core.data.fetcher._direct_session")
    @patch("core.data.fetcher._DEFAULT_PROXY", "")
    def test_direct_failure_without_proxy_raises(
        self, mock_direct: MagicMock, mock_build: MagicMock
    ) -> None:
        mock_direct.return_value.get.side_effect = requests.RequestException(
            "unreachable"
        )
        with pytest.raises(requests.RequestException):
            _http_get("https://example.test")
        mock_build.assert_not_called()


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
