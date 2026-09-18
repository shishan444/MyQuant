"""B2 死代码与假宣称裁决的测试（refactor-myquant-backend-b1-b11 · B2 组）。

- 2.3 rule_extractor 排序改 Wilson 下界（点估计保留展示）——行为变更锚定
- 2.4 文档宣称逐条可验证由 docs 修正承载（grep 断言在验证门执行）
"""

import pytest

pytestmark = [pytest.mark.unit]


# ===========================================================================
# B2.3: rule_extractor 排序改 Wilson 下界
# ===========================================================================

class TestRuleSortWilsonLowerBound:
    """规则排序以 Wilson 下界为键：小样本高纯度不再被大样本点估计险胜反超。

    旧键（confidence × samples）在乘积相近时按噪声排序：0.51×10=5.1 会
    反超 1.0×5=5.0；Wilson 下界 1.0@5≈0.566 > 0.51@10≈0.256 纠正该序。
    """

    def test_small_sample_beats_narrow_margin_large_sample(self):
        from core.discovery.rule_extractor import rule_sort_key, RuleItem

        a = RuleItem(conditions=[], direction="UP", confidence=1.0, samples=5, lift=1.0)
        b = RuleItem(conditions=[], direction="UP", confidence=0.51, samples=10, lift=1.0)
        assert rule_sort_key(a) > rule_sort_key(b)

    def test_point_estimate_field_unchanged(self):
        """排序键为 Wilson 下界，confidence 字段（点估计）保留展示语义。"""
        from core.discovery.rule_extractor import rule_sort_key, RuleItem

        a = RuleItem(conditions=[], direction="UP", confidence=0.9, samples=7, lift=1.0)
        key = rule_sort_key(a)
        assert 0.0 < key < 0.9, "Wilson 下界应收缩于点估计之下"
        assert a.confidence == 0.9, "排序不得修改 RuleItem 的点估计"

    def test_zero_samples_bottom(self):
        from core.discovery.rule_extractor import rule_sort_key, RuleItem

        z = RuleItem(conditions=[], direction="UP", confidence=1.0, samples=0, lift=1.0)
        assert rule_sort_key(z) == 0.0

    def test_same_estimate_larger_sample_ranks_higher(self):
        """点估计相同时，样本多者下界更高（不确定度更低）。"""
        from core.discovery.rule_extractor import rule_sort_key, RuleItem

        small = RuleItem(conditions=[], direction="UP", confidence=0.8, samples=5, lift=1.0)
        large = RuleItem(conditions=[], direction="UP", confidence=0.8, samples=100, lift=1.0)
        assert rule_sort_key(large) > rule_sort_key(small)
