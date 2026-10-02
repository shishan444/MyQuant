"""验证引擎的形态检测算法。

服务对象：validation.engine 的形态类动作分派（divergence/consecutive
经 _resolve_pattern、touch_bounce/role_reversal 经 _resolve_snr_pattern）
与 tests/backtest/test_patterns 直调。职责边界：纯向量形态判定
（输入列 → 布尔掩码），不负责条件解析与列名兜底（归调用方）。
"""
from __future__ import annotations

import numpy as np
import pandas as pd


def detect_divergence_top(
    df: pd.DataFrame,
    subject_col: str,
    price_col: str = "close",
    lookback: int = 20,
) -> pd.Series:
    """顶背离检测。

    价格创新高而指标未创新高——比较 lookback bar 内最近两个局部
    极值点。
    """
    if subject_col not in df.columns or price_col not in df.columns:
        return pd.Series(False, index=df.index)

    result = pd.Series(False, index=df.index)
    price = df[price_col].values
    subject = df[subject_col].values

    for i in range(lookback, len(df)):
        window_price = price[i - lookback : i + 1]
        window_subject = subject[i - lookback : i + 1]

        # 定位局部极大值
        peaks_price = _find_local_maxima(window_price)
        if len(peaks_price) < 2:
            continue

        # 取最近两个峰
        p1, p2 = peaks_price[-2], peaks_price[-1]
        # 价格高点抬高、指标高点走低
        if window_price[p2] > window_price[p1] and window_subject[p2] < window_subject[p1]:
            result.iloc[i] = True

    return result


def detect_divergence_bottom(
    df: pd.DataFrame,
    subject_col: str,
    price_col: str = "close",
    lookback: int = 20,
) -> pd.Series:
    """底背离检测。

    价格创新低而指标未创新低。
    """
    if subject_col not in df.columns or price_col not in df.columns:
        return pd.Series(False, index=df.index)

    result = pd.Series(False, index=df.index)
    price = df[price_col].values
    subject = df[subject_col].values

    for i in range(lookback, len(df)):
        window_price = price[i - lookback : i + 1]
        window_subject = subject[i - lookback : i + 1]

        # 定位局部极小值
        troughs_price = _find_local_minima(window_price)
        if len(troughs_price) < 2:
            continue

        t1, t2 = troughs_price[-2], troughs_price[-1]
        # 价格低点走低、指标低点抬高
        if window_price[t2] < window_price[t1] and window_subject[t2] > window_subject[t1]:
            result.iloc[i] = True

    return result


def detect_consecutive_up(
    df: pd.DataFrame,
    subject_col: str,
    count: int = 3,
) -> pd.Series:
    """检测 subject 列的连续上涨 bar。"""
    if subject_col not in df.columns:
        return pd.Series(False, index=df.index)

    series = df[subject_col]
    result = pd.Series(False, index=df.index)

    # 最近 count 个值是否严格递增
    for i in range(count, len(df)):
        window = series.iloc[i - count : i + 1].values
        if _is_strictly_increasing(window):
            result.iloc[i] = True

    return result


def detect_consecutive_down(
    df: pd.DataFrame,
    subject_col: str,
    count: int = 3,
) -> pd.Series:
    """检测 subject 列的连续下跌 bar。"""
    if subject_col not in df.columns:
        return pd.Series(False, index=df.index)

    series = df[subject_col]
    result = pd.Series(False, index=df.index)

    for i in range(count, len(df)):
        window = series.iloc[i - count : i + 1].values
        if _is_strictly_decreasing(window):
            result.iloc[i] = True

    return result


# ---------------------------------------------------------------------------
# 辅助函数
# ---------------------------------------------------------------------------

def _find_local_maxima(arr: np.ndarray) -> list:
    """求数组的局部极大值下标。"""
    peaks = []
    for i in range(1, len(arr) - 1):
        if arr[i] > arr[i - 1] and arr[i] > arr[i + 1]:
            peaks.append(i)
    # 端点高于邻点时计入
    if len(arr) > 1 and arr[0] > arr[1]:
        peaks.insert(0, 0)
    if len(arr) > 1 and arr[-1] > arr[-2]:
        peaks.append(len(arr) - 1)
    return peaks


def _find_local_minima(arr: np.ndarray) -> list:
    """求数组的局部极小值下标。"""
    troughs = []
    for i in range(1, len(arr) - 1):
        if arr[i] < arr[i - 1] and arr[i] < arr[i + 1]:
            troughs.append(i)
    if len(arr) > 1 and arr[0] < arr[1]:
        troughs.insert(0, 0)
    if len(arr) > 1 and arr[-1] < arr[-2]:
        troughs.append(len(arr) - 1)
    return troughs


def _is_strictly_increasing(arr: np.ndarray) -> bool:
    """数组值是否严格递增（NaN 视为不成立）。"""
    for i in range(1, len(arr)):
        if np.isnan(arr[i]) or np.isnan(arr[i - 1]):
            return False
        if arr[i] <= arr[i - 1]:
            return False
    return True


def _is_strictly_decreasing(arr: np.ndarray) -> bool:
    """数组值是否严格递减（NaN 视为不成立）。"""
    for i in range(1, len(arr)):
        if np.isnan(arr[i]) or np.isnan(arr[i - 1]):
            return False
        if arr[i] >= arr[i - 1]:
            return False
    return True


# ---------------------------------------------------------------------------
# 支撑/阻力形态检测
# ---------------------------------------------------------------------------

def detect_touch_bounce(
    df: pd.DataFrame,
    indicator_col: str,
    direction: str = "support",
    proximity_pct: float = 0.01,
    bounce_pct: float = 0.005,
) -> pd.Series:
    """对指标线的触及反弹形态检测。

    Args:
        df: 含指标列的 OHLCV DataFrame。
        indicator_col: 指标线列名。
        direction: "support" 或 "resistance"。
        proximity_pct: 触及判定的邻近阈值（线值的百分比）。
        bounce_pct: 认定反弹的最小涨幅。

    Returns:
        触及反弹事件的布尔 Series。
    """
    if indicator_col not in df.columns:
        return pd.Series(False, index=df.index)

    result = pd.Series(False, index=df.index)
    line = df[indicator_col].values
    low = df["low"].values
    high = df["high"].values
    close = df["close"].values
    n = len(df)

    for i in range(1, n - 1):
        if np.isnan(line[i]):
            continue

        tolerance = abs(line[i]) * proximity_pct

        if direction == "support":
            # 下影触及线附近 + 收盘在线上 + 次 bar 收盘更高
            touches = abs(low[i] - line[i]) <= tolerance
            closes_above = close[i] > line[i]
            bounces = close[i + 1] > close[i] * (1 + bounce_pct)
            if touches and closes_above and bounces:
                result.iloc[i] = True
        else:
            # 上影触及线附近 + 收盘在线下 + 次 bar 收盘更低
            touches = abs(high[i] - line[i]) <= tolerance
            closes_below = close[i] < line[i]
            bounces = close[i + 1] < close[i] * (1 - bounce_pct)
            if touches and closes_below and bounces:
                result.iloc[i] = True

    return result


def detect_role_reversal(
    df: pd.DataFrame,
    indicator_col: str,
    role: str = "resistance",
    lookback: int = 10,
) -> pd.Series:
    """支撑/阻力角色互换形态检测。

    Args:
        df: 含指标列的 OHLCV DataFrame。
        indicator_col: 指标线列名。
        role: "resistance"（原支撑变阻力）或 "support"（原阻力变支撑）。
        lookback: 回看 bar 数（核验先前相对位置）。

    Returns:
        角色互换事件的布尔 Series。
    """
    if indicator_col not in df.columns:
        return pd.Series(False, index=df.index)

    result = pd.Series(False, index=df.index)
    line = df[indicator_col].values
    close = df["close"].values
    n = len(df)

    for i in range(lookback, n):
        if np.isnan(line[i]):
            continue

        if role == "resistance":
            # 线原为支撑（价格在线上）→ 现价格到线下
            was_above = close[i - lookback] > line[i - lookback]
            now_below = close[i] < line[i]
            if was_above and now_below:
                result.iloc[i] = True
        else:
            # 线原为阻力（价格在线下）→ 现价格到线上
            was_below = close[i - lookback] < line[i - lookback]
            now_above = close[i] > line[i]
            if was_below and now_above:
                result.iloc[i] = True

    return result
