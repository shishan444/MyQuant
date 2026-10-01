"""derivatives_merger 特征测试网（fix/data-signals-review 修复期安全网）。

现状记录语义：期望值从当前实现行为推导钉住。建网时缺口：
双 None 直返原对象 / 空 df 与列名不符的静默跳过 / ffill 对齐手工样例。
"""

import pytest

pytestmark = [pytest.mark.unit]

import numpy as np
import pandas as pd

from core.data.derivatives_merger import merge_derivatives_into_ohlcv


def _ohlcv(n: int = 3) -> pd.DataFrame:
    idx = pd.date_range("2024-01-01", periods=n, freq="4h", tz="UTC")
    return pd.DataFrame({"close": np.arange(1.0, n + 1.0)}, index=idx)


class TestNoDataBranches:
    """无有效衍生数据时的直返与拷贝行为。"""

    def test_both_none_returns_same_object(self) -> None:
        df = _ohlcv()
        out = merge_derivatives_into_ohlcv(df, None, None)
        assert out is df  # 双 None 直返原对象（无拷贝）

    def test_empty_oi_no_columns_added(self) -> None:
        df = _ohlcv()
        empty_oi = pd.DataFrame({"open_interest": pd.Series(dtype=float)})
        out = merge_derivatives_into_ohlcv(df, empty_oi, None)
        assert "open_interest" not in out.columns
        assert out is not df  # 走了 copy 分支

    def test_wrong_column_name_ignored(self) -> None:
        df = _ohlcv()
        idx = pd.date_range("2024-01-01", periods=2, freq="4h", tz="UTC")
        bad = pd.DataFrame({"oi": [1.0, 2.0]}, index=idx)
        out = merge_derivatives_into_ohlcv(df, bad, None)
        assert list(out.columns) == ["close"]


class TestFundingFfillSemantics:
    """funding 并入的 ffill 对齐手工样例。"""

    def test_alignment_manual(self) -> None:
        idx = pd.date_range("2024-01-01", periods=3, freq="4h", tz="UTC")  # 00/04/08
        df = pd.DataFrame({"close": [1.0, 2.0, 3.0]}, index=idx)
        fr_idx = pd.DatetimeIndex([pd.Timestamp("2024-01-01 05:00", tz="UTC")])
        fr = pd.DataFrame({"funding_rate": [0.0001]}, index=fr_idx)

        out = merge_derivatives_into_ohlcv(df, None, fr)

        # 05:00 结算点之前的基准 bar 无值；08:00 bar 取 05:00 值
        assert pd.isna(out["funding_rate"].iloc[0])
        assert pd.isna(out["funding_rate"].iloc[1])
        assert out["funding_rate"].iloc[2] == pytest.approx(0.0001)
