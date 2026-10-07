"""策略链事件化锚（trigger-policy 卡5 / tasks 6.2+6.3）。

组装双出口（dna_to_signal_set 四路径 / batch_signal_sets 单周期缓存
组装）与真实回测链的事件口径：恒真入场条件下手算事件位对照（非直调
函数的假阳性锚——经 BacktestEngine 全链）；插件路径单次变换反锚
（suppress_bars 二次变换会把间隔拉长 2N——事件数对照即红）；MTF
划界防御拒绝与 batch 通道透传不吞。

12 处直译入口的跨链代表面对拍归 test_event_policy_cross_chain.py
（任务 8.2），本文件锚策略链本体行为。
"""

import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

pytestmark = [pytest.mark.integration]

from core.analysis.event_policy import mask_to_events
from core.analysis.hypothesis_service import ResearchError
from core.strategy.dna import (
    ExecutionGenes, LogicGenes, RiskGenes, SignalGene, SignalRole, StrategyDNA,
)
from core.strategy.executor import (
    _apply_entry_trigger_policy, batch_signal_sets, dna_to_signal_set,
)
from tests.helpers.data_factory import make_ohlcv


@pytest.fixture()
def df():
    return make_ohlcv(n=300, freq="4h", start="2024-01-01", seed=7)


def _policy_dna(policy=None) -> StrategyDNA:
    """恒真入场（close>0 全段单 True 段）+ 无出场——事件位手算友好。"""
    return StrategyDNA(
        signal_genes=[
            SignalGene("CLOSE", {}, SignalRole.ENTRY_TRIGGER, "close",
                       {"type": "gt", "threshold": 0}),
        ],
        logic_genes=LogicGenes(),
        execution_genes=ExecutionGenes(timeframe="4h"),
        risk_genes=RiskGenes(),
        entry_trigger_policy=policy,
    )


class TestAssembleEventPolicy:
    def test_无字段策略_entries_原样(self, df) -> None:
        ss = dna_to_signal_set(_policy_dna(None), df)
        assert bool(ss.entries.all())          # 恒真直译（零改写红线）

    def test_first_true_单周期出口_事件位(self, df) -> None:
        ss = dna_to_signal_set(_policy_dna({"mode": "first_true"}), df)
        assert ss.entries.sum() == 1           # 恒真掩码唯一段首
        assert bool(ss.entries.iloc[0])

    def test_suppress_bars_单周期出口_事件位手算(self, df) -> None:
        policy = {"mode": "suppress_bars", "bars": 50}
        ss = dna_to_signal_set(_policy_dna(policy), df)
        expected = mask_to_events(np.ones(len(df), dtype=bool), policy)
        assert np.array_equal(
            np.flatnonzero(ss.entries.values), expected)   # [0,50,100,...]

    def test_batch_signal_sets_缓存组装出口_同口径(self, df) -> None:
        policy = {"mode": "suppress_bars", "bars": 50}
        single = dna_to_signal_set(_policy_dna(policy), df)
        batched = batch_signal_sets([_policy_dna(policy)], df)
        assert np.array_equal(batched[0].entries.values,
                              single.entries.values)

    def test_bars_超界_结构化拒绝(self, df) -> None:
        with pytest.raises(ResearchError) as ei:
            dna_to_signal_set(_policy_dna({"mode": "suppress_bars",
                                           "bars": 10**6}), df)
        assert ei.value.code == "RESEARCH_PARAM_INVALID"
        assert ei.value.detail["legal_range"] == f"1..{len(df) - 1}"

    def test_bars_超界_batch_通道_同步拒绝(self, df) -> None:
        with pytest.raises(ResearchError):
            batch_signal_sets(
                [_policy_dna({"mode": "suppress_bars", "bars": 10**6})], df)


class TestRealBacktestChain:
    """真实回测链事件化（非直调函数的假阳性锚）。"""

    def test_engine_run_入场笔数_事件口径(self, df) -> None:
        """恒真+无出场：first_true 下唯一事件=唯一进场——成交笔数=1
        （空仓抑制下信号层 1 事件即 1 笔；直译形态恒真重复信号也只有
        1 笔，故用 suppress_bars 拉开差异：事件数=ceil 段）。"""
        from core.backtest.engine import BacktestEngine

        policy = {"mode": "suppress_bars", "bars": 100}
        dna = _policy_dna(policy)
        engine = BacktestEngine()
        result = engine.run(dna, df)
        n_events = len(mask_to_events(np.ones(len(df), dtype=bool), policy))
        assert result.total_trades == n_events    # 每事件一笔（无出场）

    def test_engine_run_事件化_密度单调下降(self, df) -> None:
        """关系锚：事件化只减不增信号密度——真实链笔数 ≤ 直译形态
        （无 policy 形态的既有行为由 846 例策略域回归承载零变化）。"""
        from core.backtest.engine import BacktestEngine
        engine = BacktestEngine()
        plain = engine.run(_policy_dna(None), df)
        evented = engine.run(
            _policy_dna({"mode": "suppress_bars", "bars": 100}), df)
        assert 0 < evented.total_trades < plain.total_trades


class TestPluginPathSingleTransform:
    """插件路径单次变换反锚（assemble：掩码态 base+组合后单次施加）。"""

    @pytest.fixture()
    def db(self, tmp_path: Path) -> Path:
        from core.analysis.executor import ensure_research_tables
        from core.persistence.db_ext import init_db_ext
        d = tmp_path / "plugin.db"
        init_db_ext(d)
        ensure_research_tables(d)
        return d

    def _plugin_dna(self, db, policy=None) -> StrategyDNA:
        from core.analysis.plugin_service import register_formula_plugin
        register_formula_plugin(db, name="always1",
                                expression="rolling_mean(close, 200) * 0 + 1")
        return StrategyDNA(
            signal_genes=[],                       # 纯插件条件
            logic_genes=LogicGenes(),
            execution_genes=ExecutionGenes(timeframe="4h"),
            risk_genes=RiskGenes(),
            signal_sources=[
                {"type": "formula", "ref": "fx_always1",
                 "threshold": 0.5, "operator": ">"}],
            composition={"mode": "logic", "op": "and"},
            entry_trigger_policy=policy,
        )

    def test_插件组合后_单次变换(self, db, df) -> None:
        from core.analysis.signal_assembly import assemble_signal_set
        policy = {"mode": "suppress_bars", "bars": 40}
        dna = self._plugin_dna(db, policy)
        ss = assemble_signal_set(db, dna, df)
        # 插件恒真：合并掩码恒真 → 单次变换事件位 = 0,40,80,...
        expected = mask_to_events(np.ones(len(df), dtype=bool), policy)
        actual = np.flatnonzero(ss.entries.values)
        assert np.array_equal(actual, expected)
        # 反锚核心：二次变换会把间隔拉长为 2N（事件 0,80,160 少一半）
        assert len(actual) == len(expected) > len(df) // 80

    def test_插件路径_无policy_零变化(self, db, df) -> None:
        from core.analysis.signal_assembly import assemble_signal_set
        ss = assemble_signal_set(db, self._plugin_dna(db, None), df)
        assert bool(ss.entries.iloc[50:].all())    # 恒真直译（warmup 段后）


def _mtf_policy_dna() -> StrategyDNA:
    from core.strategy.dna import TimeframeLayer
    return StrategyDNA(
        layers=[
            TimeframeLayer(timeframe="4h", role="execution",
                           signal_genes=[SignalGene(
                               "CLOSE", {}, SignalRole.ENTRY_TRIGGER,
                               "close", {"type": "gt", "threshold": 0})]),
            TimeframeLayer(timeframe="1d", role="trend",
                           signal_genes=[]),
        ],
        logic_genes=LogicGenes(),
        execution_genes=ExecutionGenes(timeframe="4h"),
        risk_genes=RiskGenes(),
        mtf_mode="direction",
        entry_trigger_policy={"mode": "first_true"},
    )


class TestMtfBoundary:
    """MTF 划界（6.3）：防御拒绝直调 + batch 通道透传不吞。"""

    def test_直调_dna_to_signal_set_显式拒绝(self, df) -> None:
        dfs = {"1d": df, "4h": df}
        with pytest.raises(ResearchError) as ei:
            dna_to_signal_set(_mtf_policy_dna(), df, dfs)
        assert ei.value.code == "RESEARCH_NOT_EXPRESSIBLE"

    def test_batch_通道_拒绝透传不吞(self, df) -> None:
        """经 batch_signal_sets（verify/进化评估消费路径）到达时错误
        可见——不被 _empty_signal_set 静默替换成「策略零信号」。"""
        dfs = {"1d": df, "4h": df}
        with pytest.raises(ResearchError) as ei:
            batch_signal_sets([_mtf_policy_dna()], df, dfs)
        assert ei.value.code == "RESEARCH_NOT_EXPRESSIBLE"


class TestEvolutionInheritance:
    """进化继承（6.3 / D11 / Q5=random.choice 沿元字段先例）。"""

    def test_crossover_子代_policy_取自亲代(self) -> None:
        from core.evolution.operators import crossover
        a = _policy_dna({"mode": "first_true"})
        b = _policy_dna({"mode": "suppress_bars", "bars": 48})
        seen = set()
        for _ in range(40):
            child = crossover(a, b)
            assert child.entry_trigger_policy in (
                {"mode": "first_true"},
                {"mode": "suppress_bars", "bars": 48})   # 非静默丢失
            seen.add(json.dumps(child.entry_trigger_policy,
                                sort_keys=True))
        assert len(seen) == 2                    # 两亲代取值都出现

    def test_crossover_单亲带policy_继承不丢失(self) -> None:
        from core.evolution.operators import crossover
        a = _policy_dna({"mode": "first_true"})
        b = _policy_dna(None)
        for _ in range(20):
            child = crossover(a, b)
            assert child.entry_trigger_policy in (
                {"mode": "first_true"}, None)

    def test_mutate_mtf_mode_带policy个体_形态不变(self) -> None:
        """带 policy 个体跳过 mtf_mode 变异（不产生 MTF+policy 形态）。"""
        from core.evolution.operators import mutate_mtf_mode
        dna = _mtf_policy_dna()
        out = mutate_mtf_mode(dna)
        assert out.entry_trigger_policy == {"mode": "first_true"}
        assert out.mtf_mode == dna.mtf_mode       # no-op：mode 与形态不变
        assert "mtf_mode" not in out.mutation_ops

    def test_变异算子_往返存活(self) -> None:
        """11 个变异算子全走 to_dict/from_dict 往返——字段经白名单
        扩展后天然存活（代表算子锚）。"""
        from core.evolution.operators import (
            mutate_confluence_threshold, mutate_proximity_mult)
        dna = _policy_dna({"mode": "suppress_bars", "bars": 24})
        for op in (mutate_confluence_threshold, mutate_proximity_mult):
            out = op(dna)
            assert out.entry_trigger_policy == {"mode": "suppress_bars",
                                                "bars": 24}
