"""条件统计模板（add-ai-research-mcp 2.1）。

语义：在给定数据上计算指标，统计"条件成立的 bar"占比与价格统计
（分位数/均值/极值）——回答"这个条件历史上出现得多频繁、出现时
价格处于什么状态"。条件形制复用假设验证引擎的
{subject, action, target}（core/validation/engine.py 同源语义），
指标计算复用 core/features/indicators._compute_indicator（任务书
授权的私有复用——同一指标定义源，不造第二套）。

统计口径：条件成立子集上的 close 统计与 close-to-close 收益统计；
置信用占比的正态近似区间（Wilson 大样本收敛，方法名声明）。
"""
from __future__ import annotations

import math
from typing import Any, Dict

import numpy as np
import pandas as pd

from core.analysis.result import AnalysisResult, build_result


def _wilson_interval(successes: int, n: int, z: float = 1.96) -> list[float]:
    """Wilson 置信区间（小样本比正态近似稳健，实现成本相同）。"""
    if n == 0:
        return [0.0, 0.0]
    p = successes / n
    denom = 1 + z * z / n
    center = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return [max(0.0, center - half), min(1.0, center + half)]


def condition_stats(
    df: pd.DataFrame,
    *,
    indicator: str,
    params: Dict[str, Any],
    condition: Dict[str, Any],
    data_ranges: list[dict[str, str]],
    min_samples: int = 30,
    hypothesis_id: str | None = None,
) -> AnalysisResult:
    """条件频率与条件内价格统计。

    Args:
        df: 含 OHLCV（及衍生品列，可选）的 DataFrame，DatetimeIndex UTC。
        indicator: INDICATOR_REGISTRY 指标名（如 "EMA"）。
        params: 指标参数（如 {"period": 50}）。
        condition: {subject, action, target}；subject 可引用指标输出列
            （命名形如 ema_50）或基础列（close/volume/open_interest...）。

    Raises:
        ResearchError: 未知指标（附注册表清单）。
        ValueError: 条件列缺失 / 小样本拒绝（经 build_result）。
    """
    from core.features.indicators import _compute_indicator
    from core.features.registry import INDICATOR_REGISTRY
    from core.validation.engine import _evaluate_single_condition

    from core.analysis.templates.column_guard import require_indicator
    require_indicator(indicator, INDICATOR_REGISTRY)

    # 指标计算（不改输入 df；新建工作帧合并新列）
    new_cols = _compute_indicator(df, indicator, dict(params))
    work = df.copy()
    for col, series in new_cols.items():
        work[col] = series

    mask = _evaluate_single_condition(work, condition)
    mask = mask.fillna(False).astype(bool)

    n_total = int(len(work))
    n_hit = int(mask.sum())

    # 条件内价格统计（close 与 close-to-close 收益）
    hit_close = work.loc[mask, "close"]
    ret = work["close"].pct_change()
    hit_ret = ret[mask]

    def _series_stats(s: pd.Series) -> Dict[str, float]:
        if s.empty:
            return {}
        return {
            "count": int(len(s)),
            "mean": float(np.nanmean(s)),
            "std": float(np.nanstd(s, ddof=1)) if len(s) > 1 else 0.0,
            "min": float(np.nanmin(s)),
            "q25": float(np.nanquantile(s, 0.25)),
            "median": float(np.nanquantile(s, 0.50)),
            "q75": float(np.nanquantile(s, 0.75)),
            "max": float(np.nanmax(s)),
        }

    rate_interval = _wilson_interval(n_hit, n_total)
    confidence = {
        "method": "wilson_interval",
        "interval": rate_interval,
        "for": "hit_rate",
        "n_total": n_total,
    }

    payload = {
        "indicator": indicator,
        "params": dict(params),
        "condition": dict(condition),
        "hit_rate": (n_hit / n_total) if n_total else 0.0,
        "hit_count": n_hit,
        "close_stats_when_hit": _series_stats(hit_close),
        "return_stats_when_hit": _series_stats(hit_ret),
        "total_bars": n_total,
    }

    return build_result(
        "condition_stats",
        sample_size=n_total,
        confidence=confidence,
        data_ranges=data_ranges,
        payload=payload,
        min_samples=min_samples,
        hypothesis_id=hypothesis_id,
    )
