"""统计工具权威实现（scoring 域拥有）。

Wilson 置信区间对比例的小样本行为比正态近似稳健；本函数是全工程
Wilson 的单一实现，analysis/discovery/validation 各消费方委托于此，
不再各自内联公式（fix-indicator-output-mapping 阶段 3 收口）。
Benjamini-Hochberg FDR 校正随 add-screen-patterns 收口于此（多重
检验族语义的单一权威，扫描/未来网格类消费者共用）。
"""

from __future__ import annotations

from typing import Sequence


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

    Raises:
        ValueError: successes 不在 [0, total]，或 z 非正/非有限——
            计数矛盾与非法置信水平须显式拒绝，优于裸 math domain
            error 或静默产倒置/NaN 区间（生产调用方计数天然合法，
            此处为防御一致性，风格对齐 benjamini_hochberg）。
    """
    if not (0 <= successes <= total):
        raise ValueError(
            f"successes={successes!r} 不在 [0, total={total!r}]——计数矛盾，须在入参前保证"
        )
    import math

    if not (0 < z < math.inf):
        raise ValueError(
            f"z={z!r} 须为正的有限置信水平 Z 值（如 1.96）——非正/非有限值无区间意义"
        )
    if total == 0:
        return (0.0, 0.0)

    p = successes / total
    denom = 1 + z * z / total
    center = (p + z * z / (2 * total)) / denom
    half = z * math.sqrt(p * (1 - p) / total + z * z / (4 * total * total)) / denom
    return (max(0.0, center - half), min(1.0, center + half))


def benjamini_hochberg(
    p_values: Sequence[float], q_threshold: float = 0.05
) -> tuple[list[float], list[bool]]:
    """Benjamini-Hochberg FDR 多重检验校正。

    族大小 m = 传入序列长度（族口径由调用方定义——全谱扫描为全部
    （条件 × 持有期）检验对；无 p 值的检验不得混入，调用方先行过滤）。
    q 值经单调性回填（step-up：从最大秩向小取 min），保证秩次单调不减。

    Args:
        p_values: 全族 p 值（每个均在 [0, 1]）。
        q_threshold: FDR 显著性水平，默认 0.05。

    Returns:
        (q_values, significant)：均按传入原序；q_values 为校正后
        q 值（截断在 [0, 1]），significant 为 q <= q_threshold 标记。

    Raises:
        ValueError: 序列为空，或任一 p 值不在 [0, 1]——无效检验须在
            入族前过滤，显式拒绝优于静默钳制扭曲校正。
    """
    m = len(p_values)
    if m == 0:
        raise ValueError("p_values 不能为空——空族无校正意义")
    for i, p in enumerate(p_values):
        if not (0.0 <= p <= 1.0):
            raise ValueError(
                f"p_values[{i}]={p!r} 不在 [0,1]——无效检验须在入族前过滤"
            )

    # 升序秩次：rank i（1-based）的原始校正值 p_(i) * m / i
    order = sorted(range(m), key=lambda i: p_values[i])
    q_by_rank: list[float] = [0.0] * m
    running_min = float("inf")
    # 从最大秩向小回填单调性（q_(i) = min(q_(i+1), p_(i)*m/i)）
    for rank in range(m, 0, -1):
        idx = order[rank - 1]
        raw = p_values[idx] * m / rank
        running_min = min(running_min, raw)
        q_by_rank[idx] = min(1.0, running_min)

    significant = [q <= q_threshold for q in q_by_rank]
    return q_by_rank, significant
