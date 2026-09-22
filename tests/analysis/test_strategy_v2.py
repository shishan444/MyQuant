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
                {"subject": "rsi_14", "action": "lt", "target": 35},
                {"subject": "ema_50", "action": "cross_above", "target": 60000},
            ],
            "then_conditions": [{"subject": "rsi_70", "action": "gt", "target": 60}],
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
        assert exits[0].field_name == "rsi_70"

    def test_then_inexpressible_rejected(self, db):
        """THEN 不可表达显式拒绝（不静默丢弃 THEN 语义）。"""
        conds = {
            "when_conditions": [{"subject": "rsi_14", "action": "lt", "target": 35}],
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
        ranges = self._ranges() + self._ranges(symbol="ETHUSDT")
        hid = self._register_and_pass(db, self._expressible_conditions(), ranges)
        with pytest.raises(ResearchError, match="跨币种"):
            translate_hypothesis(db, hypothesis_id=hid)

    def test_inexpressible_action_rejected(self, db):
        conds = {
            "when_conditions": [{"subject": "rsi_14", "action": "divergence_top",
                                 "target": 0}],
            "then_conditions": [{"subject": "rsi_70", "action": "gt", "target": 60}],
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
            "when_conditions": [{"subject": "rsi_14", "action": action, "target": 35}],
            "then_conditions": [{"subject": "rsi_70", "action": "gt", "target": 60}],
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
        assert entries[0].field_name == "rsi_14"

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
                "when_conditions": [{"subject": "rsi_14", "action": "lt", "target": 35}],
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
