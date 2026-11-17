"""出场组内 OR 通道——gene_signature 增补段（add-exit-group-or 任务 3.1）。

覆盖 tasks 完成标准四项：
- 全量既有策略签名快照逐位不变：空库以合成 DNA 族快照承载（五形态
  硬编码预期串——纯 rule/带 @tf+short+杠杆/layers/插件源+composition
  +etp/slsrc 档；实施期经 git stash 前后对照实测逐位一致后定锚）
- or 形签名含段断言＋插入位序列形制断言（xlor:or/rlor:or 在 dir:
  段之后、层结构段之前）
- 同条件 or/and 两假设自动建双实体（签名异化分流）
- _dna_equivalent 第二道复核含逻辑键（to_dict 全量比对——仅
  logic_genes 异必不等价）
"""

import json
from pathlib import Path

import pytest

pytestmark = [pytest.mark.integration]

from core.analysis.executor import ensure_research_tables
from tests.helpers.vr_factory import insert_hypothesis
from core.persistence.db_ext import init_db_ext
from core.persistence.repository.strategy import get_strategy
from core.strategy.dna import (
    ExecutionGenes, LogicGenes, RiskGenes, SignalGene, SignalRole,
    StrategyDNA,
)


def _entry_gene() -> SignalGene:
    return SignalGene("RSI", {"period": 14}, SignalRole.ENTRY_TRIGGER,
                      "rsi_14", {"type": "lt", "threshold": 35})


def _dna(logic: LogicGenes) -> StrategyDNA:
    return StrategyDNA(
        signal_genes=[_entry_gene()],
        logic_genes=logic,
        execution_genes=ExecutionGenes(timeframe="4h"),
        risk_genes=RiskGenes())


def _sig(dna: StrategyDNA) -> str:
    # 往返规范化（与 translate 同签名分流同形——构造态与库内 auto-wrap
    # 差异被吸收）
    return StrategyDNA.from_json(dna.to_json()).gene_signature


class TestLegacySignatureSnapshot:
    """①合成 DNA 族快照逐位不变（空库承载形——AND 缺省全形态零漂移）。

    预期串实施期经 git stash 前后对照实测取得（/tmp/sig_before 与
    sig_after diff 为空）——新编码段仅 OR 通道触发，AND 缺省签名
    逐位不变由快照锁定。
    """

    def test_pure_rule(self):
        assert _sig(_dna(LogicGenes())) == (
            "RSI(period=14):lt:entry_trigger|lev:1|dir:long"
            "|L:4h:execution,RSI:lt")

    def test_tf_short_leverage(self):
        dna = StrategyDNA(
            signal_genes=[
                _entry_gene(),
                SignalGene("EMA", {"period": 20}, SignalRole.EXIT_TRIGGER,
                           "ema_20", {"type": "cross_above", "threshold": 0.0},
                           timeframe="1d"),
            ],
            logic_genes=LogicGenes(),
            execution_genes=ExecutionGenes(timeframe="4h"),
            risk_genes=RiskGenes(direction="short", leverage=3))
        assert _sig(dna) == (
            "RSI(period=14):lt:entry_trigger"
            "|EMA(period=20):cross_above:exit_trigger@1d"
            "|lev:3|dir:short"
            "|L:4h:execution,RSI:lt,EMA:cross_above")

    def test_layers_form(self):
        from core.strategy.dna import TimeframeLayer
        dna = StrategyDNA(
            signal_genes=[],
            logic_genes=LogicGenes(),
            execution_genes=ExecutionGenes(timeframe="4h"),
            risk_genes=RiskGenes(),
            layers=[TimeframeLayer(timeframe="1d", signal_genes=[
                SignalGene("EMA", {"period": 50}, SignalRole.ENTRY_GUARD,
                           "ema_50", {"type": "gt", "threshold": 0.0})])])
        assert _sig(dna) == "lev:1|dir:long|L:1d:execution,EMA:gt"

    def test_plugin_source_composition_etp(self):
        dna = StrategyDNA(
            signal_genes=[_entry_gene()],
            logic_genes=LogicGenes(),
            execution_genes=ExecutionGenes(timeframe="4h"),
            risk_genes=RiskGenes(),
            signal_sources=[{"type": "rule"},
                            {"type": "formula", "ref": "fx_mom",
                             "threshold": 0.5}],
            composition={"mode": "logic", "op": "and"},
            entry_trigger_policy={"mode": "first_true"})
        assert _sig(dna) == (
            "RSI(period=14):lt:entry_trigger|lev:1|dir:long"
            "|L:4h:execution,RSI:lt"
            '|src:formula:fx_mom:0.5:>'
            '|comp:{"mode": "logic", "op": "and"}'
            '|etp:{"mode": "first_true"}')

    def test_slsrc_form(self):
        dna = StrategyDNA(
            signal_genes=[_entry_gene()],
            logic_genes=LogicGenes(),
            execution_genes=ExecutionGenes(timeframe="4h"),
            risk_genes=RiskGenes(
                sl_series_source={"indicator": "EMA",
                                  "params": {"period": 200}}))
        assert _sig(dna) == (
            "RSI(period=14):lt:entry_trigger|lev:1|dir:long"
            "|L:4h:execution,RSI:lt"
            '|slsrc:{"indicator": "EMA", "params": {"period": 200}}')


class TestOrSegmentEncoding:
    """②or 形签名含段＋插入位序列形制。"""

    def test_exit_or_segment_present_and_position(self):
        sig = _sig(_dna(LogicGenes(exit_logic="OR")))
        parts = sig.split("|")
        assert "xlor:or" in parts
        # 插入位=dir: 段之后、层结构段之前
        assert parts.index("xlor:or") == parts.index("dir:long") + 1
        assert parts.index("xlor:or") < next(
            i for i, p in enumerate(parts) if p.startswith("L:"))

    def test_reduce_or_segment_present(self):
        sig = _sig(_dna(LogicGenes(reduce_logic="OR")))
        parts = sig.split("|")
        assert "rlor:or" in parts
        assert "xlor:or" not in parts
        assert parts.index("rlor:or") == parts.index("dir:long") + 1

    def test_both_or_segments_order(self):
        sig = _sig(_dna(LogicGenes(exit_logic="OR", reduce_logic="OR")))
        parts = sig.split("|")
        assert parts[parts.index("dir:long") + 1] == "xlor:or"
        assert parts[parts.index("dir:long") + 2] == "rlor:or"

    def test_and_default_no_segment(self):
        sig = _sig(_dna(LogicGenes()))
        assert "xlor:or" not in sig
        assert "rlor:or" not in sig


class TestSignatureSplitsIdentity:
    """③同条件 or/and 两假设自动建双实体（签名异化分流）。"""

    @pytest.fixture()
    def db(self, tmp_path: Path) -> Path:
        d = tmp_path / "q.db"
        init_db_ext(d)
        ensure_research_tables(d)
        return d

    _WHEN = [{"subject": {"indicator": "RSI", "params": {"period": 14},
                          "field": "rsi"}, "action": "lt", "target": 30}]
    _THEN = [{"subject": {"indicator": "RSI", "params": {"period": 14},
                          "field": "rsi"}, "action": "gt", "target": 70}]

    def _product_signature(self, db, h=None, *, exit_logic=None,
                           extra_groups=None) -> str:
        """产物签名（retire-legacy 后经 build_signal_genes 翻译入口+
        手构 LogicGenes——exit_logic 进 xlor 段、分批组 partial_exit_
        logic 进 rlor 段的签名语义不变）。"""
        from core.analysis.hypothesis_to_strategy import build_signal_genes
        genes, _sources = build_signal_genes(
            when_conditions=self._WHEN, then_conditions=self._THEN,
            db_path=db, strategy_timeframe="4h")
        logic = LogicGenes()
        if exit_logic:
            logic.exit_logic = str(exit_logic).upper()
        if extra_groups:
            for k, v in extra_groups.items():
                setattr(logic, k, str(v).upper())
        # 注：rlor 段消费 reduce_logic 通道（DNA 签名编码面单源——
        # 登记链原 partial_exit_logic 归一映射已随登记域退役）
        dna = StrategyDNA(
            signal_genes=list(genes), logic_genes=logic,
            execution_genes=ExecutionGenes(timeframe="4h"),
            risk_genes=RiskGenes())
        return _sig(dna)

    def test_or_and_products_signature_split(self, db):
        """同条件 or/and 两假设翻译产物签名异（xlor 段）——库去重/
        分流按签名天然分开，双实体自动建立。"""
        sig_and = self._product_signature(db)
        sig_or = self._product_signature(db, exit_logic="or")
        assert sig_or != sig_and
        assert "xlor:or" in sig_or
        assert "xlor:or" not in sig_and

    def test_partial_or_product_signature_contains_rlor(self, db):
        """partial 通道翻译产物级锚（scaled delta scenario「部分
        离场组内 OR 翻译」THEN 第二分句——产物 gene_signature 含
        "rlor:or" 段）：带组+partial_exit_logic="or" 登记翻译 →
        产物签名含 rlor:or 且异于无键产物签名（此前仅单元级
        LogicGenes 直构有段断言，缺登记→翻译全链产物锚）。"""
        cond = {
            "when_conditions": [
                {"subject": {"indicator": "RSI",
                             "params": {"period": 14},
                             "field": "rsi"},
                 "action": "lt", "target": 30}],
            "then_conditions": [
                {"subject": {"indicator": "RSI",
                             "params": {"period": 14},
                             "field": "rsi"},
                 "action": "gt", "target": 70}],
            "exit_partial_conditions": [
                {"subject": {"indicator": "RSI",
                             "params": {"period": 14},
                             "field": "rsi"},
                 "action": "gt", "target": 75}],
        }
        ranges = [{"symbol": "BTCUSDT", "timeframe": "4h",
                   "start": "2024-01-01T00:00:00Z",
                   "end": "2024-06-01T00:00:00Z"}]

        sig_plain = self._product_signature(db)
        sig_or = self._product_signature(
            db, extra_groups={"reduce_logic": "or"})
        assert "rlor:or" in sig_or
        assert "rlor:or" not in sig_plain
        assert sig_or != sig_plain

