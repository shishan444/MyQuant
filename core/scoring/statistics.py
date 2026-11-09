"""统计工具权威实现（scoring 域拥有）。

Wilson 置信区间对比例的小样本行为比正态近似稳健；本函数是全工程
Wilson 的单一实现，analysis/discovery/validation 各消费方委托于此，
不再各自内联公式（fix-indicator-output-mapping 阶段 3 收口）。
Benjamini-Hochberg FDR 校正随 add-screen-patterns 收口于此（多重
检验族语义的单一权威，扫描/未来网格类消费者共用）。
bootstrap 置信区间随 add-evaluation-ruler 收口于此（评测尺子统计件
——单样本均值 CI 与双样本均值差 CI；analysis 域模板附着层委托于此，
依赖方向保持 analysis→scoring 单向）。
"""

from __future__ import annotations

from typing import Sequence

import numpy as np


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


def boot_ci(
    sample: Sequence[float], *, B: int = 3000, ci: float = 0.95, seed: int = 42
) -> tuple[float, float]:
    """bootstrap 单样本均值 percentile 置信区间。

    每次调用以 np.random.default_rng(seed) 新建生成器、逐次
    rng.choice(x, len(x), replace=True) 等长重抽样——同输入同种子
    两次调用端点逐位一致（确定性复现，评测尺子幂等复算锚）。
    常数样本不特判：全同值重抽样均值恒为该常数，区间自然收敛。

    Args:
        sample: 样本观测值（至少 3 个）。
        B: 重抽样次数，默认 3000。
        ci: 置信水平，默认 0.95（percentile 2.5/97.5）。
        seed: 随机种子，默认 42。

    Returns:
        (lower, upper) percentile 区间端点。

    Raises:
        ValueError: 样本数 < 3（fail-loud——附着层捕获后以 CI=null＋
            样本数披露呈现，此处显式抛错优于静默产无意义区间）；
            B 非正整数或 ci 不在 (0,1)（无区间意义）。
    """
    x = np.asarray(sample, dtype=float)
    if len(x) < 3:
        raise ValueError(
            f"样本数 {len(x)} < 3——bootstrap 均值 CI 需至少 3 个观测")
    if not (isinstance(B, int) and B >= 1):
        raise ValueError(f"B={B!r} 须为正整数（重抽样次数）")
    if not (0.0 < ci < 1.0):
        raise ValueError(f"ci={ci!r} 须在 (0,1)——置信水平无区间意义")

    rng = np.random.default_rng(seed)
    means = np.array(
        [float(np.mean(rng.choice(x, len(x), replace=True)))
         for _ in range(B)])
    # 分位构造取 (50∓50ci)：ci=0.95 时逐位等于字面 2.5/97.5——
    # 100*(1-ci)/2 形式会得 2.500000000000002，percentile 虚拟索引
    # 随之偏移一个 ULP（demo 逐位对账锚 r9_d4_final.py 用字面 2.5）
    q_lo = 50.0 - 50.0 * ci
    q_hi = 50.0 + 50.0 * ci
    lower, upper = np.percentile(means, [q_lo, q_hi])
    return (float(lower), float(upper))


def boot_diff(
    x: Sequence[float], y: Sequence[float], *,
    B: int = 3000, ci: float = 0.95, seed: int = 42
) -> tuple[float, float]:
    """bootstrap 双样本均值差 percentile 置信区间（方向 x_minus_y）。

    每次迭代先抽 x 后抽 y（消耗序固定），返回区间为 mean(x*)−mean(y*)
    的分布分位——方向语义显式：正区间支持 x 均值大于 y。交换两参
    重算得镜像区间（符号翻转，蒙特卡洛噪声内）。同输入同种子逐位
    一致（与 boot_ci 同款确定性契约）。

    Args:
        x: 第一组样本（至少 3 个）。
        y: 第二组样本（至少 3 个）。
        B: 重抽样次数，默认 3000。
        ci: 置信水平，默认 0.95。
        seed: 随机种子，默认 42。

    Returns:
        (lower, upper)——mean(x)−mean(y) 的 percentile 区间端点。

    Raises:
        ValueError: 任一侧样本数 < 3，或 B/ci 非法（同 boot_ci）。
    """
    xs = np.asarray(x, dtype=float)
    ys = np.asarray(y, dtype=float)
    if len(xs) < 3 or len(ys) < 3:
        raise ValueError(
            f"样本数不足——bootstrap 均值差 CI 需两侧各至少 3 个观测"
            f"（n_x={len(xs)}, n_y={len(ys)}）")
    if not (isinstance(B, int) and B >= 1):
        raise ValueError(f"B={B!r} 须为正整数（重抽样次数）")
    if not (0.0 < ci < 1.0):
        raise ValueError(f"ci={ci!r} 须在 (0,1)——置信水平无区间意义")

    rng = np.random.default_rng(seed)
    diffs = np.array(
        [float(np.mean(rng.choice(xs, len(xs), replace=True))
               - np.mean(rng.choice(ys, len(ys), replace=True)))
         for _ in range(B)])
    # 分位构造同 boot_ci（字面 2.5/97.5 逐位锚）
    q_lo = 50.0 - 50.0 * ci
    q_hi = 50.0 + 50.0 * ci
    lower, upper = np.percentile(diffs, [q_lo, q_hi])
    return (float(lower), float(upper))
