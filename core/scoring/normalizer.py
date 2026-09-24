"""DEPRECATED: Normalization is replaced by RequirementsConfig satisfaction ratios.

This module exists solely for backward compatibility with code that imports
from core.scoring.normalizer. All new code should use compute_fitness
from core.scoring.scorer instead.
"""
from __future__ import annotations

from typing import List, Tuple


# Legacy breakpoints kept for backward compat
_RETURN_BREAKPOINTS: List[Tuple[float, float]] = [
    (-1.0, 0.0), (0.0, 10.0), (0.10, 30.0), (0.30, 60.0),
    (0.50, 80.0), (1.0, 95.0), (3.0, 100.0),
]
_SHARPE_BREAKPOINTS: List[Tuple[float, float]] = [
    (-1.0, 0.0), (0.0, 5.0), (0.5, 20.0), (1.0, 50.0),
    (1.5, 75.0), (2.0, 90.0), (3.0, 100.0),
]
_DRAWDOWN_BREAKPOINTS: List[Tuple[float, float]] = [
    (0.00, 100.0), (0.10, 80.0), (0.20, 60.0), (0.30, 40.0),
    (0.50, 15.0), (0.80, 0.0),
]
_PROFIT_FACTOR_BREAKPOINTS: List[Tuple[float, float]] = [
    (0.0, 0.0), (0.5, 10.0), (1.0, 30.0), (1.5, 60.0),
    (2.0, 80.0), (3.0, 100.0),
]
_ALPHA_BREAKPOINTS: List[Tuple[float, float]] = [
    (-0.50, 0.0), (-0.20, 15.0), (0.00, 40.0), (0.20, 60.0),
    (0.50, 80.0), (1.00, 95.0), (3.00, 100.0),
]


def piecewise_normalize(value: float, breakpoints: List[Tuple[float, float]]) -> float:
    if value <= breakpoints[0][0]:
        return breakpoints[0][1]
    if value >= breakpoints[-1][0]:
        return breakpoints[-1][1]
    for i in range(len(breakpoints) - 1):
        x0, y0 = breakpoints[i]
        x1, y1 = breakpoints[i + 1]
        if x0 <= value <= x1:
            t = (value - x0) / (x1 - x0)
            return y0 + t * (y1 - y0)
    return breakpoints[-1][1]


def normalize(metric_name: str, value: float) -> float:
    if metric_name == "annual_return":
        score = piecewise_normalize(value, _RETURN_BREAKPOINTS)
    elif metric_name == "sharpe_ratio":
        score = piecewise_normalize(value, _SHARPE_BREAKPOINTS)
    elif metric_name == "max_drawdown":
        score = piecewise_normalize(abs(value), _DRAWDOWN_BREAKPOINTS)
    elif metric_name == "profit_factor":
        score = piecewise_normalize(value, _PROFIT_FACTOR_BREAKPOINTS)
    elif metric_name == "alpha":
        score = piecewise_normalize(value, _ALPHA_BREAKPOINTS)
    elif metric_name == "monthly_consistency":
        score = value * 100
    elif metric_name == "win_rate":
        if value is None:
            return 0.0
        score = (value - 0.3) / 0.4 * 100
    elif metric_name == "calmar_ratio":
        score = min(value / 5.0, 1.0) * 100
    elif metric_name == "sortino_ratio":
        score = min(value / 4.0, 1.0) * 100
        if value < 0:
            score = max(0, 50 + value * 10)
    elif metric_name == "max_consecutive_losses":
        score = max(0, 100 - value * 10)
    elif metric_name == "r_squared":
        score = max(0.0, value) * 100
    else:
        score = 50.0
    return max(0.0, min(100.0, score))
