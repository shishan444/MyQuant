"""分布对比模板（add-ai-research-mcp 2.2）。

语义：同一序列在两组样本（如两个时间窗）上的分布对比——KS 两样本
检验判分布形状差异 + 均值差 t 检验，回答"这两段的行为像不像同一
个市场状态"。AI 用它检验"市场换剧本"与区间同质性。

显著性（设计开放点落定）：KS 检验（分布形状）+ Welch t（均值差），
scipy 已在依赖链内（vectorbt 依赖），不新增依赖；p 值直接入
confidence（level 语义）。
"""
from __future__ import annotations

from typing import Any, Dict

import numpy as np
import pandas as pd
from scipy import stats as sps

from core.analysis.result import AnalysisResult, build_result
from core.analysis.templates.column_guard import require_column


def distribution_compare(
    df: pd.DataFrame,
    *,
    column: str = "close",
    transform: str = "returns",  # "returns" | "log_returns" | "raw"
    group_a: Dict[str, str],     # {"start","end"} 半开区间
    group_b: Dict[str, str],
    data_ranges: list[dict[str, str]],
    min_samples: int = 30,
    hypothesis_id: str | None = None,
) -> AnalysisResult:
    """两组区间上序列分布的对比（KS + Welch t）。

    Args:
        df: 含 column 列的 DataFrame，DatetimeIndex UTC。
        column: 对比列（close / 指标输出列 / 衍生品列）。
        transform: 先行变换（returns 逐 bar 收益率默认）。
        group_a / group_b: 两组的半开时间窗。

    Raises:
        ResearchError: 列不可用（附可用列清单）。
        ValueError: 区间空数据 / 小样本。
    """
    require_column(df, column, template="distribution_compare")

    if transform == "returns":
        series = df[column].pct_change().dropna()
    elif transform == "log_returns":
        series = np.log(df[column]).diff().dropna()
    else:
        series = df[column].dropna()

    def _slice(win: Dict[str, str]) -> pd.Series:
        sub = series[(series.index >= win["start"]) & (series.index < win["end"])]
        if sub.empty:
            raise ValueError(f"区间无数据: {win}")
        return sub

    a = _slice(group_a)
    b = _slice(group_b)

    if len(a) < min_samples or len(b) < min_samples:
        raise ValueError(
            f"小样本拒绝: group_a={len(a)}, group_b={len(b)} < {min_samples}")

    ks_stat, ks_p = sps.ks_2samp(a.values, b.values)
    t_stat, t_p = sps.ttest_ind(a.values, b.values, equal_var=False)

    confidence = {
        "method": "ks_2samp + welch_t",
        "level": float(min(ks_p, t_p)),  # 双检较严者
        "ks_p_value": float(ks_p),
        "welch_t_p_value": float(t_p),
        "alpha": 0.05,
    }
    significant = confidence["level"] < 0.05

    def _desc(s: pd.Series) -> Dict[str, float]:
        return {
            "count": int(len(s)),
            "mean": float(s.mean()),
            "std": float(s.std(ddof=1)),
            "min": float(s.min()),
            "q25": float(s.quantile(0.25)),
            "median": float(s.median()),
            "q75": float(s.quantile(0.75)),
            "max": float(s.max()),
        }

    payload = {
        "column": column,
        "transform": transform,
        "group_a": {"window": group_a, **_desc(a)},
        "group_b": {"window": group_b, **_desc(b)},
        "ks_statistic": float(ks_stat),
        "mean_diff": float(a.mean() - b.mean()),
        "significant": bool(significant),
    }

    return build_result(
        "distribution_compare",
        sample_size=min(len(a), len(b)),
        confidence=confidence,
        data_ranges=data_ranges,
        payload=payload,
        min_samples=min_samples,
        hypothesis_id=hypothesis_id,
    )
