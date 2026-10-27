"""Divergence detection: price vs RSI/MACD direction mismatch.

Detects bullish and bearish divergences where price makes new extremes
but the indicator does not confirm, signaling potential reversal.
"""

from __future__ import annotations

import pandas as pd

from core.features.patterns.candlestick import (
    detect_bearish_engulfing,
    detect_evening_star,
    detect_three_black_crows,
    detect_shooting_star,
    detect_three_white_soldiers,
    detect_morning_star,
    detect_bullish_reversal,
    detect_bearish_reversal,
)


def detect_bullish_divergence(
    df: pd.DataFrame, lookback: int = 20,
    oversold: float | None = None,
) -> pd.DataFrame:
    """Bullish divergence: 价格臂当根创前 N 根最低 AND 动量臂 RSI
    未创同期新低（两臂窗口极值——B4-P2 语义升级）。

    窗口不含当根（价格臂 low[t] < min(low[t-N..t-1])——与 B5 Donchian
    同哲学）。oversold 门槛为可选过滤（缺省 None=不启用——纯背离
    语义；研究者要超卖过滤在假设条件另加 RSI 条件，语义分层）。
    """
    result = df.copy()

    rsi_col = None
    for col in df.columns:
        if col.startswith("rsi_"):
            rsi_col = col
            break

    if rsi_col is None:
        # B4-P0：静默置 0 改结构化拒绝（正常链路经 _compute_indicator
        # 的 depends_on 闭包自动补 RSI——本分支正常不可达，仅防绕过
        # 装载层的直调；恒 0 列对研究者是「无信号」假象）
        from core.analysis.errors import ResearchError  # 局部导入（core.data 先例）
        raise ResearchError(
            "RESEARCH_INDICATOR_DEPENDENCY_FAILED",
            "依赖指标 RSI 计算失败：BullishDivergence 需 rsi 列"
            "（经装载层闭包供给——直调面缺列即拒绝，不静默置 0）",
            detail={"indicator": "BullishDivergence", "dependency": "RSI"})

    rsi_val = df[rsi_col]
    # 两臂窗口极值（B4-P2）：前 N 根窗口（shift(1) 不含当根）
    prev_low_min = df["low"].shift(1).rolling(
        window=lookback, min_periods=lookback).min()
    prev_rsi_min = rsi_val.shift(1).rolling(
        window=lookback, min_periods=lookback).min()

    price_arm = df["low"] < prev_low_min       # 当根创前 N 根最低
    momentum_arm = rsi_val > prev_rsi_min      # RSI 未创同期新低

    combined = price_arm & momentum_arm
    if oversold is not None:                   # 可选超卖过滤（缺省纯背离）
        combined = combined & (rsi_val < oversold)

    result["pattern_bullish_divergence"] = combined.fillna(0).astype(int)
    return result


def detect_bearish_divergence(
    df: pd.DataFrame, lookback: int = 20,
    overbought: float | None = None,
) -> pd.DataFrame:
    """Bearish divergence: 价格臂当根创前 N 根最高 AND 动量臂 RSI
    未创同期新高（两臂窗口极值——B4-P2 语义升级，Bullish 镜像）。

    overbought 门槛为可选过滤（缺省 None=不启用——纯背离语义）。
    """
    result = df.copy()

    rsi_col = None
    for col in df.columns:
        if col.startswith("rsi_"):
            rsi_col = col
            break

    if rsi_col is None:
        # B4-P0：同 BullishDivergence——静默置 0 改结构化拒绝（对称）
        from core.analysis.errors import ResearchError
        raise ResearchError(
            "RESEARCH_INDICATOR_DEPENDENCY_FAILED",
            "依赖指标 RSI 计算失败：BearishDivergence 需 rsi 列"
            "（经装载层闭包供给——直调面缺列即拒绝，不静默置 0）",
            detail={"indicator": "BearishDivergence", "dependency": "RSI"})

    rsi_val = df[rsi_col]
    # 两臂窗口极值（镜像）：前 N 根窗口（shift(1) 不含当根）
    prev_high_max = df["high"].shift(1).rolling(
        window=lookback, min_periods=lookback).max()
    prev_rsi_max = rsi_val.shift(1).rolling(
        window=lookback, min_periods=lookback).max()

    price_arm = df["high"] > prev_high_max      # 当根创前 N 根最高
    momentum_arm = rsi_val < prev_rsi_max       # RSI 未创同期新高

    combined = price_arm & momentum_arm
    if overbought is not None:                  # 可选超买过滤
        combined = combined & (rsi_val > overbought)

    result["pattern_bearish_divergence"] = combined.fillna(0).astype(int)
    return result


# Pattern name to function mapping for easy dispatch
PATTERN_FUNCTIONS = {
    "pattern_bearish_engulfing": detect_bearish_engulfing,
    "pattern_evening_star": detect_evening_star,
    "pattern_3blackcrows": detect_three_black_crows,
    "pattern_shooting_star": detect_shooting_star,
    "pattern_3whitesoldiers": detect_three_white_soldiers,
    "pattern_morning_star": detect_morning_star,
    "pattern_bullish_reversal": detect_bullish_reversal,
    "pattern_bearish_reversal": detect_bearish_reversal,
    "pattern_bullish_divergence": detect_bullish_divergence,
    "pattern_bearish_divergence": detect_bearish_divergence,
}
