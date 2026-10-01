"""衍生品数据拉取单测（core/data/fetcher.py 的 S3 归档函数与 updater 增量逻辑）。

全部离线：网络层 mock 在 _download_archive_csv / _http_get 边界。
"""

import io
import zipfile
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

pytestmark = [pytest.mark.unit]

from core.data.fetcher import (
    _http_get,
    fetch_funding_history,
    fetch_open_interest_history,
)
from core.data.updater import update_funding_data, update_open_interest_data


def _zip_response(csv_text: str, status: int = 200) -> MagicMock:
    """构造一个 Response mock：content 为含单 CSV 的 zip。"""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("data.csv", csv_text)
    resp = MagicMock()
    resp.status_code = status
    resp.content = buf.getvalue()
    resp.raise_for_status = MagicMock()
    return resp


# ---------------------------------------------------------------- funding

def test_fetch_funding_history_basic() -> None:
    csv = "calc_time,funding_interval_hours,last_funding_rate\n"
    # 2026-08-01 00:00 / 08:00 / 16:00 UTC（毫秒）
    csv += "1785542400000,8,0.0001\n1785571200000,8,0.0002\n1785599999999,8,0.0003\n"

    with patch("core.data.fetcher._http_get", return_value=_zip_response(csv)):
        df = fetch_funding_history("BTCUSDT", start="2026-08-01", end="2026-09-01")

    assert list(df.columns) == ["funding_rate"]
    assert len(df) == 3
    assert df["funding_rate"].tolist() == pytest.approx([0.0001, 0.0002, 0.0003])
    assert df.index.tz is not None and str(df.index.tz) == "UTC"
    assert df.index.min() == pd.Timestamp("2026-08-01 00:00:00", tz="UTC")


def test_fetch_funding_history_skips_missing_months() -> None:
    """404 月份跳过，不影响其他月份数据。"""
    # 09 月归档里的时间戳：2026-09-01 00:00 UTC（毫秒）
    with patch(
        "core.data.fetcher._http_get",
        side_effect=[_zip_response("", status=404),
                     _zip_response("calc_time,funding_interval_hours,last_funding_rate\n1788220800000,8,0.0001\n")],
    ) as mock_get:
        # 月份顺序：2026-08（404），2026-09（有数据）
        df = fetch_funding_history("BTCUSDT", start="2026-08-15", end="2026-09-20")

    assert mock_get.call_count == 2
    assert len(df) == 1
    assert df.index.min() == pd.Timestamp("2026-09-01", tz="UTC")


def test_fetch_funding_history_empty_window() -> None:
    """窗口内无归档数据 → 空 DataFrame 且带列名。"""
    with patch("core.data.fetcher._http_get", return_value=_zip_response("", status=404)):
        df = fetch_funding_history("BTCUSDT", start="2026-08-01", end="2026-09-01")
    assert df.empty and list(df.columns) == ["funding_rate"]


# ---------------------------------------------------------------- OI

def test_fetch_open_interest_history_basic() -> None:
    csv = (
        "create_time,symbol,sum_open_interest,sum_open_interest_value\n"
        "2026-09-10 00:00:00,BTCUSDT,100.5,1.0\n"
        "2026-09-10 00:05:00,BTCUSDT,101.5,1.0\n"
    )
    with patch("core.data.fetcher._http_get", return_value=_zip_response(csv)):
        df = fetch_open_interest_history("BTCUSDT", start="2026-09-10", end="2026-09-11")

    assert list(df.columns) == ["open_interest"]
    assert len(df) == 2
    assert df["open_interest"].tolist() == pytest.approx([100.5, 101.5])
    assert str(df.index.tz) == "UTC"


def test_fetch_open_interest_history_partial_days() -> None:
    """部分日期 404（归档延迟）→ 跳过，仅返回有数据的天。"""
    ok_csv = (
        "create_time,symbol,sum_open_interest\n"
        "2026-09-10 00:00:00,BTCUSDT,100.0\n"
    )
    with patch(
        "core.data.fetcher._http_get",
        side_effect=[_zip_response(ok_csv), _zip_response("", status=404), _zip_response("", status=404)],
    ):
        df = fetch_open_interest_history("BTCUSDT", start="2026-09-10", end="2026-09-12")
    assert len(df) == 1


# ---------------------------------------------------------------- _http_get 回退

class TestHttpGetFallback:
    def test_direct_first(self) -> None:
        """直连成功时不走代理。

        锚点为 _session_for（CARD-10 后 _http_get 的协作者——thread-local
        缓存使工厂 patch 在同线程先行用例后失效），按通道分流 mock。
        """
        direct = MagicMock()
        direct.get.return_value = MagicMock(status_code=200)
        proxy = MagicMock()
        with patch(
            "core.data.fetcher._session_for",
            side_effect=lambda kind: direct if kind == "direct" else proxy,
        ):
            resp = _http_get("https://example.com")
        assert resp is direct.get.return_value
        proxy.get.assert_not_called()

    def test_fallback_to_proxy(self) -> None:
        """直连抛网络异常时回退代理。"""
        import requests as _requests

        direct = MagicMock()
        direct.get.side_effect = _requests.ConnectionError("boom")
        proxy = MagicMock()
        proxy_resp = MagicMock(status_code=200)
        proxy.get.return_value = proxy_resp
        with patch(
            "core.data.fetcher._session_for",
            side_effect=lambda kind: direct if kind == "direct" else proxy,
        ):
            resp = _http_get("https://example.com")
        assert resp is proxy_resp
        proxy.get.assert_called_once()


# ---------------------------------------------------------------- updater 增量

def test_update_funding_data_full_and_incremental(tmp_path) -> None:
    csv1 = "calc_time,funding_interval_hours,last_funding_rate\n1785542400000,8,0.0001\n"
    # 全量首跑：2026-08 窗口一个月
    with patch("core.data.fetcher._http_get", return_value=_zip_response(csv1)):
        df = update_funding_data("BTCUSDT", data_dir=tmp_path, history_start="2026-08-01")
    assert len(df) == 1
    assert (tmp_path / "BTCUSDT_funding.parquet").exists()

    # 增量：本地最新 1785542400000（08-01 00:00），起点应回退 8h 之内
    csv2 = (
        "calc_time,funding_interval_hours,last_funding_rate\n"
        "1785542400000,8,0.0001\n"   # 与存量重叠，merge keep last
        "1785571200000,8,0.0009\n"   # 新增一期
    )
    with patch("core.data.fetcher._http_get", return_value=_zip_response(csv2)) as mock_get:
        df2 = update_funding_data("BTCUSDT", data_dir=tmp_path, history_start="2024-01-01")
    # 增量拉取的起始参数应晚于 history_start（说明按本地最新增量）
    called_url = mock_get.call_args[0][0]
    assert "monthly" in called_url
    assert len(df2) == 2
    assert df2["funding_rate"].iloc[-1] == pytest.approx(0.0009)


def test_update_open_interest_data_resample(tmp_path) -> None:
    """5m 原生文件维护 + 重采样生成各周期文件。"""
    rows = ["create_time,symbol,sum_open_interest"]
    # 1h 内 12 个 5m 点：值递增，各周期取 last
    base = pd.Timestamp("2026-09-10 00:00:00")
    for i in range(12):
        ts = base + pd.Timedelta(minutes=5 * i)
        rows.append(f"{ts},BTCUSDT,{100.0 + i}")
    csv = "\n".join(rows) + "\n"

    with patch("core.data.fetcher._http_get", return_value=_zip_response(csv)):
        out = update_open_interest_data(
            "BTCUSDT", data_dir=tmp_path, history_start="2026-09-10", timeframes=["5m", "15m", "1h"]
        )

    assert out["5m"]["open_interest"].tolist() == pytest.approx([100.0 + i for i in range(12)])
    # 15m 周期：3 个点，各取段内 last（102, 105, 108, 111）
    assert out["15m"]["open_interest"].tolist() == pytest.approx([102.0, 105.0, 108.0, 111.0])
    # 1h 周期：1 个点，取 111
    assert out["1h"]["open_interest"].tolist() == pytest.approx([111.0])
    assert (tmp_path / "BTCUSDT_oi_15m.parquet").exists()
    assert (tmp_path / "BTCUSDT_oi_1h.parquet").exists()
