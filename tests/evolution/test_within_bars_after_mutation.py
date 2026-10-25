"""R4 变异域锚（fix-mtf-execution-consistency 组 3，T⑥.8+进化前提）。

- T⑥.8 构造完整性：mock 随机选择强制命中 within_bars_after——产物
  必含 window+inner 嵌套形且经求值端可求值，裸 {"type": ...} 不出现
  （收录面自动获得、构造面显式分支——两面独立，缺分支即 KeyError→
  batch 降级全 False 适应度静默失真）；
- 进化前提锚：变异族全算子产物 signal_genes 无 timeframe 键——
  Q4 收窄理由的结构性前提（防未来算子扩容静默破坏「进化侧无带轴
  个体」前提）。
"""
import random
from unittest.mock import patch

import pytest

pytestmark = [pytest.mark.unit]

from core.evolution import operators as ops
from core.features.registry import INDICATOR_REGISTRY


def _rvol_reg():
    reg = INDICATOR_REGISTRY["RVOL"]
    assert "within_bars_after" in reg.supported_conditions
    return reg


class TestT6_8MutationConstruction:
    def test_forced_hit_produces_complete_nested_form(self):
        """mock 强制命中：产物必含 window+inner 且可求值。"""
        reg = _rvol_reg()
        with patch.object(random, "choice",
                          side_effect=lambda seq: (
                              "within_bars_after" if "within_bars_after" in
                              seq else seq[0])):
            cond = ops.generate_random_condition("RVOL", reg=reg, use_profile=False)
        assert cond["type"] == "within_bars_after"
        assert isinstance(cond["window"], int) and cond["window"] >= 1
        assert set(cond["inner"].keys()) >= {"type"}
        assert cond["inner"]["type"] in ("price_above", "price_below",
                                         "gt", "lt")
        # 可求值：执行链求值不抛（合法结构完整性）
        import numpy as np
        import pandas as pd
        from core.strategy.executor import evaluate_condition
        n = 120
        rng = np.random.RandomState(7)
        rsi = pd.Series(rng.uniform(20, 80, n))
        close = pd.Series(np.full(n, 100.0))
        out = evaluate_condition(rsi, close, cond)
        assert len(out) == n

    def test_no_bare_type_condition_in_pool(self):
        """全词表随机构造族：无裸 {"type": ...}（缺 window/inner）产物。"""
        random.seed(20261024)
        for name, reg in list(INDICATOR_REGISTRY.items())[:20]:
            if not reg.supported_conditions:
                continue
            for _ in range(30):
                cond = ops.generate_random_condition(name, reg=reg)
                if cond["type"] == "within_bars_after":
                    assert "window" in cond and "inner" in cond, (
                        f"{name} 变异产物缺 window/inner：{cond}")


class TestEvolutionPremiseNoTimeframeKey:
    def test_mutation_pool_products_have_no_gene_timeframe(self):
        """进化前提锚：变异族产物 signal_genes 无 timeframe 键。

        Q4 收窄（新一轮自然重评不追溯）的结构性前提——进化侧构造
        路径不产带轴基因（带轴个体入种群唯一途径=研究转化产物从库
        加载）；未来算子扩容若引入 timeframe 键，此锚红即提示前提
        破坏（Q4 处置面需重估）。
        """
        random.seed(20261024)
        checked = 0
        for name, reg in list(INDICATOR_REGISTRY.items()):
            if not reg.supported_conditions or getattr(reg, "guard_only",
                                                       False):
                continue
            for _ in range(10):
                cond = ops.generate_random_condition(name, reg=reg)
                assert "timeframe" not in cond, (
                    f"变异产物意外携带 timeframe 键（前提破坏）：{cond}")
                checked += 1
        assert checked > 50, f"样本量不足（checked={checked}）"
