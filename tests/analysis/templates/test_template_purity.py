"""分析模板输入不可变锚（CARD-19 配套，同族契约对称锚）。

模板纯计算契约（templates/__init__ docstring：「DataFrame 输入 →
AnalysisResult 输出」）：同族四模板入口 copy（condition_stats:113 /
screen_patterns:190 / param_sweep:219 / regime_split 入口副本），
调用方 df 不被注入指标列、值不被改写——regime_split 的 in-place
写已在 CARD-19 消除，本锚把四家契约钉死（任一回潮即红）。
"""

import numpy as np
import pandas as pd
import pytest

pytestmark = [pytest.mark.unit]

_RANGES = [{"symbol": "TESTUSDT", "timeframe": "1d",
            "start": "2024-01-01", "end": "2025-09-01"}]


def _df(n: int = 400, seed: int = 7) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    idx = pd.date_range("2024-01-01", periods=n, freq="1d", tz="UTC",
                        name="timestamp")
    close = 100 + np.cumsum(rng.normal(0, 1, n))
    return pd.DataFrame({
        "open": close, "high": close + 1, "low": close - 1,
        "close": close, "volume": rng.uniform(10, 100, n)}, index=idx)


@pytest.fixture()
def snapshot():
    """输入快照（列名集 + 逐值冻结）对照用。"""
    df = _df()
    return df, list(df.columns), df.copy()


class TestTemplateInputImmutability:
    def test_condition_stats_输入不可变(self, snapshot) -> None:
        from core.analysis.templates.condition_stats import condition_stats
        df, cols_before, frozen = snapshot
        med = float(df["close"].median())
        condition_stats(df, indicator="EMA", params={"period": 10},
                        condition={"subject": "close", "action": "gt",
                                   "target": med}, data_ranges=_RANGES)
        assert list(df.columns) == cols_before
        assert df.equals(frozen)

    def test_screen_patterns_输入不可变(self, snapshot) -> None:
        from core.analysis.templates.screen_patterns import screen_patterns
        df, cols_before, frozen = snapshot
        screen_patterns(df, data_ranges=_RANGES, timeframe="1d",
                        features=["RSI"], horizons=[4])
        assert list(df.columns) == cols_before
        assert df.equals(frozen)

    def test_param_sweep_输入不可变(self, snapshot) -> None:
        from core.analysis.templates.param_sweep import param_sweep
        df, cols_before, frozen = snapshot
        param_sweep(df, data_ranges=_RANGES, timeframe="1d",
                    indicator="RSI",
                    condition={"subject": {"indicator": "RSI",
                                           "params": {"period": 14}},
                               "action": "lt", "target": 35.0},
                    sweep={"axis": "target", "values": [30.0, 35.0, 40.0]})
        assert list(df.columns) == cols_before
        assert df.equals(frozen)

    def test_regime_split_输入不可变(self, snapshot) -> None:
        """CARD-19 修复直接锚：原 in-place 注入 adx_14/atr_14 到调用
        方 df，入口 copy 后消除——注入回潮或值改写即红。"""
        from core.analysis.templates.regime_split import regime_split
        df, cols_before, frozen = snapshot
        regime_split(df, data_ranges=_RANGES, timeframe="1d")
        assert list(df.columns) == cols_before, "指标列注入调用方 df——纯计算契约破坏"
        assert df.equals(frozen)
        # 显式反证锚：修复前此断言必红（adx_14 被注入）
        assert "adx_14" not in df.columns
        assert "atr_14" not in df.columns
