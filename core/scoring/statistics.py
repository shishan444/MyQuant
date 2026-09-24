"""统计工具权威实现（scoring 域拥有）。

Wilson 置信区间对比例的小样本行为比正态近似稳健；本函数是全工程
Wilson 的单一实现，analysis/discovery/validation 各消费方委托于此，
不再各自内联公式（fix-indicator-output-mapping 阶段 3 收口）。
"""

from __future__ import annotations


def wilson_interval(
    successes: int, total: int, z: float = 1.96
) -> tuple[float, float]:
    """Wilson score 置信区间（比例的小样本稳健估计）。

    Args:
        successes: 成功次数（0 <= successes <= total）。
        total: 总次数；0 时返回 (0.0, 0.0)。
        z: 置信水平 Z 值，默认 1.96（95%）。

    Returns:
        (lower, upper)，均截断在 [0, 1]。
    """
    if total == 0:
        return (0.0, 0.0)
    import math

    p = successes / total
    denom = 1 + z * z / total
    center = (p + z * z / (2 * total)) / denom
    half = z * math.sqrt(p * (1 - p) / total + z * z / (4 * total * total)) / denom
    return (max(0.0, center - half), min(1.0, center + half))
