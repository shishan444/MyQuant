"""updater 特征测试网（fix/data-signals-review 修复期安全网）。

现状记录语义：期望值从当前实现行为推导钉住。建网时缺口：
增量拉取为空的三条返回分支 / funding 与 OI 增量起点的回退重叠窗口。
"""

import pytest

pytestmark = [pytest.mark.unit]

from unittest.mock import patch

import numpy as np
import pandas as pd

from core.data.storage import load_parquet, save_parquet
from core.data.updater import (
    update_funding_data,
    update_market_data,
    update_open_interest_data,
)


def make_ohlcv(n: int, start: str = "2024-01-01", freq: str = "4h") -> pd.DataFrame:
    idx = pd.date_range(start, periods=n, freq=freq, tz="UTC")
    close = pd.Series(np.arange(1, n + 1, dtype=float) * 10.0, index=idx)
    return pd.DataFrame(
        {
            "open": close - 5.0,
            "high": close + 5.0,
            "low": close - 8.0,
            "close": close,
            "volume": pd.Series(np.arange(1, n + 1, dtype=float) * 100.0, index=idx),
            "trades": pd.Series(np.arange(1, n + 1, dtype=int) * 10, index=idx),
        },
        index=idx,
    )


def _empty_klines() -> pd.DataFrame:
    return pd.DataFrame(
        columns=["open", "high", "low", "close", "volume", "trades"]
    )


class TestUpdateMarketDataNoNew:
    """增量拉取为空：返回存量、磁盘不被改写。"""

    def test_incremental_empty_fetch_returns_existing(self, tmp_path) -> None:
        existing = make_ohlcv(100)
        save_parquet(existing, tmp_path / "BTCUSDT_4h.parquet")

        with patch(
            "core.data.updater.fetch_klines", return_value=_empty_klines()
        ):
            out = update_market_data("BTCUSDT", "4h", data_dir=tmp_path)

        assert len(out) == 100
        # check_freq=False：parquet 往返不保留 index.freq 元数据（序列化必然丢失）
        pd.testing.assert_frame_equal(out, existing, check_freq=False)
        # 落盘内容同样保持存量
        pd.testing.assert_frame_equal(
            load_parquet(tmp_path / "BTCUSDT_4h.parquet"), existing, check_freq=False
        )


class TestUpdateFundingNoNew:
    """funding 增量为空：有存量返回存量，无存量返回空帧。"""

    @patch("core.data.updater.fetch_funding_history")
    def test_empty_no_existing_returns_empty(
        self, mock_fetch, tmp_path
    ) -> None:
        mock_fetch.return_value = pd.DataFrame(columns=["funding_rate"])
        out = update_funding_data("BTCUSDT", data_dir=tmp_path)
        assert out.empty

    @patch("core.data.updater.fetch_funding_history")
    def test_empty_with_existing_returns_existing(
        self, mock_fetch, tmp_path
    ) -> None:
        existing = make_ohlcv(50, freq="8h")[["close"]].rename(
            columns={"close": "funding_rate"}
        )
        save_parquet(existing, tmp_path / "BTCUSDT_funding.parquet")
        mock_fetch.return_value = pd.DataFrame(columns=["funding_rate"])

        out = update_funding_data("BTCUSDT", data_dir=tmp_path)

        assert len(out) == 50
        pd.testing.assert_frame_equal(out, existing, check_freq=False)


class TestUpdateFundingIncrementalStart:
    """增量起点 = 本地最新 - 8h（重叠一个结算期，merge 幂等去重）。"""

    @patch("core.data.updater.fetch_funding_history")
    def test_incremental_start_backtracks_8h(self, mock_fetch, tmp_path) -> None:
        idx = pd.date_range("2024-01-01", periods=50, freq="8h", tz="UTC")
        existing = pd.DataFrame(
            {"funding_rate": np.linspace(0.0001, 0.0002, 50)}, index=idx
        )
        save_parquet(existing, tmp_path / "BTCUSDT_funding.parquet")
        mock_fetch.return_value = pd.DataFrame(columns=["funding_rate"])

        update_funding_data("BTCUSDT", data_dir=tmp_path)

        start_arg = mock_fetch.call_args.kwargs["start"]
        assert pd.Timestamp(start_arg, tz="UTC") == idx[-1] - pd.Timedelta(hours=8)


class TestUpdateOpenInterestNoData:
    """OI 增量为空：无原始文件 / 原始文件为空均返回空 dict。"""

    @patch("core.data.updater.fetch_open_interest_history")
    def test_empty_no_raw_returns_empty_dict(self, mock_fetch, tmp_path) -> None:
        mock_fetch.return_value = pd.DataFrame(columns=["open_interest"])
        out = update_open_interest_data("BTCUSDT", data_dir=tmp_path)
        assert out == {}

    @patch("core.data.updater.fetch_open_interest_history")
    def test_empty_raw_file_returns_empty_dict(self, mock_fetch, tmp_path) -> None:
        empty_raw = pd.DataFrame({"open_interest": pd.Series(dtype=float)})
        save_parquet(empty_raw, tmp_path / "BTCUSDT_oi_5m.parquet")
        mock_fetch.return_value = pd.DataFrame(columns=["open_interest"])

        out = update_open_interest_data("BTCUSDT", data_dir=tmp_path)

        assert out == {}


class TestUpdateOpenInterestIncrementalStart:
    """增量起点 = 本地最新 - 10min（5m 原生粒度重叠两个点）。"""

    @patch("core.data.updater.fetch_open_interest_history")
    def test_incremental_start_backtracks_10min(self, mock_fetch, tmp_path) -> None:
        idx = pd.date_range("2024-01-01", periods=288, freq="5min", tz="UTC")
        raw = pd.DataFrame(
            {"open_interest": np.arange(1, 289, dtype=float)}, index=idx
        )
        save_parquet(raw, tmp_path / "BTCUSDT_oi_5m.parquet")
        mock_fetch.return_value = pd.DataFrame(columns=["open_interest"])

        update_open_interest_data("BTCUSDT", data_dir=tmp_path, timeframes=["5m"])

        start_arg = mock_fetch.call_args.kwargs["start"]
        assert pd.Timestamp(start_arg, tz="UTC") == idx[-1] - pd.Timedelta(
            minutes=10
        )


class TestMergeParquetReturnValue:
    """merge_parquet 返回值契约（CARD-07：None → pd.DataFrame）。

    双分支：文件已存在返回合并结果（与磁盘一致）、新建返回 new_df。
    消费方 updater 两处已改为直接 return（省重复读盘）。
    """

    def _funding(self, n: int, start: str = "2024-01-01"):
        idx = pd.date_range(start, periods=n, freq="8h", tz="UTC")
        return pd.DataFrame(
            {"funding_rate": np.linspace(0.0001, 0.0002, n)}, index=idx
        )

    def test_existing_file_returns_merged(self, tmp_path) -> None:
        path = tmp_path / "merged.parquet"
        save_parquet(self._funding(10), path)
        new_part = self._funding(5, start="2024-01-03 16:00")  # 与存量重叠

        from core.data.storage import merge_parquet

        out = merge_parquet(new_part, path)

        pd.testing.assert_frame_equal(out, load_parquet(path), check_freq=False)
        assert len(out) == len(pd.concat([self._funding(10), new_part]).index.unique())

    def test_new_file_returns_new_df(self, tmp_path) -> None:
        from core.data.storage import merge_parquet

        new_df = self._funding(5)
        out = merge_parquet(new_df, tmp_path / "fresh.parquet")
        pd.testing.assert_frame_equal(out, new_df, check_freq=False)
        assert (tmp_path / "fresh.parquet").exists()

    @patch("core.data.updater.fetch_funding_history")
    def test_update_funding_returns_merge_result(self, mock_fetch, tmp_path) -> None:
        """update_funding_data 正常增量：直接返回 merge_parquet 结果。"""
        save_parquet(self._funding(10), tmp_path / "BTCUSDT_funding.parquet")
        new_part = self._funding(5, start="2024-01-03 16:00")
        mock_fetch.return_value = new_part

        out = update_funding_data("BTCUSDT", data_dir=tmp_path)

        assert len(out) > 10  # 合并了新数据
        pd.testing.assert_frame_equal(out, load_parquet(tmp_path / "BTCUSDT_funding.parquet"), check_freq=False)
