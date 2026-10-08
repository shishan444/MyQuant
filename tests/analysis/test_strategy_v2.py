"""策略表达 v2 与转化器测试（add-ai-research-mcp 6.1-6.5）。"""

import json
import subprocess
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

pytestmark = [pytest.mark.integration]

from core.analysis.hypothesis_service import ResearchError, register_hypothesis
from core.analysis.hypothesis_to_strategy import translate_hypothesis
from core.analysis.signal_assembly import assemble_signal_set
from core.analysis.validation_service import run_isolated_backtest
from core.evolution.operators import mutate_composition
from core.persistence.db_ext import init_db_ext, save_strategy
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


class TestMutateComposition:
    def test_noop_on_degenerate(self):
        dna = _rule_dna()
        out = mutate_composition(dna)
        assert out.to_json() == dna.to_json()

    def test_composition_gene_mutates_legally(self):
        dna = StrategyDNA(
            signal_genes=_rule_dna().signal_genes,
            signal_sources=[{"type": "rule"}, {"type": "formula", "ref": "fx_a",
                                               "weight": 1.0}],
            composition={"mode": "weighted_vote", "weights": {}, "threshold": 0.5})
        for _ in range(30):
            child = mutate_composition(dna)
            if child.composition and "threshold" in child.composition:
                assert 0.0 <= child.composition["threshold"] <= 1.0
            for s in child.signal_sources or []:
                if "weight" in s:
                    assert 0.0 <= s["weight"] <= 2.0
            # 子代可解析（合法 DNA）
            StrategyDNA.from_json(child.to_json())


class TestTranslator:
    def _register_and_pass(self, db, conditions, ranges):
        hid = register_hypothesis(db, conditions=conditions, statement="s",
                                  data_ranges=ranges)["hypothesis_id"]
        rr.save_validation_with_log(
            db, record_id=f"rec_{hid[:8]}", hypothesis_id=hid,
            strategy_id="st_x", level="isolated_backtest",
            params="{}", result=None, verdict="pass", range_hash=f"rh_{hid[:6]}")
        return hid

    def _expressible_conditions(self):
        # THEN 参与翻译（M4：预期兑现即离场）——须同为指标列形制
        return {
            "when_conditions": [
                {"subject": {"indicator": "RSI", "params": {"period": 14}}, "action": "lt", "target": 35},
                {"subject": {"indicator": "EMA", "params": {"period": 50}}, "action": "cross_above", "target": 60000},
            ],
            "then_conditions": [{"subject": {"indicator": "RSI", "params": {"period": 30}}, "action": "gt", "target": 60}],
        }

    def _ranges(self, symbol="BTCUSDT"):
        return [{"symbol": symbol, "timeframe": "4h",
                 "start": "2024-01-01T00:00:00Z", "end": "2024-02-01T00:00:00Z"}]

    def test_expressible_translates(self, db):
        hid = self._register_and_pass(db, self._expressible_conditions(),
                                      self._ranges())
        out = translate_hypothesis(db, hypothesis_id=hid)
        assert out["duplicated"] is False
        assert out["status"] == "draft"
        assert out["risk_genes"] == "platform_default"
        from core.persistence.db_ext import get_strategy
        row = get_strategy(db, out["strategy_id"])
        assert row["status"] == "draft"
        dna = StrategyDNA.from_json(row["dna_json"])
        assert hid in dna.parent_ids, "谱系回链"

    def test_unvalidated_translates_to_draft(self, db):
        """M4 语义反转：无 pass 记录也可转化（构建权与出货权拆开——
        pass 前置由转正门承载，转化即验证资格）。"""
        hid = register_hypothesis(db, conditions=self._expressible_conditions(),
                                  statement="s", data_ranges=self._ranges())["hypothesis_id"]
        out = translate_hypothesis(db, hypothesis_id=hid)
        assert out["duplicated"] is False and out["status"] == "draft"

    def test_terminal_hypothesis_rejected(self, db):
        """终态假设的转化被拒（提示登记新假设）。"""
        hid = self._register_and_pass(db, self._expressible_conditions(),
                                      self._ranges())
        from core.analysis.hypothesis_service import transition_hypothesis
        transition_hypothesis(db, hid, target_status="supported",
                              evidence_ref=f"rec_{hid[:8]}")
        with pytest.raises(ResearchError, match="已终态"):
            translate_hypothesis(db, hypothesis_id=hid)

    def test_then_translated_to_exit_trigger(self, db):
        """THEN 条件翻译为出场信号（预期兑现即离场）。"""
        hid = self._register_and_pass(db, self._expressible_conditions(),
                                      self._ranges())
        out = translate_hypothesis(db, hypothesis_id=hid)
        from core.persistence.db_ext import get_strategy
        from core.strategy.dna import SignalRole
        genes = StrategyDNA.from_json(
            get_strategy(db, out["strategy_id"])["dna_json"]).signal_genes
        exits = [g for g in genes if g.role == SignalRole.EXIT_TRIGGER]
        assert len(exits) == 1
        assert exits[0].field_name == "rsi"   # N4: 语义名（非编码前缀形）

    def test_then_inexpressible_rejected(self, db):
        """THEN 不可表达显式拒绝（不静默丢弃 THEN 语义）。"""
        conds = {
            "when_conditions": [{"subject": {"indicator": "RSI", "params": {"period": 14}}, "action": "lt", "target": 35}],
            "then_conditions": [{"subject": "close", "action": "gt", "target": 0}],
        }
        hid = self._register_and_pass(db, conds, self._ranges())
        with pytest.raises(ResearchError, match="then_conditions"):
            translate_hypothesis(db, hypothesis_id=hid)

    def test_same_signature_active_split(self, db):
        """转化产物与既有 active 策略同签名：显式返回既有 id 并标注，
        不静默不落库（防幽灵 id）。"""
        from core.persistence.db_ext import (
            delete_strategy, get_strategy, save_strategy)
        # 先转化取 DNA，清掉 draft 后以同 DNA 存 active（模拟已出货）
        conds = self._expressible_conditions()
        hid1 = self._register_and_pass(db, conds, self._ranges())
        out1 = translate_hypothesis(db, hypothesis_id=hid1)
        dna_json = get_strategy(db, out1["strategy_id"])["dna_json"]
        delete_strategy(db, out1["strategy_id"])
        save_strategy(db, strategy_id="st_active_same",
                      dna_json=dna_json, symbol="BTCUSDT", timeframe="4h")
        # 另一假设同 conditions → 同签名 → 分流返回既有 active
        ranges2 = [{"symbol": "BTCUSDT", "timeframe": "4h",
                    "start": "2024-03-01T00:00:00Z", "end": "2024-04-01T00:00:00Z"}]
        hid2 = self._register_and_pass(db, self._expressible_conditions(), ranges2)
        out2 = translate_hypothesis(db, hypothesis_id=hid2)
        assert out2["same_dna_existing"] is True
        assert out2["strategy_id"] == "st_active_same"

    def test_cross_symbol_rejected(self, db):
        """0.2.14 收紧：跨 symbol 区间在登记入口即拒（data_ranges 单
        symbol 单 timeframe——基准周期唯一）；转化器「跨币种」防线
        成为不可达的防御纵深（存量实测零跨 symbol 假设，零迁移面）。"""
        ranges = self._ranges() + self._ranges(symbol="ETHUSDT")
        with pytest.raises(ResearchError, match="基准周期唯一"):
            self._register_and_pass(db, self._expressible_conditions(), ranges)

    def test_inexpressible_action_rejected(self, db):
        conds = {
            "when_conditions": [{"subject": {"indicator": "RSI", "params": {"period": 14}}, "action": "divergence_top",
                                 "target": 0}],
            "then_conditions": [{"subject": {"indicator": "RSI", "params": {"period": 30}}, "action": "gt", "target": 60}],
        }
        hid = self._register_and_pass(db, conds, self._ranges())
        with pytest.raises(ResearchError, match="action 不可表达"):
            translate_hypothesis(db, hypothesis_id=hid)

    @pytest.mark.parametrize(
        ("action", "expected_condition"),
        [
            pytest.param("gt", {"type": "gt", "threshold": 35.0}, id="gt 动作保真"),
            pytest.param("lt", {"type": "lt", "threshold": 35.0}, id="lt 动作保真"),
            pytest.param("ge", {"type": "ge", "threshold": 35.0}, id="ge 动作保真"),
            pytest.param("le", {"type": "le", "threshold": 35.0}, id="le 动作保真"),
            pytest.param("cross_above", {"type": "cross_above", "threshold": 35.0}, id="cross_above 动作保真"),
            pytest.param("cross_below", {"type": "cross_below", "threshold": 35.0}, id="cross_below 动作保真"),
            pytest.param("breakout", {"type": "cross_above", "threshold": 35.0}, id="breakout 映射 cross_above"),
            pytest.param("breakdown", {"type": "cross_below", "threshold": 35.0}, id="breakdown 映射 cross_below"),
        ],
    )
    def test_translation_field_fidelity(self, db, action, expected_condition):
        """M2/1.6 翻译保真（8 动作全覆盖）：产物基因的条件值与登记
        conditions 逐字段一致——篡改任一映射时对应行红。"""
        conds = {
            "when_conditions": [{"subject": {"indicator": "RSI", "params": {"period": 14}}, "action": action, "target": 35}],
            "then_conditions": [{"subject": {"indicator": "RSI", "params": {"period": 30}}, "action": "gt", "target": 60}],
        }
        hid = self._register_and_pass(db, conds, self._ranges())
        out = translate_hypothesis(db, hypothesis_id=hid)
        from core.persistence.db_ext import get_strategy
        from core.strategy.dna import SignalRole
        genes = StrategyDNA.from_json(get_strategy(db, out["strategy_id"])["dna_json"]).signal_genes
        entries = [g for g in genes if g.role == SignalRole.ENTRY_TRIGGER]
        assert len(entries) == 1
        assert entries[0].condition == expected_condition
        assert entries[0].params == {"period": 14}
        assert entries[0].field_name == "rsi"   # N4: 语义名（非编码前缀形）

    def test_statement_wording_does_not_change_product(self, db):
        """M2 补充：仅改 statement 措辞，产物 genes 逐位相同。"""
        conds = self._expressible_conditions()
        hid1 = register_hypothesis(db, conditions=conds, statement="措辞甲",
                                   data_ranges=self._ranges())["hypothesis_id"]
        # 同 conditions 同 ranges → 幂等命中同一假设（statement 不参与身份）
        hid2 = register_hypothesis(db, conditions=conds, statement="完全不同的措辞乙",
                                   data_ranges=self._ranges())["hypothesis_id"]
        assert hid2 == hid1, "statement 措辞不得改变假设身份（幂等键不含措辞）"

    def test_idempotent_and_statement_ignored(self, db):
        """同假设重复转化返回既有（幂等）；statement 措辞差异不影响产物。"""
        conds = self._expressible_conditions()
        hid = self._register_and_pass(db, conds, self._ranges())
        out1 = translate_hypothesis(db, hypothesis_id=hid)
        out2 = translate_hypothesis(db, hypothesis_id=hid)
        assert out2["duplicated"] is True
        assert out2["strategy_id"] == out1["strategy_id"]


class TestCompositeGate:
    """6.5：组合策略加严门（分量证据缺失被拒 + 收紧门槛生效）。"""

    def _env(self, db, tmp_path, with_component_evidence=False):
        data_dir = tmp_path / "market"
        data_dir.mkdir(exist_ok=True)
        # 数据起点前移：请求区间（2/1）前须有 >= 200 bar warmup
        rng = np.random.default_rng(6)
        idx = pd.date_range("2023-11-01", periods=900, freq="4h", tz="UTC",
                            name="timestamp")
        close = 60000 + np.cumsum(rng.normal(0, 110, 900))
        pd.DataFrame({"open": close, "high": close + 60, "low": close - 60,
                      "close": close, "volume": rng.uniform(100, 800, 900),
                      "trades": 40}, index=idx).to_parquet(
            data_dir / "BTCUSDT_4h.parquet")

        from core.analysis.plugin_service import register_formula_plugin
        register_formula_plugin(db, name="gate_fx", expression="zscore(close, 20)")
        from core.analysis.registry_meta import available_signal_columns
        col = next(c["column_key"] for c in available_signal_columns(db)
                   if c["source"] == "formula")

        if with_component_evidence:
            rr.save_validation_with_log(
                db, record_id="rec_comp", hypothesis_id="adhoc",
                strategy_id=None, level="component",
                params=json.dumps({"column": col}), result=None,
                verdict="pass", range_hash="rh_comp")

        hid = register_hypothesis(
            db,
            conditions={
                "when_conditions": [{"subject": {"indicator": "RSI", "params": {"period": 14}}, "action": "lt", "target": 35}],
                "then_conditions": [{"subject": "close", "action": "gt", "target": 0}],
            },
            statement="s", data_ranges=[
                {"symbol": "BTCUSDT", "timeframe": "4h",
                 "start": "2024-01-01T00:00:00Z", "end": "2024-02-01T00:00:00Z"}]
        )["hypothesis_id"]

        dna = StrategyDNA(
            signal_genes=[SignalGene("RSI", {"period": 14}, SignalRole.ENTRY_TRIGGER,
                                     "rsi_14", {"type": "lt", "threshold": 35})],
            execution_genes=ExecutionGenes(timeframe="4h"),
            signal_sources=[{"type": "rule"}, {"type": "formula", "ref": col}],
            composition={"mode": "gate"})
        save_strategy(db, strategy_id="st_v2", dna_json=dna.to_json(),
                      symbol="BTCUSDT", timeframe="4h")
        # 对照版：同 rule 基因、去除插件源（对照法断言用）
        rule_only = StrategyDNA(
            signal_genes=[SignalGene("RSI", {"period": 14}, SignalRole.ENTRY_TRIGGER,
                                     "rsi_14", {"type": "lt", "threshold": 35})],
            execution_genes=ExecutionGenes(timeframe="4h"))
        save_strategy(db, strategy_id="st_rule_only", dna_json=rule_only.to_json(),
                      symbol="BTCUSDT", timeframe="4h")
        return hid, data_dir, col

    def test_missing_component_evidence_rejected(self, db, tmp_path):
        hid, data_dir, col = self._env(db, tmp_path)
        with pytest.raises(ResearchError) as ei:
            run_isolated_backtest(db, data_dir, hypothesis_id=hid,
                                  strategy_id="st_v2",
                                  request_range={"symbol": "BTCUSDT", "timeframe": "4h",
                                                 "start": "2024-02-01T00:00:00Z",
                                                 "end": "2024-02-20T00:00:00Z"})
        assert ei.value.code == "RESEARCH_COMPONENT_EVIDENCE_MISSING"
        assert col in str(ei.value.detail)

    def test_with_evidence_runs_tightened(self, db, tmp_path):
        hid, data_dir, col = self._env(db, tmp_path, with_component_evidence=True)
        out = run_isolated_backtest(db, data_dir, hypothesis_id=hid,
                                    strategy_id="st_v2",
                                    request_range={"symbol": "BTCUSDT", "timeframe": "4h",
                                                   "start": "2024-02-01T00:00:00Z",
                                                   "end": "2024-02-20T00:00:00Z"})
        assert out["duplicated"] is False
        # 组合门槛收紧生效：回撤上限 0.25/1.2 < 0.25（收紧 profile 名可见）
        records = rr.list_validation_records(db, hypothesis_id=hid)
        rec = next(r for r in records if r["record_id"] == out["record_id"])
        assert json.loads(rec["params"])["threshold_profile"].startswith("default_tightened")
        assert json.loads(rec["components"]) == [col]
        # 对照法（验收实质化，3.2）：同区间「去除插件源的同构策略」交易数
        # 须不同——插件源真实参与信号；静默跳过时两数相等
        from core.analysis.validation_service import run_sandbox_backtest
        rng_kw = dict(symbol="BTCUSDT", timeframe="4h",
                      start="2024-02-01T00:00:00Z", end="2024-02-20T00:00:00Z")
        mixed = run_sandbox_backtest(db, data_dir, strategy_id="st_v2", **rng_kw)
        rule_only = run_sandbox_backtest(db, data_dir,
                                         strategy_id="st_rule_only", **rng_kw)
        assert rule_only["metrics"]["total_trades"] > 0, "对照版须有交易"
        assert (mixed["metrics"]["total_trades"]
                != rule_only["metrics"]["total_trades"]), "插件源须影响 entries"


class TestGapClosureTranslator:
    """阶段四盲区消解：翻译器拒绝面与 draft 命中分支。"""

    def test_假设不存在_404(self, db) -> None:
        with pytest.raises(ResearchError) as ei:
            translate_hypothesis(db, hypothesis_id="hyp_ghost")
        assert ei.value.code == "RESEARCH_HYPOTHESIS_NOT_FOUND"
        assert ei.value.status == 404

    def test_无可表达条件_NOT_EXPRESSIBLE(self, db) -> None:
        """全部条件不可表达（action 族外）→ 314 拒。"""
        hid = register_hypothesis(
            db,
            conditions={
                "when_conditions": [
                    {"subject": "close", "action": "within_pct",
                     "target": 5}],
                "then_conditions": [
                    {"subject": "close", "action": "within_pct",
                     "target": 5}],
            },
            statement="全不可表达",
            data_ranges=[{"symbol": "BTCUSDT", "timeframe": "4h",
                          "start": "2024-01-01T00:00:00Z",
                          "end": "2024-02-01T00:00:00Z"}])["hypothesis_id"]
        with pytest.raises(ResearchError) as ei:
            translate_hypothesis(db, hypothesis_id=hid)
        assert ei.value.code == "RESEARCH_NOT_EXPRESSIBLE"

    def test_when带不可表达action_拒(self, db) -> None:
        hid = register_hypothesis(
            db,
            conditions={
                "when_conditions": [
                    {"subject": "close", "action": "within_pct",
                     "target": 5}],
                "then_conditions": [
                    {"subject": "close", "action": "gt", "target": 0}],
            },
            statement="when 不可表达",
            data_ranges=[{"symbol": "BTCUSDT", "timeframe": "4h",
                          "start": "2024-01-01T00:00:00Z",
                          "end": "2024-02-01T00:00:00Z"}])["hypothesis_id"]
        with pytest.raises(ResearchError, match="不可表达"):
            translate_hypothesis(db, hypothesis_id=hid)


class TestGapClosureDraftHit:
    """第三批：同 DNA 已有 draft 的命中返回分支。"""

    def test_同DNA不同区间_第二次翻译命中既有draft(self, db) -> None:
        cond = {"when_conditions": [
            {"subject": {"indicator": "RSI", "params": {"period": 14}},
             "action": "lt", "target": 45}],
            "then_conditions": [
                {"subject": {"indicator": "EMA", "params": {"period": 10}},
                 "action": "gt", "target": 0}]}
        hid_a = register_hypothesis(
            db, conditions=cond, statement="A",
            data_ranges=[_R(1, 15)])["hypothesis_id"]
        first = translate_hypothesis(db, hypothesis_id=hid_a)
        assert first["status"] == "draft"
        # 同条件不同区间 → 新假设 B（DNA 相同）
        hid_b = register_hypothesis(
            db, conditions=cond, statement="B",
            data_ranges=[_R(20, 28)])["hypothesis_id"]
        assert hid_b != hid_a
        second = translate_hypothesis(db, hypothesis_id=hid_b)
        assert second["status"] == "draft"
        assert second["duplicated"] is True
        assert second["strategy_id"] == first["strategy_id"]


def _R(d1, d2):
    return {"symbol": "BTCUSDT", "timeframe": "4h",
            "start": f"2024-01-{d1:02d}T00:00:00Z",
            "end": f"2024-01-{d2:02d}T00:00:00Z"}


class TestTranslatorTriggerPolicy:
    """带 policy 假设翻译（trigger-policy 卡5 / tasks 6.1 分流锚）。

    同条件集异 policy = 不同基因型：各自落库互不命中同签名分流；
    翻译产物携带归一 policy；无 policy 假设产物零变化（不写字段）。
    """

    def _conditions(self):
        return {
            "when_conditions": [
                {"subject": {"indicator": "RSI", "params": {"period": 14}},
                 "action": "lt", "target": 35}],
            "then_conditions": [
                {"subject": {"indicator": "RSI", "params": {"period": 30}},
                 "action": "gt", "target": 60}],
        }

    def _register(self, db, policy=None):
        return register_hypothesis(
            db, conditions=self._conditions(), statement="s",
            data_ranges=[{"symbol": "BTCUSDT", "timeframe": "4h",
                          "start": "2024-01-01T00:00:00Z",
                          "end": "2024-02-01T00:00:00Z"}],
            trigger_policy=policy)["hypothesis_id"]

    def _translate_dna(self, db, hid):
        from core.persistence.db_ext import get_strategy
        out = translate_hypothesis(db, hypothesis_id=hid)
        row = get_strategy(db, out["strategy_id"])
        return out, StrategyDNA.from_json(row["dna_json"])

    def test_带policy假设_翻译产物携带policy(self, db):
        hid = self._register(db, {"mode": "first_true"})
        out, dna = self._translate_dna(db, hid)
        assert dna.entry_trigger_policy == {"mode": "first_true"}

    def test_同条件异policy_各自落库互不混挂(self, db):
        hid_a = self._register(db, {"mode": "first_true"})
        hid_b = self._register(db, {"mode": "suppress_bars", "bars": 48})
        out_a, dna_a = self._translate_dna(db, hid_a)
        out_b, dna_b = self._translate_dna(db, hid_b)
        assert out_a["strategy_id"] != out_b["strategy_id"]
        assert dna_a.entry_trigger_policy == {"mode": "first_true"}
        assert dna_b.entry_trigger_policy == {"mode": "suppress_bars",
                                              "bars": 48}
        assert dna_a.gene_signature != dna_b.gene_signature

    def test_无policy假设_翻译产物零变化(self, db):
        hid = self._register(db)                    # 缺省
        out, dna = self._translate_dna(db, hid)
        assert dna.entry_trigger_policy is None
        assert "entry_trigger_policy" not in dna.to_dict()

    def test_显式none假设_翻译产物同缺省(self, db):
        hid = self._register(db, {"mode": "none"})
        out, dna = self._translate_dna(db, hid)
        assert dna.entry_trigger_policy is None


class TestTranslatorRejections:
    """translate_hypothesis 四类结构化拒绝（跨币种/subject 非法/action
    不可表达/when 无可表达条件）。"""

    def test_跨币种假设_拒绝并附币种清单(self, db):
        """跨币种在 register 入口已拒（data_ranges 同币种校验）；翻译侧
        防御对 DB 直写的存量跨币种行生效（绕开入口的形态）。"""
        ranges = [
            {"symbol": "BTCUSDT", "timeframe": "4h",
             "start": "2024-01-01T00:00:00Z", "end": "2024-02-01T00:00:00Z"},
            {"symbol": "ETHUSDT", "timeframe": "4h",
             "start": "2024-01-01T00:00:00Z", "end": "2024-02-01T00:00:00Z"},
        ]
        hid = "hyp_cross_symbol"
        rr.save_hypothesis_with_log(
            db, hypothesis_id=hid, conditions=json.dumps(self._conds()),
            conditions_hash="ch_cross", statement="s",
            data_ranges=json.dumps(ranges), ranges_hash="rh_cross")
        with pytest.raises(ResearchError) as ei:
            translate_hypothesis(db, hypothesis_id=hid)
        assert ei.value.code == "RESEARCH_NOT_EXPRESSIBLE"
        assert sorted((ei.value.detail or {})["symbols"]) == \
            ["BTCUSDT", "ETHUSDT"]

    def test_subject结构化引用非法_转译拒绝(self, db):
        """未知指标在 register 预检已拒；翻译侧 ColumnRefError 转译
        防御对 DB 直写行生效。"""
        conds = {
            "when_conditions": [
                {"subject": {"indicator": "NOPE", "params": {}},
                 "action": "lt", "target": 35}],
            "then_conditions": [
                {"subject": "close", "action": "gt", "target": 0}],
        }
        hid = "hyp_bad_subject"
        rr.save_hypothesis_with_log(
            db, hypothesis_id=hid, conditions=json.dumps(conds),
            conditions_hash="ch_bad", statement="s",
            data_ranges=json.dumps(self._ranges()), ranges_hash="rh_bad")
        with pytest.raises(ResearchError) as ei:
            translate_hypothesis(db, hypothesis_id=hid)
        assert ei.value.code == "RESEARCH_CONDITION_INVALID"

    def test_action不可表达_拒绝附合法动作清单(self, db):
        conds = {
            "when_conditions": [
                {"subject": {"indicator": "RSI", "params": {"period": 14}},
                 "action": "bogus_action", "target": 35},
                {"subject": "close", "action": "gt", "target": 0}],
            "then_conditions": [
                {"subject": "close", "action": "gt", "target": 0}],
        }
        hid = register_hypothesis(
            db, conditions=conds, statement="s",
            data_ranges=self._ranges())["hypothesis_id"]
        with pytest.raises(ResearchError) as ei:
            translate_hypothesis(db, hypothesis_id=hid)
        assert ei.value.code == "RESEARCH_NOT_EXPRESSIBLE"
        assert "bogus_action" in str(ei.value)

    def test_when无可表达条件_拒绝(self, db):
        """when 组空（经 DB 直写绕开 register 非空校验——翻译器对
        存量行形态的防御）→ 无入场条件拒绝。逐条件路径要么产基因/
        插件源要么即抛，组级空集唯一来源即空列表。"""
        hid = "hyp_empty_when"
        rr.save_hypothesis_with_log(
            db, hypothesis_id=hid,
            conditions=json.dumps({"when_conditions": [],
                                   "then_conditions": []}),
            conditions_hash="ch_empty", statement="s",
            data_ranges=json.dumps(self._ranges()), ranges_hash="rh_empty")
        with pytest.raises(ResearchError, match="无可表达条件"):
            translate_hypothesis(db, hypothesis_id=hid)

    @staticmethod
    def _conds():
        return {
            "when_conditions": [
                {"subject": {"indicator": "RSI", "params": {"period": 14}},
                 "action": "lt", "target": 35}],
            "then_conditions": [
                {"subject": "close", "action": "gt", "target": 0}],
        }

    @staticmethod
    def _ranges():
        return [{"symbol": "BTCUSDT", "timeframe": "4h",
                 "start": "2024-01-01T00:00:00Z",
                 "end": "2024-02-01T00:00:00Z"}]


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


class TestTranslatorTargetColumnRejected:
    """翻译器 target 列名形拒绝：action 合法但 target 非数值
    （字符串列名不可表达）→ RESEARCH_NOT_EXPRESSIBLE。"""

    def test_when_target列名_拒绝(self, db):
        hid = "hyp_target_col"
        conds = {
            "when_conditions": [
                {"subject": {"indicator": "RSI", "params": {"period": 14}},
                 "action": "lt", "target": "close"}],
            "then_conditions": [
                {"subject": "close", "action": "gt", "target": 0}],
        }
        rr.save_hypothesis_with_log(
            db, hypothesis_id=hid, conditions=json.dumps(conds),
            conditions_hash="ch_tgtcol", statement="s",
            data_ranges=json.dumps([{"symbol": "BTCUSDT", "timeframe": "4h",
                                     "start": "2024-01-01T00:00:00Z",
                                     "end": "2024-02-01T00:00:00Z"}]),
            ranges_hash="rh_tgtcol")
        with pytest.raises(ResearchError) as ei:
            translate_hypothesis(db, hypothesis_id=hid)
        assert ei.value.code == "RESEARCH_NOT_EXPRESSIBLE"
        assert "target 须为数值" in str(ei.value)


class TestRegisterPrecheckLogFailureTolerated:
    """登记预检留痕「尽力而为」：append_research_log 抛异常 →
    warning 降级，登记主通道照常成功返回（不反噬）。"""

    def test_留痕写入失败_登记照常成功(self, db, tmp_path, monkeypatch):
        import core.persistence.repository.research as research_mod

        data_dir = tmp_path / "data"
        data_dir.mkdir()
        # 数据起点 2024-01-01；区间 start 留足 200 bar warmup 前缀
        _df(500).to_parquet(data_dir / "BTCUSDT_4h.parquet")

        def _boom(*a, **kw):
            raise RuntimeError("log sink down")

        monkeypatch.setattr(research_mod, "append_research_log", _boom)
        conds = {
            "when_conditions": [
                {"subject": {"indicator": "RSI", "params": {"period": 14}},
                 "action": "lt", "target": 35}],
            "then_conditions": [
                {"subject": {"indicator": "RSI", "params": {"period": 14}},
                 "action": "gt", "target": 50}],
        }
        out = register_hypothesis(
            db, conditions=conds, statement="s",
            data_ranges=[{"symbol": "BTCUSDT", "timeframe": "4h",
                          "start": "2024-02-15T00:00:00Z",
                          "end": "2024-03-20T00:00:00Z"}],
            precheck_data_dir=data_dir)
        assert out["hypothesis_id"]
        assert out["duplicated"] is False
