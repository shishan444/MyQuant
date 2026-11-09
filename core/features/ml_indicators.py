"""ML-based indicators: FractalEntropy.

Lazy-computed indicators using statistical learning techniques.
These require more computation than standard indicators and are
only calculated when explicitly requested (skip_lazy=False).
"""
from __future__ import annotations

import numpy as np
import pandas as pd


def compute_fractal_entropy(
    close: pd.Series,
    bins: int = 10,
    lookback: int = 100,
) -> pd.Series:
    """Compute normalized Shannon entropy of discretized returns.

    Rolling window entropy measures market complexity/randomness.
    Low entropy indicates a trending regime; high entropy indicates random walk.

    语义分诊（add-champion-indicators D9 互指）：本件为分形维数熵——
    对数收益直方分桶的分布熵，度量价格自相似复杂度；与 ShannonEntropy
    （information 类，三态涨跌方向分布熵）是不同的度量对象。R245 探针
    实证：本件内置可用但判别力弱于本地香农熵（研究档案 R217 :239）——
    涨跌方向结构度量用 ShannonEntropy，分形自相似度量用本件；两件
    互指分诊、判别力差异以此披露。

    Args:
        close: Close price series.
        bins: Number of bins for return distribution discretization.
        lookback: Rolling window size.

    Returns:
        Series with values in [0, 1], NaN during warmup period.
    """
    returns = close.pct_change()
    values = returns.values
    n = len(close)
    result = np.full(n, np.nan)
    max_entropy = np.log2(bins) if bins > 1 else 1.0

    for i in range(lookback - 1, n):
        window = values[i - lookback + 1: i + 1]
        window = window[~np.isnan(window)]
        if len(window) < 10:
            continue
        hist, _ = np.histogram(window, bins=bins)
        prob = hist / hist.sum()
        prob = prob[prob > 0]
        entropy = -np.sum(prob * np.log2(prob))
        result[i] = entropy / max_entropy

    return pd.Series(result, index=close.index)


