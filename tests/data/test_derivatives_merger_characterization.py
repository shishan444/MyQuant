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


class TestMergeProperties:
    """merger 不变量属性锚（阶段四步骤 4 属性补强——独立对照与
    包含性验证，不复制生产算法）。"""

    def test_no_future_values_asof_identity(self) -> None:
        """无未来值恒等式：任意 bar 的衍生列值==衍生序列 asof 该 bar
        时刻的值（pd.Series.asof 独立对照——早于首点 NaN、之后取
        最近已知值）。"""
        rng = np.random.default_rng(11)
        bar_idx = pd.date_range("2024-01-01", periods=50, freq="4h",
                                tz="UTC")
        df = pd.DataFrame(
            {"close": np.cumprod(1 + rng.normal(0, 0.01, 50))},
            index=bar_idx)
        fr_idx = pd.date_range("2024-01-01 03:30", periods=7, freq="11h",
                               tz="UTC")
        rates = rng.normal(0.0001, 0.00005, 7)
        fr = pd.DataFrame({"funding_rate": rates}, index=fr_idx)

        out = merge_derivatives_into_ohlcv(df, None, fr)
        series = fr["funding_rate"]
        for i, ts in enumerate(bar_idx):
            expected = series.asof(ts)
            got = out["funding_rate"].iloc[i]
            if pd.isna(expected):
                assert pd.isna(got), (ts, got)
            else:
                assert got == pytest.approx(expected), (ts, got, expected)

    def test_original_columns_untouched(self) -> None:
        """包含性：输出列=输入列∪至多两衍生列；原始列值逐位不变
        （注入只增不改）。"""
        rng = np.random.default_rng(12)
        idx = pd.date_range("2024-01-01", periods=30, freq="4h", tz="UTC")
        df = pd.DataFrame({
            "open": np.arange(1.0, 31.0),
            "high": np.arange(1.0, 31.0) + 0.5,
            "low": np.arange(1.0, 31.0) - 0.5,
            "close": np.arange(1.0, 31.0) + 0.1,
            "volume": np.arange(100.0, 130.0),
        }, index=idx)
        oi_idx = pd.date_range("2024-01-01", periods=10, freq="12h",
                               tz="UTC")
        oi = pd.DataFrame(
            {"open_interest": np.arange(1000.0, 1010.0)}, index=oi_idx)
        fr_idx = pd.date_range("2024-01-01", periods=10, freq="8h",
                               tz="UTC")
        fr = pd.DataFrame(
            {"funding_rate": np.full(10, 0.0001)}, index=fr_idx)

        out = merge_derivatives_into_ohlcv(df, oi, fr)

        assert set(df.columns) <= set(out.columns)
        assert set(out.columns) - set(df.columns) <= {
            "open_interest", "funding_rate"}
        for col in df.columns:
            assert (out[col] == df[col]).all(), col

    def test_tail_segment_constant_after_archive_end(self) -> None:
        """stale 单调延伸：归档末端之后的 bar 衍生列恒等于最后已知
        值（尾段常量——运行期 stale 语义的 merger 侧不变量）。"""
        idx = pd.date_range("2024-01-01", periods=40, freq="4h", tz="UTC")
        df = pd.DataFrame({"close": np.arange(1.0, 41.0)}, index=idx)
        fr_idx = pd.date_range("2024-01-01", periods=3, freq="4h",
                               tz="UTC")  # 只覆盖前 3 根 bar
        fr = pd.DataFrame(
            {"funding_rate": [0.0001, 0.0002, 0.0003]}, index=fr_idx)

        out = merge_derivatives_into_ohlcv(df, None, fr)

        last = out["funding_rate"].iloc[2]  # 0.0003（末个已知值）
        assert last == pytest.approx(0.0003)
        assert (out["funding_rate"].iloc[3:] == last).all(), \
            "归档末端后尾段恒等于最后已知值"
