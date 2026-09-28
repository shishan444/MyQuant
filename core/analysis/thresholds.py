"""验证门槛表（add-ai-research-mcp 3.2/6.5 / add-inconclusive-verdict 三段闸门）。

信任模型纪律：验证判据是**预定义数值门槛**，AI 当次判断无权决定
通过与否（spec research-validation-protocol）。门槛 profile 配置化，
机械判定（judge 返回逐条规则的通过明细，不掺主观）。

三段闸门（0.2.11 inconclusive 第三判定值）：证据闸门前置——样本量
低于闸门线（profile 的 total_trades 阈值，缺省 5）或每笔收益均值
95% CI 跨零判 inconclusive（证据不足非证伪）；缺键仍走规则 fail
（工程错误 ≠ 证据不足）；证伪/通过路径零变化。CI 口径 t 分布
（小样本正确口径，比正态近似更宽 → 更保守）。

组合策略加严门（6.5）：随机窗口最差表现门槛按倍数收紧
（COMPOSITE_WINDOW_MULTIPLIER），倍数配置化可随实盘经验调整。
闸门线随 profile 收紧（tighten 后 6——组合要求更多证据才可定论，
样本不足仍 inconclusive 而非 fail）。
"""
from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional, Tuple

import numpy as np

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

# 证据闸门缺省样本线（profile 无 total_trades 规则时的兜底；0.2.11）
_MIN_TRADES_DEFAULT = 5

_OPS = {
    "gt": lambda v, t: v > t,
    "ge": lambda v, t: v >= t,
    "lt": lambda v, t: v < t,
    "le": lambda v, t: v <= t,
}


def _rule_details(metrics: Dict[str, Any], prof: Dict[str, Any]) -> Tuple[bool, List[Dict[str, Any]]]:
    """既有规则循环（证伪/通过判定与逐条明细）——原 judge 主体零变化。"""
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
    return all_passed, details


def _ci_entry(trade_returns: "np.ndarray") -> Optional[Dict[str, Any]]:
    """每笔收益均值的 95% 置信区间披露条目（t 分布，ddof=1）。

    n≥2 才有定义（调用侧证据闸门保证 n≥闸门线≥5，此处防御性兜底）。
    返回 details 追加条目——rule="ci_95" 是分类标签（与规则条目共用
    键位），不带 op/threshold/actual/passed（非门槛规则）；条目存在
    即 CI 实际计算过。std=0（全同值）时半宽为 0，区间退化为点，
    跨零判定照常（mean==0 即跨）。
    """
    from scipy import stats  # 项目惯例：函数内按需导入（同 condition_kernel）

    n = int(len(trade_returns))
    if n < 2:
        return None
    mean = float(np.mean(trade_returns))
    std = float(np.std(trade_returns, ddof=1))
    t_crit = float(stats.t.ppf(0.975, n - 1))
    half = t_crit * std / (n ** 0.5)
    lower, upper = mean - half, mean + half
    return {"rule": "ci_95", "n": n, "mean": mean, "std": std,
            "half_width": float(half), "lower": float(lower),
            "upper": float(upper),
            "crosses_zero": bool(lower <= 0 <= upper)}


def judge(
    metrics: Dict[str, float],
    profile: Dict[str, Any] | None = None,
    trade_returns: "np.ndarray | None" = None,
) -> Tuple[str, List[Dict[str, Any]]]:
    """按 profile 机械判定（三段闸门，0.2.11 三态）。

    判定序（优先级从高到低）：
    1. 证据闸门：total_trades 缺键 → 跳过闸门走规则路径（缺数据不过
       门 fail）；total_trades < 闸门线（profile 的 total_trades 阈值，
       缺省 5）→ inconclusive（样本不足）；trade_returns 缺失或长度
       与 total_trades 不匹配 → fail（工程错误——数据通道断裂）；
       95% CI 跨零 → inconclusive（方向不可区分于噪声）。
    2. 证伪检查（证据充分前提下）：规则不过 → fail。
    3. 通过检查（其余）→ pass。

    Args:
        metrics: 回测指标字典（键须覆盖 profile 规则字段）。
        profile: 门槛 profile（None 用 DEFAULT_PROFILE）。
        trade_returns: 每笔收益序列（PnL/init_cash，engine 通道）——
            CI 判定输入；metrics 保持纯可序列化（序列不落库）。

    Returns:
        (verdict, details)：verdict ∈ {"pass", "fail", "inconclusive"}；
        details 逐条规则 {rule, op, threshold, actual, passed} + 可能
        追加 ci_95 披露条目（仅 CI 实际计算过时存在）。
    """
    prof = profile or DEFAULT_PROFILE
    all_passed, details = _rule_details(metrics, prof)

    total_trades = metrics.get("total_trades")
    if total_trades is not None:
        gate_line = prof["rules"].get(
            "total_trades", {}).get("value", _MIN_TRADES_DEFAULT)
        if total_trades < gate_line:
            return "inconclusive", details
        if trade_returns is None or len(trade_returns) != total_trades:
            return "fail", details  # 工程错误：数据通道断裂
        ci = _ci_entry(trade_returns)
        if ci is not None:
            details.append(ci)
            if ci["crosses_zero"]:
                return "inconclusive", details
    return ("pass" if all_passed else "fail"), details


def _aggregate_verdicts(verdicts: Iterable[str]) -> str:
    """最差聚合三态映射（random_window 总判定与 staged 汇总唯一实现）。

    优先级 fail > inconclusive > pass；空列表防御返回 "inconclusive"
    （无证据=不定论——与证据闸门精神同向；物理不可达路径的防御值
    语义仍须站得住）。
    """
    vs = list(verdicts)
    if not vs:
        return "inconclusive"
    if any(v == "fail" for v in vs):
        return "fail"
    if any(v == "inconclusive" for v in vs):
        return "inconclusive"
    return "pass"


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
