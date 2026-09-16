"""L0-A 进化有效性契约：守护'进化真的产出更好策略'这一系统根本价值。

审计发现 test_evolution_effectiveness.py 名为有效性实为 schema 校验(0 数值断言)，
进化算法本身的有效性从未被验证——这是 MyQuant 作为'进化工具'存在的根本意义却
无守护。本文件用确定性梯度 evaluate_fn 验证:

  A1 梯度 climbing —— 进化能沿梯度产出显著优于初始池的 champion, best 单调不降
  A2 进化 vs 随机  —— 同评估预算下进化显著优于纯随机搜索(进化算法有效性证明)
  A3 自适应 boost  —— 1/5 success rule 自适应变异强度(Rechenberg)
  A4 早停 4 规则   —— target/stagnation/decline/max_generations 正确触发

设计要点:
- evaluate_fn 是注入式纯函数接口(engine.py:186), 用梯度函数绕开'真实回测慢+无oracle'
- 进化用 random 模块(engine.py:139/378/402), random.seed 固定确定性可复现
- A1 用 1 维 stop_loss 梯度(连续 float, 无 engine 强制覆盖, 与 leverage 不同)
- A2 用 2 维乘法梯度(stop_loss × position_size, AND 关系), 使进化优势相对随机显著
"""
import random

import pytest

pytestmark = [pytest.mark.integration]

from core.evolution.engine import (  # noqa: E402
    EvolutionEngine,
    EarlyStopChecker,
    _AdaptiveMutationController,
)
from core.evolution.population import create_random_dna  # noqa: E402

TARGET_SL = 0.075  # 在 create_random_dna 范围 [0.01, 0.15] 内, 变异可达
TARGET_PS = 0.35   # 在 [0.10, 0.60] 中点


def gradient_stop_loss(dna) -> float:
    """A1 单维梯度: stop_loss 越接近 TARGET_SL fitness 越高, 立方衰减。

    线性梯度下初始池均值偏高(create_random_dna stop_loss∈[0.01,0.15] 围绕目标 0.075),
    champion 与 initial 差距不足; 立方衰减让偏离惩罚更陡, initial 均值降低。
    """
    sl = float(dna.risk_genes.stop_loss)
    score = max(0.0, 1.0 - abs(sl - TARGET_SL) / TARGET_SL)
    return round(100.0 * score ** 3, 4)


def gradient_2d(dna) -> float:
    """A2 二维梯度: stop_loss × position_size, 立方尖峰使进化优势显现。

    线性乘法梯度可分离, random 450 采样能分解优化两维, evo 仅优 ~6 分不显著。
    立方尖峰让最优点变窄, random 采样难精确命中, 进化 local 优化(stop_loss step 0.005)
    能逼近峰值, 优势拉开到 10+ 分。
    """
    sl = float(dna.risk_genes.stop_loss)
    ps = float(dna.risk_genes.position_size)
    sl_score = max(0.0, 1.0 - abs(sl - TARGET_SL) / TARGET_SL)
    ps_score = max(0.0, 1.0 - abs(ps - TARGET_PS) / 0.25)  # 半宽 0.25
    return round(100.0 * (sl_score * ps_score) ** 3, 4)


class TestEvolutionClimbsGradient:
    """A1: 进化沿单维梯度 climbing, champion 显著优于初始池, best 单调不降。"""

    def test_champion_significantly_beats_initial_population(self):
        random.seed(42)
        ancestor = create_random_dna(leverage=1, direction="long")
        engine = EvolutionEngine(
            target_score=99.5, max_generations=40, population_size=15, patience=20,
        )
        result = engine.evolve(ancestor, evaluate_fn=gradient_stop_loss)

        initial_avg = result["history"][0]["avg_score"]
        champion = result["champion_score"]
        # champion 至少爬坡 40 分且逼近峰值 100
        assert champion > initial_avg + 40.0, (
            f"champion {champion:.1f} 未显著优于初始池均值 {initial_avg:.1f}"
        )
        assert champion >= 95.0, f"champion {champion:.1f} 未逼近梯度峰值"

    def test_best_score_monotonically_nondecreasing(self):
        """champion 保护逻辑(engine.py:265-270)保证 best 单调不降。"""
        random.seed(42)
        ancestor = create_random_dna(leverage=1, direction="long")
        engine = EvolutionEngine(
            target_score=99.5, max_generations=40, population_size=15, patience=20,
        )
        result = engine.evolve(ancestor, evaluate_fn=gradient_stop_loss)

        best = [h["best_score"] for h in result["history"]]
        violations = [
            (i, b1, b2) for i, (b1, b2) in enumerate(zip(best, best[1:]))
            if b2 < b1 - 1e-9
        ]
        assert not violations, f"best_score 出现下降: {violations}"


class TestEvolutionBeatsRandom:
    """A2: 同评估预算下进化显著优于纯随机搜索(进化算法有效性的科学证明)。

    公平性三要素: 同 fitness 函数 / 同种子族 / 同采样数(pop*gens)。
    target_score=200 不可达 + patience=GENS 锁定跑满代数, 保证进化评估数对等。
    """

    def test_evolution_outperforms_random_search(self):
        POP, GENS = 15, 30

        # 进化侧
        random.seed(42)
        ancestor = create_random_dna(leverage=1, direction="long")
        engine = EvolutionEngine(
            target_score=200, max_generations=GENS, population_size=POP, patience=GENS,
        )
        evo_result = engine.evolve(ancestor, evaluate_fn=gradient_2d)
        evo_champion = evo_result["champion_score"]

        # 随机侧: 同样 POP*GENS 个独立随机 DNA 取最优
        random.seed(42)
        random_dns = [create_random_dna(leverage=1, direction="long") for _ in range(POP * GENS)]
        random_best = max(gradient_2d(d) for d in random_dns)

        assert evo_champion > random_best + 10.0, (
            f"进化 champion {evo_champion:.1f} 未显著优于随机最优 {random_best:.1f}"
        )


class TestAdaptiveMutationBoost:
    """A3: _AdaptiveMutationController 1/5 success rule 自适应变异强度(engine.py:92-122)。

    success_rate>0.3(改善多)→boost=0.85(减弱变异); <0.15(停滞)→1.3(增强); 中间→1.0。
    """

    def test_high_success_rate_lowers_boost(self):
        c = _AdaptiveMutationController(window_size=10)
        for v in [1, 2, 3, 4, 5, 6, 7]:  # 单调上升, 7 次全 improved
            c.record(float(v))
        assert c.success_rate > 0.3
        assert c.mutation_boost == 0.85

    def test_low_success_rate_raises_boost(self):
        c = _AdaptiveMutationController(window_size=10)
        c.record(1.0)              # improved (prev=-inf)
        for _ in range(9):         # 9 次不变
            c.record(1.0)
        assert c.success_rate < 0.15
        assert c.mutation_boost == 1.3

    def test_mid_success_rate_neutral_boost(self):
        c = _AdaptiveMutationController(window_size=10)
        c.record(1.0)
        c.record(2.0)              # 2 次 improved
        for _ in range(8):         # 8 次不变 -> rate=0.2 ∈ [0.15, 0.3]
            c.record(2.0)
        assert abs(c.success_rate - 0.2) < 1e-9
        assert c.mutation_boost == 1.0

    def test_window_slides_out_old_records(self):
        c = _AdaptiveMutationController(window_size=10)
        for _ in range(15):
            c.record(1.0)
        assert len(c._improvements) == 10


class TestEarlyStopFourRules:
    """A4: EarlyStopChecker 4 条早停规则各自正确触发(engine.py:52-89)。"""

    def test_target_reached_requires_min_generations(self):
        # 未达 min_generations 时, 即使 best>=target 也不停
        c = EarlyStopChecker(target_score=90.0, max_generations=200, min_generations=20)
        assert c.check(95.0, 5) == ("continue", "")
        # 达 min_generations 且 best>=target -> 停
        c2 = EarlyStopChecker(target_score=90.0, max_generations=200, min_generations=20)
        assert c2.check(95.0, 20) == ("stop", "target_reached")

    def test_stagnation_after_patience_generations(self):
        c = EarlyStopChecker(
            patience=3, min_improvement=0.5, target_score=1e9,
            max_generations=200, min_generations=0,
        )
        c.check(10.0, 1)   # best=10
        c.check(10.2, 2)   # improvement 0.2 < 0.5 -> no_improve=1
        c.check(10.3, 3)   # -> 2
        _, reason = c.check(10.4, 4)  # -> 3 >= patience
        assert reason == "stagnation"

    def test_decline_after_decline_limit(self):
        c = EarlyStopChecker(
            decline_limit=3, target_score=1e9, max_generations=200,
            min_improvement=0.0, patience=15, min_generations=0,
        )
        c.check(100.0, 1)
        c.check(99.0, 2)   # decline=1
        c.check(98.0, 3)   # =2
        _, reason = c.check(97.0, 4)  # =3 >= decline_limit
        assert reason == "decline"

    def test_max_generations(self):
        c = EarlyStopChecker(max_generations=10, target_score=1e9, min_generations=0)
        assert c.check(50.0, 10) == ("stop", "max_generations")
