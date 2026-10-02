"""多样性保护：基因型/表型多样性度量 + 新鲜血液注入。

服务对象：EvolutionEngine 的换血（inject_fresh_blood）与多样性
维护（check_and_maintain_diversity）每代调用，runner 的
compute_phenotype_diversity 指标上报。职责边界：多样性测度与
种群修补；不负责评估（距离度量消费评估挂载的 _eval_diagnostics）。
"""
from __future__ import annotations

import math
import random
from typing import Dict, List, Optional, Set, Tuple

from core.strategy.dna import StrategyDNA
from core.evolution.population import create_random_dna


# ---------------------------------------------------------------------------
# 基因型签名（委托 StrategyDNA.gene_signature）
# ---------------------------------------------------------------------------

def _gene_signature(dna: StrategyDNA) -> str:
    """``StrategyDNA.gene_signature`` 的向后兼容包装。

    基因型签名逻辑已内聚到 StrategyDNA 自身（DNA 的内在属性，
    供多样性度量、策略去重与进化排除集使用）。保留本函数使既有
    调用方与测试不改；新代码直接用 ``dna.gene_signature``。
    """
    return dna.gene_signature


# ---------------------------------------------------------------------------
# 表型距离度量
# ---------------------------------------------------------------------------

def genotype_distance(dna_a: StrategyDNA, dna_b: StrategyDNA) -> float:
    """两个策略基因型的结构距离。

    相同 0.0，最大差异 1.0。
    """
    components = 0.0
    max_components = 0.0

    # 信号基因按 role 桶配对比较
    def _by_role(genes):
        groups = {}
        for g in genes:
            groups.setdefault(g.role.value, []).append(g)
        return groups

    groups_a = _by_role(dna_a.signal_genes)
    groups_b = _by_role(dna_b.signal_genes)
    all_roles = set(groups_a) | set(groups_b)

    for role in all_roles:
        ga = groups_a.get(role, [])
        gb = groups_b.get(role, [])
        pairs = min(len(ga), len(gb))
        extra = abs(len(ga) - len(gb))

        for i in range(pairs):
            max_components += 3
            if ga[i].indicator != gb[i].indicator:
                components += 1.0
            # 参数相似度
            shared_keys = set(ga[i].params) & set(gb[i].params)
            if shared_keys:
                param_diff = 0.0
                for k in shared_keys:
                    try:
                        va = float(ga[i].params.get(k, 0))
                        vb = float(gb[i].params.get(k, 0))
                        param_diff += abs(va - vb)
                    except (ValueError, TypeError):
                        param_diff += 1.0
                components += min(1.0, param_diff / 50.0)
            else:
                components += 0.5
            # 条件相似度
            ca = ga[i].condition or {}
            cb = gb[i].condition or {}
            if ca.get("type") != cb.get("type"):
                components += 0.5

        max_components += extra * 1.5
        components += extra * 1.0

    # 风险基因比较
    max_components += 3
    components += abs(dna_a.risk_genes.stop_loss - dna_b.risk_genes.stop_loss) / 0.2
    components += 0 if dna_a.risk_genes.direction == dna_b.risk_genes.direction else 1.0
    components += abs(dna_a.risk_genes.leverage - dna_b.risk_genes.leverage) / 5.0

    return min(1.0, components / max(max_components, 1.0))


def _get_diagnostics(dna: StrategyDNA) -> Optional[dict]:
    """安全取个体上的评估诊断（未评估过返回 None）。"""
    return getattr(dna, '_eval_diagnostics', None)


def signal_distance(dna_a: StrategyDNA, dna_b: StrategyDNA) -> float:
    """基于交易信号相似度的表型距离。

    用回测产出的实际净值曲线/交易回报度量行为差异。

    行为相同 0.0，完全不同 1.0；诊断缺失时回退基因型距离。
    """
    diag_a = _get_diagnostics(dna_a)
    diag_b = _get_diagnostics(dna_b)

    # 任一方缺诊断（未评估）回退基因型距离
    if not diag_a or not diag_b:
        return genotype_distance(dna_a, dna_b)

    # 关键行为指标逐项比较
    raw_a = diag_a.get("raw_metrics", {})
    raw_b = diag_b.get("raw_metrics", {})

    if not raw_a or not raw_b:
        return genotype_distance(dna_a, dna_b)

    distance = 0.0
    comparisons = 0

    # 交易笔数相似度
    trades_a = raw_a.get("total_trades", 0)
    trades_b = raw_b.get("total_trades", 0)
    if trades_a + trades_b > 0:
        distance += abs(trades_a - trades_b) / max(trades_a, trades_b, 1)
        comparisons += 1

    # 胜率相似度
    # win_rate 缺失语义（零交易 None）按 0 参与距离比较
    wr_a = raw_a.get("win_rate") or 0.0
    wr_b = raw_b.get("win_rate") or 0.0
    distance += abs(wr_a - wr_b)
    comparisons += 1

    # 年化收益相似度
    ar_a = raw_a.get("annual_return", 0.0)
    ar_b = raw_b.get("annual_return", 0.0)
    distance += min(1.0, abs(ar_a - ar_b) / max(abs(ar_a), abs(ar_b), 0.01))
    comparisons += 1

    # 最大回撤相似度
    dd_a = abs(raw_a.get("max_drawdown", 0.0))
    dd_b = abs(raw_b.get("max_drawdown", 0.0))
    distance += min(1.0, abs(dd_a - dd_b) / max(dd_a, dd_b, 0.01))
    comparisons += 1

    return distance / max(comparisons, 1)


def equity_distance(dna_a: StrategyDNA, dna_b: StrategyDNA) -> float:
    """基于 score 与 dimension_scores 的距离。

    用评分维度度量行为相似度。行为相同 0.0，完全不同 1.0；
    诊断或维度缺失时回退 signal_distance。
    """
    diag_a = _get_diagnostics(dna_a)
    diag_b = _get_diagnostics(dna_b)

    if not diag_a or not diag_b:
        return signal_distance(dna_a, dna_b)

    ds_a = diag_a.get("dimension_scores", {})
    ds_b = diag_b.get("dimension_scores", {})

    if not ds_a or not ds_b:
        return signal_distance(dna_a, dna_b)

    all_dims = set(ds_a) | set(ds_b)
    if not all_dims:
        return 1.0

    total_diff = 0.0
    for dim in all_dims:
        va = ds_a.get(dim, 0.0)
        vb = ds_b.get(dim, 0.0)
        total_diff += abs(va - vb) / 100.0  # 归一到 0-1

    return min(1.0, total_diff / len(all_dims))


# ---------------------------------------------------------------------------
# 种群多样性指标
# ---------------------------------------------------------------------------

def compute_diversity(population: List[StrategyDNA]) -> float:
    """种群多样性 = 唯一基因签名占比。

    Returns:
        0-1 浮点：1 = 全体唯一，0 = 全体相同。
    """
    if len(population) <= 1:
        return 0.0

    signatures = [ind.gene_signature for ind in population]
    unique = len(set(signatures))
    return unique / len(population)


def compute_phenotype_diversity(population: List[StrategyDNA]) -> Dict[str, float]:
    """种群多层多样性指标。

    Returns:
        dict：
        - genotype：唯一基因签名占比（快指标）
        - signal：成对 signal_distance 均值
        - score：成对 equity_distance 均值
    """
    n = len(population)
    if n <= 1:
        return {"genotype": 0.0, "signal": 0.0, "score": 0.0}

    # 基因型多样性（快指标）
    genotype_div = compute_diversity(population)

    # 表型多样性成对采样（大种群限至多 45 对控计算量）
    max_pairs = min(45, n * (n - 1) // 2)
    pairs: List[Tuple[int, int]] = []
    if n * (n - 1) // 2 <= max_pairs:
        for i in range(n):
            for j in range(i + 1, n):
                pairs.append((i, j))
    else:
        seen: Set[Tuple[int, int]] = set()
        while len(seen) < max_pairs:
            i, j = random.sample(range(n), 2)
            pair = (min(i, j), max(i, j))
            if pair not in seen:
                seen.add(pair)
                pairs.append(pair)

    signal_dists = []
    score_dists = []
    for i, j in pairs:
        signal_dists.append(signal_distance(population[i], population[j]))
        score_dists.append(equity_distance(population[i], population[j]))

    return {
        "genotype": round(genotype_div, 3),
        "signal": round(sum(signal_dists) / len(signal_dists), 3) if signal_dists else 0.0,
        "score": round(sum(score_dists) / len(score_dists), 3) if score_dists else 0.0,
    }


# ---------------------------------------------------------------------------
# 新鲜血液注入
# ---------------------------------------------------------------------------

def inject_fresh_blood(
    population: List[StrategyDNA],
    n: int = 2,
    leverage: int = 1,
    direction: str = "long",
    timeframe_pool: Optional[List[str]] = None,
) -> List[StrategyDNA]:
    """向种群追加 n 个随机个体以保多样性。

    Args:
        population: 当前种群。
        n: 追加个体数。
        leverage: 任务级杠杆约束。
        direction: 任务级方向约束。
        timeframe_pool: MTF 层生成的可用周期集。

    Returns:
        追加新鲜个体后的种群（原地追加）。
    """
    for _ in range(n):
        fresh = create_random_dna(
            timeframe=population[0].execution_genes.timeframe if population else "4h",
            symbol=population[0].execution_genes.symbol if population else "BTCUSDT",
            leverage=leverage,
            direction=direction,
            timeframe_pool=timeframe_pool,
        )
        population.append(fresh)
    return population


def check_and_maintain_diversity(
    population: List[StrategyDNA],
    threshold: float = 0.30,
    leverage: int = 1,
    direction: str = "long",
    timeframe_pool: Optional[List[str]] = None,
) -> List[StrategyDNA]:
    """多样性低于阈值时以新鲜个体替换重复个体。

    Args:
        population: 当前种群。
        threshold: 可接受的最小多样性占比。
        leverage: 任务级杠杆约束。
        direction: 任务级方向约束。
        timeframe_pool: MTF 层生成的可用周期集。

    Returns:
        多样性维护后的种群——替换仅在双重条件成立时发生：
        多样性 < threshold 且最高频签名占比 > 30%（此时其后
        重复个体替换为随机新鲜个体，首个保留）；否则原样返回。
    """
    diversity = compute_diversity(population)
    if diversity < threshold:
        # 定位最高频签名
        signatures = [ind.gene_signature for ind in population]
        from collections import Counter
        counts = Counter(signatures)
        most_common_sig, most_common_count = counts.most_common(1)[0]

        if most_common_count > len(population) * 0.3:
            # 重复签名个体替换为随机新鲜个体（首个保留）
            seen = set()
            new_pop = []
            for ind in population:
                sig = ind.gene_signature
                if sig in seen:
                    fresh = create_random_dna(
                        timeframe=ind.execution_genes.timeframe,
                        symbol=ind.execution_genes.symbol,
                        leverage=leverage,
                        direction=direction,
                        timeframe_pool=timeframe_pool,
                    )
                    new_pop.append(fresh)
                else:
                    seen.add(sig)
                    new_pop.append(ind)
            return new_pop

    return population
