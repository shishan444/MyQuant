"""进化算子守卫与继承分支测试（阶段四盲区消解）。

覆盖 operators.py 未触达分支（按契约语义推导，不从实现反推）：
- 层变异守卫：remove/layer_timeframe 的不可操作形态短路（原样返回）
- 指标替换守卫：可互换集为空 / 全 guard_only 过滤后空 → 无变异
- add_signal 守卫：空基因池 / 空指标注册表 → 无变异
- 参数装配：画像推荐缺参走默认（indicator_replacement / add_signal）
- crossover 层继承：DIRECTION 基因保留（747）、双空层继承（749）
- mutate_composition：logic 模式 op 翻转（811）
- _pick_series_target：候选集枯竭回落 "EMA"（164）

随机入口以 random.seed 固定（工程既有测试同法）。
"""
from __future__ import annotations

import random

import pytest

pytestmark = [pytest.mark.unit]

import core.evolution.operators as ops
from core.evolution.operators import (
    crossover,
    mutate_add_signal,
    mutate_composition,
    mutate_indicator,
    mutate_layer_timeframe,
    mutate_remove_layer,
    _pick_series_target,
)
from core.features.indicators import INDICATOR_REGISTRY
from core.strategy.dna import (
    ExecutionGenes, LogicGenes, RiskGenes, SignalGene, SignalRole,
    StrategyDNA, TimeframeLayer,
)


def _gene(role=SignalRole.ENTRY_TRIGGER, indicator="RSI"):
    return SignalGene(indicator, {"period": 14} if indicator in ("RSI", "EMA")
                      else {}, role, None, {"type": "lt", "threshold": 30})


def _dna(genes=None, layers=None, **kw):
    return StrategyDNA(
        signal_genes=genes if genes is not None else [_gene()],
        logic_genes=LogicGenes(),
        execution_genes=ExecutionGenes(timeframe="4h"),
        risk_genes=RiskGenes(),
        layers=layers,
        **kw,
    )


def _layer(tf="1d", genes=None):
    return TimeframeLayer(timeframe=tf,
                          signal_genes=genes if genes is not None else [_gene()],
                          logic_genes=LogicGenes())


class TestLayerMutationGuards:
    """层变异算子的不可操作形态守卫（原样返回，不产子代）。"""

    def test_remove_layer_单层mtf_原样返回(self):
        dna = _dna(genes=[], layers=[_layer(tf="4h")])
        random.seed(7)
        out = mutate_remove_layer(dna)
        assert out is dna  # 单层（执行层）无可移除层

    def test_remove_layer_全执行周期层_原样返回(self):
        dna = _dna(genes=[], layers=[_layer(tf="4h"), _layer(tf="4h")])
        random.seed(7)
        out = mutate_remove_layer(dna)
        assert out is dna

    def test_layer_timeframe_全执行周期层_原样返回(self):
        dna = _dna(genes=[], layers=[_layer(tf="4h"), _layer(tf="4h")])
        random.seed(7)
        out = mutate_layer_timeframe(dna)
        assert out is dna

    def test_layer_timeframe_候选集枯竭_原样返回(self):
        """非执行层存在，但候选周期全被当前/执行/兄弟周期占用。"""
        dna = _dna(genes=[], layers=[_layer(tf="4h"), _layer(tf="1d")])
        out = mutate_layer_timeframe(dna, candidate_timeframes=["1d"])
        assert out is dna


class TestReplacementGuards:
    """指标替换守卫：可互换集空 / 全 guard_only 过滤后空。"""

    def test_可互换集为空_无变异(self, monkeypatch):
        dna = _dna()
        monkeypatch.setattr(ops, "get_interchangeable", lambda _: [])
        random.seed(3)
        out = mutate_indicator(dna)
        # 无可互换 → 基因不变（仅溯源字段更新）
        assert out.signal_genes[0].indicator == "RSI"

    def test_可互换集全guard_only_触发角色过滤后空_无变异(self, monkeypatch):
        reg = INDICATOR_REGISTRY["RSI"]
        monkeypatch.setattr(ops, "get_interchangeable", lambda _: ["RSI"])
        monkeypatch.setattr(reg, "guard_only", True)
        dna = _dna()  # entry_trigger 角色 → guard_only 剔除
        random.seed(3)
        out = mutate_indicator(dna)
        assert out.signal_genes[0].indicator == "RSI"


class TestReplacementParamDefaults:
    """画像推荐命中但缺参名 → 该参数走注册表默认（485/586 两处同构）。"""

    def test_replacement_推荐缺参走默认(self, monkeypatch):
        from types import SimpleNamespace

        target = next(
            (name for name in INDICATOR_REGISTRY
             if name != "RSI"
             and not INDICATOR_REGISTRY[name].guard_only
             and INDICATOR_REGISTRY[name].params),
            None)
        assert target is not None, "注册表须存在非 guard 带参指标"
        monkeypatch.setattr(
            ops, "get_interchangeable", lambda _: [target])
        # 推荐表非空（外层 if 需 truthy）但缺 target 的参数名 →
        # 循环内 else 走注册表默认
        monkeypatch.setitem(
            ops.PROFILES, target,
            SimpleNamespace(recommended_params={"__无关键__": [1.0]},
                            follow_probability=1.0,
                            recommended_conditions=[]))
        dna = _dna()
        random.seed(5)
        out = mutate_indicator(dna)
        assert out.signal_genes[0].indicator == target
        pdef = INDICATOR_REGISTRY[target].params
        for pname, pdefn in pdef.items():
            expected = (int(pdefn.default) if pdefn.type == "int"
                        else float(pdefn.default))
            assert out.signal_genes[0].params[pname] == expected

    def test_add_signal_推荐缺参走默认(self, monkeypatch):
        from types import SimpleNamespace

        target = next(
            (name for name in INDICATOR_REGISTRY
             if not INDICATOR_REGISTRY[name].guard_only
             and INDICATOR_REGISTRY[name].params),
            None)
        # 注册表收缩为单指标 → 选择确定命中 target
        monkeypatch.setattr(
            ops, "INDICATOR_REGISTRY", {target: INDICATOR_REGISTRY[target]})
        # 推荐表非空但缺 target 的参数名 → 循环内 else 走注册表默认
        monkeypatch.setitem(
            ops.PROFILES, target,
            SimpleNamespace(recommended_params={"__无关键__": [1.0]},
                            follow_probability=1.0,
                            recommended_conditions=[]))
        random.seed(13)
        dna = _dna([_gene(), _gene(SignalRole.ENTRY_GUARD),
                    _gene(SignalRole.EXIT_GUARD)])
        out = mutate_add_signal(dna)
        new_genes = out.signal_genes[len(dna.signal_genes):]
        # 新基因命中 target 且全参取注册表默认（推荐缺参兜底）
        assert new_genes and all(g.indicator == target for g in new_genes)
        pdef = INDICATOR_REGISTRY[target].params
        for g in new_genes:
            for pname, pdefn in pdef.items():
                expected = (int(pdefn.default) if pdefn.type == "int"
                            else float(pdefn.default))
                assert g.params[pname] == expected


class TestAddSignalGuards:
    """add_signal 守卫：空基因池 / 空注册表。"""

    def test_空基因池_无追加(self):
        dna = _dna(genes=[])
        random.seed(9)
        out = mutate_add_signal(dna)
        assert out.signal_genes == []

    def test_空指标注册表_无追加(self, monkeypatch):
        monkeypatch.setattr(ops, "INDICATOR_REGISTRY", {})
        dna = _dna()
        random.seed(9)
        out = mutate_add_signal(dna)
        assert len(out.signal_genes) == 1  # 原基因不变、未追加


class TestPickSeriesTargetFallback:
    """_pick_series_target 候选集枯竭回落 "EMA"（同类+trend 全空）。"""

    def test_候选枯竭_回落ema(self, monkeypatch):
        monkeypatch.setattr(ops, "get_interchangeable", lambda _: [])
        monkeypatch.setattr(ops, "INDICATOR_REGISTRY", {})
        assert _pick_series_target("RSI", None) == "EMA"


class TestCrossoverLayerInheritance:
    """crossover 同位层继承分支（进化继承 D11 配套）。"""

    def test_同位层direction基因_必保留恰一个(self):
        """层基因混排（entry 槽与 direction 槽各归其位）→ direction
        槽恰保留一个、取自亲代集合（D11 继承契约）。"""
        entry_a = _gene(SignalRole.ENTRY_TRIGGER, "RSI")
        entry_b = _gene(SignalRole.ENTRY_TRIGGER, "RSI")
        dir_a = SignalGene("EMA", {"period": 14}, SignalRole.DIRECTION,
                           None, {"type": "price_above"})
        dir_b = SignalGene("EMA", {"period": 20}, SignalRole.DIRECTION,
                           None, {"type": "price_below"})
        a = _dna(genes=[], layers=[_layer(tf="4h", genes=[entry_a, dir_a])])
        b = _dna(genes=[], layers=[_layer(tf="4h", genes=[dir_b, entry_b])])
        random.seed(11)
        child = crossover(a, b)
        dirs = [g for g in child.layers[0].signal_genes
                if g.role == SignalRole.DIRECTION]
        assert len(dirs) == 1
        assert dirs[0].params["period"] in (14, 20)  # ∈ 亲代集合

    def test_双亲同位层皆空基因_继承空层(self):
        a = _dna(genes=[], layers=[_layer(tf="4h", genes=[])])
        b = _dna(genes=[], layers=[_layer(tf="4h", genes=[])])
        random.seed(11)
        child = crossover(a, b)
        assert child.layers[0].signal_genes == []


class TestMutateCompositionLogicFlip:
    """mutate_composition：logic 模式下 op AND/OR 翻转。"""

    def test_logic模式_op翻转产生合法对侧(self):
        dna = _dna(
            signal_sources=[{"type": "rule"}],
            composition={"mode": "logic", "op": "AND"})
        outcomes = set()
        for seed in range(24):
            random.seed(seed)
            out = mutate_composition(dna)
            outcomes.add(out.composition["op"])
            assert out.composition["op"] in ("AND", "OR")
        assert outcomes == {"AND", "OR"}  # 两侧均可产出（真翻转非恒定）


class TestMutateParamsFallbackTiers:
    """mutate_params 三级回退路径锚：未注册指标跳过变异 /
    画像缺位走注册表候选 / 候选未中走多项式有界变异。"""

    def test_未注册指标_参数零改变(self):
        from core.evolution.operators import mutate_params

        gene = SignalGene("ZZZ_NOT_REG", {"x": 1}, SignalRole.ENTRY_TRIGGER,
                          "zz", {"type": "gt", "threshold": 1})
        out = mutate_params(_dna(genes=[gene]))
        assert out.signal_genes[0].params == {"x": 1}

    def test_画像缺位_走注册表候选值域(self, monkeypatch):
        from core.evolution import operators as ops

        monkeypatch.setattr(ops, "PROFILES", {})
        from core.evolution.operators import mutate_params

        monkeypatch.setattr(ops.random, "random", lambda: 0.1)
        out = mutate_params(_dna())
        period = out.signal_genes[0].params["period"]
        reg = ops.INDICATOR_REGISTRY["RSI"]
        assert reg.params["period"].min <= period <= reg.params["period"].max

    def test_候选未中_走多项式有界变异(self, monkeypatch):
        from core.evolution import operators as ops

        monkeypatch.setattr(ops, "PROFILES", {})
        monkeypatch.setattr(ops.random, "random", lambda: 0.9)
        from core.evolution.operators import mutate_params

        out = mutate_params(_dna())
        period = out.signal_genes[0].params["period"]
        reg = ops.INDICATOR_REGISTRY["RSI"]
        # 多项式变异同样有界（clamp 到注册表值域）
        assert reg.params["period"].min <= period <= reg.params["period"].max


class TestMutateCompositionWritebackShapes:
    """mutate_composition 写回二形：comp-only（无 sources）与
    sources-only（无 comp）——各跳过对侧写回分支。"""

    @staticmethod
    def _src_dna(signal_sources=None, composition=None):
        return _dna(signal_sources=signal_sources, composition=composition)

    def test_comp_only_阈值扰动_sources缺省不写回(self, monkeypatch):
        from core.evolution import operators as ops

        monkeypatch.setattr(ops.random, "random", lambda: 0.0)
        monkeypatch.setattr(ops.random, "uniform", lambda a, b: 0.0)
        dna = self._src_dna(composition={"mode": "threshold", "threshold": 0.5})
        out = ops.mutate_composition(dna)
        assert out.composition["threshold"] == 0.5

    def test_sources_only_权重扰动_comp缺省不写回(self, monkeypatch):
        from core.evolution import operators as ops

        monkeypatch.setattr(ops.random, "random", lambda: 0.9)
        dna = self._src_dna(signal_sources=[
            {"type": "formula", "ref": "f1", "weight": 1.0}])
        out = ops.mutate_composition(dna)
        assert out.composition is None
        assert out.signal_sources[0]["type"] == "formula"


class TestMutateParamsCandidateFilterUnreachable:
    """mutate_params 候选过滤空分支（operators.py:425 False 侧 → :432）
    不可达判定。

    调用图依据：进入 :422 要求 pdef.candidates 非空；:423-424 过滤
    ``pdef.min <= c <= pdef.max`` 后为空需要 candidates 存在越界项。
    注册表现状（core/features/indicators.py INDICATOR_REGISTRY）全量
    核对：带 candidates 的参数（EMA period [7,15,20,30,60,90] 等）
    其候选全部落在 [min, max] 值域内（单一数据源不变式）——合法
    注册表数据下过滤恒非空，False 侧仅在注册表引入越界候选
    （回归错误形态）时可达。
    """
