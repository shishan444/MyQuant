"""验证门槛表（add-ai-research-mcp 3.2/6.5）。

信任模型纪律：验证判据是**预定义数值门槛**，AI 当次判断无权决定
通过与否（spec research-validation-protocol）。门槛 profile 配置化，
机械判定（judge 返回逐条规则的通过明细，不掺主观）。

组合策略加严门（6.5）：随机窗口最差表现门槛按倍数收紧
（COMPOSITE_WINDOW_MULTIPLIER），倍数配置化可随实盘经验调整。
"""
from __future__ import annotations

from typing import Any, Dict, List, Tuple

# 默认门槛 profile：BacktestResult 现有字段上的机械规则。
# - 盈利：区间总收益 > 0
# - 回撤上限：最大回撤 < 25%
# - 交易样本下限：总交易数 >= 5（过少交易无统计意义）
DEFAULT_PROFILE: Dict[str, Any] = {
    "name": "default",
    "rules": {
        "total_return": {"op": "gt", "value": 0.0},
        "max_drawdown": {"op": "lt", "value": 0.25},
        "total_trades": {"op": "ge", "value": 5},
    },
}

# 组合策略随机窗口门槛加严倍数（最差窗口指标按此倍数收紧，6.5）
COMPOSITE_WINDOW_MULTIPLIER = 1.2

_OPS = {
    "gt": lambda v, t: v > t,
    "ge": lambda v, t: v >= t,
    "lt": lambda v, t: v < t,
    "le": lambda v, t: v <= t,
}


def judge(metrics: Dict[str, float], profile: Dict[str, Any] | None = None) -> Tuple[str, List[Dict[str, Any]]]:
    """按 profile 机械判定。

    Args:
        metrics: 回测指标字典（键须覆盖 profile 规则字段）。

    Returns:
        (verdict, details)：verdict ∈ {"pass", "fail"}；
        details 逐条 {rule, op, threshold, actual, passed}。
        profile 引用了 metrics 缺失的键 → 该条 fail（缺数据不过门，
        宁可多拒）。
    """
    prof = profile or DEFAULT_PROFILE
    details: List[Dict[str, Any]] = []
    all_passed = True
    for rule, spec in prof["rules"].items():
        op = spec["op"]
        threshold = spec["value"]
        actual = metrics.get(rule)
        passed = (
            actual is not None
            and _OPS[op](actual, threshold)
        )
        if not passed:
            all_passed = False
        details.append({
            "rule": rule, "op": op,
            "threshold": threshold, "actual": actual,
            "passed": bool(passed),
        })
    return ("pass" if all_passed else "fail"), details


def tighten_profile(multiplier: float = COMPOSITE_WINDOW_MULTIPLIER) -> Dict[str, Any]:
    """按倍数收紧门槛（组合策略加严用；正向上界收紧、负向下界收紧）。"""
    import copy

    prof = copy.deepcopy(DEFAULT_PROFILE)
    prof["name"] = f"default_tightened_x{multiplier}"
    for rule, spec in prof["rules"].items():
        v = spec["value"]
        if spec["op"] in ("gt", "ge"):
            spec["value"] = v * multiplier if v != 0 else 0.0
        elif spec["op"] in ("lt", "le"):
            spec["value"] = v / multiplier if v != 0 else 0.0
        else:  # total_trades 等计数规则：按倍数上取整
            spec["value"] = v * multiplier
    return prof
