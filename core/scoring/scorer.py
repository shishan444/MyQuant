"""Objective-driven fitness evaluation.

Single objective function (Sharpe/Calmar/annual_return) drives evolution selection.
Constraints (drawdown, trades, profit_factor) act as hard gates — fail any and fitness=0.
Satisfaction ratios are still computed for dimension_scores and diversity, but do not
participate in fitness calculation.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, Literal, Optional

ObjectiveType = Literal["sharpe", "calmar", "annual_return"]

_VALID_OBJECTIVES = {"sharpe", "calmar", "annual_return"}


@dataclass
class RequirementsConfig:
    """User-configurable requirements for strategy qualification."""
    objective: str = "sharpe"
    # Constraints
    max_drawdown: float = 0.30
    min_total_trades: int = 10
    min_profit_factor: float = 1.2
    min_annual_return: float = 0.0
    # Kept for backward compatibility but not used in fitness
    min_win_rate: float = 0.0


# Default requirements used when none specified
DEFAULT_REQUIREMENTS = RequirementsConfig()


def compute_fitness(
    metrics: dict,
    requirements: RequirementsConfig | None = None,
    liquidated: bool = False,
) -> Dict:
    """Compute fitness from metrics against requirements.

    Uses a single objective function (controlled by requirements.objective) as fitness,
    with constraints as hard gates.  Any constraint failure sets fitness=0.

    Returns:
        Dict with fitness (0+), qualified (bool), satisfaction details,
        raw_metrics, objective_name, objective_value, and liquidated flag.
    """
    if requirements is None:
        requirements = DEFAULT_REQUIREMENTS

    # Zero trades or liquidated = zero fitness
    if metrics.get("total_trades", 0) == 0 or liquidated:
        return _zero_result(metrics, liquidated, requirements.objective)

    objective = requirements.objective
    if objective not in _VALID_OBJECTIVES:
        objective = "sharpe"

    # --- Constraint checks (hard gates) ---
    actual_return = metrics.get("annual_return", 0.0)
    actual_drawdown = abs(metrics.get("max_drawdown", 0.0))
    actual_trades = metrics.get("total_trades", 0)
    actual_pf = metrics.get("profit_factor", 0.0)
    actual_winrate = metrics.get("win_rate")

    constraint_failures = []

    if actual_drawdown > requirements.max_drawdown:
        constraint_failures.append("max_drawdown")
    if actual_trades < requirements.min_total_trades:
        constraint_failures.append("total_trades")
    if actual_pf < requirements.min_profit_factor:
        constraint_failures.append("profit_factor")
    if requirements.min_annual_return > 0 and actual_return < requirements.min_annual_return:
        constraint_failures.append("annual_return")

    # --- Objective function ---
    if objective == "sharpe":
        objective_value = metrics.get("sharpe_ratio", 0.0)
    elif objective == "calmar":
        objective_value = metrics.get("calmar_ratio", 0.0)
    else:  # annual_return
        objective_value = actual_return

    # Clamp negative objective to 0
    fitness = max(0.0, objective_value)

    # If any constraint failed, fitness = 0
    if constraint_failures:
        fitness = 0.0

    # Qualified = no constraint failures and fitness > 0
    qualified = len(constraint_failures) == 0 and fitness > 0

    # --- Satisfaction ratios (for dimension_scores / diversity) ---
    satisfaction = _compute_satisfaction(
        actual_return, actual_drawdown, actual_winrate,
        actual_trades, actual_pf, requirements,
    )

    return {
        "fitness": round(fitness, 6),
        "qualified": qualified,
        "satisfaction": satisfaction,
        "raw_metrics": metrics,
        "liquidated": liquidated,
        "objective_name": objective,
        "objective_value": round(objective_value, 6),
        "constraint_failures": constraint_failures,
    }


def _compute_satisfaction(
    actual_return: float,
    actual_drawdown: float,
    actual_winrate: float,
    actual_trades: int,
    actual_pf: float,
    req: RequirementsConfig,
) -> Dict:
    """Compute per-dimension satisfaction ratios (for dimension_scores / diversity).

    These ratios are NOT used for fitness — they serve downstream consumers
    (diversity.py equity_distance, frontend dimension bars).
    """
    satisfaction = {}

    # Return ratio
    min_ar = req.min_annual_return if req.min_annual_return > 0 else 0.15
    if actual_return < 0:
        return_ratio = max(0.0, 1.0 + actual_return / min_ar) * 0.5
    else:
        return_ratio = actual_return / min_ar if min_ar > 0 else 1.0
    satisfaction["annual_return"] = _dim_detail(
        actual_return, min_ar, return_ratio, actual_return >= min_ar, is_percent=True,
    )

    # Drawdown ratio (inverted)
    if actual_drawdown > 0 and req.max_drawdown > 0:
        dd_ratio = req.max_drawdown / actual_drawdown
    else:
        dd_ratio = 1.0
    satisfaction["max_drawdown"] = _dim_detail(
        actual_drawdown, req.max_drawdown, dd_ratio,
        actual_drawdown <= req.max_drawdown, is_percent=True, inverted=True,
    )

    # Win rate ratio；零交易缺失语义不参与评分维度（spec：无交易缺省）
    if actual_winrate is None:
        satisfaction["win_rate"] = {
            "actual": None, "required": req.min_win_rate, "ratio": None,
            "met": False, "is_percent": True, "inverted": False, "missing": True,
        }
    else:
        if req.min_win_rate > 0:
            wr_ratio = actual_winrate / req.min_win_rate
        else:
            wr_ratio = 1.0 if actual_winrate > 0 else 0.0
        satisfaction["win_rate"] = _dim_detail(
            actual_winrate, req.min_win_rate, wr_ratio,
            actual_winrate >= req.min_win_rate, is_percent=True,
        )

    # Trade count ratio
    if req.min_total_trades > 0:
        trades_ratio = actual_trades / req.min_total_trades
    else:
        trades_ratio = 1.0
    satisfaction["total_trades"] = _dim_detail(
        float(actual_trades), float(req.min_total_trades), trades_ratio,
        actual_trades >= req.min_total_trades, is_percent=False,
    )

    # Profit factor ratio
    if req.min_profit_factor > 0:
        pf_ratio = actual_pf / req.min_profit_factor
    else:
        pf_ratio = 1.0
    satisfaction["profit_factor"] = _dim_detail(
        actual_pf, req.min_profit_factor, pf_ratio,
        actual_pf >= req.min_profit_factor, is_percent=False,
    )

    return satisfaction


def _dim_detail(
    actual: float,
    required: float,
    ratio: float,
    met: bool,
    is_percent: bool,
    inverted: bool = False,
) -> Dict:
    """Build satisfaction detail dict for one dimension."""
    return {
        "actual": round(actual, 6),
        "required": round(required, 6),
        "ratio": round(ratio, 4),
        "met": met,
        "is_percent": is_percent,
        "inverted": inverted,
    }


def _zero_result(metrics: dict, liquidated: bool, objective: str = "sharpe") -> Dict:
    """Return zero-fitness result for trivial cases."""
    return {
        "fitness": 0.0,
        "qualified": False,
        "satisfaction": {},
        "raw_metrics": metrics,
        "liquidated": liquidated,
        "objective_name": objective,
        "objective_value": 0.0,
        "constraint_failures": [],
    }


# ---------------------------------------------------------------------------
# Backward compatibility shim
# ---------------------------------------------------------------------------
# Old code imports score_strategy from this module.
# We provide a thin wrapper that translates old call patterns to new compute_fitness.

def score_strategy(
    metrics: dict,
    template_name: str = "explorer",
    template=None,
    liquidated: bool = False,
    max_drawdown_limit: float | None = None,
    min_annual_return_limit: float | None = None,
) -> Dict:
    """Backward-compatible wrapper around compute_fitness.

    Translates old template-based scoring calls to objective-driven fitness.
    Returns a dict with both new (fitness/qualified) and legacy (total_score) fields
    so callers can migrate incrementally.
    """
    # Resolve template aliases
    _ALIASES = {
        "balanced": "optimizer", "steady": "optimizer", "custom": "optimizer",
        "aggressive": "explorer", "profit_first": "explorer",
        "conservative": "optimizer", "risk_first": "optimizer",
    }
    resolved_name = _ALIASES.get(template_name, template_name)

    # Map template to objective and constraints
    if resolved_name == "optimizer":
        objective = "sharpe"
        hc_return = min_annual_return_limit if min_annual_return_limit is not None else 0.10
        hc_drawdown = abs(max_drawdown_limit) if max_drawdown_limit is not None else 0.60
        actual_return = metrics.get("annual_return", 0.0)
        actual_dd = abs(metrics.get("max_drawdown", 0.0))
        if actual_return < hc_return:
            return _hard_constraint_fail(metrics, liquidated, resolved_name, "annual_return")
        if actual_dd > hc_drawdown:
            return _hard_constraint_fail(metrics, liquidated, resolved_name, "max_drawdown")
    elif resolved_name == "max_return":
        objective = "annual_return"
        hc_return = min_annual_return_limit if min_annual_return_limit is not None else -1.0
        hc_drawdown = abs(max_drawdown_limit) if max_drawdown_limit is not None else 0.99
    else:  # explorer
        objective = "sharpe"
        hc_return = min_annual_return_limit if min_annual_return_limit is not None else -1.0
        hc_drawdown = abs(max_drawdown_limit) if max_drawdown_limit is not None else 0.99

    # Build requirements — lenient defaults for backward compatibility
    req = RequirementsConfig(
        objective=objective,
        min_annual_return=max(0.0, hc_return) if hc_return and hc_return > 0 else 0.0,
        max_drawdown=hc_drawdown,
        min_win_rate=0.0,
        min_total_trades=0,
        min_profit_factor=0.0,
    )

    result = compute_fitness(metrics, requirements=req, liquidated=liquidated)

    # Map fitness to total_score (0-100) for legacy consumers
    fitness = result["fitness"]
    if objective == "sharpe":
        # Sharpe range ~0-5 → map to 0-100
        legacy_score = min(100.0, max(0.0, fitness * 20.0))
    elif objective == "calmar":
        # Calmar range ~0-5 → map to 0-100
        legacy_score = min(100.0, max(0.0, fitness * 20.0))
    else:  # annual_return
        # annual_return range ~0-10+ → map to 0-100
        legacy_score = min(100.0, max(0.0, fitness * 10.0))

    result["total_score"] = round(legacy_score, 2)
    result["template_name"] = resolved_name
    result["threshold"] = 60.0
    # 缺失维度（如零交易 win_rate 的 ratio=None）不参与评分：保留
    # satisfaction 明细（含 missing 标记），dimension_scores 仅含有值维度
    result["dimension_scores"] = {
        k: round(v["ratio"] * 100, 1)
        for k, v in result.get("satisfaction", {}).items()
        if v.get("ratio") is not None
    }

    return result


def _hard_constraint_fail(metrics: dict, liquidated: bool, template_name: str, dim: str) -> Dict:
    """Return zero-score result when a hard constraint fails."""
    return {
        "fitness": 0.0,
        "qualified": False,
        "satisfaction": {},
        "raw_metrics": metrics,
        "liquidated": liquidated,
        "total_score": 0.0,
        "template_name": template_name,
        "threshold": 60.0,
        "dimension_scores": {},
        "hard_constraint_failed": dim,
    }
