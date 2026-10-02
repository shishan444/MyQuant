"""Pivot 点检测：识别价格序列的局部峰与谷。

服务对象：top_pattern（detect_pivots）与 pattern_match
（PivotPoint 结构）两模块，及 tests/evolution 特征锚直调。
职责边界：纯滚动窗口极值判定，不做形态识别与事后校验。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import List

import numpy as np
import pandas as pd


@dataclass
class PivotPoint:
    """单个 pivot 点（局部峰或谷）。"""

    index: int          # DataFrame 中的 bar 位置
    price: float        # 峰取 high，谷取 low
    pivot_type: str     # "peak" 或 "trough"
    volume: float = 0.0 # 该 bar 的成交量


def detect_pivots(
    df: pd.DataFrame,
    bars_left: int = 6,
    bars_right: int = 6,
) -> List[PivotPoint]:
    """以滚动窗口方式检测 pivot 点。

    某 bar 是 *峰*：其 high 为邻域窗口 [i - bars_left ..
    i + bars_right] 内的最大值；是 *谷*：其 low 为同窗口的
    最小值。并列极值不取——窗口内极值出现多于一次时该 bar
    不判为 pivot。

    Args:
        df: 至少含 ``high`` 与 ``low`` 列的 DataFrame。
        bars_left: 候选 bar 左侧的 bar 数。
        bars_right: 候选 bar 右侧的 bar 数。

    Returns:
        按时间序排列的 PivotPoint 实例列表。
    """
    highs = df["high"].values.astype(np.float64)
    lows = df["low"].values.astype(np.float64)
    volumes = df["volume"].values.astype(np.float64) if "volume" in df.columns else np.zeros(len(df))
    n = len(df)
    window = bars_left + bars_right + 1

    if n < window:
        return []

    pivots: List[PivotPoint] = []

    for i in range(bars_left, n - bars_right):
        left = i - bars_left
        right = i + bars_right + 1

        # 峰：中心 bar 的 high 为窗口最大值
        h_window = highs[left:right]
        if highs[i] == np.max(h_window) and np.sum(h_window == highs[i]) == 1:
            pivots.append(PivotPoint(
                index=i, price=float(highs[i]),
                pivot_type="peak", volume=float(volumes[i]),
            ))

        # 谷：中心 bar 的 low 为窗口最小值
        l_window = lows[left:right]
        if lows[i] == np.min(l_window) and np.sum(l_window == lows[i]) == 1:
            pivots.append(PivotPoint(
                index=i, price=float(lows[i]),
                pivot_type="trough", volume=float(volumes[i]),
            ))

    # 按时间序排序
    pivots.sort(key=lambda p: p.index)
    return pivots
