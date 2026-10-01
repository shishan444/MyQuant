"""mtf_alignment 特征测试网（fix/data-signals-review 修复期安全网）。

现状记录语义：期望值从当前实现行为推导钉住。建网时缺口：
TfLike=Timedelta 直通路径 / pulse_completed 无触发信号分支。
"""

import pytest

pytestmark = [pytest.mark.unit]

import numpy as np
import pandas as pd

from core.data.mtf_alignment import project_completed, pulse_completed


class TestProjectCompletedTimedelta:
    """周期参数为 Timedelta 时与等价字符串键行为一致（直通路径）。"""

    def test_timedelta_equivalent_to_string(self) -> None:
        idx = pd.date_range("2024-01-01", periods=6, freq="1h", tz="UTC")
        high_idx = pd.date_range("2024-01-01", periods=2, freq="4h", tz="UTC")
        values = pd.Series([10.0, 20.0], index=high_idx)

        by_td = project_completed(values, idx, pd.Timedelta(hours=4))
        by_str = project_completed(values, idx, "4h")

        # 手工推导：高周期 00:00 值挂 04:00、04:00 值挂 08:00（超出基准末根）；
        # 基准 [00..05] 中仅 04:00/05:00 可见值 10.0，首段 NaN
        expected = pd.Series([np.nan, np.nan, np.nan, np.nan, 10.0, 10.0], index=idx)
        pd.testing.assert_series_equal(by_td, expected)
        pd.testing.assert_series_equal(by_str, expected)


class TestPulseCompletedNoFire:
    """无触发信号：全 False 输出且长度与基准索引一致。"""

    def test_all_false_signal_returns_all_false(self) -> None:
        base_idx = pd.date_range("2024-01-01", periods=5, freq="1h", tz="UTC")
        signal = pd.Series(
            False, index=pd.date_range("2024-01-01", periods=2, freq="4h", tz="UTC")
        )

        out = pulse_completed(signal, base_idx, "4h")

        assert len(out) == 5
        assert not out.any()
        assert (out.index == base_idx).all()

    def test_empty_signal_returns_all_false(self) -> None:
        base_idx = pd.date_range("2024-01-01", periods=5, freq="1h", tz="UTC")
        out = pulse_completed(pd.Series(dtype=bool), base_idx, "4h")
        assert len(out) == 5
        assert not out.any()
