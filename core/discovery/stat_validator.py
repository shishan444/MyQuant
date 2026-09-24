"""Statistical validator: conditional probability tables and cross-validation.

Validates discovered rules using statistical methods:
- Discretizes indicator states into bins
- Builds conditional probability tables
- Uses Wilson confidence intervals for small samples
- Cross-validates rules against out-of-sample data
"""

from __future__ import annotations

from typing import Dict, List, Tuple

import numpy as np
import pandas as pd


def wilson_confidence(successes: int, total: int,
                      z: float = 1.96) -> Tuple[float, float]:
    """Wilson score confidence interval for a proportion.

    委托 scoring 权威实现（core.scoring.statistics.wilson_interval），
    保持原签名与返回，formula 收口不再内联。
    """
    from core.scoring.statistics import wilson_interval

    return wilson_interval(successes, total, z)


def discretize_indicator(series: pd.Series, name: str) -> pd.Series:
    """Discretize an indicator into labeled bins.

    Args:
        series: Indicator value series.
        name: Indicator name (used to determine binning strategy).

    Returns:
        Series of string labels (e.g., "oversold", "neutral", "overbought").
    """
    if "rsi" in name.lower():
        bins = [0, 30, 50, 70, 100]
        labels = ["oversold", "low", "high", "overbought"]
        return pd.cut(series, bins=bins, labels=labels, include_lowest=True)

    if "bb_percent" in name:
        bins = [0, 0.2, 0.5, 0.8, 1.0]
        labels = ["lower_band", "below_mid", "above_mid", "upper_band"]
        return pd.cut(series, bins=bins, labels=labels, include_lowest=True)

    if "stoch" in name:
        bins = [0, 20, 50, 80, 100]
        labels = ["oversold", "low", "high", "overbought"]
        return pd.cut(series, bins=bins, labels=labels, include_lowest=True)

    # Default: quartile-based
    try:
        return pd.qcut(series, q=4, labels=["Q1", "Q2", "Q3", "Q4"], duplicates="drop")
    except (ValueError, TypeError):
        return pd.Series("unknown", index=series.index)


def build_conditional_prob_table(
    df: pd.DataFrame,
    indicator_col: str,
    target_col: str,
    target_value: str = "UP",
) -> Dict[str, Dict[str, float]]:
    """Build a conditional probability table for one indicator.

    Args:
        df: DataFrame with indicator and target columns.
        indicator_col: Column name of the indicator.
        target_col: Column name of the target (e.g., "direction").
        target_value: Value of the target to compute probability for.

    Returns:
        Dict mapping bin label -> {prob, lower_ci, upper_ci, count}.
    """
    discretized = discretize_indicator(df[indicator_col], indicator_col)

    result = {}
    for label in discretized.unique():
        if pd.isna(label):
            continue
        mask = discretized == label
        subset = df.loc[mask, target_col]
        total = len(subset)
        successes = (subset == target_value).sum()

        lower, upper = wilson_confidence(int(successes), total)
        result[str(label)] = {
            "prob": round(float(successes / total), 4) if total > 0 else 0.0,
            "lower_ci": round(lower, 4),
            "upper_ci": round(upper, 4),
            "count": total,
        }

    return result


def validate_rule_lift(
    df: pd.DataFrame,
    conditions: List[dict],
    target_col: str = "direction",
    target_value: str = "UP",
) -> float:
    """Validate a rule by computing its lift over baseline.

    Args:
        df: DataFrame with indicator and target columns.
        conditions: List of {feature, operator, threshold} dicts.
        target_col: Column name of the target.
        target_value: Value of the target.

    Returns:
        Lift ratio (>1 means rule is better than baseline).
    """
    # Baseline probability
    baseline_prob = (df[target_col] == target_value).mean()
    if baseline_prob == 0:
        return 0.0

    # Apply conditions
    mask = pd.Series(True, index=df.index)
    for cond in conditions:
        feat = cond.get("feature", "")
        op = cond.get("operator", "")
        threshold = cond.get("threshold", 0)

        if feat not in df.columns:
            continue

        if op == "le":
            mask &= df[feat] <= threshold
        elif op == "gt":
            mask &= df[feat] > threshold
        elif op == "lt":
            mask &= df[feat] < threshold
        elif op == "ge":
            mask &= df[feat] >= threshold

    # Compute conditional probability
    subset = df.loc[mask, target_col]
    if len(subset) < 10:
        return 0.0

    conditional_prob = (subset == target_value).mean()

    return round(float(conditional_prob / baseline_prob), 4)
