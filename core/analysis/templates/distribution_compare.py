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
from core.analysis.templates.param_guard import (
    _require_enum,
    _require_int_range,
    _require_time_window,
)


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
        ResearchError: 列不可用（附可用列清单）；区间空数据
            （RESEARCH_DATA_EMPTY，数据语义空结果非形制非法）；
            小样本（RESEARCH_PARAM_INVALID）。
    """
    require_column(df, column, template="distribution_compare")

    # TD-2 入参守卫（clear-analysis-tds）：transform 枚举封闭（消灭非法
    # 串静默落 raw 语义的结论污染）；阈值类 0..无上界
    # TD-A 时间窗守卫（trigger-policy 卡6）：非对象形/缺键/值非时间串
    # 现状 TypeError/KeyError 落 500 的通道错置修正
    _require_enum(transform, name="transform",
                  choices=("returns", "log_returns", "raw"))
    _require_int_range(min_samples, name="min_samples", lo=0)
    _require_time_window(group_a, name="group_a")
    _require_time_window(group_b, name="group_b")

    if transform == "returns":
        series = df[column].pct_change().dropna()
    elif transform == "log_returns":
        series = np.log(df[column]).diff().dropna()
    else:
        series = df[column].dropna()

    def _slice(win: Dict[str, str], name: str) -> pd.Series:
        sub = series[(series.index >= win["start"]) & (series.index < win["end"])]
        if sub.empty:
            # 0.3.7 H1：数据语义空结果结构化拒绝（数据空 ≠ 请求错 ≠
            # 服务器错误）——与形制非法（param_guard 422）和小样本
            # （下方 422）可区分；异步降级路径经既有失败任务落库同构
            from core.analysis.hypothesis_service import ResearchError
            span = ([str(series.index[0]), str(series.index[-1])]
                    if not series.empty else None)
            raise ResearchError(
                "RESEARCH_DATA_EMPTY",
                f"{name} 区间无数据: {win}",
                detail={
                    "group": name, "window": win, "column": column,
                    "series_span": span,
                    "hint": "调整分组窗口至数据覆盖范围内，或确认数据集已覆盖该区间"})
        return sub

    a = _slice(group_a, "group_a")
    b = _slice(group_b, "group_b")

    if len(a) < min_samples or len(b) < min_samples:
        # 0.3.2 卡4 分流实施补全：小样本属业务拒绝（422）非内部错误
        # ——原裸 ValueError 经快路径收尾落 500，误导 Agent 盲重试
        from core.analysis.hypothesis_service import ResearchError
        raise ResearchError(
            "RESEARCH_PARAM_INVALID",
            f"小样本拒绝: group_a={len(a)}, group_b={len(b)} < {min_samples}",
            detail={"group_a_n": len(a), "group_b_n": len(b),
                    "min_samples": min_samples,
                    "hint": "扩大分组窗口或调低 min_samples"})

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
