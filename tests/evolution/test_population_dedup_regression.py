"""缺陷回归：population 去重回填路径的 _sig NameError（batch1 任务 2.1）。

缺陷：init_population(exclude_signatures 非空) 且去重后需回填时，
population.py 的回填循环调用未定义的 `_sig`，触发 NameError——
连续进化注入新种子后必炸。
修复前红、修复后绿。
"""
from __future__ import annotations

import pytest

pytestmark = [pytest.mark.unit]

from core.evolution.population import init_population
from core.strategy.dna import StrategyDNA


def _make_dna(timeframe: str = "4h", symbol: str = "BTCUSDT") -> StrategyDNA:
    """构造一个最小合法 DNA。"""
    return StrategyDNA.from_dict({
        "signal_genes": [
            {
                "indicator": "EMA",
                "params": {"period": 20},
                "role": "entry_trigger",
                "condition": {"type": "price_above"},
            },
            {
                "indicator": "EMA",
                "params": {"period": 20},
                "role": "exit_trigger",
                "condition": {"type": "price_below"},
            },
        ],
        "logic_genes": {"entry_logic": "AND", "exit_logic": "AND"},
        "execution_genes": {"timeframe": timeframe, "symbol": symbol},
        "risk_genes": {"stop_loss": 0.03, "take_profit": 0.06,
                        "position_size": 1.0, "leverage": 1, "direction": "long"},
    })


def test_backfill_with_excluded_signatures_does_not_raise():
    """id=去重回填路径不触发 NameError（缺陷复现）"""
    # 把种子的签名排除掉（确定性来自 exclude 集合本身：必然进入回填分支，与随机值无关）
    seed = _make_dna()
    excluded = {seed.gene_signature}

    population = init_population(
        size=8,
        ancestor=seed,
        timeframe="4h",
        symbol="BTCUSDT",
        exclude_signatures=excluded,
    )

    # 修复后：函数正常返回种群，且不包含被排除的签名
    assert len(population) >= 1
    assert all(ind.gene_signature not in excluded for ind in population)
