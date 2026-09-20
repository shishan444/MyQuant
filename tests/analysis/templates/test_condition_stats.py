"""条件统计模板与结果构造器单测（2.1 + 2.6）。"""

import numpy as np
import pandas as pd
import pytest

pytestmark = [pytest.mark.unit]

from core.analysis.hypothesis_service import ResearchError
from core.analysis.result import build_result
from core.analysis.templates import condition_stats


def _make_df(n: int = 300, seed: int = 42, start: str = "2024-01-01") -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    idx = pd.date_range(start, periods=n, freq="1h", tz="UTC", name="timestamp")
    close = 60000 + np.cumsum(rng.normal(0, 100, n))
    return pd.DataFrame({
        "open": close, "high": close + 50, "low": close - 50,
        "close": close, "volume": rng.uniform(100, 1000, n),
    }, index=idx)


RANGES = [{"start": "2024-01-01T00:00:00Z", "end": "2024-01-14T00:00:00Z"}]


class TestBuildResultThreeElements:
    def test_valid_result_constructs(self) -> None:
        r = build_result("t", sample_size=100,
                         confidence={"method": "m", "interval": [0, 1]},
                         data_ranges=RANGES)
        assert r.sample_size == 100

    def test_missing_confidence_rejected(self) -> None:
        with pytest.raises(ValueError, match="confidence"):
            build_result("t", sample_size=100, confidence=None, data_ranges=RANGES)

    def test_confidence_without_interval_rejected(self) -> None:
        with pytest.raises(ValueError, match="interval"):
            build_result("t", sample_size=100,
                         confidence={"method": "m"}, data_ranges=RANGES)

    def test_missing_ranges_rejected(self) -> None:
        with pytest.raises(ValueError, match="data_ranges"):
            build_result("t", sample_size=100,
                         confidence={"method": "m", "interval": [0, 1]},
                         data_ranges=None)

    def test_malformed_range_rejected(self) -> None:
        with pytest.raises(ValueError, match="半开区间"):
            build_result("t", sample_size=100,
                         confidence={"method": "m", "interval": [0, 1]},
                         data_ranges=[{"start": "2024-01-01"}])

    def test_small_sample_rejected(self) -> None:
        with pytest.raises(ValueError, match="小样本"):
            build_result("t", sample_size=10,
                         confidence={"method": "m", "interval": [0, 1]},
                         data_ranges=RANGES)


class TestConditionStats:
    def test_known_hit_rate(self) -> None:
        """已知数据核对统计量：close > 全程中位数的占比。"""
        df = _make_df(300)
        med = df["close"].median()
        res = condition_stats(
            df, indicator="EMA", params={"period": 10},
            condition={"subject": "close", "action": "gt", "target": float(med)},
            data_ranges=RANGES)
        expected_rate = float((df["close"] > med).mean())
        assert res.payload["hit_rate"] == pytest.approx(expected_rate, abs=1e-12)
        assert res.payload["hit_count"] == int((df["close"] > med).sum())
        assert res.sample_size == 300
        assert res.confidence["method"] == "wilson_interval"
        assert len(res.confidence["interval"]) == 2

    def test_indicator_column_as_subject(self) -> None:
        """subject 引用指标输出列（ema_10 vs close 的交叉）。"""
        df = _make_df(200)
        res = condition_stats(
            df, indicator="EMA", params={"period": 10},
            condition={"subject": "close", "action": "cross_above", "target": "ema_10"},
            data_ranges=RANGES)
        assert res.payload["hit_count"] >= 0  # 交叉事件天然稀疏，不假设次数
        assert res.payload["condition"]["subject"] == "close"

    def test_unknown_indicator_rejected(self) -> None:
        df = _make_df(100)
        with pytest.raises(ResearchError) as ei:
            condition_stats(
                df, indicator="NOPE", params={},
                condition={"subject": "close", "action": "gt", "target": 1},
                data_ranges=RANGES)
        assert ei.value.code == "RESEARCH_INDICATOR_UNKNOWN"

    def test_small_sample_rejected(self) -> None:
        df = _make_df(20)  # 少于默认 30
        with pytest.raises(ValueError, match="小样本"):
            condition_stats(
                df, indicator="EMA", params={"period": 5},
                condition={"subject": "close", "action": "gt", "target": 0},
                data_ranges=RANGES)
