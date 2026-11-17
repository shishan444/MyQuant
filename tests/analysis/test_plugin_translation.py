"""插件列假设翻译与公式 lookback 推导测试（fix-plugin-column-translation 1.x/2.1）。

覆盖：插件列条件→formula 信号源（四比较 operator 保真）、混合条件
logic AND 组合、纯插件退化形态、三类拒绝文案区分、组装器 operator
向后兼容与无规则源中性基线、gene_signature 覆盖 signal_sources、
公式表达式 lookback 静态推导（含依赖闭包递归）。
"""

import json
import uuid
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

pytestmark = [pytest.mark.integration]

from core.analysis.errors import ResearchError
from core.analysis.hypothesis_to_strategy import build_signal_genes
from core.analysis.plugin_service import register_formula_plugin
from core.analysis.signal_assembly import assemble_signal_set
from core.persistence.repository.strategy import get_strategy
from core.persistence.db_ext import init_db_ext
from core.persistence.repository import research as rr
from core.strategy.dna import (
    ExecutionGenes, LogicGenes, RiskGenes, SignalGene, SignalRole, StrategyDNA)
from core.strategy.executor import dna_to_signal_set


def _df(n=400, seed=6):
    rng = np.random.default_rng(seed)
    idx = pd.date_range("2024-01-01", periods=n, freq="4h", tz="UTC", name="timestamp")
    close = 60000 + np.cumsum(rng.normal(0, 110, n))
    return pd.DataFrame({
        "open": close, "high": close + 60, "low": close - 60,
        "close": close, "volume": rng.uniform(100, 800, n), "trades": 40,
    }, index=idx)


def _ranges(symbol="BTCUSDT"):
    return [{"symbol": symbol, "timeframe": "4h",
             "start": "2024-01-01T00:00:00Z", "end": "2024-02-01T00:00:00Z"}]


@pytest.fixture()
def db(tmp_path: Path) -> Path:
    d = tmp_path / "q.db"
    init_db_ext(d)
    from core.analysis.executor import ensure_research_tables
    ensure_research_tables(d)
    return d


@pytest.fixture()
def data_dir(tmp_path: Path) -> Path:
    """试算装载目录（unblock-research-validation 起 translate 需
    data_dir——公式组件试算在 data_ranges 首区间最小装载）。"""
    d = tmp_path / "market"
    d.mkdir()
    _df(400).to_parquet(d / "BTCUSDT_4h.parquet")
    return d


def _plugin(db, name="mom", expression="zscore(close, 20)") -> str:
    """注册测试公式插件，返回其信号列名。"""
    return register_formula_plugin(
        db, name=name, expression=expression)["column"]


def _translate(db, when, then=None):
    """条件组直入存活翻译入口（登记域与 translate_hypothesis 均已
    退役——retire-legacy 后 _translate_conditions 行为经 build_signal_genes
    承载；原 _raw_hyp「绕过登记直落假设行」语义由直传条件列表替代）。"""
    return build_signal_genes(
        when_conditions=when, then_conditions=then or [],
        db_path=db, strategy_timeframe="4h")


class TestPluginTranslation:
    def test_plugin_condition_translates_to_formula_source(self, db, data_dir):
        """纯插件列条件：翻译为 formula 源（ref/threshold/operator），
        不产 rule 基因（插件源即全部条件）。"""
        col = _plugin(db)
        genes, sources = _translate(
            db, when=[{"subject": col, "action": "gt", "target": 0.5}])
        assert genes == []
        assert sources == [
            {"type": "formula", "ref": col, "threshold": 0.5, "operator": ">",
             "role": "entry"}]

    def test_mixed_conditions_compose_and(self, db, data_dir):
        """混合假设：内置条件照旧进 rule 基因，插件条件成 formula 源，
        composition logic AND（合取语义，不发明优先级）。"""
        col = _plugin(db)
        genes, sources = _translate(db, when=[
            {"subject": {"indicator": "RSI", "params": {"period": 14}}, "action": "lt", "target": 35},
            {"subject": col, "action": "gt", "target": 0.5},
        ], then=[{"subject": {"indicator": "RSI", "params": {"period": 30}}, "action": "gt", "target": 60}])
        # 内置条件全部进基因（when 入场 + then 出场，语义不变）
        assert [g.field_name for g in genes] == ["rsi", "rsi"]  # N4 语义名
        # build_signal_genes 只返回插件/模型源（rule 源声明属策略组装层，
        # retire-legacy 后组装面退役——断言守护插件源翻译形制本身）
        assert sources == [
            {"type": "formula", "ref": col, "threshold": 0.5, "operator": ">",
             "role": "entry"}]

    @pytest.mark.parametrize(
        ("action", "operator"),
        [("gt", ">"), ("lt", "<"), ("ge", ">="), ("le", "<=")],
    )
    def test_operator_mapping_four_actions(self, db, data_dir, action, operator):
        """四种数值比较 action 的 operator 映射保真（lt/le 不被吞成 >）。"""
        col = _plugin(db)
        _genes, sources = _translate(db, when=[
            {"subject": col, "action": action, "target": 1.25}])
        src = sources[0]
        assert src == {"type": "formula", "ref": col,
                       "threshold": 1.25, "operator": operator,
                       "role": "entry"}

    def test_plugin_cross_above_rejected(self, db, data_dir):
        """穿越类 action 作用于插件列：显式拒绝并指明需数值比较。"""
        col = _plugin(db)
        with pytest.raises(ResearchError, match="插件列暂不支持该 action"):
            _translate(db, when=[
                {"subject": col, "action": "cross_above", "target": 0.5}])

    def test_plugin_non_numeric_target_rejected(self, db, data_dir):
        col = _plugin(db)
        with pytest.raises(ResearchError, match="target 须为数值"):
            _translate(db, when=[{"subject": col, "action": "gt",
                                  "target": "ema_50"}])

    def test_then_plugin_condition_translates_exit_role(self, db, data_dir):
        """（R2 翻转）THEN 插件列放行：产物 signal_sources 含
        role="exit" 条目（出场桶 AND 组合——原「仅支持入场」拒绝随
        卡 F 6.1 放行消亡）。"""
        col = _plugin(db)
        _genes, sources = _translate(
            db, when=[{"subject": {"indicator": "RSI", "params": {"period": 14}}, "action": "lt",
                       "target": 35}],
            then=[{"subject": col, "action": "gt", "target": 0.5}])
        roles = [t.get("role") for t in sources
                 if t.get("type") in ("formula", "model")]
        assert roles == ["exit"], f"出场插件源 role=exit，got {roles}"

    def test_stale_plugin_column_rejected(self, db, data_dir):
        """失效插件列（注册表曾有判定路径但列不存在）：拒绝且文案
        含「插件」与「失效」，区别于基础列/未知列。"""
        with pytest.raises(ResearchError, match="插件列 subject（fx_ghost）已失效"):
            _translate(db, when=[{"subject": "fx_ghost", "action": "gt",
                                  "target": 0.5}])

    @pytest.mark.parametrize("col", [
        "open", "high", "low", "volume", "trades",
        "open_interest", "funding_rate"])
    def test_base_column_subject_rejected_unchanged(self, db, col):
        """B1-T09 回归：其余 7 基础列 subject 维持不可表达、文案
        逐字节不变（变更一 B1 起 close 的 lt/gt+编码列名 target 组合
        已放行——close 移出本拒绝集，新路径覆盖在
        test_price_action_translation.py 同域扩展）。"""
        with pytest.raises(
                ResearchError,
                match=f"内置基础列 subject（{col}）不可表达为"):
            _translate(db, when=[{"subject": col, "action": "lt",
                                  "target": "ema_20"}])

    def test_close_numeric_target_new_message(self, db, data_dir):
        """B1-T07（三段格局第二段）：close+lt/gt+数值 target——
        引导文案改写为「现仅支持编码列名 target」，与 ge/le 新文案
        （T06）可辨；不再落笼统基础列拒绝文案。"""
        with pytest.raises(
                ResearchError,
                match="close 基础列主体现仅支持编码列名 target") as ei:
            _translate(db, when=[{"subject": "close", "action": "gt",
                                  "target": 60000}])
        # team3 reviewer 发现 2 修正：断言对象=实际异常消息（原字面量
        # 自断言恒真）——确认不落笼统基础列文案
        assert "内置基础列" not in ei.value.message

        with pytest.raises(ResearchError, match="字符串 subject.*definitely_not_a_column"):
            _translate(db, when=[{"subject": "definitely_not_a_column",
                                  "action": "gt", "target": 1}])

    def test_bare_indicator_prefix_rejected_as_builtin(self, db, data_dir):
        """指标前缀本身（rsi 无参数后缀）非参数化形制：仍拒绝。"""
        with pytest.raises(ResearchError, match="不可表达"):
            _translate(db, when=[{"subject": "rsi", "action": "gt",
                                  "target": 50}])


class TestAssemblyOperatorCompat:
    """组装器配套：operator 可选比较（默认 ">" 向后兼容）与无规则源
    中性基线。"""

    def _rule_genes(self):
        return [SignalGene("RSI", {"period": 14}, SignalRole.ENTRY_TRIGGER,
                           "rsi_14", {"type": "lt", "threshold": 35})]

    def test_default_operator_backward_compatible(self, db, data_dir):
        """无 operator 字段的源行为不变（">"，既有 weighted_vote 用例
        语义不变）。"""
        col = _plugin(db)
        df = _df()
        dna = StrategyDNA(
            signal_genes=self._rule_genes(),
            execution_genes=ExecutionGenes(timeframe="4h"),
            signal_sources=[{"type": "rule"},
                            {"type": "formula", "ref": col, "threshold": 0.5}],
            composition={"mode": "logic", "op": "and"})
        from core.analysis.plugin_service import enrich_with_plugin_columns
        series = enrich_with_plugin_columns(db, df, {col})[col]
        base = dna_to_signal_set(dna, df)
        sig = assemble_signal_set(db, dna, df)
        expected = base.entries.fillna(False) & (
            series.astype(float) > 0.5).fillna(False)
        assert sig.entries.equals(expected.astype(bool))

    @pytest.mark.parametrize(
        ("operator", "threshold"),
        [(">", 0.5), ("<", -0.5), (">=", 0.25), ("<=", 2.0)],
    )
    def test_operator_compare_fidelity(self, db, operator, threshold):
        """四比较算子在组装器保真（布尔化按源 operator 比较）。"""
        col = _plugin(db)
        df = _df()
        dna = StrategyDNA(
            signal_genes=self._rule_genes(),
            execution_genes=ExecutionGenes(timeframe="4h"),
            signal_sources=[{"type": "rule"},
                            {"type": "formula", "ref": col,
                             "threshold": threshold, "operator": operator}],
            composition={"mode": "logic", "op": "and"})
        from core.analysis.plugin_service import enrich_with_plugin_columns
        series = enrich_with_plugin_columns(db, df, {col})[col]
        base = dna_to_signal_set(dna, df)
        sig = assemble_signal_set(db, dna, df)
        import operator as pyop
        cmp_fn = {">": pyop.gt, "<": pyop.lt,
                  ">=": pyop.ge, "<=": pyop.le}[operator]
        expected = base.entries.fillna(False) & cmp_fn(
            series.astype(float), threshold).fillna(False)
        assert sig.entries.equals(expected.astype(bool))

    def test_pure_plugin_neutral_baseline(self, db, data_dir):
        """无规则源（纯插件条件策略）：AND 单位元基线——entries 即
        插件布尔本身，不被空 rule 基因的全 False 静默作废。"""
        col = _plugin(db)
        df = _df()
        dna = StrategyDNA(
            signal_genes=[],
            execution_genes=ExecutionGenes(timeframe="4h"),
            signal_sources=[{"type": "formula", "ref": col,
                             "threshold": 0.5, "operator": ">"}],
            composition={"mode": "logic", "op": "and"})
        from core.analysis.plugin_service import enrich_with_plugin_columns
        series = enrich_with_plugin_columns(db, df, {col})[col]
        sig = assemble_signal_set(db, dna, df)
        expected = (series.astype(float) > 0.5).fillna(False)
        assert sig.entries.equals(expected.astype(bool))
        assert bool(sig.entries.any()), "纯插件策略不得被静默作废成永不入场"


class TestGeneSignatureCoversSources:
    """签名修复：signal_sources/composition 必须进签名——否则混合产物
    与同基因纯 rule 策略同签名（translate 同签名分流/库去重失真）。"""

    def _dna(self, **kw):
        genes = [SignalGene("RSI", {"period": 14}, SignalRole.ENTRY_TRIGGER,
                            "rsi_14", {"type": "lt", "threshold": 35})]
        base = dict(
            signal_genes=genes, logic_genes=LogicGenes(),
            execution_genes=ExecutionGenes(timeframe="4h"),
            risk_genes=RiskGenes())
        base.update(kw)
        return StrategyDNA(**base)

    def test_plugin_source_changes_signature(self):
        rule_only = self._dna(signal_sources=[{"type": "rule"}])
        legacy = self._dna()  # 无 signal_sources 字段（存量形制）
        mixed = self._dna(
            signal_sources=[{"type": "rule"},
                            {"type": "formula", "ref": "fx_a",
                             "threshold": 0.5, "operator": ">"}],
            composition={"mode": "logic", "op": "and"})
        assert rule_only.gene_signature == legacy.gene_signature, \
            "退化形态签名零迁移（无插件源不追加任何部分）"
        assert mixed.gene_signature != rule_only.gene_signature, \
            "同内置基因 + 插件源 ≠ 纯 rule 策略（防同签名分流误判）"

    def test_source_details_change_signature(self):
        kwargs = dict(signal_sources=[{"type": "rule"},
                                      {"type": "formula", "ref": "fx_a",
                                       "threshold": 0.5, "operator": ">"}],
                      composition={"mode": "logic", "op": "and"})
        a = self._dna(**kwargs)
        other_ref = self._dna(signal_sources=[
            {"type": "rule"},
            {"type": "formula", "ref": "fx_b",
             "threshold": 0.5, "operator": ">"}],
            composition={"mode": "logic", "op": "and"})
        other_op = self._dna(signal_sources=[
            {"type": "rule"},
            {"type": "formula", "ref": "fx_a",
             "threshold": 0.5, "operator": "<="}],
            composition={"mode": "logic", "op": "and"})
        assert len({a.gene_signature, other_ref.gene_signature,
                    other_op.gene_signature}) == 3, \
            "ref/threshold/operator 任一不同即不同基因型"

