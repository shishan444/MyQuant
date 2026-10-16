"""validator 基因级 timeframe 值域校验测试（fix-review B5 / security S-1）。

security S-1 链路：非法 tf 此前可落库（手工 DNA 五入口无校验），
装载/执行时点才炸且 find_parquet 路径 ValueError 裸穿 500——本
校验面在 validate_dna 落库前拦截。判据与翻译防线同源：tf ∈
TF_SECONDS 且 >= 执行周期（assert_higher_tf >= 语义）；顶层与
layers 基因同面；errors 收集形不 raise。
"""
from __future__ import annotations

import pytest

pytestmark = [pytest.mark.unit]

from core.strategy.dna import (
    ExecutionGenes, RiskGenes, SignalGene, SignalRole, StrategyDNA,
    TimeframeLayer,
)
from core.strategy.validator import validate_dna


def _mk_dna(gene_tfs=None, exec_tf="4h") -> StrategyDNA:
    """构造进出双基因 DNA，按序给额外基因挂 timeframe。"""
    genes = [
        SignalGene("RSI", {"period": 14}, SignalRole.ENTRY_TRIGGER,
                   "rsi_14", {"type": "lt", "threshold": 30},
                   timeframe=None),
        SignalGene("RSI", {"period": 14}, SignalRole.EXIT_TRIGGER,
                   "rsi_14", {"type": "gt", "threshold": 70},
                   timeframe=None),
    ]
    for tf in (gene_tfs or []):
        genes.append(SignalGene(
            "RSI", {"period": 14}, SignalRole.ADD_TRIGGER,
            "rsi_14", {"type": "gt", "threshold": 50}, timeframe=tf))
    return StrategyDNA(
        signal_genes=genes, risk_genes=RiskGenes(),
        execution_genes=ExecutionGenes(timeframe=exec_tf))


class TestGeneTimeframeDomain:
    """基因级 tf 值域：非法拒绝、低周期拒绝、合法三态放行。"""

    def test_unknown_tf_rejected(self):
        """非法 tf（不在 TF_SECONDS）→ errors 收集拒绝（S-1 落库前
        拦截面）。"""
        result = validate_dna(_mk_dna(gene_tfs=["10h"]))
        assert not result.is_valid
        assert any("timeframe must be one of" in e and "10h" in e
                   for e in result.errors), result.errors

    def test_lower_than_exec_tf_rejected(self):
        """tf 低于执行周期（1h 基因 @4h 执行）→ 拒绝（与翻译面
        assert_higher_tf >= 语义一致）。"""
        result = validate_dna(_mk_dna(gene_tfs=["1h"], exec_tf="4h"))
        assert not result.is_valid
        assert any("must be >= execution timeframe" in e
                   for e in result.errors), result.errors

    def test_equal_to_exec_tf_passes(self):
        """tf == 执行周期放行（>= 含等值——翻译面归一语义的等值态
        在 validator 不拒）。"""
        assert validate_dna(_mk_dna(gene_tfs=["4h"])).is_valid

    def test_higher_than_exec_tf_passes(self):
        """tf 严格高于执行周期放行（基因级高周期正常形态）。"""
        assert validate_dna(_mk_dna(gene_tfs=["1d"], exec_tf="4h")).is_valid

    def test_no_timeframe_passes(self):
        """无基因级 tf（全部 None）放行——校验面不误伤存量主流形。"""
        assert validate_dna(_mk_dna()).is_valid

    def test_layer_gene_same_face(self):
        """layers 内基因同面拒绝（prefix 点名层与序）。"""
        dna = _mk_dna()
        dna.layers = [TimeframeLayer(
            timeframe="4h",
            signal_genes=[
                SignalGene("RSI", {"period": 14}, SignalRole.ENTRY_TRIGGER,
                           "rsi_14", {"type": "lt", "threshold": 30},
                           timeframe="bad_tf"),
                SignalGene("RSI", {"period": 14}, SignalRole.EXIT_TRIGGER,
                           "rsi_14", {"type": "gt", "threshold": 70},
                           timeframe=None),
            ],
            role="execution")]
        result = validate_dna(dna)
        assert not result.is_valid
        assert any("Layer 4h gene 0" in e and "bad_tf" in e
                   for e in result.errors), result.errors
