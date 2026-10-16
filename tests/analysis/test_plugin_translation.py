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

from core.analysis.hypothesis_service import (
    ResearchError, register_hypothesis)
from core.analysis.hypothesis_to_strategy import translate_hypothesis
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


def _hyp(db, when, then=None) -> str:
    """经登记域登记假设（subject 校验生效——合法列用例走这里）。"""
    conditions = {"when_conditions": when, "then_conditions": then or []}
    return register_hypothesis(db, conditions=conditions, statement="s",
                               data_ranges=_ranges())["hypothesis_id"]


def _raw_hyp(db, when, then=None) -> str:
    """绕过登记校验直落假设行（失效插件列/未知列等登记不可能的形态）。"""
    hid = f"hyp_{uuid.uuid4().hex[:12]}"
    conds = {"when_conditions": when, "then_conditions": then or []}
    rr.save_hypothesis_with_log(
        db, hypothesis_id=hid,
        conditions=json.dumps(conds, ensure_ascii=False),
        conditions_hash=uuid.uuid4().hex, statement="s",
        data_ranges=json.dumps(_ranges(), ensure_ascii=False),
        ranges_hash=uuid.uuid4().hex)
    return hid


def _dna_of(db, strategy_id) -> StrategyDNA:
    return StrategyDNA.from_json(get_strategy(db, strategy_id)["dna_json"])


class TestPluginTranslation:
    def test_plugin_condition_translates_to_formula_source(self, db, data_dir):
        """纯插件列假设：条件翻译为 formula 源（ref/threshold/operator），
        不产 rule 基因，组合层 logic and（插件源即全部条件）。"""
        col = _plugin(db)
        # 登记域强制 then 非空，纯插件最小形态（when=[插件]/then=[]）
        # 经直落假设行构造——翻译器输入契约是库内假设行
        hid = _raw_hyp(db, when=[{"subject": col, "action": "gt", "target": 0.5}])
        out = translate_hypothesis(db, data_dir=data_dir, hypothesis_id=hid)
        assert out["status"] == "draft"
        dna = _dna_of(db, out["strategy_id"])
        assert dna.signal_genes == []
        assert dna.signal_sources == [
            {"type": "formula", "ref": col, "threshold": 0.5, "operator": ">"}]
        assert dna.composition == {"mode": "logic", "op": "and"}
        assert hid in dna.parent_ids, "谱系回链"

    def test_mixed_conditions_compose_and(self, db, data_dir):
        """混合假设：内置条件照旧进 rule 基因，插件条件成 formula 源，
        composition logic AND（合取语义，不发明优先级）。"""
        col = _plugin(db)
        hid = _hyp(db, when=[
            {"subject": {"indicator": "RSI", "params": {"period": 14}}, "action": "lt", "target": 35},
            {"subject": col, "action": "gt", "target": 0.5},
        ], then=[{"subject": {"indicator": "RSI", "params": {"period": 30}}, "action": "gt", "target": 60}])
        out = translate_hypothesis(db, data_dir=data_dir, hypothesis_id=hid)
        dna = _dna_of(db, out["strategy_id"])
        # 内置条件全部进基因（when 入场 + then 出场，语义不变）
        assert [g.field_name for g in dna.signal_genes] == ["rsi", "rsi"]  # N4 语义名
        assert dna.signal_sources == [
            {"type": "rule"},
            {"type": "formula", "ref": col, "threshold": 0.5, "operator": ">"}]
        assert dna.composition == {"mode": "logic", "op": "and"}

    @pytest.mark.parametrize(
        ("action", "operator"),
        [("gt", ">"), ("lt", "<"), ("ge", ">="), ("le", "<=")],
    )
    def test_operator_mapping_four_actions(self, db, data_dir, action, operator):
        """四种数值比较 action 的 operator 映射保真（lt/le 不被吞成 >）。"""
        col = _plugin(db)
        hid = _raw_hyp(db, when=[
            {"subject": col, "action": action, "target": 1.25}])
        out = translate_hypothesis(db, data_dir=data_dir, hypothesis_id=hid)
        src = _dna_of(db, out["strategy_id"]).signal_sources[0]
        assert src == {"type": "formula", "ref": col,
                       "threshold": 1.25, "operator": operator}

    def test_plugin_cross_above_rejected(self, db, data_dir):
        """穿越类 action 作用于插件列：显式拒绝并指明需数值比较。"""
        col = _plugin(db)
        hid = _raw_hyp(db, when=[
            {"subject": col, "action": "cross_above", "target": 0.5}])
        with pytest.raises(ResearchError, match="插件列暂不支持该 action"):
            translate_hypothesis(db, data_dir=data_dir, hypothesis_id=hid)

    def test_plugin_non_numeric_target_rejected(self, db, data_dir):
        col = _plugin(db)
        hid = _raw_hyp(db, when=[{"subject": col, "action": "gt",
                                  "target": "ema_50"}])
        with pytest.raises(ResearchError, match="target 须为数值"):
            translate_hypothesis(db, data_dir=data_dir, hypothesis_id=hid)

    def test_then_plugin_condition_rejected(self, db, data_dir):
        """THEN 插件列显式拒绝（出场组合待后续版本；不静默误译为
        入场门控——出场语义颠倒比拒绝更糟）。"""
        col = _plugin(db)
        hid = _hyp(db, when=[{"subject": {"indicator": "RSI", "params": {"period": 14}}, "action": "lt",
                              "target": 35}],
                   then=[{"subject": {"indicator": "RSI", "params": {"period": 30}}, "action": "gt", "target": 60}])
        # THEN 插件列假设（then=插件列）经直落构造
        hid = _raw_hyp(db, when=[{"subject": {"indicator": "RSI", "params": {"period": 14}}, "action": "lt",
                                  "target": 35}],
                       then=[{"subject": col, "action": "gt", "target": 0.5}])
        # 0.4.2 R3 文案泛化（覆盖分批组）："暂不支持出场"→"仅支持入场"
        with pytest.raises(ResearchError, match="then_conditions.*仅支持\n?\s*入场信号源"):
            translate_hypothesis(db, data_dir=data_dir, hypothesis_id=hid)

    def test_stale_plugin_column_rejected(self, db, data_dir):
        """失效插件列（注册表曾有判定路径但列不存在）：拒绝且文案
        含「插件」与「失效」，区别于基础列/未知列。"""
        hid = _raw_hyp(db, when=[{"subject": "fx_ghost", "action": "gt",
                                  "target": 0.5}])
        with pytest.raises(ResearchError, match="插件列 subject（fx_ghost）已失效"):
            translate_hypothesis(db, data_dir=data_dir, hypothesis_id=hid)

    @pytest.mark.parametrize("col", [
        "open", "high", "low", "volume", "trades",
        "open_interest", "funding_rate"])
    def test_base_column_subject_rejected_unchanged(self, db, col):
        """B1-T09 回归：其余 7 基础列 subject 维持不可表达、文案
        逐字节不变（变更一 B1 起 close 的 lt/gt+编码列名 target 组合
        已放行——close 移出本拒绝集，新路径覆盖在
        test_price_action_translation.py 同域扩展）。"""
        hid = _raw_hyp(db, when=[{"subject": col, "action": "lt",
                                  "target": "ema_20"}])
        with pytest.raises(
                ResearchError,
                match=f"内置基础列 subject（{col}）不可表达为"):
            translate_hypothesis(db, data_dir=data_dir, hypothesis_id=hid)

    def test_close_numeric_target_new_message(self, db, data_dir):
        """B1-T07（三段格局第二段）：close+lt/gt+数值 target——
        引导文案改写为「现仅支持编码列名 target」，与 ge/le 新文案
        （T06）可辨；不再落笼统基础列拒绝文案。"""
        hid = _raw_hyp(db, when=[{"subject": "close", "action": "gt",
                                  "target": 60000}])
        with pytest.raises(
                ResearchError,
                match="close 基础列主体现仅支持编码列名 target") as ei:
            translate_hypothesis(db, data_dir=data_dir, hypothesis_id=hid)
        # team3 reviewer 发现 2 修正：断言对象=实际异常消息（原字面量
        # 自断言恒真）——确认不落笼统基础列文案
        assert "内置基础列" not in ei.value.message

        hid2 = _raw_hyp(db, when=[{"subject": "definitely_not_a_column",
                                   "action": "gt", "target": 1}])
        with pytest.raises(ResearchError, match="字符串 subject.*definitely_not_a_column"):
            translate_hypothesis(db, data_dir=data_dir, hypothesis_id=hid2)

    def test_bare_indicator_prefix_rejected_as_builtin(self, db, data_dir):
        """指标前缀本身（rsi 无参数后缀）非参数化形制：仍拒绝。"""
        hid = _raw_hyp(db, when=[{"subject": "rsi", "action": "gt",
                                  "target": 50}])
        with pytest.raises(ResearchError, match="不可表达"):
            translate_hypothesis(db, data_dir=data_dir, hypothesis_id=hid)


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


class TestComponentTrialAndEvidence:
    """T7/T7f'/T8：组件试算+三命中点补试算+门禁双源
    （unblock-research-validation B 段）。"""

    def _component_logs(self, db, ref_id=None):
        return [e for e in rr.list_research_log(
            db, entry_type="component_eval", limit=200)
            if ref_id is None or e.get("ref_id") == ref_id]

    def test_t7b_record_shape(self, db, data_dir):
        """T7b 记录形制逐键（契约形制——逐组件一条）：单组件场景
        logs 恰 1 条，entry_type/ref_id/summary.column（单键）/
        source/evaluated_bars>0。"""
        col = _plugin(db)
        hid = _raw_hyp(db, when=[{"subject": col, "action": "gt",
                                  "target": 0.5}])
        out = translate_hypothesis(db, data_dir=data_dir, hypothesis_id=hid)
        logs = self._component_logs(db, out["strategy_id"])
        assert logs, "翻译过=有记录（不变式）"
        assert len(logs) == 1, f"单组件应逐组件一条，got {len(logs)}"
        summary = json.loads(logs[0]["summary"])
        assert summary["column"] == col
        assert summary["source"] == "formula"
        assert summary["evaluated_bars"] > 0

    def test_t7c_pure_rule_zero_log(self, db, data_dir):
        """T7c 纯 rule 产物零留痕（无 formula 组件不试算）。"""
        hid = _hyp(db, when=[{"subject": {"indicator": "RSI",
                                          "params": {"period": 14}},
                              "action": "lt", "target": 35}],
                   then=[{"subject": {"indicator": "RSI",
                                      "params": {"period": 30}},
                          "action": "gt", "target": 60}])
        translate_hypothesis(db, data_dir=data_dir, hypothesis_id=hid)
        assert self._component_logs(db) == []

    def test_t7d_failed_trial_rejects_translation(self, db, data_dir):
        """T7d 试算失败翻译整体拒（坏表达式组件——zscore 窗口参数
        越界形（表达式合法注册但求值崩）不可求值的 draft 是死策略）。"""
        # 表达式静态校验面很全（未知标识符/窗口越界注册即拒）——真实
        # 失败面经坏数据构造：parquet 缺 close 列（表达式引用 close，
        # 注册静态校验通过、试算装载求值崩）
        bad_dir = data_dir.parent / "bad_market"
        bad_dir.mkdir()
        bad_df = _df(60).drop(columns=["close"])
        bad_df.to_parquet(bad_dir / "BTCUSDT_4h.parquet")
        col = _plugin(db)  # zscore(close, 20)——静态合法
        hid = _raw_hyp(db, when=[{"subject": col, "action": "gt",
                                  "target": 0.5}])
        with pytest.raises(ResearchError) as ei:
            translate_hypothesis(db, data_dir=bad_dir, hypothesis_id=hid)
        assert ei.value.code == "RESEARCH_CONDITION_INVALID"

    def test_t7h_range_unavailable_rejects(self, db, tmp_path):
        """T7h 区间不可得拒（data_dir=None 附 reason）。"""
        col = _plugin(db)
        hid = _raw_hyp(db, when=[{"subject": col, "action": "gt",
                                  "target": 0.5}])
        with pytest.raises(ResearchError) as ei:
            translate_hypothesis(db, hypothesis_id=hid)  # 不传 data_dir
        assert ei.value.code == "RESEARCH_CONDITION_INVALID"
        assert ei.value.detail["reason"] == "data_dir_unavailable"

    def test_t7f_prime_three_hitpoints_backfill(self, db, data_dir):
        """T7f' 三命中点补试算：存量 draft（试算上线前形态——抹掉
        component_eval 记录模拟）经谱系幂等重译补记录。"""
        col = _plugin(db)
        hid = _raw_hyp(db, when=[{"subject": col, "action": "gt",
                                  "target": 0.5}])
        out = translate_hypothesis(db, data_dir=data_dir, hypothesis_id=hid)
        sid = out["strategy_id"]
        assert self._component_logs(db, sid)  # 首译已写
        # 模拟存量：直删日志行（无 update——构造无记录态重演）
        from core.persistence.repository.research import _engine_for, ResearchLog
        from sqlalchemy import delete
        with _engine_for(db).begin() as conn:
            conn.execute(delete(ResearchLog).where(
                ResearchLog.entry_type == "component_eval"))
        assert self._component_logs(db) == []
        replay = translate_hypothesis(db, data_dir=data_dir,
                                      hypothesis_id=hid)  # 谱系幂等命中点
        assert replay["strategy_id"] == sid
        assert self._component_logs(db, sid), "命中点补试算写记录"

    def test_t8a_source_releases_gate(self, db, data_dir):
        """T8a 门禁 (a) 源放行：翻译即产 component_eval 记录 →
        组合策略验证不被组件证据门禁拦截（原 GAP-09 死锁解除锚）。"""
        col = _plugin(db)
        hid = _raw_hyp(db, when=[{"subject": col, "action": "gt",
                                  "target": 0.5}])
        out = translate_hypothesis(db, data_dir=data_dir, hypothesis_id=hid)
        from core.analysis.validation_service import _missing_component_evidence
        missing = _missing_component_evidence(db, [
            {"type": "formula", "ref": col}])
        assert missing == [], f"(a) 源应放行，missing={missing}"

    def test_t8_both_missing_blocked(self, db, tmp_path):
        """T8 双无拦截：全新未注册执行列（无记录无 component pass）
        ——手工 DNA 直查门禁。"""
        from core.analysis.validation_service import _missing_component_evidence
        missing = _missing_component_evidence(db, [
            {"type": "formula", "ref": "fx_never_evaluated"}])
        assert missing == ["fx_never_evaluated"]

    def test_t8b_source_releases_and_precise_match(self, db, data_dir):
        """T8 (b) 源放行+精确匹配边界：手工 component pass 记录命中
        foo；foo_v2 前缀相似仍拦。"""
        from core.analysis.validation_service import _missing_component_evidence
        rr.save_validation_with_log(
            db, record_id="rec_comp_b", hypothesis_id="adhoc",
            strategy_id="st_x", level="component",
            params=json.dumps({"column": "foo"}),
            result=None, verdict="pass", range_hash="rh_b")
        assert _missing_component_evidence(
            db, [{"type": "formula", "ref": "foo"}]) == []
        assert _missing_component_evidence(
            db, [{"type": "formula", "ref": "foo_v2"}]) == ["foo_v2"]

    def test_t8_verdict_pass_filter_kept(self, db, tmp_path):
        """T8 verdict=pass 过滤保持：fail 的 component 记录不算证据。"""
        from core.analysis.validation_service import _missing_component_evidence
        rr.save_validation_with_log(
            db, record_id="rec_comp_fail", hypothesis_id="adhoc",
            strategy_id="st_x", level="component",
            params=json.dumps({"column": "bar"}),
            result=None, verdict="fail", range_hash="rh_f")
        assert _missing_component_evidence(
            db, [{"type": "formula", "ref": "bar"}]) == ["bar"]
