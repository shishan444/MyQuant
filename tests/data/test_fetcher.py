"""Tests for fetch_klines (core/data/fetcher.py)."""

import pytest

pytestmark = [pytest.mark.unit]

from unittest.mock import patch, MagicMock

import pandas as pd

from core.data.fetcher import fetch_klines


def _make_kline_rows(n: int = 5, base_ts: int = 1700000000000) -> list[list]:
    """Build *n* fake Binance kline rows (12-element lists).

    Each row: [timestamp_ms, open, high, low, close, volume,
               close_time, quote_volume, trades,
               taker_buy_base, taker_buy_quote, ignore]
    """
    rows = []
    for i in range(n):
        ts = base_ts + i * 3600_000  # 1 h apart
        price = 40000.0 + i * 100
        rows.append([
            ts,
            str(price),
            str(price + 50),
            str(price - 50),
            str(price + 25),
            "1234.5",
            ts + 3599_999,
            "50000.0",
            500,
            "600.0",
            "24000.0",
            "0",
        ])
    return rows


def _mock_session(kline_rows: list[list]) -> MagicMock:
    """Build a mock requests.Session that returns *kline_rows*."""
    session = MagicMock()
    resp = MagicMock()
    resp.json.return_value = kline_rows
    resp.raise_for_status = MagicMock()
    session.get.return_value = resp
    return session


def _patch_session_for(session: MagicMock):
    """Patch _session_for（CARD-10 后 _http_get 的真实协作者）恒返同一 session。

    旧锚点 patch _direct_session/_build_session 工厂在 thread-local 缓存
    下失效：同线程先行用例已缓存 session 后工厂不再被触达（产生红与
    假绿）。side_effect 闭包保证翻页多次取用拿到同一实例。
    """
    return patch(
        "core.data.fetcher._session_for", side_effect=lambda kind: session
    )


class TestFetchKlinesReturnsDataframe:
    """fetch_klines returns a properly structured DataFrame."""

    def test_returns_dataframe(self) -> None:
        with _patch_session_for(_mock_session(_make_kline_rows(3))):
            df = fetch_klines(symbol="BTCUSDT", interval="4h")

        assert isinstance(df, pd.DataFrame)
        expected_cols = ["open", "high", "low", "close", "volume", "trades"]
        assert list(df.columns) == expected_cols

    def test_column_types(self) -> None:
        with _patch_session_for(_mock_session(_make_kline_rows(2))):
            df = fetch_klines()

        for col in ["open", "high", "low", "close", "volume"]:
            assert df[col].dtype == float
        assert df["trades"].dtype == int

    def test_session_called_with_correct_params(self) -> None:
        mock_session = _mock_session(_make_kline_rows(1))

        with _patch_session_for(mock_session):
            fetch_klines(
                symbol="ETHUSDT",
                interval="1d",
                start_str="1 year ago UTC",
            )

        mock_session.get.assert_called_once()
        call_kwargs = mock_session.get.call_args
        assert "ETHUSDT" in str(call_kwargs)
        assert "1d" in str(call_kwargs)


class TestFetchKlinesRemovesDuplicates:
    """Duplicate timestamps are deduplicated (keep=first)."""

    def test_removes_duplicates(self) -> None:
        rows = _make_kline_rows(3)
        dup_row = rows[1].copy()
        dup_row[4] = "99999.0"
        rows_with_dup = rows[:2] + [dup_row] + rows[2:]

        with _patch_session_for(_mock_session(rows_with_dup)):
            df = fetch_klines()

        assert len(df) == 3
        assert df.iloc[1]["close"] == float(rows[1][4])


class TestFetchKlinesDatetimeIndex:
    """The DataFrame index is a timezone-aware DatetimeIndex."""

    def test_creates_datetime_index(self) -> None:
        with _patch_session_for(_mock_session(_make_kline_rows(2))):
            df = fetch_klines()

        assert isinstance(df.index, pd.DatetimeIndex)
        assert df.index.tz is not None
        assert str(df.index.tz) == "UTC"
