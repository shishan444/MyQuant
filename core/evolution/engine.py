"""进化引擎：代际主循环（早停 + 批量/逐个评估）。

服务对象：api/runner.py 进化任务线程（runner → EvolutionEngine.evolve
→ evaluate_population → BacktestEngine + compute_fitness）与
tests/evolution 契约测试。职责边界：种群初始化后的代际编排——评估、
锦标赛选择、精英保留、交叉变异、新鲜血液注入与多样性维护、早停
判定；数据装载与 fitness 公式由调用方以 evaluate_fn/
evaluate_population 回调注入，引擎本身不持有。
"""
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
    """代际早停判定（每代 check 一次，跨代持有计数状态）。

    四规则：①达标——best_fitness >= target（须满 min_generations）；
    ②停滞——连续 patience 代改善不足；③衰退——连续 decline_limit 代
    最优回退；④代数上限 max_generations。
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
        """判定进化是否应停止。

        Returns:
            (action, reason)：action 为 "continue"/"stop"，reason 为
            停因标识（target_reached/stagnation/decline/max_generations）。
        """
        # 达标停：须满 min_generations，防早期偶然高分致过早退出
        if current_best >= self.target_score and generation >= self.min_generations:
            return ("stop", "target_reached")

        # 停滞计数：改善不足 min_improvement 则累加
        improvement = current_best - self.best_score
        if improvement < self.min_improvement:
            self.no_improve_count += 1
        else:
            self.no_improve_count = 0

        if self.no_improve_count >= self.patience:
            return ("stop", "stagnation")

        # 衰退计数：最优回退则累加
        if current_best < self.best_score:
            self.decline_count += 1
        else:
            self.decline_count = 0

        if self.decline_count >= self.decline_limit:
            return ("stop", "decline")

        # 代数上限
        if generation >= self.max_generations:
            return ("stop", "max_generations")

        # 未触发停判才更新历史最优（本代分数先参与全部判定）
        if current_best > self.best_score:
            self.best_score = current_best

        return ("continue", "")


class _AdaptiveMutationController:
    """1/5 成功规则（Rechenberg）自适应变异强度控制器。"""

    def __init__(self, window_size: int = 10):
        self._window_size = window_size
        self._improvements: list[bool] = []
        self._prev_best = -float("inf")

    def record(self, current_best: float) -> None:
        """记录本代最优是否改善（滑动窗口供 success_rate 统计）。"""
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
        """变异强度乘数：停滞时 >1（增强探索），改善中 <1（加速收敛）。"""
        rate = self.success_rate
        if rate > 0.3:
            return 0.85
        elif rate < 0.15:
            return 1.3
        return 1.0


def validate_offspring(dna: StrategyDNA, enhanced_df=None) -> StrategyDNA:
    """变异/交叉产物入库前校验+清洗（unify-tool-return-contract 双检）。

    两层防线：
    - 结构校验（fix-executor-batch-health 既有最小集，raise 形）：
      1. 条件 threshold 显式为 None（求值层数值 vs None 比较失败）；
      2. 基因签名不可哈希（条件含 dict/list 值——去重与组装的 hash 失败）。
    - 基因级双检（剔除形——坏基因剔除、个体保留）：
      3. 条件 type ∉ EVALUABLE_CONDITION_TYPES（执行器合法集单源）；
      4. 指标列可达性试解析（enhanced_df 在场时 _get_indicator_column，
         ValueError → 剔除；无 df 跳过该维——繁殖侧无数据帧）。

    被剔除个体 mutation_ops 注记 "sanitized:{reason}"（可查性）；
    适应度降权由评估层按注记施加（evaluation.SANITIZED_FITNESS_PENALTY）。
    保底不变量：清洗后零 ENTRY_TRIGGER 基因 → 整个体淘汰（raise，
     走繁殖循环既有「换随机个体」路径）。

    MTF 边界（fix-review CARD-06 披露）：本清洗只遍历顶层
    signal_genes，MTF 个体 layers 内基因不在本清洗面——防线为
    进化新生基因恒合法（supported_conditions 与执行器合法集双源
    互锁）+ 评估层对求值失败个体兜底 0 分 + batch 路径 D10 防御
    拒绝透传；layers 内基因的同等清洗完整化归包二 MTF 执行链
    改造。

    Returns:
        清洗后的同一 dna 实例（原地修改 signal_genes 与 mutation_ops）。

    Raises:
        ValueError: 结构非法（threshold None/不可哈希）或清洗后零
            ENTRY_TRIGGER（消息带 indicator 与原因）。
    """
    from core.strategy.executor import _gene_signature, EVALUABLE_CONDITION_TYPES

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

    # 基因级双检剔除（入库前）
    kept_genes = []
    removed_any = False
    for gene in dna.signal_genes:
        cond = gene.condition or {}
        cond_type = cond.get("type") if isinstance(cond, dict) else None
        if cond_type not in EVALUABLE_CONDITION_TYPES:
            dna.mutation_ops.append(
                f"sanitized:unknown_condition_type:{gene.indicator}")
            removed_any = True
            continue
        if enhanced_df is not None:
            try:
                from core.strategy.executor import _get_indicator_column
                _get_indicator_column(enhanced_df, gene)
            except ValueError:
                dna.mutation_ops.append(
                    f"sanitized:column_unreachable:{gene.indicator}")
                removed_any = True
                continue
        kept_genes.append(gene)

    if removed_any:
        from core.strategy.dna import SignalRole
        has_entry_trigger = any(
            g.role == SignalRole.ENTRY_TRIGGER for g in kept_genes)
        if not has_entry_trigger:
            raise ValueError(
                "非法变异产物：清洗后零 ENTRY_TRIGGER 基因（坏基因占比过高）")
        dna.signal_genes = kept_genes

    return dna


def _tournament_select(
    scored: List[Tuple[StrategyDNA, float]],
    k: int,
    tournsize: int = 3,
) -> List[StrategyDNA]:
    """锦标赛选择 k 个个体。

    每场锦标赛随机取 tournsize 个参赛者、最优者胜出——保留选择
    压力的同时让弱个体偶有繁殖机会，维持基因多样性（替代截断
    选择的探索-利用平衡考量）。
    """
    selected = []
    pop_size = len(scored)
    for _ in range(k):
        aspirant_indices = random.sample(range(pop_size), min(tournsize, pop_size))
        winner_idx = max(aspirant_indices, key=lambda i: scored[i][1])
        selected.append(scored[winner_idx][0])
    return selected


class EvolutionEngine:
    """进化主循环编排。

    种群管理、评估、选择、交叉、变异与早停的代际协调。面向编程
    调用（runner 以回调注入评估函数与停止检查）。

    选择策略：锦标赛选择（tournsize=3）替代截断选择，换取更好的
    探索-利用平衡。
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
        """跑完整进化循环（代际：评估 → 早停判 → 选择 → 交叉变异 → 换血）。

        Args:
            ancestor: 起始祖先策略。
            evaluate_fn: 单体评估函数（StrategyDNA → 0-100 分）。
            on_generation: 每代回调 (gen, best_score, avg_score)。
            exclude_signatures: 种群初始化时排除的基因签名集。
            stop_check: 停止请求回调（抛异常打断；逐个评估路径每
                3 个体间调用，批量路径每代一次）。
            evaluate_population: evaluate_fn 的批量版（种群 → 分数
                列表）。提供时优先使用（如 BacktestEngine.batch_run）。

        Returns:
            Dict：champion/champion_score/history/stop_reason/
            total_generations/target_reached。
        """
        # 种群初始化（祖先 + 多起点 extra_ancestors）
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
            # 评估前强制任务级约束（leverage/direction 以任务配置为准）
            for ind in population:
                ind.risk_genes.leverage = self.leverage
                ind.risk_genes.direction = self.direction

            # 评估全种群（批量路径优先）
            evaluated = []
            if evaluate_population is not None:
                # 批量评估：整种群一次调用
                if stop_check is not None:
                    stop_check()
                fitnesses = evaluate_population(population)
                evaluated = list(zip(population, fitnesses))
            else:
                # 逐个评估（每 3 个体穿插停止检查）
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

            # 冠军追踪与停滞计数
            if best_fitness > champion_fitness:
                champion = evaluated[0][0]
                champion_fitness = best_fitness
                stagnation_count = 0
            else:
                stagnation_count += 1

            # 供自适应变异 1/5 规则记录
            adaptive_mut.record(best_fitness)

            # 暴露按分降序的评估后种群——回调方可稳定经 _population[0]
            # 取到当代最优个体
            self._population = [ind for ind, _ in evaluated]

            # 代际回调
            if on_generation:
                on_generation(gen, best_fitness, avg_fitness)

            # 早停判定
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

            # --- 选择 ---

            # 精英：top elite_ratio（最少 2 个）原样存活
            elite_count = max(2, int(len(evaluated) * self.elite_ratio))
            elites = [ind for ind, _ in evaluated[:elite_count]]

            # 交叉亲代：全种群锦标赛选择
            n_children = self.population_size - elite_count - 3  # 预留 3 个新鲜血液名额
            parents = _tournament_select(evaluated, n_children * 2, tournsize=3)

            # --- 变异权重：停滞档位 + 自适应控制器 ---
            if stagnation_count > 8:
                mut_weights = [15, 30, 10, 15, 20, 10]  # 重指标替换（深停滞强探索）
                n_mutations_choices = [2, 3, 4]
                n_mut_weights = [30, 45, 25]
            elif stagnation_count > 4:
                mut_weights = [25, 20, 15, 20, 10, 10]  # 均衡
                n_mutations_choices = [1, 2, 3]
                n_mut_weights = [25, 45, 30]
            else:
                mut_weights = [35, 10, 10, 25, 10, 10]  # 重参数+risk 微调（正常收敛态）
                n_mutations_choices = [1, 2, 3]
                n_mut_weights = [50, 35, 15]

            boost = adaptive_mut.mutation_boost
            if boost > 1.0:
                # 权重移向更多次变异（几何衰减从高位起）
                n_mut_weights = [
                    int(w * (0.5 ** i)) for i, w in enumerate(reversed(n_mut_weights))
                ]
                n_mut_weights.reverse()
            elif boost < 1.0:
                # 权重移向更少次变异
                n_mut_weights = [
                    int(w * (0.5 ** i)) for i, w in enumerate(n_mut_weights)
                ]

            total_w = sum(mut_weights)
            mut_weights = [w / total_w * 100 for w in mut_weights]

            mutation_pool = [
                mutate_params, mutate_indicator, mutate_logic, mutate_risk,
                mutate_add_signal, mutate_remove_signal,
            ]
            # MTF 专属变异：层的增删改
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

            # --- 交叉 + 变异 ---
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
                    # 源头校验+清洗（fix-executor-batch-health /
                    # unify-tool-return-contract 双检）：变异产物在入种
                    # 群前拦截结构非法（threshold None/不可哈希）并剔除
                    # 非法条件类型基因（繁殖侧无数据帧，可达性维由评估
                    # 层补充）；抛 ValueError 被下方既有 except 捕获→换
                    # 随机个体（与变异失败同构处置，零新控制流）
                    child = validate_offspring(child)
                    children.append(child)
                except Exception:
                    children.append(create_random_dna(
                        leverage=self.leverage, direction=self.direction,
                        timeframe_pool=tf_pool_arg,
                    ))
                if len(children) >= n_children:
                    break

            # 交叉产物不足额以随机个体补齐
            while len(children) < n_children:
                children.append(create_random_dna(
                    leverage=self.leverage, direction=self.direction,
                    timeframe_pool=tf_pool_arg,
                ))

            # 新鲜血液：每代注入 3-5 个随机个体保多样性
            population = elites + children
            target_fresh = random.randint(3, 5)
            shortfall = max(target_fresh, self.population_size - len(population))
            population = inject_fresh_blood(
                population, n=shortfall,
                leverage=self.leverage, direction=self.direction,
                timeframe_pool=tf_pool_arg,
            )

            # 多样性维护：克隆个体替换为新鲜个体
            population = check_and_maintain_diversity(
                population,
                leverage=self.leverage, direction=self.direction,
                timeframe_pool=tf_pool_arg,
            )

            self._population = population

            # 截齐规模
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
