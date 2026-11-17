"""策略表达 v2 与转化器测试（add-ai-research-mcp 6.1-6.5）。"""

import json
import subprocess
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

pytestmark = [pytest.mark.integration]

from core.analysis.errors import ResearchError
from core.analysis.signal_assembly import assemble_signal_set
from core.persistence.repository.strategy import save_strategy
from core.persistence.db_ext import init_db_ext
from core.persistence.repository import research as rr
from core.strategy.dna import (
    ExecutionGenes, LogicGenes, RiskGenes, SignalGene, SignalRole, StrategyDNA,
    TimeframeLayer,
)


def _df(n=400, seed=6):
    rng = np.random.default_rng(seed)
    idx = pd.date_range("2024-01-01", periods=n, freq="4h", tz="UTC", name="timestamp")
    close = 60000 + np.cumsum(rng.normal(0, 110, n))
    return pd.DataFrame({
        "open": close, "high": close + 60, "low": close - 60,
        "close": close, "volume": rng.uniform(100, 800, n), "trades": 40,
    }, index=idx)


def _rule_dna():
    return StrategyDNA(
        signal_genes=[SignalGene("RSI", {"period": 14}, SignalRole.ENTRY_TRIGGER,
                                 "rsi_14", {"type": "lt", "threshold": 35})],
        logic_genes=LogicGenes(),
        execution_genes=ExecutionGenes(timeframe="4h"),
        risk_genes=RiskGenes())


@pytest.fixture()
def db(tmp_path: Path) -> Path:
    d = tmp_path / "q.db"
    init_db_ext(d)
    from core.analysis.executor import ensure_research_tables
    ensure_research_tables(d)
    return d


class TestDnaV2Compat:
    def test_legacy_roundtrip_stable(self):
        """退化形态序列化往返稳定（from_dict 的 layers auto-wrap 为存量
        既有行为，首轮往返即规范化——二轮起逐位一致；v2 字段不出现）。"""
        d = _rule_dna()
        s1 = StrategyDNA.from_json(d.to_json()).to_json()
        s2 = StrategyDNA.from_json(s1).to_json()
        assert s1 == s2
        assert "signal_sources" not in json.loads(s1)
        assert "composition" not in json.loads(s1)

    def test_v2_fields_carried(self):
        d = StrategyDNA(
            signal_genes=_rule_dna().signal_genes,
            signal_sources=[{"type": "rule"}, {"type": "formula", "ref": "fx_a"}],
            composition={"mode": "gate"})
        out = json.loads(d.to_json())
        assert out["signal_sources"][1]["ref"] == "fx_a"
        assert out["composition"]["mode"] == "gate"
        # 回读
        d2 = StrategyDNA.from_json(d.to_json())
        assert d2.composition["mode"] == "gate"

    def test_fallback_safety_unknown_keys(self):
        """含 v2 字段的 JSON 经旧解析（白名单过滤）不报错。"""
        payload = json.dumps({
            "signal_genes": [], "signal_sources": [{"type": "rule"}],
            "composition": {"mode": "logic", "op": "OR"},
            "unknown_future": [1, 2],
        })
        d = StrategyDNA.from_json(payload)
        assert d.signal_sources and d.composition["op"] == "OR"


class TestSignalAssembly:
    def test_degenerate_uses_original_path_bitwise(self):
        """退化形态组装结果与 dna_to_signal_set 原路径逐位一致。"""
        from core.strategy.executor import dna_to_signal_set
        df = _df()
        dna = _rule_dna()
        direct = dna_to_signal_set(dna, df)
        assembled = assemble_signal_set(Path("/nonexistent"), dna, df)
        assert direct.entries.equals(assembled.entries)
        assert direct.exits.equals(assembled.exits)

    def test_v2_composite_assembles(self, db, tmp_path):
        """v2 组合策略经组装产出 SignalSet（formula 源 + weighted_vote）。"""
        from core.analysis.plugin_service import register_formula_plugin
        register_formula_plugin(db, name="mom", expression="zscore(close, 20)")
        from core.analysis.registry_meta import available_signal_columns
        col = next(c["column_key"] for c in available_signal_columns(db)
                   if c["source"] == "formula")

        dna = StrategyDNA(
            signal_genes=_rule_dna().signal_genes,
            execution_genes=ExecutionGenes(timeframe="4h"),
            signal_sources=[{"type": "rule"}, {"type": "formula", "ref": col,
                                               "threshold": 0.5}],
            composition={"mode": "weighted_vote", "weights": {col: 1.0},
                         "threshold": 0.5})
        sig = assemble_signal_set(db, dna, _df())
        assert len(sig.entries) == 400
        assert sig.entries.dtype == bool

    def test_dangling_reference_rejected(self, db):
        """引用失效插件显式拒绝（不静默降级）。"""
        dna = StrategyDNA(
            signal_genes=_rule_dna().signal_genes,
            execution_genes=ExecutionGenes(timeframe="4h"),
            signal_sources=[{"type": "formula", "ref": "fx_ghost"}],
            composition={"mode": "gate"})
        with pytest.raises(ResearchError, match="引用失效"):
            assemble_signal_set(db, dna, _df())

    def test_backtest_zero_signals_import(self):
        """backtest 零新增依赖静态断言：core/backtest 无 core.signals import。"""
        import core.backtest.engine as eng
        src = Path(eng.__file__).read_text(encoding="utf-8")
        assert "core.signals" not in src, "backtest 不得依赖 core/signals（D13）"


class TestDnaLayersFallbackDeadCode:
    """dna.py `_resolve_signal_genes`（:312-316）不可达——死方法现状锚。

    调用图依据：全库检索该方法名仅定义无调用点（dna.py:312 一处），
    求值方（executor.py:768 等）直接迭代顶层 ``dna.signal_genes`` 字段，
    不经任何回退解析——「layers 存在且顶层为空时取 layers[0]」的回退
    行为未接线。现状行为锚：显式 layers 且顶层空 → signal_genes 保持
    空（无回退）。已记 TD 台账（测试侧不为死代码写行为用例）。
    """

    def test_显式layers顶层空_signal_genes现状无回退(self):
        layer = TimeframeLayer(
            timeframe="1d",
            signal_genes=[SignalGene("RSI", {"period": 14},
                                     SignalRole.ENTRY_TRIGGER, "rsi_14",
                                     {"type": "lt", "threshold": 30})],
            logic_genes=LogicGenes())
        dna = StrategyDNA(
            signal_genes=[], logic_genes=LogicGenes(),
            execution_genes=ExecutionGenes(timeframe="4h"),
            risk_genes=RiskGenes(), layers=[layer])
        assert dna.signal_genes == []


class TestFromDictTolerantShapes:
    """dna.py from_dict 宽容形制：role 已是枚举、子结构已是对象时
    直通不再重复转换（DB 往返/进程内对象的二形入参契约）。"""

    def test_signal_gene_role已是枚举_直通(self):
        raw = {"indicator": "RSI", "params": {"period": 14},
               "role": SignalRole.ENTRY_TRIGGER,
               "condition": {"type": "lt", "threshold": 30}}
        sg = SignalGene.from_dict(raw)
        assert sg.role is SignalRole.ENTRY_TRIGGER

    def test_layer_signal_genes已是对象列表_元素直通(self):
        genes = [SignalGene("RSI", {"period": 14}, SignalRole.ENTRY_TRIGGER,
                            "rsi_14", {"type": "lt", "threshold": 30})]
        layer = TimeframeLayer.from_dict({
            "timeframe": "1d", "signal_genes": genes})
        assert layer.signal_genes[0] is genes[0]

    def test_layer_signal_genes键缺失_跳过转换走默认(self):
        layer = TimeframeLayer.from_dict({"timeframe": "1d"})
        assert layer.signal_genes == []

    def test_layer_logic_genes已是对象_直通(self):
        lg = LogicGenes()
        layer = TimeframeLayer.from_dict({
            "timeframe": "1d", "logic_genes": lg})
        assert layer.logic_genes is lg


class TestGeneSignatureCompositionSkip:
    """gene_signature：有插件源但 composition 为空 → 跳过 comp 段
    （src 段照常产出；无 policy 签名逐位不变原则的组成面）。"""

    def test_有sources无composition_签名含src不含comp痕迹(self):
        dna = StrategyDNA(
            signal_genes=[SignalGene("RSI", {"period": 14},
                                     SignalRole.ENTRY_TRIGGER, "rsi_14",
                                     {"type": "lt", "threshold": 30})],
            logic_genes=LogicGenes(),
            execution_genes=ExecutionGenes(timeframe="4h"),
            risk_genes=RiskGenes(),
            signal_sources=[{"type": "formula", "ref": "f1"}],
            composition=None)
        sig = dna.gene_signature
        assert "src:formula:f1:0.0:>" in sig
        assert "comp:" not in sig


