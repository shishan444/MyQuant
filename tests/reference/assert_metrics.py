"""比对器：计数精确 / 浮点相对容差 1e-9 / 缺省语义区分（None≠0）。"""

from __future__ import annotations

import math
from dataclasses import dataclass

RTOL = 1e-9

# 计数类：口径分歧零容差
EXACT_KEYS = ("total_trades",)
# 浮点类：相对容差
FLOAT_KEYS = ("total_return", "sharpe_ratio", "max_drawdown", "win_rate",
              "annual_return", "calmar_ratio", "total_funding_cost")


@dataclass
class DiffReport:
    """结构化差异报告（口径分歧/实现缺陷的登记证据形态）。"""

    scenario: str
    diffs: list[str]

    def __bool__(self) -> bool:  # True = 有分歧
        return bool(self.diffs)

    def __str__(self) -> str:
        return f"[DiffReport scenario={self.scenario}] " + "; ".join(self.diffs)


def _num(v) -> float:
    return float(v)


def assert_metrics_equal(
    actual: dict, expected: dict, *, scenario: str = "", rtol: float = RTOL,
) -> None:
    """Assert two metric dicts equal under graded tolerance.

    - 计数类精确相等（分歧即口径分歧，不容容差）
    - 浮点类相对容差 rtol（默认 1e-9；双方均≈0 时用绝对零阈值）
    - None 与 0.0 语义区分：expected=None 要求 actual 亦为 None/缺失语义；
      expected=0.0 要求数值（不放过把缺失算成 0 的实现）
    失败时抛 AssertionError 并内嵌 DiffReport 形态的两侧明细。
    """
    diffs: list[str] = []
    keys = set(expected) | {k for k in actual if k in FLOAT_KEYS or k in EXACT_KEYS}
    for key in sorted(keys):
        if key not in expected:
            continue
        exp, act = expected[key], actual.get(key, "<missing>")
        if exp is None:
            if act is not None:
                diffs.append(f"{key}: expected None(缺失) got {act!r}")
            continue
        if isinstance(act, str):  # "<missing>"
            diffs.append(f"{key}: missing in actual")
            continue
        if act is None:
            diffs.append(f"{key}: expected {exp!r} got None(缺失)")
            continue
        if key in EXACT_KEYS:
            if int(act) != int(exp):
                diffs.append(f"{key}: EXACT expected {exp} got {act} (Δ={int(act) - int(exp):+d})")
            continue
        try:
            e, a = _num(exp), _num(act)
        except (TypeError, ValueError):
            if act != exp:
                diffs.append(f"{key}: expected {exp!r} got {act!r}")
            continue
        if not (math.isfinite(e) and math.isfinite(a)):
            if not (math.isnan(e) and math.isnan(a)) and e != a:
                diffs.append(f"{key}: expected {exp!r} got {act!r}")
            continue
        denom = max(abs(e), abs(a), 1e-300)
        if abs(a - e) > rtol * denom and abs(a - e) > 0.0:
            diffs.append(
                f"{key}: expected {e!r} got {a!r} "
                f"(Δabs={a - e:+.3e}, Δrel={(a - e) / denom:+.3e} > rtol={rtol:g})"
            )
    if diffs:
        raise AssertionError(str(DiffReport(scenario=scenario, diffs=diffs)))
