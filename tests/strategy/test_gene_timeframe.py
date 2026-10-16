"""信号基因第六属性 timeframe 成对测试（unblock-research-validation 任务 1）。

T1a-f 断言面：无 tf 逐字节不变/带 tf 含键/往返兼容/签名三锚/策略名
异周期不同/API 模型字段。
"""
from __future__ import annotations

import json

import pytest

pytestmark = [pytest.mark.unit]

from core.strategy.dna import (
    ExecutionGenes, LogicGenes, RiskGenes, SignalGene, SignalRole, StrategyDNA,
)


def _gene(**kw) -> SignalGene:
    return SignalGene("RSI", {"period": 14}, SignalRole.ENTRY_TRIGGER,
                      "rsi_14", {"type": "lt", "threshold": 35}, **kw)


def _dna(gene: SignalGene) -> StrategyDNA:
    return StrategyDNA(
        signal_genes=[gene], logic_genes=LogicGenes(),
        execution_genes=ExecutionGenes(timeframe="4h"),
        risk_genes=RiskGenes())


class TestGeneTimeframeField:
    """T1a/T1b/T1c：序列化形制与往返。"""

    def test_t1a_no_tf_byte_identical(self):
        """T1a 无 timeframe 基因序列化与改造前逐字节一致（键缺省不写）。"""
        d = _gene().to_dict()
        assert d == {"indicator": "RSI", "params": {"period": 14},
                     "role": "entry_trigger", "field": "rsi_14",
                     "condition": {"type": "lt", "threshold": 35}}
        assert "timeframe" not in d

    def test_t1b_with_tf_contains_key_explicit_none_not(self):
        """T1b 带 tf 含键；显式 None 不含键（缺省即 None 语义）。"""
        d = _gene(timeframe="1d").to_dict()
        assert d["timeframe"] == "1d"
        assert "timeframe" not in _gene(timeframe=None).to_dict()

    def test_t1c_roundtrip_and_legacy(self):
        """T1c 往返（带 tf 重建还原）+ 旧形（无 tf 键）兼容重建。"""
        g = _gene(timeframe="1d")
        g2 = SignalGene.from_dict(json.loads(json.dumps(g.to_dict())))
        assert g2.timeframe == "1d" and g2.indicator == "RSI"
        # 旧形（存量 dna_json 无 timeframe 键）
        legacy = {"indicator": "RSI", "params": {"period": 14},
                  "role": "entry_trigger", "field": "rsi_14",
                  "condition": {"type": "lt", "threshold": 35}}
        g3 = SignalGene.from_dict(legacy)
        assert g3.timeframe is None


class TestSignatureAnchors:
    """T1d：签名三锚。"""

    def test_t1d_no_tf_signature_unchanged(self):
        """T1d-1 无 tf 基因签名与改造前逐位一致。"""
        sig = _dna(_gene()).gene_signature
        assert "RSI(period=14):lt:entry_trigger" in sig
        assert "@" not in sig.split("|")[0], "无 tf 不带 @ 后缀"

    def test_t1d_with_tf_suffix(self):
        """T1d-2 带 tf 签名含 @{tf} 段后缀。"""
        sig = _dna(_gene(timeframe="1d")).gene_signature
        assert "RSI(period=14):lt:entry_trigger@1d" in sig

    def test_t1d_same_gene_diff_tf_diff_signature(self):
        """T1d-3 同基因异周期必异签名（D2：防 GAP-11 同签名分流复发）。"""
        sig_a = _dna(_gene(timeframe="1d")).gene_signature
        sig_b = _dna(_gene(timeframe="1w")).gene_signature
        sig_none = _dna(_gene()).gene_signature
        assert len({sig_a, sig_b, sig_none}) == 3

    def test_t1e_strategy_name_differs_by_tf(self):
        """T1e 策略名异周期不同（哈希段计入 tf）。"""
        from core.strategy.dna import generate_strategy_name
        n1 = generate_strategy_name(_dna(_gene(timeframe="1d")))
        n2 = generate_strategy_name(_dna(_gene(timeframe="1w")))
        n3 = generate_strategy_name(_dna(_gene()))
        assert len({n1, n2, n3}) == 3


class TestApiModelSync:
    """T1f：API 模型字段同步（工程铁律）。"""

    def test_t1f_signal_gene_model_field(self):
        from api.schemas import SignalGeneModel
        m = SignalGeneModel(**_gene(timeframe="1d").to_dict())
        assert m.timeframe == "1d"
        m2 = SignalGeneModel(**_gene().to_dict())
        assert m2.timeframe is None
        with pytest.raises(Exception):
            SignalGeneModel(**_gene().to_dict(), bogus_key=1)  # extra=forbid 保持
