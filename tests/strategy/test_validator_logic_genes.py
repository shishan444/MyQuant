"""validator logic_genes 值域校验测试（fix-silent-degradation 审查批 SEC-3）。

SEC-3 链路：LogicGenes 四算子字段（entry/exit/add/reduce_logic）原在
validate_dna 零校验——非法值（XOR 等）可落库，执行期 combine_signals
R-7 raise 才拒。本校验面为入口期上游补全：落库/装载前 422，执行期
raise 保留为已落库存量兜底（单元锚 test_boundary_p0.py 承载）。判据
与 R-7 legal_values 同源：值域恰 "AND"/"OR"（大小写敏感）；顶层与
layers 的 TimeframeLayer.logic_genes 同面；errors 收集形不 raise。
"""
from __future__ import annotations

import pytest

pytestmark = [pytest.mark.unit]

from core.strategy.dna import (
    ExecutionGenes, LogicGenes, RiskGenes, SignalGene, SignalRole,
    StrategyDNA, TimeframeLayer,
)
from core.strategy.validator import validate_dna


def _mk_dna(logic_genes: LogicGenes | None = None) -> StrategyDNA:
    """构造进出双基因 DNA（非 MTF 形态），按参挂 logic_genes。"""
    genes = [
        SignalGene("RSI", {"period": 14}, SignalRole.ENTRY_TRIGGER,
                   "rsi_14", {"type": "lt", "threshold": 30},
                   timeframe=None),
        SignalGene("RSI", {"period": 14}, SignalRole.EXIT_TRIGGER,
                   "rsi_14", {"type": "gt", "threshold": 70},
                   timeframe=None),
    ]
    return StrategyDNA(
        signal_genes=genes, logic_genes=logic_genes or LogicGenes(),
        risk_genes=RiskGenes(),
        execution_genes=ExecutionGenes(timeframe="4h"))


class TestLogicGenesDomain:
    """logic_genes 四算子值域：合法 AND/OR 放行、非法拒绝（顶层）。"""

    def test_defaults_pass(self):
        """全缺省（AND×4）放行——校验面不误伤存量主流形。"""
        assert validate_dna(_mk_dna()).is_valid

    def test_legal_and_or_mixed_fields_pass(self):
        """四字段 AND/OR 混搭全合法放行（与 R-7 合法值同源）。"""
        dna = _mk_dna(LogicGenes(
            entry_logic="OR", exit_logic="OR",
            add_logic="AND", reduce_logic="OR"))
        assert validate_dna(dna).is_valid

    @pytest.mark.parametrize("field", [
        "entry_logic", "exit_logic", "add_logic", "reduce_logic"])
    def test_xor_rejected_per_field(self, field):
        """非法串（XOR）逐字段拒绝，消息点名层级与字段。"""
        dna = _mk_dna()
        setattr(dna.logic_genes, field, "XOR")
        result = validate_dna(dna)
        assert not result.is_valid
        assert any(
            f"logic_genes.{field}" in e and "XOR" in e
            and "must be 'AND' or 'OR'" in e
            for e in result.errors), result.errors

    @pytest.mark.parametrize("bad", ["xor", None, 123, "A" * 300],
                             ids=["小写", "None", "非串int", "超长串"])
    def test_bad_value_forms_rejected(self, bad):
        """非法形制四态拒绝（小写/None/非串/超长——与 R-7 值域边界
        判据同源：大小写敏感、非串不豁免）。"""
        dna = _mk_dna()
        dna.logic_genes.entry_logic = bad
        result = validate_dna(dna)
        assert not result.is_valid
        assert any("entry_logic" in e for e in result.errors), result.errors


class TestLayerLogicGenesDomain:
    """TimeframeLayer.logic_genes 层内同面（SEC-3 卡面补全面）。"""

    @pytest.mark.parametrize("field", [
        "entry_logic", "exit_logic", "add_logic", "reduce_logic"])
    def test_layer_bad_logic_rejected(self, field):
        """层内四字段逐项拒绝，消息点名层（Layer {tf} logic_genes）。"""
        dna = _mk_dna()
        dna.layers = [TimeframeLayer(
            timeframe="4h",
            signal_genes=[
                SignalGene("RSI", {"period": 14}, SignalRole.ENTRY_TRIGGER,
                           "rsi_14", {"type": "lt", "threshold": 30},
                           timeframe=None),
                SignalGene("RSI", {"period": 14}, SignalRole.EXIT_TRIGGER,
                           "rsi_14", {"type": "gt", "threshold": 70},
                           timeframe=None),
            ],
            logic_genes=LogicGenes(**{field: "XOR"}),
            role="execution")]
        result = validate_dna(dna)
        assert not result.is_valid
        assert any(
            f"Layer 4h logic_genes.{field}" in e and "XOR" in e
            for e in result.errors), result.errors

    def test_layer_legal_and_or_pass(self):
        """层内合法 OR 形态放行（合法值域不误伤 MTF 正常形态）。"""
        dna = _mk_dna()
        dna.layers = [TimeframeLayer(
            timeframe="4h",
            signal_genes=[
                SignalGene("RSI", {"period": 14}, SignalRole.ENTRY_TRIGGER,
                           "rsi_14", {"type": "lt", "threshold": 30},
                           timeframe=None),
                SignalGene("RSI", {"period": 14}, SignalRole.EXIT_TRIGGER,
                           "rsi_14", {"type": "gt", "threshold": 70},
                           timeframe=None),
            ],
            logic_genes=LogicGenes(
                entry_logic="OR", exit_logic="OR",
                add_logic="OR", reduce_logic="OR"),
            role="execution")]
        assert validate_dna(dna).is_valid

    def test_top_and_layer_bad_reported_together(self):
        """顶层与层内同时非法 → 两面消息各在场（errors 收集形不短路）。"""
        dna = _mk_dna()
        dna.logic_genes.entry_logic = "XOR"
        dna.layers = [TimeframeLayer(
            timeframe="4h",
            signal_genes=[
                SignalGene("RSI", {"period": 14}, SignalRole.ENTRY_TRIGGER,
                           "rsi_14", {"type": "lt", "threshold": 30},
                           timeframe=None),
                SignalGene("RSI", {"period": 14}, SignalRole.EXIT_TRIGGER,
                           "rsi_14", {"type": "gt", "threshold": 70},
                           timeframe=None),
            ],
            logic_genes=LogicGenes(reduce_logic="XOR"),
            role="execution")]
        result = validate_dna(dna)
        assert not result.is_valid
        assert any(e.startswith("logic_genes.entry_logic")
                   for e in result.errors), result.errors
        assert any("Layer 4h logic_genes.reduce_logic" in e
                   for e in result.errors), result.errors
