"""Evolution engine: main loop with early stopping and parallel evaluation."""
from __future__ import annotations

import random
from typing import Callable, Dict, List, Optional, Tuple

from core.strategy.dna import StrategyDNA
from functools import partial

from core.evolution.operators import (
    mutate_params, mutate_indicator, mutate_logic, mutate_risk, crossover,
    mutate_cross_logic,
    mutate_add_signal, mutate_remove_signal,
    mutate_add_layer, mutate_remove_layer, mutate_layer_timeframe,
    mutate_mtf_mode, mutate_confluence_threshold, mutate_proximity_mult,
    mutate_composition,
)
from core.evolution.population import init_population, create_random_dna
from core.evolution.diversity import (
    compute_diversity, inject_fresh_blood, check_and_maintain_diversity,
)


class EarlyStopChecker:
    """Check early stopping conditions after each generation.

    4 rules:
    1. Target reached: best_fitness >= target
    2. Stagnation: no improvement for patience generations
    3. Decline: best_fitness declining for decline_limit generations
    4. Max generations: reached max_generations
    """

    def __init__(
        self,
        target_score: float = 1.0,
        max_generations: int = 200,
        patience: int = 15,
        min_improvement: float = 0.5,
        decline_limit: int = 10,
        min_generations: int = 20,
    ):
        self.target_score = target_score
        self.max_generations = max_generations
        self.patience = patience
        self.min_improvement = min_improvement
        self.decline_limit = decline_limit
        self.min_generations = min_generations
        self.best_score = -float("inf")
        self.no_improve_count = 0
        self.decline_count = 0

    def check(self, current_best: float, generation: int) -> Tuple[str, str]:
        """Check if evolution should stop.

        Returns:
            (action, reason) where action is "continue" or "stop".
        """
        # Target reached: only stop after min_generations to avoid premature exit
        if current_best >= self.target_score and generation >= self.min_generations:
            return ("stop", "target_reached")

        # Check improvement
        improvement = current_best - self.best_score
        if improvement < self.min_improvement:
            self.no_improve_count += 1
        else:
            self.no_improve_count = 0

        if self.no_improve_count >= self.patience:
            return ("stop", "stagnation")

        # Check decline
        if current_best < self.best_score:
            self.decline_count += 1
        else:
            self.decline_count = 0

        if self.decline_count >= self.decline_limit:
            return ("stop", "decline")

        # Check max generations
        if generation >= self.max_generations:
            return ("stop", "max_generations")

        # Update best
        if current_best > self.best_score:
            self.best_score = current_best

        return ("continue", "")


class _AdaptiveMutationController:
    """1/5 success rule (Rechenberg) for adaptive mutation intensity."""

    def __init__(self, window_size: int = 10):
        self._window_size = window_size
        self._improvements: list[bool] = []
        self._prev_best = -float("inf")

    def record(self, current_best: float) -> None:
        """Record whether this generation improved the best score."""
        improved = current_best > self._prev_best
        self._improvements.append(improved)
        if len(self._improvements) > self._window_size:
            self._improvements.pop(0)
        self._prev_best = current_best

    @property
    def success_rate(self) -> float:
        if not self._improvements:
            return 0.0
        return sum(self._improvements) / len(self._improvements)

    @property
    def mutation_boost(self) -> float:
        """Multiplier for mutation intensity: >1 when stuck, <1 when improving."""
        rate = self.success_rate
        if rate > 0.3:
            return 0.85
        elif rate < 0.15:
            return 1.3
        return 1.0


def validate_offspring(dna: StrategyDNA) -> None:
    """变异/交叉产物结构校验（fix-executor-batch-health 校验最小集）。

    只覆盖两个实证非法形态（发现新形态时增量扩，不做全字段 schema）：
    1. 条件 threshold 显式为 None（求值层数值 vs None 比较失败）；
    2. 基因签名不可哈希（条件含 dict/list 值——去重与组装的 hash 失败）。

    Raises:
        ValueError: 产物含结构非法基因（消息带 indicator 与原因）。
    """
    from core.strategy.executor import _gene_signature

    for gene in getattr(dna, "signal_genes", []):
        cond = gene.condition or {}
        if isinstance(cond, dict) and cond.get("threshold", 0) is None \
                and "threshold" in cond:
            raise ValueError(
                f"非法变异产物：{gene.indicator} 条件 target(threshold) 为 None")
        try:
            hash(_gene_signature(gene))
        except TypeError:
            raise ValueError(
                f"非法变异产物：{gene.indicator} 基因签名不可哈希"
                f"（condition={cond!r}）") from None


def _tournament_select(
    scored: List[Tuple[StrategyDNA, float]],
    k: int,
    tournsize: int = 3,
) -> List[StrategyDNA]:
    """Select k individuals via tournament selection.

    Each tournament picks tournsize random individuals and returns the best.
    This preserves selection pressure while allowing weaker individuals
    to occasionally reproduce, maintaining genetic diversity.
    """
    selected = []
    pop_size = len(scored)
    for _ in range(k):
        aspirant_indices = random.sample(range(pop_size), min(tournsize, pop_size))
        winner_idx = max(aspirant_indices, key=lambda i: scored[i][1])
        selected.append(scored[winner_idx][0])
    return selected


class EvolutionEngine:
    """Main evolution loop.

    Coordinates population management, evaluation, selection, crossover,
    mutation, and early stopping. Designed for CLI/programmatic use.

    Selection strategy: tournament selection (tournsize=3) replaces
    truncation selection for better exploration-exploitation balance.
    """

    def __init__(
        self,
        target_score: float = 1.0,
        population_size: int = 15,
        max_generations: int = 200,
        patience: int = 15,
        decline_limit: int = 10,
        elite_ratio: float = 0.15,
        leverage: int = 1,
        direction: str = "long",
        timeframe_pool: Optional[list] = None,
    ):
        self.target_score = target_score
        self.population_size = population_size
        self.max_generations = max_generations
        self.patience = patience
        self.decline_limit = decline_limit
        self.elite_ratio = elite_ratio
        self.leverage = leverage
        self.direction = direction
        self.timeframe_pool = timeframe_pool or []

    def evolve(
        self,
        ancestor: StrategyDNA,
        evaluate_fn: Callable[[StrategyDNA], float],
        on_generation: Optional[Callable[[int, float, float], None]] = None,
        extra_ancestors: Optional[List[StrategyDNA]] = None,
        exclude_signatures: Optional[set] = None,
        stop_check: Optional[Callable[[], None]] = None,
        evaluate_population: Optional[Callable[[List[StrategyDNA]], List[float]]] = None,
    ) -> Dict:
        """Run the full evolution loop.

        Args:
            ancestor: Starting strategy.
            evaluate_fn: Function that takes a StrategyDNA and returns a score (0-100).
            on_generation: Optional callback(gen, best_score, avg_score) after each gen.
            exclude_signatures: Set of gene signatures to avoid in population init.
            stop_check: Optional callback that raises on stop request.
                Called between individual evaluations.
            evaluate_population: Optional batch version of evaluate_fn.
                Takes a list of StrategyDNA, returns a list of scores.
                When provided, used instead of per-individual evaluate_fn.

        Returns:
            Dict with champion, history, stop_reason, total_generations.
        """
        # Initialize population
        population = init_population(
            self.population_size, ancestor,
            extra_ancestors=extra_ancestors,
            leverage=self.leverage, direction=self.direction,
            timeframe_pool=self.timeframe_pool if len(self.timeframe_pool) > 1 else None,
            exclude_signatures=exclude_signatures,
        )
        self._population = population
        stop_checker = EarlyStopChecker(
            target_score=self.target_score,
            max_generations=self.max_generations,
            patience=self.patience,
            decline_limit=self.decline_limit,
        )

        history = []
        champion = None
        champion_fitness = -1.0
        stagnation_count = 0
        adaptive_mut = _AdaptiveMutationController(window_size=10)

        for gen in range(1, self.max_generations + 1):
            # Enforce task-level constraints on all individuals before evaluation
            for ind in population:
                ind.risk_genes.leverage = self.leverage
                ind.risk_genes.direction = self.direction

            # Evaluate all individuals
            evaluated = []
            if evaluate_population is not None:
                # Batch evaluation: one call for the whole population
                if stop_check is not None:
                    stop_check()
                fitnesses = evaluate_population(population)
                evaluated = list(zip(population, fitnesses))
            else:
                # Sequential per-individual evaluation (with stop checks)
                for idx, ind in enumerate(population):
                    if stop_check is not None and idx > 0 and idx % 3 == 0:
                        stop_check()
                    evaluated.append((ind, evaluate_fn(ind)))
            evaluated.sort(key=lambda x: x[1], reverse=True)

            best_fitness = evaluated[0][1]
            avg_fitness = sum(f for _, f in evaluated) / len(evaluated)

            history.append({
                "generation": gen,
                "best_score": best_fitness,
                "avg_score": avg_fitness,
                "best_fitness": best_fitness,
                "avg_fitness": avg_fitness,
            })

            # Track champion and stagnation
            if best_fitness > champion_fitness:
                champion = evaluated[0][0]
                champion_fitness = best_fitness
                stagnation_count = 0
            else:
                stagnation_count += 1

            # Record for adaptive mutation
            adaptive_mut.record(best_fitness)

            # Expose evaluated population (sorted descending) so callbacks can
            # reliably access the best individual via _population[0].
            self._population = [ind for ind, _ in evaluated]

            # Callback
            if on_generation:
                on_generation(gen, best_fitness, avg_fitness)

            # Early stop check
            action, reason = stop_checker.check(best_fitness, gen)
            if action == "stop":
                return {
                    "champion": champion,
                    "champion_score": champion_fitness,
                    "champion_fitness": champion_fitness,
                    "history": history,
                    "stop_reason": reason,
                    "total_generations": gen,
                    "target_reached": reason == "target_reached",
                }

            # --- Selection ---

            # Elite: top elite_ratio (min 2) survive unchanged
            elite_count = max(2, int(len(evaluated) * self.elite_ratio))
            elites = [ind for ind, _ in evaluated[:elite_count]]

            # Parents for crossover: tournament selection from full population
            n_children = self.population_size - elite_count - 3  # reserve 3 for fresh blood
            parents = _tournament_select(evaluated, n_children * 2, tournsize=3)

            # --- Mutation weights based on stagnation + adaptive controller ---
            if stagnation_count > 8:
                mut_weights = [15, 30, 10, 15, 20, 10]  # Heavy on indicator replacement
                n_mutations_choices = [2, 3, 4]
                n_mut_weights = [30, 45, 25]
            elif stagnation_count > 4:
                mut_weights = [25, 20, 15, 20, 10, 10]  # Balanced
                n_mutations_choices = [1, 2, 3]
                n_mut_weights = [25, 45, 30]
            else:
                mut_weights = [35, 10, 10, 25, 10, 10]  # Heavy on params + risk (fine-tuning)
                n_mutations_choices = [1, 2, 3]
                n_mut_weights = [50, 35, 15]

            # Apply adaptive mutation boost (1/5 rule)
            # When stuck (boost > 1), shift towards more mutations
            # When improving (boost < 1), shift towards fewer mutations
            boost = adaptive_mut.mutation_boost
            if boost > 1.0:
                # Shift weight towards higher mutation counts
                n_mut_weights = [
                    int(w * (0.5 ** i)) for i, w in enumerate(reversed(n_mut_weights))
                ]
                n_mut_weights.reverse()
            elif boost < 1.0:
                # Shift weight towards lower mutation counts
                n_mut_weights = [
                    int(w * (0.5 ** i)) for i, w in enumerate(n_mut_weights)
                ]

            total_w = sum(mut_weights)
            mut_weights = [w / total_w * 100 for w in mut_weights]

            mutation_pool = [
                mutate_params, mutate_indicator, mutate_logic, mutate_risk,
                mutate_add_signal, mutate_remove_signal,
            ]
            # MTF-specific mutations: add/remove/modify layers
            if len(self.timeframe_pool) > 1:
                mutation_pool.append(partial(mutate_add_layer, candidate_timeframes=list(self.timeframe_pool)))
                mut_weights.append(5)
                mutation_pool.append(mutate_remove_layer)
                mut_weights.append(3)
                mutation_pool.append(partial(mutate_layer_timeframe, candidate_timeframes=list(self.timeframe_pool)))
                mut_weights.append(3)
                mutation_pool.append(mutate_cross_logic)
                mut_weights.append(10)
                mutation_pool.append(mutate_mtf_mode)
                mut_weights.append(3)
                mutation_pool.append(mutate_confluence_threshold)
                mut_weights.append(3)
                mutation_pool.append(mutate_proximity_mult)
                mut_weights.append(3)

            # --- Crossover + Mutation ---
            children = []
            tf_pool_arg = self.timeframe_pool if len(self.timeframe_pool) > 1 else None

            for i in range(0, len(parents) - 1, 2):
                p1 = parents[i]
                p2 = parents[i + 1]
                try:
                    child = crossover(p1, p2)
                    n_mutations = random.choices(
                        n_mutations_choices, weights=n_mut_weights,
                    )[0]
                    for _ in range(n_mutations):
                        mut = random.choices(mutation_pool, weights=mut_weights)[0]
                        child = mut(child)
                    # 组合基因变异（add-ai-research-mcp 6.3）：不占既有
                    # 权重名额（否则稀释有效变异密度）；仅有组合基因的
                    # 个体追加，退化形态零改变
                    if getattr(child, "signal_sources", None) or getattr(child, "composition", None):
                        if random.random() < 0.3:
                            child = mutate_composition(child)
                    # 源头校验（fix-executor-batch-health）：变异产物结构
                    # 合法性在入种群前拦截——非法（条件 target 为 None、
                    # 条件含不可哈希值）抛 ValueError 被下方既有 except
                    # 捕获→换随机个体（与变异失败同构处置，零新控制流；
                    # 既有兜底管不住「合法返回的非法结构」，此为缺口本质）
                    validate_offspring(child)
                    children.append(child)
                except Exception:
                    children.append(create_random_dna(
                        leverage=self.leverage, direction=self.direction,
                        timeframe_pool=tf_pool_arg,
                    ))
                if len(children) >= n_children:
                    break

            # Fill shortfall with random individuals
            while len(children) < n_children:
                children.append(create_random_dna(
                    leverage=self.leverage, direction=self.direction,
                    timeframe_pool=tf_pool_arg,
                ))

            # Fresh blood: 3-5 random individuals for diversity
            population = elites + children
            target_fresh = random.randint(3, 5)
            shortfall = max(target_fresh, self.population_size - len(population))
            population = inject_fresh_blood(
                population, n=shortfall,
                leverage=self.leverage, direction=self.direction,
                timeframe_pool=tf_pool_arg,
            )

            # Diversity maintenance: replace clones with fresh individuals
            population = check_and_maintain_diversity(
                population,
                leverage=self.leverage, direction=self.direction,
                timeframe_pool=tf_pool_arg,
            )

            self._population = population

            # Trim to exact size
            population = population[:self.population_size]

        return {
            "champion": champion,
            "champion_score": champion_fitness,
            "champion_fitness": champion_fitness,
            "history": history,
            "stop_reason": "max_generations",
            "total_generations": self.max_generations,
            "target_reached": champion_fitness >= self.target_score,
        }
