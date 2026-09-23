"""compute_metrics 基准对照两键与 bars_per_year 公共常量单测。

improve-backtest-benchmark-disclosure 1.1：
- benchmark_return/excess_return 数值正确性（构造已知序列手工对照）；
- 无基准/基准过短/无交易三条 None 容缺边界；
- BARS_PER_YEAR 公共常量与 engine、batch 两处引用一致（含 1m/5m/2h 档）。
"""
import numpy as np
import pandas as pd
import pytest

from core.scoring.metrics import (
    BARS_PER_YEAR,
    DEFAULT_BARS_PER_YEAR,
    compute_metrics,
)


def _series(values):
    idx = pd.date_range("2024-01-01", periods=len(values), freq="4h", tz="UTC",
                        name="timestamp")
    return pd.Series(values, index=idx, dtype=float)


class TestBenchmarkKeys:
    """区间口径两键（Decision 1）：零费基准，None=无基准。"""

    def test_values_match_manual_computation(self):
        # equity 100→121（total_return=0.21）；benchmark 100→115（0.15）；
        # 手工对照：benchmark_return=0.15，excess_return=0.21-0.15=0.06
        eq = _series([100.0, 105.0, 110.0, 121.0])
        bm = _series([100.0, 102.0, 108.0, 115.0])
        m = compute_metrics(
            eq, total_trades=3, bars_per_year=2,  # 4 bar = 2 年
            trade_returns=np.array([0.05, 0.05, 0.0476]),
            benchmark_close=bm)
        assert m["benchmark_return"] == pytest.approx(0.15)
        assert m["excess_return"] == pytest.approx(0.21 - 0.15)
        # 年化口径并存（不删除既有 alpha/market_annual_return）
        assert m["market_annual_return"] == pytest.approx(1.15 ** 0.5 - 1)
        assert m["alpha"] == pytest.approx(m["annual_return"] - m["market_annual_return"])

    def test_benchmark_missing_gives_none(self):
        m = compute_metrics(
            _series([100.0, 110.0, 120.0]), total_trades=2,
            trade_returns=np.array([0.1, 0.0818]))
        assert m["benchmark_return"] is None
        assert m["excess_return"] is None

    def test_benchmark_too_short_gives_none(self):
        # len(benchmark_close) < 2：与既有 alpha 块同一守卫，区间口径同 None
        m = compute_metrics(
            _series([100.0, 110.0, 120.0]), total_trades=2,
            trade_returns=np.array([0.1, 0.0818]),
            benchmark_close=_series([100.0]))
        assert m["benchmark_return"] is None
        assert m["excess_return"] is None

    def test_no_trades_early_return_gives_none(self):
        # 早退分支（无交易）：两键 None（容缺语义，不虚构对照）
        m = compute_metrics(
            _series([100.0, 100.0, 100.0]), total_trades=0,
            benchmark_close=_series([100.0, 120.0]))
        assert m["benchmark_return"] is None
        assert m["excess_return"] is None


class TestBarsPerYearUnification:
    """Decision 6：公共常量为单一权威源，engine 与 batch 同源引用。"""

    def test_constant_covers_all_tiers(self):
        # batch 全集 + engine 独有 3d 档；数值与既有 batch.py:149-152 一致
        assert BARS_PER_YEAR == {
            "1m": 525600, "5m": 105120, "15m": 35040, "30m": 17520,
            "1h": 8760, "2h": 4380, "4h": 2190, "1d": 365,
            "3d": 365 // 3,
        }
        assert DEFAULT_BARS_PER_YEAR == 2190

    def test_batch_processor_same_source(self):
        from api.routes.strategies.batch import _BatchBacktestProcessor
        for tf, expected in BARS_PER_YEAR.items():
            assert _BatchBacktestProcessor._bars_per_year(None, tf) == expected
        # 未知档缺省不变（=4h 档）
        assert _BatchBacktestProcessor._bars_per_year(None, "9h") == DEFAULT_BARS_PER_YEAR

    def test_engine_uses_constant_for_1m(self):
        # 原 engine 映射缺 1m 档 → 错落缺省 2190；统一后 1m 真跑取 525600
        from tests.helpers.data_factory import make_dna, make_engine, make_enhanced_df
        engine = make_engine()
        dna = make_dna(indicator="RSI", timeframe="1m")
        enhanced = make_enhanced_df(n=300, freq="1min")
        result = engine.run(dna, enhanced)
        assert result.bars_per_year == BARS_PER_YEAR["1m"]
        assert result.bars_per_year == 525600
