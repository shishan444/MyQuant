"""进化 pool/weights 对齐复现测试（harden-research-test-coverage 3.4）。

复现的坑：组合变异算子加入 mutation_pool 未配 weights → random.choices
(7 pop vs 6 weights) 抛 ValueError → 被进化循环 except 吞 → 全部子代
fallback 成随机个体 → 进化退化为纯随机搜索（champion==random_best 实锤）。

本测试在真实变异调用点捕获 (population, weights)，断言长度恒等——
pool 配重失衡即刻红。
"""

import random
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

pytestmark = [pytest.mark.integration]

from core.evolution import engine as evo_engine


@pytest.fixture()
def mini_market(tmp_path: Path) -> Path:
    data_dir = tmp_path / "market"
    data_dir.mkdir()
    n = 400
    idx = pd.date_range("2024-01-01", periods=n, freq="1d", tz="UTC", name="timestamp")
    rng = np.random.default_rng(2)
    close = 100 + np.cumsum(rng.normal(0, 1, n))
    pd.DataFrame({"open": close, "high": close + 1, "low": close - 1,
                  "close": close, "volume": rng.uniform(10, 100, n),
                  "trades": 10}, index=idx).to_parquet(data_dir / "BTCUSDT_1d.parquet")
    return data_dir


def test_random_choices_population_weights_aligned(mini_market, tmp_path, monkeypatch):
    """变异调用点：random.choices 的 population 与 weights 长度恒等。

    进化权重坑的复现锚：任一长度失衡即红（原缺陷形态为
    ValueError 被 except 吞、子代全体退化随机）。
    """
    real_choices = random.choices
    captured = []

    def _spy_choices(population, weights=None, *a, **kw):
        if weights is not None:
            captured.append((len(population), len(weights)))
        return real_choices(population, weights=weights, *a, **kw)

    monkeypatch.setattr(evo_engine.random, "choices", _spy_choices)

    # 直接驱动 evolve 循环（评估函数给常数即可——被测的是变异调用点，
    # 不是进化效果）
    from core.strategy.dna import (
        ExecutionGenes, LogicGenes, RiskGenes, SignalGene, SignalRole, StrategyDNA)
    ancestor = StrategyDNA(
        signal_genes=[SignalGene("RSI", {"period": 14}, SignalRole.ENTRY_TRIGGER,
                                 "rsi_14", {"type": "lt", "threshold": 35})],
        logic_genes=LogicGenes(),
        execution_genes=ExecutionGenes(timeframe="1d"),
        risk_genes=RiskGenes())
    engine = evo_engine.EvolutionEngine(
        population_size=6, max_generations=2, patience=5)
    result = engine.evolve(ancestor, evaluate_fn=lambda dna: 50.0)
    assert result["total_generations"] >= 1
    assert captured, "未捕获到变异调用（进化未跑或算子未参与）"
    bad = [(p, w) for p, w in captured if p != w]
    assert not bad, f"population/weights 长度失衡（原缺陷形态）: {bad}"


def test_composition_not_in_global_pool():
    """mutate_composition 不得进入全局 mutation_pool（占权重名额稀释
    有效变异密度）——专属追加路径（组合基因个体 30% 概率）是裁决形制。"""
    import inspect
    src = inspect.getsource(evo_engine)
    pool_block = src.split("mutation_pool = [")[1].split("]")[0]
    assert "mutate_composition" not in pool_block, (
        "组合算子回到全局池将重现进化退化（random.choices 权重失衡或稀释）")
    # 专属路径存在
    assert "mutate_composition(child)" in src
