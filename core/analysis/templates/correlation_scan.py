"""相关扫描模板（add-ai-research-mcp 2.3）。

语义：候选信号列与未来 N-bar 收益的滞后相关扫描（lag 0..max_lag），
回答"这个量领先还是滞后于价格、领先多少"。AI 用它筛选有预测性的
指标/衍生品列，找出滞后峰值。

口径：Pearson 相关；信号列取 t 时刻值与 t+lag 的前向收益配对——
前向收益必然引入 NaN 尾部，逐 lag 丢尾配对（不 pad，防伪相关）。
"""
from __future__ import annotations

from typing import Any, Dict

import numpy as np
import pandas as pd

from core.analysis.result import AnalysisResult, build_result
from core.analysis.templates.column_guard import require_column


def correlation_scan(
    df: pd.DataFrame,
    *,
    signal_column: str,
    horizon: int = 24,
    max_lag: int = 12,
    data_ranges: list[dict[str, str]],
    min_samples: int = 30,
    hypothesis_id: str | None = None,
) -> AnalysisResult:
    """信号列与前向收益的滞后相关扫描。

    Args:
        signal_column: 候选信号列（指标输出 / 衍生品 / 基础列）。
        horizon: 前向收益窗口（bar）。
        max_lag: 扫描的最大滞后（0..max_lag）。

    Raises:
        ResearchError: 信号列不可用（附可用列清单）。
        ValueError: 小样本。
    """
    require_column(df, signal_column, template="correlation_scan")

    sig = df[signal_column].astype(float)
    fwd_return = df["close"].pct_change(horizon).shift(-horizon)  # t+horizon 前向收益

    lags: list[int] = []
    corrs: list[float] = []
    for lag in range(0, max_lag + 1):
        s = sig.shift(lag)  # 滞后 lag 的信号（只用过去）
        pair = pd.concat([s, fwd_return], axis=1, keys=["s", "r"]).dropna()
        if len(pair) < min_samples:
            continue
        corr = float(pair["s"].corr(pair["r"]))
        if np.isnan(corr):
            continue
        lags.append(lag)
        corrs.append(corr)

    if not lags:
        raise ValueError(f"可用配对不足（< {min_samples}）——无法扫描")

    peak_idx = int(np.argmax(np.abs(corrs)))
    peak_lag = lags[peak_idx]
    peak_corr = corrs[peak_idx]
    n_pairs = int(len(pair))

    # 峰值相关的近似显著性：|r|·sqrt(n-2)/sqrt(1-r²) ~ t 分布
    t_stat = abs(peak_corr) * np.sqrt(n_pairs - 2) / np.sqrt(1 - peak_corr ** 2)
    from scipy import stats as sps
    p_value = float(2 * (1 - sps.t.cdf(t_stat, df=n_pairs - 2)))

    confidence = {
        "method": "pearson_t_test",
        "p_value": p_value,
        "n_pairs": n_pairs,
        "alpha": 0.05,
    }

    payload = {
        "signal_column": signal_column,
        "horizon": horizon,
        "lag_correlations": dict(zip(lags, (round(c, 6) for c in corrs))),
        "peak_lag": peak_lag,
        "peak_correlation": peak_corr,
        "peak_significant": bool(p_value < 0.05),
    }

    return build_result(
        "correlation_scan",
        sample_size=n_pairs,
        confidence=confidence,
        data_ranges=data_ranges,
        payload=payload,
        min_samples=min_samples,
        hypothesis_id=hypothesis_id,
    )
