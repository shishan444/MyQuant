"""因子方向性测试（fix-indicator-output-mapping 阶段 2，C 域）。

因子语义方向锚定：上游指标语义互换（阶段 0 修复的五处之一）曾使
bb_squeeze 完全反转（bb_width 恒为负 → rank 次序反转 → 带宽最大被
当成最挤压）。本文件用单调合成数据锚定因子方向，互换复发即红。
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

pytestmark = [pytest.mark.unit]

from core.features.indicators import compute_all_indicators
from core.prediction.factors import compute_factors
from core.prediction.genes import PredictionDNA


N = 300


def _vol_shrinking(seed: int = 5) -> pd.DataFrame:
    """波动幅度单调收窄：噪声 std 从 5.0 线性降至 0.1，带宽随之收窄。"""
    rng = np.random.default_rng(seed)
    idx = pd.date_range("2026-01-01", periods=N, freq="h")
    stds = np.linspace(5.0, 0.1, N)
    step = rng.standard_normal(N) * stds
    close = pd.Series(100.0 + np.cumsum(step), index=idx)
    spread = np.abs(rng.standard_normal(N)) * stds + 0.01
    return pd.DataFrame(
        {
            "open": close,
            "high": close + spread,
            "low": close - spread,
            "close": close,
            "volume": pd.Series(rng.random(N) * 1000, index=idx),
        },
        index=idx,
    )


def _vol_expanding(seed: int = 6) -> pd.DataFrame:
    """波动幅度单调扩张：噪声 std 从 0.1 线性升至 5.0。"""
    rng = np.random.default_rng(seed)
    idx = pd.date_range("2026-01-01", periods=N, freq="h")
    stds = np.linspace(0.1, 5.0, N)
    step = rng.standard_normal(N) * stds
    close = pd.Series(100.0 + np.cumsum(step), index=idx)
    spread = np.abs(rng.standard_normal(N)) * stds + 0.01
    return pd.DataFrame(
        {
            "open": close,
            "high": close + spread,
            "low": close - spread,
            "close": close,
            "volume": pd.Series(rng.random(N) * 1000, index=idx),
        },
        index=idx,
    )


def _factors_at(df: pd.DataFrame, pos: int) -> dict[str, float]:
    dna = PredictionDNA(short_window=15, mid_window=60, long_window=200)
    return compute_factors(compute_all_indicators(df), pos, dna)


class TestFactorDirectionality:
    def test_bb_squeeze_rises_when_band_narrows(self):
        """带宽收窄段（尾部）的 squeeze 应高于扩张段（头部）。"""
        early = _factors_at(_vol_shrinking(), 150)
        late = _factors_at(_vol_shrinking(), N - 1)
        assert late["bb_squeeze"] > early["bb_squeeze"], (
            f"收窄方向失效: early={early['bb_squeeze']:.4f} "
            f"late={late['bb_squeeze']:.4f}（bb_width 为负的互换病征兆）"
        )

    def test_bb_squeeze_falls_when_band_expands(self):
        """带宽扩张段（尾部）的 squeeze 应低于收窄段（头部）。"""
        early = _factors_at(_vol_expanding(), 150)
        late = _factors_at(_vol_expanding(), N - 1)
        assert late["bb_squeeze"] < early["bb_squeeze"], (
            f"扩张方向失效: early={early['bb_squeeze']:.4f} "
            f"late={late['bb_squeeze']:.4f}"
        )

    def test_bb_width_positive_after_fix(self):
        """修复后 bb_width 恒为正（互换时恒为负，最直接的方向证据）。"""
        from core.features.indicators import compute_all_indicators

        out = compute_all_indicators(_vol_shrinking())
        width = (out["bb_upper_20_2"] - out["bb_lower_20_2"]).dropna()
        assert (width > 0).all()

    def test_vol_regime_follows_volatility_expansion(self):
        """波动扩张尾段 ATR 高于其均线 → vol_regime > 1。"""
        late = _factors_at(_vol_expanding(), N - 1)
        assert late["vol_regime"] > 1.0, (
            f"ATR 扩张方向失效: vol_regime={late['vol_regime']:.4f}"
        )

    def test_vol_regime_below_one_when_volatility_shrinks(self):
        """波动收窄尾段 ATR 低于其均线 → vol_regime < 1。"""
        late = _factors_at(_vol_shrinking(), N - 1)
        assert late["vol_regime"] < 1.0, (
            f"ATR 收缩方向失效: vol_regime={late['vol_regime']:.4f}"
        )
