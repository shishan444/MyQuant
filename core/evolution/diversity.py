"""Diversity protection: genotype and phenotype diversity measurement + fitness sharing."""
from __future__ import annotations

import math
import random
from typing import Dict, List, Optional, Set, Tuple

from core.strategy.dna import StrategyDNA
from core.evolution.population import create_random_dna


# ---------------------------------------------------------------------------
# Genotype signature (delegates to StrategyDNA.gene_signature)
# ---------------------------------------------------------------------------

def _gene_signature(dna: StrategyDNA) -> str:
    """Backward-compatible wrapper around ``StrategyDNA.gene_signature``.

    The genotype-signature logic now lives on StrategyDNA itself (it is an
    intrinsic property of the DNA, used for diversity measurement, strategy
    dedup, and the evolution exclude-set). This function is kept so existing
    callers and tests importing it keep working; new code should use
    ``dna.gene_signature`` directly.
    """
    return dna.gene_signature


# ---------------------------------------------------------------------------
# Phenotype distance metrics
# ---------------------------------------------------------------------------

def genotype_distance(dna_a: StrategyDNA, dna_b: StrategyDNA) -> float:
    """Compute structural distance between two strategy genotypes.

    Returns 0.0 if identical, up to 1.0 for maximally different.
    """
    components = 0.0
    max_components = 0.0

    # Compare signal genes by role buckets
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
            # Parameter similarity
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
            # Condition similarity
            ca = ga[i].condition or {}
            cb = gb[i].condition or {}
            if ca.get("type") != cb.get("type"):
                components += 0.5

        max_components += extra * 1.5
        components += extra * 1.0

    # Risk gene comparison
    max_components += 3
    components += abs(dna_a.risk_genes.stop_loss - dna_b.risk_genes.stop_loss) / 0.2
    components += 0 if dna_a.risk_genes.direction == dna_b.risk_genes.direction else 1.0
    components += abs(dna_a.risk_genes.leverage - dna_b.risk_genes.leverage) / 5.0

    return min(1.0, components / max(max_components, 1.0))


def _get_diagnostics(dna: StrategyDNA) -> Optional[dict]:
    """Safely get evaluation diagnostics from a DNA individual."""
    return getattr(dna, '_eval_diagnostics', None)


def signal_distance(dna_a: StrategyDNA, dna_b: StrategyDNA) -> float:
    """Compute phenotype distance based on trade signal similarity.

    Uses the actual equity curves or trade returns from backtesting
    to measure behavioral difference.

    Returns 0.0 if identical behavior, 1.0 if completely different.
    """
    diag_a = _get_diagnostics(dna_a)
    diag_b = _get_diagnostics(dna_b)

    # If either lacks diagnostics, fall back to genotype distance
    if not diag_a or not diag_b:
        return genotype_distance(dna_a, dna_b)

    # Compare key behavioral metrics
    metrics_to_compare = ["total_trades", "win_rate"]
    raw_a = diag_a.get("raw_metrics", {})
    raw_b = diag_b.get("raw_metrics", {})

    if not raw_a or not raw_b:
        return genotype_distance(dna_a, dna_b)

    distance = 0.0
    comparisons = 0

    # Trade count similarity
    trades_a = raw_a.get("total_trades", 0)
    trades_b = raw_b.get("total_trades", 0)
    if trades_a + trades_b > 0:
        distance += abs(trades_a - trades_b) / max(trades_a, trades_b, 1)
        comparisons += 1

    # Win rate similarity
    # win_rate 缺失语义（零交易 None）按 0 参与距离比较
    wr_a = raw_a.get("win_rate") or 0.0
    wr_b = raw_b.get("win_rate") or 0.0
    distance += abs(wr_a - wr_b)
    comparisons += 1

    # Annual return similarity
    ar_a = raw_a.get("annual_return", 0.0)
    ar_b = raw_b.get("annual_return", 0.0)
    distance += min(1.0, abs(ar_a - ar_b) / max(abs(ar_a), abs(ar_b), 0.01))
    comparisons += 1

    # Max drawdown similarity
    dd_a = abs(raw_a.get("max_drawdown", 0.0))
    dd_b = abs(raw_b.get("max_drawdown", 0.0))
    distance += min(1.0, abs(dd_a - dd_b) / max(dd_a, dd_b, 0.01))
    comparisons += 1

    return distance / max(comparisons, 1)


def equity_distance(dna_a: StrategyDNA, dna_b: StrategyDNA) -> float:
    """Compute distance based on score and dimension_scores.

    Uses the scoring dimensions to measure behavioral similarity.

    Returns 0.0 if same behavior, 1.0 if completely different.
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
        total_diff += abs(va - vb) / 100.0  # normalize to 0-1

    return min(1.0, total_diff / len(all_dims))


# ---------------------------------------------------------------------------
# Population diversity metrics
# ---------------------------------------------------------------------------

def compute_diversity(population: List[StrategyDNA]) -> float:
    """Compute population diversity as ratio of unique gene signatures.

    Returns:
        Float 0-1 where 1 = all individuals unique, 0 = all identical.
    """
    if len(population) <= 1:
        return 0.0

    signatures = [ind.gene_signature for ind in population]
    unique = len(set(signatures))
    return unique / len(population)


def compute_phenotype_diversity(population: List[StrategyDNA]) -> Dict[str, float]:
    """Compute multi-level diversity metrics for a population.

    Returns dict with:
        genotype: unique gene signature ratio (existing metric)
        signal: average pairwise signal distance
        score: average pairwise equity/score distance
    """
    n = len(population)
    if n <= 1:
        return {"genotype": 0.0, "signal": 0.0, "score": 0.0}

    # Genotype diversity (fast, existing metric)
    genotype_div = compute_diversity(population)

    # Phenotype diversity (sample pairwise for efficiency)
    # Use at most 30 pairs for large populations
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
# Fresh blood injection (existing, unchanged)
# ---------------------------------------------------------------------------

def inject_fresh_blood(
    population: List[StrategyDNA],
    n: int = 2,
    leverage: int = 1,
    direction: str = "long",
    timeframe_pool: Optional[List[str]] = None,
) -> List[StrategyDNA]:
    """Add n random individuals to the population for diversity.

    Args:
        population: Current population.
        n: Number of fresh individuals to add.
        leverage: Task-level leverage constraint.
        direction: Task-level direction constraint.
        timeframe_pool: Available timeframes for MTF layer generation.

    Returns:
        Population with fresh individuals appended.
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
    """If diversity is below threshold, replace similar individuals.

    Args:
        population: Current population.
        threshold: Minimum acceptable diversity ratio.
        leverage: Task-level leverage constraint.
        direction: Task-level direction constraint.
        timeframe_pool: Available timeframes for MTF layer generation.

    Returns:
        Population with diversity maintained.
    """
    diversity = compute_diversity(population)
    if diversity < threshold:
        # Replace the most common individuals
        signatures = [ind.gene_signature for ind in population]
        from collections import Counter
        counts = Counter(signatures)
        most_common_sig, most_common_count = counts.most_common(1)[0]

        if most_common_count > len(population) * 0.3:
            # Replace duplicates with fresh individuals
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
