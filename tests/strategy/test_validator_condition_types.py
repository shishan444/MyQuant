"""validator cond_type 合法集校验测试（fix-review CARD-01）。

锚点面：
- cond_type ∉ EVALUABLE_CONDITION_TYPES → error（单源常量与 executor
  执行分派集同源，装载面与执行侧 RESEARCH_NOT_EXPRESSIBLE 拒绝面
  一致化——存量库内非法 cond_type 策略装载从通过变拒绝）。
- lookback inner 深度校验：inner 缺 type（执行侧委托主入口裸下标
  炸链）/ 阈值四类缺 threshold（执行侧静默用 0，executor.py:204-205）
  / inner type 非法 / inner 非 dict，均在装载前结构拦截。
- 16 合法类型完整结构全部放行（防新校验误伤合法面）。
"""
from __future__ import annotations

import pytest

pytestmark = [pytest.mark.unit]

from core.strategy.dna import RiskGenes, SignalGene, SignalRole, StrategyDNA
from core.strategy.executor import EVALUABLE_CONDITION_TYPES
from core.strategy.validator import validate_dna


def _mk_dna_with_condition(condition: dict) -> StrategyDNA:
    """构造单 ENTRY 基因 DNA（condition 直接注入待测基因）。"""
    genes = [
        SignalGene("RSI", {"period": 14}, SignalRole.ENTRY_TRIGGER,
                   condition=condition),
        SignalGene("RSI", {"period": 14}, SignalRole.EXIT_TRIGGER,
                   condition={"type": "gt", "threshold": 70}),
    ]
    return StrategyDNA(signal_genes=genes, risk_genes=RiskGenes())


# 16 合法类型完整结构样本（role_reversal 补 'role'——validator 既有
# 结构校验要求必需键；其余与 tests/strategy/test_executor.py 穷举
# 样本同构）
_LEGAL_CASES = {
    "eq": {"type": "eq", "threshold": 50},
    "lt": {"type": "lt", "threshold": 50},
    "gt": {"type": "gt", "threshold": 50},
    "le": {"type": "le", "threshold": 50},
    "ge": {"type": "ge", "threshold": 50},
    "cross_above": {"type": "cross_above", "threshold": 50},
    "cross_below": {"type": "cross_below", "threshold": 50},
    "price_above": {"type": "price_above"},
    "price_below": {"type": "price_below"},
    "cross_above_series": {
        "type": "cross_above_series",
        "target_indicator": "EMA", "target_params": {"period": 20}},
    "cross_below_series": {
        "type": "cross_below_series",
        "target_indicator": "EMA", "target_params": {"period": 20}},
    "lookback_any": {
        "type": "lookback_any", "window": 3,
        "inner": {"type": "gt", "threshold": 50}},
    "lookback_all": {
        "type": "lookback_all", "window": 3,
        "inner": {"type": "gt", "threshold": 50}},
    "touch_bounce": {
        "type": "touch_bounce", "threshold": 0.02,
        "direction": "support"},
    "role_reversal": {"type": "role_reversal", "role": "support"},
    "wick_touch": {
        "type": "wick_touch", "threshold": 0.01,
        "direction": "support"},
}


class TestCondTypeLegalSet:
    """cond_type 合法集校验：非法拒绝、合法全放行。"""

    def test_unknown_cond_type_rejected(self):
        """非法 cond_type → error 点名类型与合法集（与 sl_mode 校验
        'must be one of' 同消息形制）。"""
        result = validate_dna(
            _mk_dna_with_condition({"type": "no_such_type", "threshold": 50}))
        assert not result.is_valid
        assert any(
            "condition type must be one of" in e and "no_such_type" in e
            for e in result.errors), result.errors

    def test_missing_type_key_rejected(self):
        """type 键整体缺失（cond_type=''）→ 同面拒绝。"""
        result = validate_dna(_mk_dna_with_condition({"threshold": 50}))
        assert not result.is_valid
        assert any("condition type must be one of" in e
                   for e in result.errors), result.errors

    def test_all_legal_types_pass(self):
        """16 合法类型完整结构全部放行（穷举样本与常量集一致性自锚
        ——防新校验误伤合法面）。"""
        assert set(_LEGAL_CASES) == set(EVALUABLE_CONDITION_TYPES), \
            "穷举样本与常量集不一致（样本缺或常量多）"
        for cond_type, condition in _LEGAL_CASES.items():
            result = validate_dna(_mk_dna_with_condition(condition))
            assert result.is_valid, (
                f"合法类型 {cond_type} 不应被拒：{result.errors}")

    def test_layer_gene_unknown_cond_type_rejected(self):
        """layer 基因同面拒绝（prefix 点名层与序）。"""
        from core.strategy.dna import (
            ExecutionGenes, LogicGenes, TimeframeLayer,
        )
        dna = StrategyDNA(
            signal_genes=[],
            logic_genes=LogicGenes(),
            execution_genes=ExecutionGenes(timeframe="4h"),
            risk_genes=RiskGenes(),
            layers=[
                TimeframeLayer(
                    timeframe="1h",
                    signal_genes=[
                        SignalGene("RSI", {"period": 14},
                                   SignalRole.ENTRY_TRIGGER, "rsi_14",
                                   {"type": "bad_type"}),
                        SignalGene("RSI", {"period": 14},
                                   SignalRole.EXIT_TRIGGER, "rsi_14",
                                   {"type": "gt", "threshold": 70}),
                    ],
                    logic_genes=LogicGenes(),
                    role="execution",
                ),
            ],
        )
        result = validate_dna(dna)
        assert not result.is_valid
        assert any(
            "Layer 1h gene 0" in e and "bad_type" in e
            for e in result.errors), result.errors


class TestLookbackInnerDepth:
    """lookback inner 深度校验：缺 type/threshold、非法 type、非 dict。"""

    def _errors_with_inner(self, inner) -> list:
        return validate_dna(_mk_dna_with_condition({
            "type": "lookback_any", "window": 3, "inner": inner,
        })).errors

    def test_inner_missing_type_rejected(self):
        """inner 缺 type → error（执行侧委托主入口裸下标炸链，装载前拦）。"""
        errors = self._errors_with_inner({"threshold": 50})
        assert any("inner condition requires 'type'" in e
                   for e in errors), errors

    def test_inner_threshold_compare_missing_threshold_rejected(self):
        """inner 阈值四类缺 threshold → error（执行侧静默用 0 的
        结构性错误点，executor.py:204-205 同族小面收口）。"""
        for inner_type in ("lt", "gt", "le", "ge"):
            errors = self._errors_with_inner({"type": inner_type})
            assert any(
                f"({inner_type}) requires 'threshold'" in e
                for e in errors), (inner_type, errors)

    def test_inner_threshold_present_passes(self):
        """inner 阈值四类带 threshold 完整结构放行。"""
        result = validate_dna(_mk_dna_with_condition({
            "type": "lookback_any", "window": 3,
            "inner": {"type": "lt", "threshold": 30},
        }))
        assert result.is_valid, result.errors

    def test_inner_non_threshold_type_without_threshold_passes(self):
        """inner 非阈值类（如 price_above）无 threshold 键也放行
        ——执行侧该类不消费 threshold，不设多余要求。"""
        result = validate_dna(_mk_dna_with_condition({
            "type": "lookback_all", "window": 3,
            "inner": {"type": "price_above"},
        }))
        assert result.is_valid, result.errors

    def test_inner_unknown_type_rejected(self):
        """inner type 非法 → error（与外层 cond_type 合法集同源单判）。"""
        errors = self._errors_with_inner({"type": "no_such_inner"})
        assert any("inner condition type must be one of" in e
                   and "no_such_inner" in e
                   for e in errors), errors

    def test_inner_non_dict_rejected(self):
        """inner 非 dict（如 str）→ error 点名实际类型（执行侧
        inner.get 将 AttributeError 炸链）。"""
        errors = self._errors_with_inner("gt:50")
        assert any("'inner' must be a dict" in e and "str" in e
                   for e in errors), errors

    def test_inner_missing_all_pointed_out_together(self):
        """window 与 inner 同时缺失 → 两错误并报（错误收集形制）。"""
        result = validate_dna(
            _mk_dna_with_condition({"type": "lookback_any"}))
        assert any("requires 'window'" in e for e in result.errors)
        assert any("requires 'inner'" in e for e in result.errors)


class TestValidatorExecutorLineup:
    """两防线同判锚：validator 前置拦截面与 executor 执行拒绝面经
    EVALUABLE_CONDITION_TYPES 单源一致——同一非法类型两侧同判（防
    未来任一侧改 import 源或本地复制集合造成防线分叉）。"""

    @pytest.mark.parametrize("bad_type", ["no_such_type", "eq_"])
    def test_same_bad_type_rejected_by_both_lines(self, bad_type):
        """validator 拒绝（error 收集）且 executor raise
        RESEARCH_NOT_EXPRESSIBLE——同判不依赖两侧各自维护的集合。"""
        import pandas as pd

        from core.analysis.errors import ResearchError
        from core.strategy.executor import evaluate_condition

        result = validate_dna(
            _mk_dna_with_condition({"type": bad_type, "threshold": 50}))
        assert not result.is_valid, f"validator 应拒绝 {bad_type}"

        series = pd.Series([50.0, 55.0, 60.0])
        with pytest.raises(ResearchError) as exc_info:
            evaluate_condition(
                series, series.copy(), {"type": bad_type, "threshold": 50})
        assert exc_info.value.code == "RESEARCH_NOT_EXPRESSIBLE"
