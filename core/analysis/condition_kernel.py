"""条件统计内核（掩码求值 + 去重叠 t 检验；add-regime-split 提升）。

0.2.5 全谱扫描定义于 screen_patterns.py 私有；0.2.6 param_sweep 以同包
import 成为第二消费者（其 design D4 明文「第三个消费者出现时再议提升，
现在搬是为重排而重排」）——add-regime-split regime_split 即第三消费者，
时机兑现，提升为分析域共享模块。内容自 screen_patterns 逐字保真。

消费者：screen_patterns / param_sweep / regime_split（改动须按
tests/README 守护清单回归全部消费者）。
"""
from __future__ import annotations

from typing import Any, Dict, Optional, Tuple

import numpy as np
import pandas as pd


def _mask_for(work: pd.DataFrame, condition: Dict[str, Any]) -> pd.Series:
    """单条件掩码（0.2.13 两段式内聚——四消费者共用：
    screen/param_sweep/regime_split/inspect_cases）。

    结构化 subject：resolve → 按 params 现算指标列（encode 得精确列名）
    → 精确列名求值（condition_stats 现行两段式成为全域标准形）；
    字符串 subject：精确列名命中。列缺失返回全 False 安全降级（既有契约）。
    """
    subject = condition["subject"]
    col = subject
    if isinstance(subject, dict):
        from core.analysis.column_ref import resolve_column_ref
        cdef = resolve_column_ref(subject)      # 校验前置（入口已过）
        col = cdef.encoded_name()
        if col not in work.columns:
            from core.features.indicators import _compute_indicator
            for cname, series in _compute_indicator(
                    work, cdef.indicator, dict(cdef.params)).items():
                work[cname] = series
    from core.validation.engine import _evaluate_single_condition
    mask = _evaluate_single_condition(work, {
        "subject": col,
        "action": condition["action"],
        "target": condition["target"],
    })
    return mask.fillna(False).astype(bool)


def _p_value_for(
    fwd: pd.Series, stat_mask: np.ndarray, horizon: int,
    baseline_mean: Optional[float],
) -> tuple[Optional[float], int]:
    """去重叠子集上的单样本 t 检验 p 值（0.2.1 condition_stats 同口径）。

    Returns:
        (p_value, n_valid)——p 为 None 表示不可检（样本<2/零方差/无基线）。
    """
    if baseline_mean is None:
        return None, 0
    locs = np.where(stat_mask)[0]
    picked: list[int] = []
    for loc in locs:
        if not picked or loc - picked[-1] >= horizon:
            picked.append(loc)
    subset = fwd.iloc[picked].dropna() if picked else fwd.iloc[0:0]
    if len(subset) < 2 or float(np.std(subset.to_numpy(), ddof=1)) <= 0:
        return None, int(len(subset))
    from scipy import stats  # 形制同 condition_stats：函数内按需导入

    _, p = stats.ttest_1samp(subset.to_numpy(), baseline_mean)
    return float(p), int(len(subset))
