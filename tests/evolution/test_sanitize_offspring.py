"""进化前置清洗双检测试（unify-tool-return-contract 任务 2）。

S7a-e 断言面：坏基因剔除且好基因保留 / 全剔除淘汰 / 零
ENTRY_TRIGGER 淘汰 / mutation_ops 注记可查 / 可达性维独立生效。
"""
from __future__ import annotations

import pytest

pytestmark = [pytest.mark.unit]

from core.evolution.engine import validate_offspring
from core.strategy.dna import SignalRole
from tests.helpers.data_factory import (
    make_dna, make_enhanced_df, make_signal_gene,
)


def _dna_with_bad_type_gene() -> tuple:
    """合法 DNA 前插一个未知条件类型基因（返回 dna 与坏基因引用）。"""
    dna = make_dna()
    bad = make_signal_gene(
        indicator="RSI", condition_type="phantom_type", condition_value=50)
    dna.signal_genes = [bad, *dna.signal_genes]
    return dna, bad


class TestSanitizeDoubleCheck:
    """S7a/S7d：类型维剔除+注记。"""

    def test_s7a_bad_gene_removed_good_kept(self):
        """剔除坏基因、其余保留（entry/exit 合法基因原样在场）。"""
        dna, bad = _dna_with_bad_type_gene()
        n_before = len(dna.signal_genes)

        result = validate_offspring(dna)

        assert result is dna, "原地修改返回同一实例"
        assert bad not in result.signal_genes, "坏基因被剔除"
        assert len(result.signal_genes) == n_before - 1
        # 好基因保留（make_dna 自带的 entry+exit 都在）
        roles = [g.role for g in result.signal_genes]
        assert SignalRole.ENTRY_TRIGGER in roles
        assert SignalRole.EXIT_TRIGGER in roles

    def test_s7d_mutation_ops_annotated(self):
        """被剔除个体 mutation_ops 注记 sanitized:{reason}（可查）。"""
        dna, _ = _dna_with_bad_type_gene()
        validate_offspring(dna)
        notes = [op for op in dna.mutation_ops if op.startswith("sanitized:")]
        assert any("unknown_condition_type:RSI" in op for op in notes), \
            f"注记应含 sanitized:unknown_condition_type:RSI，got {dna.mutation_ops}"

    def test_clean_dna_untouched(self):
        """合法 DNA 零剔除零注记（清洗不误伤）。"""
        dna = make_dna()
        n_before = len(dna.signal_genes)
        ops_before = list(dna.mutation_ops)
        validate_offspring(dna)
        assert len(dna.signal_genes) == n_before
        assert dna.mutation_ops == ops_before


class TestEliminationBottomLine:
    """S7b/S7c：淘汰保底。"""

    def test_s7b_all_bad_genes_eliminated(self):
        """全部基因坏 → 整个体淘汰（ValueError，走繁殖循环换随机个体）。"""
        dna = make_dna()
        dna.signal_genes = [
            make_signal_gene(condition_type="bad_a", condition_value=1),
            make_signal_gene(condition_type="bad_b", condition_value=1),
        ]
        with pytest.raises(ValueError, match="ENTRY_TRIGGER"):
            validate_offspring(dna)

    def test_s7c_no_entry_trigger_eliminated(self):
        """剔除后零 ENTRY_TRIGGER（exit 类好基因在场）→ 整个体淘汰。"""
        dna = make_dna()
        dna.signal_genes = [
            make_signal_gene(condition_type="bad_x", condition_value=1),
            make_signal_gene(role=SignalRole.EXIT_TRIGGER,
                             condition_value=70),
        ]
        with pytest.raises(ValueError, match="ENTRY_TRIGGER"):
            validate_offspring(dna)


class TestReachabilityCheck:
    """S7e：可达性维独立生效（数据帧在场时）。"""

    def test_s7e_unreachable_column_removed_with_df(self):
        """合法类型但指标列不可达（df 无该列）→ 剔除+column_unreachable
        注记（无 df 时该维跳过——繁殖侧无数据帧）。"""
        df = make_enhanced_df(n=50)
        dna = make_dna()
        ghost = make_signal_gene(
            indicator="GHOST_UNREACHABLE", params={},
            condition_value=50)  # 合法类型 lt + 不可达列
        dna.signal_genes = [ghost, *dna.signal_genes]

        validate_offspring(dna, enhanced_df=df)

        assert ghost not in dna.signal_genes
        assert any("column_unreachable:GHOST_UNREACHABLE" in op
                   for op in dna.mutation_ops)

    def test_s7e_no_df_skips_reachability(self):
        """无 df（繁殖侧）时可达性维跳过——同一 ghost 基因不被剔除
        （该维由评估侧补充，两段防线不重叠不缺位）。"""
        dna = make_dna()
        ghost = make_signal_gene(
            indicator="GHOST_UNREACHABLE", params={}, condition_value=50)
        dna.signal_genes = [ghost, *dna.signal_genes]

        validate_offspring(dna)  # enhanced_df=None

        assert ghost in dna.signal_genes, "无 df 不做可达性检（评估侧负责）"


class TestStructuralChecksPreserved:
    """既有结构校验维持 raise 形（fix-executor-batch-health 基线不回归）。"""

    def test_threshold_none_still_raises(self):
        dna = make_dna()
        gene = make_signal_gene(condition_value=30)
        gene.condition = {"type": "lt", "threshold": None}
        dna.signal_genes = [gene, *dna.signal_genes[1:]]
        with pytest.raises(ValueError, match="threshold"):
            validate_offspring(dna)

    def test_unhashable_condition_still_raises(self):
        dna = make_dna()
        gene = make_signal_gene(condition_value=30)
        gene.condition = {"type": "lt", "threshold": {"nested": 30}}
        dna.signal_genes = [gene, *dna.signal_genes[1:]]
        with pytest.raises(ValueError, match="hash|可哈希"):
            validate_offspring(dna)
