"""crossover v2 字段原子继承+进化评估插件源在场即拒红锚
（dna-strong-typing-and-disclosure-hygiene 件⑤ H2+Q-3，design 2.5 /
specs strategy-expression「组合决策层可进化」「进化链插件源能力边界」）。

契约出处：
- H2 继承契约：crossover 子代的 signal_sources 与 composition 必须
  同亲代原子继承（random.choice 整对——两键逻辑不可分：composition
  .weights 键集按插件源 ref 列名索引，跨亲代拼合必产键集与源集错位
  的无意义基因型）。退化形态：两亲代均无 v2 字段→子代两键 None；
  单亲代有→「有值/None」间取其一（沿 entry_trigger_policy 元字段
  先例的探索语义）。
- Q-3 评估入口族三道拒绝（A 选项）：
  ①任务入口——initial_dna 含插件源（type∈{formula, model}）→422
  PLUGIN_SOURCES_UNSUPPORTED，先于任务落库；
  ②批量主路径——evaluate_population→batch_run 常态路径对插件源
  个体前置过滤（不进引擎、原位 0 分占位，返回 List[float] 形制与
  顺序零变化，归因经 logger.warning 结构化字段）；
  ③单体/回退路径——evaluate_dna 对插件源个体 0 分+error 标记+
  可归因 reason（不炸任务不失真评分）。

红锚语义：①②（继承）在实现前红——现状子代构造段无 v2 字段
（:784 注释自认 TD-E 存量缺陷）；③在入口拒绝前红（现状 201 落库）；
④⑤在评估判据前红（现状插件源经引擎按 rule-only 失真打分）。
reviewer P0-2 教训：批量主路径（evaluate_population→batch_run）与
单体回退路径（evaluate_dna）两落点分别构造——单测只走 evaluate_dna
会绿而真实进化循环仍失真。
"""
from __future__ import annotations

import pytest

pytestmark = [pytest.mark.integration]

from core.strategy.dna import (
    ExecutionGenes, LogicGenes, RiskGenes, SignalGene, SignalRole,
    StrategyDNA,
)


def _genes():
    return [
        SignalGene("RSI", {"period": 14}, SignalRole.ENTRY_TRIGGER,
                   "rsi", {"type": "lt", "threshold": 35}),
        SignalGene("RSI", {"period": 14}, SignalRole.EXIT_TRIGGER,
                   "rsi", {"type": "gt", "threshold": 65}),
    ]


def _rule_dna(**kw) -> StrategyDNA:
    return StrategyDNA(
        signal_genes=_genes(), logic_genes=LogicGenes(),
        execution_genes=ExecutionGenes(symbol="BTCUSDT", timeframe="4h"),
        risk_genes=RiskGenes(), **kw)


def _plugin_source(ref: str) -> dict:
    return {"type": "formula", "ref": ref, "threshold": 0.5,
            "operator": ">"}


# ===========================================================================
# 红锚①②：v2 字段原子继承（H2）
# ===========================================================================

class TestCrossoverV2Inheritance:
    def test_child_inherits_v2_pair_from_one_parent(self):
        """子代 v2 字段整对继承：signal_sources 与 composition 同源
        于某一亲代（对象同一性——choice 取引用），现状丢字段为红。"""
        from core.evolution.operators import crossover

        src_a = [_plugin_source("fx_a")]
        comp_a = {"mode": "weighted_vote",
                  "weights": {"fx_a": 0.7}, "threshold": 0.5}
        src_b = [_plugin_source("fx_b")]
        comp_b = {"mode": "logic", "op": "AND"}
        parent_a = _rule_dna(signal_sources=src_a, composition=comp_a)
        parent_b = _rule_dna(signal_sources=src_b, composition=comp_b)
        hit_any = False
        for _ in range(40):
            child = crossover(parent_a, parent_b)
            if child.signal_sources is src_a:
                assert child.composition is comp_a, (
                    "signal_sources 取 a 而 composition 取 b——拼合形态")
                hit_any = True
            elif child.signal_sources is src_b:
                assert child.composition is comp_b, (
                    "signal_sources 取 b 而 composition 取 a——拼合形态")
                hit_any = True
            else:
                pytest.fail(
                    f"子代未继承任一亲代 signal_sources（丢字段）："
                    f"got {child.signal_sources!r}")
        assert hit_any

    def test_weights_keyset_matches_source_set(self):
        """拼合禁止的行为面锚：weights 键集恒=所继承源集合的 ref 集
        ——跨亲代拼合必产 fx_a 源配 fx_b 权重的错位形（红）。"""
        from core.evolution.operators import crossover

        src_a = [_plugin_source("fx_a"), _plugin_source("fx_ax")]
        comp_a = {"mode": "weighted_vote",
                  "weights": {"fx_a": 0.6, "fx_ax": 0.4}, "threshold": 0.5}
        src_b = [_plugin_source("fx_b")]
        comp_b = {"mode": "weighted_vote", "weights": {"fx_b": 1.0}}
        parent_a = _rule_dna(signal_sources=src_a, composition=comp_a)
        parent_b = _rule_dna(signal_sources=src_b, composition=comp_b)
        for _ in range(40):
            child = crossover(parent_a, parent_b)
            if child.signal_sources is None:
                continue
            refs = {s["ref"] for s in child.signal_sources
                    if isinstance(s, dict) and s.get("type") in ("formula", "model")}
            weights = (child.composition or {}).get("weights", {})
            assert refs == set(weights.keys()), (
                f"拼合形态：源集 {refs} 与权重键集 {set(weights)} 错位")

    def test_both_parents_without_v2_degenerate(self):
        """退化形态逐位不变：两亲代均无 v2 字段→子代两键 None。"""
        from core.evolution.operators import crossover

        child = crossover(_rule_dna(), _rule_dna())
        assert child.signal_sources is None
        assert child.composition is None

    def test_single_parent_v2_choice_semantics(self):
        """单亲代有 v2：choice 在「有值/None」间取其一（探索语义，
        不特判——40 次循环两种取值都应出现过）。"""
        from core.evolution.operators import crossover

        src_a = [_plugin_source("fx_a")]
        comp_a = {"mode": "gate"}
        carrier = _rule_dna(signal_sources=src_a, composition=comp_a)
        plain = _rule_dna()
        seen = set()
        for _ in range(40):
            child = crossover(carrier, plain)
            if child.signal_sources is src_a:
                assert child.composition is comp_a
                seen.add("carrier")
            elif child.signal_sources is None:
                assert child.composition is None
                seen.add("plain")
            else:
                pytest.fail(f"非亲代形：{child.signal_sources!r}")
        assert seen == {"carrier", "plain"}, (
            f"探索语义应双向出现（40 次 choice 仅命中 {seen}）")


# ===========================================================================
# 红锚③：任务入口拒绝（Q-3 之一——现状 201 落库）
# ===========================================================================

class TestEvolutionInitialDnaPluginReject:
    def test_plugin_initial_dna_422(self, api_client):
        body = {
            "symbol": "BTCUSDT", "timeframe": "4h",
            "target_score": 80.0, "score_template": "explorer",
            "population_size": 15, "max_generations": 200,
            "initial_dna": {
                "signal_genes": [g.to_dict() for g in _genes()],
                "logic_genes": LogicGenes().to_dict(),
                "execution_genes": {"symbol": "BTCUSDT",
                                    "timeframe": "4h"},
                "risk_genes": RiskGenes().to_dict(),
                "signal_sources": [_plugin_source("fx_demo")],
                "composition": {"mode": "gate"},
            },
        }
        r = api_client.post("/api/evolution/tasks", json=body)
        assert r.status_code == 422, (
            f"插件源 initial_dna 现状 {r.status_code} 落库（落库后任务内"
            f"失真评估）——入口拒绝未生效：{r.text[:300]}")
        assert r.json()["detail"]["code"] == "PLUGIN_SOURCES_UNSUPPORTED"
        # 消息表单源锚（_PLUGIN_REJECT_MESSAGES 进化条目——design 2.5
        # 定稿文案指明「进化评估链未接入插件信号组装」）
        assert "进化评估链未接入插件信号组装" in r.json()["detail"]["message"]

    def test_rule_initial_dna_still_accepted(self, api_client):
        """护栏：纯 rule initial_dna 创建不受影响（201）。"""
        body = {
            "symbol": "BTCUSDT", "timeframe": "4h",
            "target_score": 80.0, "score_template": "explorer",
            "population_size": 15, "max_generations": 200,
            "initial_dna": {
                "signal_genes": [g.to_dict() for g in _genes()],
                "logic_genes": LogicGenes().to_dict(),
                "execution_genes": {"symbol": "BTCUSDT",
                                    "timeframe": "4h"},
                "risk_genes": RiskGenes().to_dict(),
            },
        }
        r = api_client.post("/api/evolution/tasks", json=body)
        assert r.status_code in (201, 200), r.text


# ===========================================================================
# 红锚④⑤：评估入口族（Q-3 之二/之三——两落点分别构造）
# ===========================================================================

class TestEvaluationPluginReject:
    """批量主路径与单体回退路径分别验证（P0-2：单路径绿不构成兑现）。"""

    @staticmethod
    def _task_row() -> dict:
        return {"symbol": "BTCUSDT", "timeframe": "4h",
                "data_start": None, "data_end": None}

    def test_batch_path_filters_plugin_individual(self, tmp_path, monkeypatch,
                                                  caplog):
        """批量主路径：插件源个体不进 batch_run（引擎收到的种群不含
        它）+原位 0.0 占位（返回形制与顺序零变化）+归因经 logger.warning
        结构化字段（code+strategy_id+reason——spec「结构化 error 标记」
        在批量路径的落地形制，调用方只消费分数序列）。"""
        import logging
        import types

        import pandas as pd

        from core.evolution import evaluation as ev

        plugin = _rule_dna(
            signal_sources=[_plugin_source("fx_demo")],
            composition={"mode": "gate"})
        plugin.strategy_id = "st_plg_batch"
        rule = _rule_dna()
        received = {}

        class _FakeEngine:
            def batch_run(self, pop, *a, **kw):
                received["pop_ids"] = [id(x) for x in pop]
                return []

        fake_engine_mod = types.SimpleNamespace(BacktestEngine=_FakeEngine)
        # 函数内延迟 import 经 sys.modules 缓存命中（probe 证明触达：
        # received 记录引擎实际收到的种群）
        monkeypatch.setitem(
            __import__("sys").modules, "core.backtest.engine",
            fake_engine_mod)
        with caplog.at_level(logging.WARNING,
                             logger="core.evolution.evaluation"):
            scores = ev.evaluate_population(
                [plugin, rule], self._task_row(), tmp_path,
                enhanced_df=pd.DataFrame({"close": [1.0] * 5}))
        assert len(scores) == 2 and scores[0] == 0.0, (
            f"插件源个体应原位 0.0 占位且长度不变：{scores}")
        assert id(rule) in received.get("pop_ids", []), "rule 个体应进引擎"
        assert id(plugin) not in received.get("pop_ids", []), (
            "插件源个体不得进 batch_run")
        warn = [r for r in caplog.records
                if "插件源个体拒绝评估" in r.getMessage()]
        assert warn, "批量路径拒绝无留痕（归因日志未落）"
        msg = warn[0].getMessage()
        assert "code=PLUGIN_SOURCES_UNSUPPORTED" in msg
        assert "strategy_id=st_plg_batch" in msg
        assert "进化评估链未接入插件信号组装" in msg

    def test_single_path_zero_score_with_reason(self, tmp_path):
        """单体回退路径：evaluate_dna 对插件源个体 0 分+error 标记+
        可归因 reason（不炸不失真）。"""
        import pandas as pd

        from core.evolution.evaluation import evaluate_dna

        plugin = _rule_dna(
            signal_sources=[_plugin_source("fx_demo")],
            composition={"mode": "gate"})
        out = evaluate_dna(
            plugin, self._task_row(), tmp_path,
            enhanced_df=pd.DataFrame({"close": [1.0] * 5}))
        assert out["score"] == 0.0
        assert out.get("error") is True
        assert "PLUGIN_SOURCES_UNSUPPORTED" in str(out.get("reason", ""))
