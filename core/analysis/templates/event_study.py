"""事件研究模板（add-ai-research-mcp 2.4）。

语义：给定事件触发条件，把每个事件后的 [0, window] bar 价格路径
对齐到事件时刻（相对事件 bar 收盘归一），统计路径分布（均值/分位）
与终点收益分布——回答"这类事件之后典型怎么走、最坏最好差多少"。

窗口对齐：事件 bar 为 t0，路径元素 (close[t0+k] - close[t0]) /
close[t0]，k = 1..window（事件当 bar 不计，避免把事件本身算进路径）。
尾部不完整路径的事件丢弃（不 pad）。
"""
from __future__ import annotations

from typing import Any, Dict

import numpy as np
import pandas as pd

from core.analysis.result import AnalysisResult, build_result


def event_study(
    df: pd.DataFrame,
    *,
    condition: Dict[str, Any],
    window: int = 12,
    data_ranges: list[dict[str, str]],
    min_events: int = 30,
    hypothesis_id: str | None = None,
) -> AnalysisResult:
    """事件窗对齐的路径统计。

    Args:
        condition: {subject, action, target}（假设引擎同源形制）。
        window: 事件后观察窗（bar 数）。

    Raises:
        ValueError: 事件数不足（小样本拒绝）。
    """
    from core.validation.engine import _evaluate_single_condition

    mask = _evaluate_single_condition(df, condition)
    mask = mask.fillna(False).astype(bool)
    event_idx = np.where(mask.values)[0]

    # 丢弃尾部不完整窗口的事件
    event_idx = event_idx[event_idx + window < len(df)]

    if len(event_idx) < min_events:
        raise ValueError(
            f"事件数 {len(event_idx)} 不足 {min_events}（小样本拒绝）")

    close = df["close"].values
    # 路径矩阵：行=事件，列=k=1..window
    paths = np.empty((len(event_idx), window))
    for row, t0 in enumerate(event_idx):
        base = close[t0]
        paths[row] = (close[t0 + 1: t0 + 1 + window] - base) / base

    mean_path = paths.mean(axis=0)
    q05 = np.quantile(paths, 0.05, axis=0)
    q25 = np.quantile(paths, 0.25, axis=0)
    q50 = np.quantile(paths, 0.50, axis=0)
    q75 = np.quantile(paths, 0.75, axis=0)
    q95 = np.quantile(paths, 0.95, axis=0)

    terminal = paths[:, -1]
    # 终点收益的 t 检验（H0: 事件后终点收益均值为 0）
    from scipy import stats as sps
    t_stat, p_value = sps.ttest_1samp(terminal, 0.0)

    confidence = {
        "method": "one_sample_t",
        "p_value": float(p_value),
        "n_events": int(len(event_idx)),
        "alpha": 0.05,
    }
    payload = {
        "condition": dict(condition),
        "window": window,
        "n_events": int(len(event_idx)),
        "path": {
            "k": list(range(1, window + 1)),
            "mean": mean_path.round(6).tolist(),
            "q05": q05.round(6).tolist(),
            "q25": q25.round(6).tolist(),
            "median": q50.round(6).tolist(),
            "q75": q75.round(6).tolist(),
            "q95": q95.round(6).tolist(),
        },
        "terminal_return": {
            "mean": float(terminal.mean()),
            "std": float(terminal.std(ddof=1)),
            "min": float(terminal.min()),
            "max": float(terminal.max()),
            "positive_rate": float((terminal > 0).mean()),
        },
        "first_event_time": str(df.index[event_idx[0]]),
        "last_event_time": str(df.index[event_idx[-1]]),
    }

    return build_result(
        "event_study",
        sample_size=len(event_idx),
        confidence=confidence,
        data_ranges=data_ranges,
        payload=payload,
        min_samples=min_events,
        hypothesis_id=hypothesis_id,
    )
