"""执行器条件合法集契约与双静默面收口测试（unify-tool-return-contract）。

覆盖任务 1（合法集常量同源）与任务 3（未知类型结构化拒绝 S6 /
列解析收口 S8 / 防线独立性 S9 / batch 增捕 S12）全部断言面。
锚点编号=backend-tester 包一方案（S6b 双向相等主断言等）。
"""
from __future__ import annotations

import logging

import pytest

pytestmark = [pytest.mark.unit]

import numpy as np
import pandas as pd

from core.analysis.errors import ResearchError
from core.strategy.executor import (
    EVALUABLE_CONDITION_TYPES,
    batch_signal_sets,
    clear_indicator_cache,
    dna_to_signal_set,
    evaluate_condition,
)
from core.features.registry import INDICATOR_REGISTRY
from tests.helpers.data_factory import (
    make_dna, make_enhanced_df, make_signal_gene,
)


@pytest.fixture(autouse=True)
def _clean_cache():
    clear_indicator_cache()
    yield
    clear_indicator_cache()


class TestLegalTypeSet:
    """S6b/S6 前提：合法集常量与注册表同源（双向相等）+穷举穿透。"""

    def test_s6b_registry_union_bidirectional_equal(self):
        """主断言：EVALUABLE_CONDITION_TYPES == ∪全部注册表
        supported_conditions（基准=INDICATOR_REGISTRY 运行时遍历，
        双向漂移任一侧都红——执行器加类型忘更注册表、或注册表声明
        了执行器不认识的类型，两方向都抓）。"""
        union: set[str] = set()
        for reg in INDICATOR_REGISTRY.values():
            sc = getattr(reg, "supported_conditions", None)
            if sc:
                union.update(sc)
        # 现状披露（非断言面）：两处 ConditionType 枚举为历史异集
        # ——core/strategy/dna.py ConditionType 15 成员无 eq；
        # api/schemas.py ConditionType 8 成员。枚举不做子集/相等
        # 断言（历史形制非缺陷）。
        assert EVALUABLE_CONDITION_TYPES == union

    def test_s6b_constant_is_frozenset_of_17(self):
        """常量形制锚：frozenset、17 类型（执行器实际分派集基数——
        fix-mtf-execution-consistency R4 增 within_bars_after 16→17）。"""
        assert isinstance(EVALUABLE_CONDITION_TYPES, frozenset)
        assert len(EVALUABLE_CONDITION_TYPES) == 17

    def test_enumerate_all_types_pass_through(self):
        """穷举穿透：每类型最小样本基因求值不触发未知类型拒绝
        （返回布尔 Series 即可——语义正确性由 test_new_conditions
        等既有用例负责，此处只锁「合法类型全部可达」）。"""
        df = make_enhanced_df(n=60)
        series = df["rsi_14"]
        close = df["close"]
        cases = {
            "eq": {"type": "eq", "threshold": 50},
            "lt": {"type": "lt", "threshold": 50},
            "gt": {"type": "gt", "threshold": 50},
            "le": {"type": "le", "threshold": 50},
            "ge": {"type": "ge", "threshold": 50},
            "cross_above": {"type": "cross_above", "threshold": 50},
            "cross_below": {"type": "cross_below", "threshold": 50},
            "price_above": {"type": "price_above"},
            "price_below": {"type": "price_below"},
            "cross_above_series": {
                "type": "cross_above_series",
                "target_indicator": "EMA", "target_params": {"period": 20}},
            "cross_below_series": {
                "type": "cross_below_series",
                "target_indicator": "EMA", "target_params": {"period": 20}},
            "lookback_any": {
                "type": "lookback_any", "window": 3,
                "inner": {"type": "gt", "threshold": 50}},
            "lookback_all": {
                "type": "lookback_all", "window": 3,
                "inner": {"type": "gt", "threshold": 50}},
            "within_bars_after": {
                "type": "within_bars_after", "window": 3,
                "inner": {"type": "gt", "threshold": 50}},
            "touch_bounce": {
                "type": "touch_bounce", "threshold": 0.02,
                "direction": "support"},
            "role_reversal": {
                "type": "role_reversal", "threshold": 0.02},
            "wick_touch": {
                "type": "wick_touch", "threshold": 0.01,
                "direction": "support"},
        }
        assert set(cases) == set(EVALUABLE_CONDITION_TYPES), \
            "穷举样本与常量集不一致（样本缺或常量多）"
        for cond_type, condition in cases.items():
            result = evaluate_condition(series, close, condition, df=df)
            assert isinstance(result, pd.Series), cond_type
            assert result.dtype == bool, cond_type
            assert len(result) == len(series), cond_type


class TestUnknownTypeRejection:
    """S6a/S6c/S6d：第一静默面收口——未知类型结构化拒绝。"""

    def test_s6a_unknown_type_rejected_with_detail(self):
        """未知类型 raise RESEARCH_NOT_EXPRESSIBLE，detail 逐键
        （condition_type/indicator 调用层补注/legal_types）。"""
        df = make_enhanced_df(n=30)
        dna = make_dna()
        dna.signal_genes = [
            make_signal_gene(condition_type="no_such_type", condition_value=50),
        ]
        with pytest.raises(ResearchError) as exc_info:
            dna_to_signal_set(dna, df)
        err = exc_info.value
        assert err.code == "RESEARCH_NOT_EXPRESSIBLE"
        assert err.detail["condition_type"] == "no_such_type"
        assert err.detail["indicator"] == "RSI"
        assert sorted(err.detail["legal_types"]) == sorted(EVALUABLE_CONDITION_TYPES)

    def test_s6c_malformed_params_stay_all_false(self):
        """范围限定锚：已知类型畸形参数防御分支维持全 False（不 raise）
        ——收口只针对未知类型，不扩大到参数形态。"""
        df = make_enhanced_df(n=30)
        # cross_above 已知但 threshold 缺失 → 既有防御分支全 False
        result = evaluate_condition(
            df["rsi_14"], df["close"], {"type": "cross_above"})
        assert isinstance(result, pd.Series)
        assert not result.any()

    def test_s6d_http_status_channel(self):
        """S6d：ResearchError 形制自带 status=422——经既有
        research-error-channel 传播零新通道（错误对象形制断言）。"""
        df = make_enhanced_df(n=30)
        dna = make_dna()
        dna.signal_genes = [
            make_signal_gene(condition_type="bogus", condition_value=1),
        ]
        with pytest.raises(ResearchError) as exc_info:
            dna_to_signal_set(dna, df)
        assert exc_info.value.status == 422


class TestColumnResolutionRejection:
    """S8a/S8b：第二静默面收口——列解析失败结构化拒绝。"""

    def test_s8a_column_resolution_failed_detail(self):
        """不可解析基因列 → RESEARCH_CONDITION_INVALID，detail 四键
        （indicator/params 原样/field/cause）。"""
        df = make_enhanced_df(n=30)
        gene = make_signal_gene(indicator="NOPE_GHOST", params={},
                                condition_value=50)
        dna = make_dna()
        dna.signal_genes = [gene]
        with pytest.raises(ResearchError) as exc_info:
            dna_to_signal_set(dna, df)
        err = exc_info.value
        assert err.code == "RESEARCH_CONDITION_INVALID"
        assert err.detail["indicator"] == "NOPE_GHOST"
        assert err.detail["params"] == {}
        assert err.detail["field"] is None
        assert "cause" in err.detail and err.detail["cause"]

    def test_s8a_field_echoed_when_declared(self):
        """field 两态的 str 态：gene.field_name 声明时 detail.field
        原样回显（field_name 是多产出指标的列名声明，直通不丢）。"""
        df = make_enhanced_df(n=30)
        gene = make_signal_gene(indicator="NOPE_GHOST", params={},
                                condition_value=50, field_name="ghost_col")
        dna = make_dna()
        dna.signal_genes = [gene]
        with pytest.raises(ResearchError) as exc_info:
            dna_to_signal_set(dna, df)
        assert exc_info.value.detail["field"] == "ghost_col"

    def test_s8b_two_silent_faces_both_reject(self):
        """两层静默专项：未知类型基因与不可解析列基因同策略共存 →
        均显式拒绝直达顶层（任一先触达即 raise，无一被吞）。"""
        df = make_enhanced_df(n=30)
        bad_type_gene = make_signal_gene(
            condition_type="mystery_type", condition_value=50)
        bad_col_gene = make_signal_gene(
            indicator="GHOST_COL", params={}, condition_value=50)
        dna = make_dna()
        dna.signal_genes = [bad_type_gene, bad_col_gene]
        with pytest.raises(ResearchError):
            dna_to_signal_set(dna, df)
        # 顺序反转同样直达（两基因都不可吞）
        dna2 = make_dna()
        dna2.signal_genes = [bad_col_gene, bad_type_gene]
        with pytest.raises(ResearchError):
            dna_to_signal_set(dna2, df)

    def test_s8c_no_continue_escape(self):
        """无 continue 逃逸：源码层锁定——单周期直译路径不存在
        「吞基因 continue」残留（第一基因坏即 raise，不再静默丢）。"""
        df = make_enhanced_df(n=30)
        dna = make_dna()  # 含合法 entry+exit 基因
        # 前插一个坏基因：raise 说明坏基因不被 continue 吞掉
        dna.signal_genes = [
            make_signal_gene(condition_type="zzz_unknown", condition_value=1),
            *dna.signal_genes,
        ]
        with pytest.raises(ResearchError):
            dna_to_signal_set(dna, df)


class TestMissingParamKeyRejection:
    """A5 四格锚（fix-executor-column-and-exit-guard D1）：两段×两类
    收口完备性——resolve 段与现算段各自拦 KeyError/TypeError，统一
    ValueError（文案锚 "bad or missing parameter key"），链路顶层
    收编 RESEARCH_CONDITION_INVALID（detail 四键、无 500 裸穿）。

    四格：a EMA(params={})——default-naming 族缺参，resolve get 不抛、
    落现算段 KeyError；b BB(period=20 缺 std)——参数化 naming 族，
    resolve 段 KeyError；c1 BB(period=None)——resolve 段 TypeError
    （int(None) 先于 std 缺失）；c2 EMA(period=None)——现算段
    TypeError（default 分支 get 不抛、落现算段 int(None)）。c2 专红
    「resolve 段包全两类、现算段偷懒只包 KeyError」的部分实现。
    """

    def _assert_value_error(self, df, gene):
        from core.strategy.executor import _get_indicator_column
        with pytest.raises(ValueError, match="bad or missing parameter key"):
            _get_indicator_column(df, gene)

    def _assert_research_error(self, df, gene, indicator, params):
        dna = make_dna()
        dna.signal_genes = [gene]
        with pytest.raises(ResearchError) as exc_info:
            dna_to_signal_set(dna, df)
        err = exc_info.value
        assert err.code == "RESEARCH_CONDITION_INVALID"
        assert err.status == 422          # 无 500 裸穿
        assert err.detail["indicator"] == indicator
        assert err.detail["params"] == params
        assert "field" in err.detail
        assert "bad or missing parameter key" in err.detail["cause"]

    def test_a5a_ema_missing_period_compute_segment(self):
        df = make_enhanced_df(n=30)
        gene = make_signal_gene(indicator="EMA", params={},
                                condition_type="gt", condition_value=0)
        self._assert_value_error(df, gene)
        self._assert_research_error(df, gene, "EMA", {})

    def test_a5b_bb_missing_std_resolve_segment(self):
        df = make_enhanced_df(n=30)
        gene = make_signal_gene(indicator="BB", params={"period": 20},
                                condition_type="price_below",
                                field_name="lower")
        self._assert_value_error(df, gene)
        self._assert_research_error(df, gene, "BB", {"period": 20})

    def test_a5c1_bb_none_period_resolve_segment(self):
        df = make_enhanced_df(n=30)
        gene = make_signal_gene(indicator="BB", params={"period": None},
                                condition_type="price_below",
                                field_name="lower")
        self._assert_value_error(df, gene)
        self._assert_research_error(df, gene, "BB", {"period": None})

    def test_a5c2_ema_none_period_compute_segment(self):
        df = make_enhanced_df(n=30)
        gene = make_signal_gene(indicator="EMA", params={"period": None},
                                condition_type="gt", condition_value=0)
        self._assert_value_error(df, gene)
        self._assert_research_error(df, gene, "EMA", {"period": None})

    def test_a8_unregistered_prefix_hitable_still_rejects(self):
        """A8：未注册指标且 df 存在前缀可命中列（"NOPE" vs "nope_x"，
        手工 DNA 直挂形态）→ 不存在「前缀命中静默取列」——与全落空
        形态（NOPE_GHOST 族 s8a）同码同 detail 形制拒绝。
        """
        df = make_enhanced_df(n=30)
        df["nope_x"] = df["close"]   # 前缀可命中的诱饵列
        gene = make_signal_gene(indicator="NOPE", params={},
                                condition_type="lt", condition_value=1)
        from core.strategy.executor import _get_indicator_column
        with pytest.raises(ValueError, match="Cannot find column for indicator NOPE"):
            _get_indicator_column(df, gene)
        dna = make_dna()
        dna.signal_genes = [gene]
        with pytest.raises(ResearchError) as exc_info:
            dna_to_signal_set(dna, df)
        err = exc_info.value
        assert err.code == "RESEARCH_CONDITION_INVALID"
        assert err.status == 422
        assert err.detail["indicator"] == "NOPE"
        assert err.detail["params"] == {}
        assert "cause" in err.detail and err.detail["cause"]


class TestEntryExitMutexWarning:
    """F1-F6（fix-executor-column-and-exit-guard D5）：同 bar 开平互斥
    清洗单点 helper——清洗逐字节等价（F3）+ 抹杀率告警（F1/F4 触发、
    F2/F5 不触发、F6 除零守护）。五处调用点（executor 层内/MTF 组合
    出口/单周期主路径/batch 组装出口/mtf_engine）的逐位等价由既有
    覆盖测试回归承载（SignalSet 全字段断言）。
    """

    @staticmethod
    def _bools(vals):
        return pd.Series(vals, dtype=bool)

    def test_f1_helper_full_kill_warns_and_clears(self, caplog):
        """F1 单元面：恒真出场形（部分进场全被抹）→ caplog 告警 +
        清洗后 entries 全 False（清洗语义不变）。"""
        from core.strategy.executor import _apply_entry_exit_mutex
        entries = self._bools([True, True, False, True])
        exits = self._bools([True, True, True, True])     # 恒真出场
        with caplog.at_level(logging.WARNING,
                             logger="core.strategy.executor"):
            cleaned = _apply_entry_exit_mutex(entries, exits)
        assert not bool(cleaned.any())                    # 全 False
        joined = "\n".join(r.getMessage() for r in caplog.records)
        assert "同 bar 进出场冲突抹杀进场信号" in joined
        assert "3/3（100%）" in joined                    # killed/total 数字

    def test_f1_dna_chain_full_kill_warns(self, caplog):
        """F1 链路面（BUG-2 实录形）：恒真出场 DNA 经 dna_to_signal_set
        → 告警在场 + entries 全 False（执行链兜底不经登记链的路径）。"""
        from core.strategy.dna import SignalGene, SignalRole
        df = make_enhanced_df(n=30)
        df["hour_utc"] = df.index.hour
        entry = SignalGene("RSI", {"period": 14}, SignalRole.ENTRY_TRIGGER,
                           None, {"type": "lt", "threshold": 30})
        exit_gene = SignalGene("hour_utc", {}, SignalRole.EXIT_TRIGGER,
                               None, {"type": "gt", "threshold": -1})
        dna = make_dna()
        dna.signal_genes = [entry, exit_gene]
        with caplog.at_level(logging.WARNING,
                             logger="core.strategy.executor"):
            ss = dna_to_signal_set(dna, df)
        assert not bool(ss.entries.any())
        assert any("同 bar 进出场冲突抹杀进场信号" in r.getMessage()
                   for r in caplog.records)

    def test_f2_normal_dna_no_warning(self, caplog):
        """F2：正常 DNA（出场低频，冲突比例远低于半数）无该告警。"""
        from core.strategy.dna import SignalGene, SignalRole
        df = make_enhanced_df(n=200)
        entry = SignalGene("RSI", {"period": 14}, SignalRole.ENTRY_TRIGGER,
                           None, {"type": "lt", "threshold": 50})
        exit_gene = SignalGene("RSI", {"period": 14}, SignalRole.EXIT_TRIGGER,
                               None, {"type": "gt", "threshold": 95})
        dna = make_dna()
        dna.signal_genes = [entry, exit_gene]
        with caplog.at_level(logging.WARNING,
                             logger="core.strategy.executor"):
            dna_to_signal_set(dna, df)
        assert not any("同 bar 进出场冲突抹杀进场信号" in r.getMessage()
                       for r in caplog.records)

    def test_f3_equivalent_to_inline_formula(self):
        """F3：清洗与 entries & ~(entries & exits) 逐位等价（随机
        布尔对 × 多组 seed）。"""
        from core.strategy.executor import _apply_entry_exit_mutex
        for seed in (1, 7, 42):
            rng = np.random.default_rng(seed)
            entries = pd.Series(rng.random(500) < 0.4)
            exits = pd.Series(rng.random(500) < 0.4)
            pd.testing.assert_series_equal(
                _apply_entry_exit_mutex(entries, exits),
                entries & ~(entries & exits), check_names=False)

    def test_f4_exact_half_boundary_triggers(self, caplog):
        """F4：killed/total 恰 0.5（2 进场 1 抹杀）触发（≥ 含边界）。"""
        from core.strategy.executor import _apply_entry_exit_mutex
        entries = self._bools([True, True, False])
        exits = self._bools([True, False, False])
        with caplog.at_level(logging.WARNING,
                             logger="core.strategy.executor"):
            _apply_entry_exit_mutex(entries, exits)
        assert any("1/2（50%）" in r.getMessage() for r in caplog.records)

    def test_f5_one_third_no_warning(self, caplog):
        """F5：1/3 形（killed/total=0.33 < 0.5）不触发。"""
        from core.strategy.executor import _apply_entry_exit_mutex
        entries = self._bools([True, True, True, False])
        exits = self._bools([True, False, False, False])
        with caplog.at_level(logging.WARNING,
                             logger="core.strategy.executor"):
            cleaned = _apply_entry_exit_mutex(entries, exits)
        assert not any("同 bar 进出场冲突抹杀进场信号" in r.getMessage()
                       for r in caplog.records)
        assert bool(cleaned.iloc[1]) and bool(cleaned.iloc[2])  # 清洗仍正确

    def test_f6_zero_entries_no_warning_no_division_error(self, caplog):
        """F6：total=0（进场空集）无告警无 ZeroDivisionError。"""
        from core.strategy.executor import _apply_entry_exit_mutex
        entries = self._bools([False, False])
        exits = self._bools([True, True])
        with caplog.at_level(logging.WARNING,
                             logger="core.strategy.executor"):
            cleaned = _apply_entry_exit_mutex(entries, exits)   # 不抛即过
        assert not bool(cleaned.any())
        assert not caplog.records

    # ---------------- F3 补锚：五处清洗点逐处在场性 ----------------
    # spec MUST「清洗点经单点 helper 同语义覆盖全部五处」——F1 链路锚
    # 只触达单周期主路径；其余四处任一漏替换回内联两行时，既有回归
    # （只护清洗语义）全绿而该路径告警 MUST 失效。以下四锚逐处锁
    # 「该路径经 helper 且告警在场」，推导链：F1-F6 锁 helper 行为 +
    # 本组锁每处调用点在链上。

    def test_f3a_evaluate_layer_internal_cleanup_warns(self, caplog):
        """F3-a 清洗点①evaluate_layer 层内（executor.py:664）：恒真
        出场层（entry 恒真 × exit 恒真）直调求值 → 告警在场 + entries
        全 False。该形制只经过层内清洗单点，caplog 来源无歧义。"""
        from core.strategy.dna import (
            LogicGenes, SignalGene, SignalRole, TimeframeLayer,
        )
        from core.strategy.executor import evaluate_layer
        df = make_enhanced_df(n=60, freq="1h", seed=3)
        df["hour_utc"] = df.index.hour
        layer = TimeframeLayer("1h", [
            SignalGene("hour_utc", {}, SignalRole.ENTRY_TRIGGER, None,
                       {"type": "gt", "threshold": -1}),
            SignalGene("hour_utc", {}, SignalRole.EXIT_TRIGGER, None,
                       {"type": "gt", "threshold": -1}),
        ], LogicGenes(), None)
        with caplog.at_level(logging.WARNING,
                             logger="core.strategy.executor"):
            ss = evaluate_layer(layer, df)
        assert not bool(ss.entries.any())            # 清洗语义不变
        assert any("同 bar 进出场冲突抹杀进场信号" in r.getMessage()
                   for r in caplog.records)

    def test_f3b_mtf_combined_exit_via_helper(self, monkeypatch):
        """F3-b 清洗点②MTF 组合出口（dna_to_signal_set MTF 分支）：
        spy 计数锚——两层数据下 helper 调用恰 3 次（逐层 evaluate_layer
        各 1 + 组合出口 1）。组合出口漏替换回内联两行时计数=2 即红。
        caplog 无法区分层内与组合出口的告警来源（单点 helper 同
        logger），故以调用计数锁在场性；helper 告警行为由 F1-F6 锚定。"""
        from core.strategy import executor as exc_mod
        from core.strategy.dna import (
            ExecutionGenes, LogicGenes, RiskGenes, SignalGene, SignalRole,
            StrategyDNA, TimeframeLayer,
        )
        calls = []
        real = exc_mod._apply_entry_exit_mutex

        def spy(entries, exits):
            calls.append(1)
            return real(entries, exits)

        monkeypatch.setattr(exc_mod, "_apply_entry_exit_mutex", spy)
        base = make_enhanced_df(n=120, freq="1h", seed=5)
        base["hour_utc"] = base.index.hour
        hi = make_enhanced_df(n=30, freq="1d", seed=6)
        hi["hour_utc"] = hi.index.hour
        gene = lambda role: SignalGene(   # noqa: E731 —— 两层同形构造
            "hour_utc", {}, role, None, {"type": "gt", "threshold": -1})
        dna = StrategyDNA(
            signal_genes=[],
            logic_genes=LogicGenes(),
            execution_genes=ExecutionGenes(timeframe="1h"),
            risk_genes=RiskGenes(),
            layers=[
                TimeframeLayer("1h", [gene(SignalRole.ENTRY_TRIGGER)],
                               LogicGenes(), None),
                TimeframeLayer("1d", [gene(SignalRole.ENTRY_TRIGGER)],
                               LogicGenes(), None),
            ],
        )
        assert dna.is_mtf, "测试前提：MTF 个体走组合分支"
        # dfs_by_timeframe 须含执行层自身数据（缺则层降级跳过——
        # executor.py:833 fail-loud 留痕，组合面只剩 1d 层）
        ss = dna_to_signal_set(dna, base, {"1h": base, "1d": hi})
        assert len(calls) == 3, (
            f"两层数据 helper 调用应恰 3 次（层内×2+组合出口×1），"
            f"实际 {len(calls)}——组合出口清洗未经单点 helper")
        assert bool(ss.entries.any())   # 两层恒真 entry、无 exit——组合后保留

    def test_f3c_batch_assembly_cleanup_warns(self, caplog):
        """F3-c 清洗点③batch 组装出口（batch_signal_sets 组装段
        executor.py:1296）：恒真出场形经批量通道 → 告警在场。单周期
        个体走基因去重组装（不经 evaluate_layer），组装出口是该路径
        唯一清洗点，caplog 来源无歧义。"""
        from core.strategy.dna import SignalGene, SignalRole
        df = make_enhanced_df(n=120, freq="1h", seed=7)
        df["hour_utc"] = df.index.hour
        dna = make_dna()
        dna.signal_genes = [
            SignalGene("hour_utc", {}, SignalRole.ENTRY_TRIGGER, None,
                       {"type": "gt", "threshold": -1}),
            SignalGene("hour_utc", {}, SignalRole.EXIT_TRIGGER, None,
                       {"type": "gt", "threshold": -1}),
        ]
        with caplog.at_level(logging.WARNING,
                             logger="core.strategy.executor"):
            results = batch_signal_sets([dna], df)
        assert len(results) == 1
        assert not bool(results[0].entries.any())    # 全抹（清洗语义不变）
        assert any("同 bar 进出场冲突抹杀进场信号" in r.getMessage()
                   for r in caplog.records)

    def test_f3d_mtf_engine_combined_warns(self, caplog):
        """F3-d 清洗点④mtf_engine._build_exec_signal_set
        （mtf_engine.py:853）：执行轴信号集合并出口直调——恒真 entry ×
        恒真 exit → 告警在场 + entries 全 False。mtf_engine 经
        from-import 绑定 helper，告警 logger 仍为 core.strategy.executor
        单源（本锚兼证 from-import 绑定未断）。"""
        from core.strategy.dna import LogicGenes, TimeframeLayer
        from core.strategy.executor import SignalSet
        from core.strategy.mtf_engine import (
            LayerResult, _build_exec_signal_set,
        )
        df = make_enhanced_df(n=60, freq="4h", seed=9)
        idx = df.index
        always = SignalSet(
            entries=pd.Series(True, index=idx),
            exits=pd.Series(True, index=idx),
            adds=pd.Series(False, index=idx),
            reduces=pd.Series(False, index=idx))
        dna = make_dna()   # execution timeframe="4h" 与层 tf 一致（reindex 直取）
        dna.layers = [TimeframeLayer("4h", [], LogicGenes(), None)]
        with caplog.at_level(logging.WARNING,
                             logger="core.strategy.executor"):
            out = _build_exec_signal_set(
                [("4h", LayerResult(signal_set=always))], dna, df)
        assert not bool(out.entries.any())           # 清洗语义不变
        assert any("同 bar 进出场冲突抹杀进场信号" in r.getMessage()
                   for r in caplog.records)


class TestDefenseIndependence:
    """S9：防线独立性——绕过清洗直调执行器仍兜底拒绝。"""

    def test_s9_executor_rejects_without_sanitizer(self):
        """进化清洗不在场（直调执行器）时，未知类型基因仍被
        evaluate_condition 兜底拒绝（清洗是优化非防线本体）。"""
        df = make_enhanced_df(n=30)
        with pytest.raises(ResearchError) as exc_info:
            evaluate_condition(
                df["rsi_14"], df["close"],
                {"type": "never_seen_type", "threshold": 1})
        assert exc_info.value.code == "RESEARCH_NOT_EXPRESSIBLE"


class TestBatchResearchErrorTolerance:
    """S12a/S12b/S12c：batch 域增捕——坏基因跳过+披露不炸批。"""

    def test_s12a_bad_gene_batch_survives_others_normal(self):
        """存量坏个体进 batch 不炸批；坏基因恒全 False、其余基因
        正常产信号（与不含坏基因的同 DNA 信号逐位一致）。"""
        df = make_enhanced_df(n=200)
        dna_clean = make_dna(entry_value=30, exit_value=70)
        dna_bad = make_dna(entry_value=30, exit_value=70)
        bad_gene = make_signal_gene(
            indicator="GHOST_INDICATOR_X", params={},
            role=dna_bad.signal_genes[0].role,
            condition_type="lt", condition_value=30)
        dna_bad.signal_genes = [bad_gene, *dna_bad.signal_genes[1:]]

        results = batch_signal_sets([dna_clean, dna_bad], df)

        assert len(results) == 2, "整批完成不炸"
        # 坏基因替换掉首个 entry 基因 → entries 恒 False（只剩坏基因
        # 在 entry 位）；exit 基因正常
        assert not bool(results[1].entries.any())
        # 干净个体不受同批坏个体影响
        solo = batch_signal_sets([dna_clean], df)
        pd.testing.assert_series_equal(
            results[0].entries, solo[0].entries, check_names=False)

    def test_s12b_warning_disclosure_countable(self, caplog):
        """四断言面：①warning 含 code 与 detail 键；②含 indicator+
        condition 概要；③个体级标识（indicator 即可得标识）；④warning
        条数==被跳过坏基因数（可计数性下限锚——逐基因披露+汇总计数）。"""
        df = make_enhanced_df(n=100)
        dna = make_dna()
        # 两个坏基因（均为未知类型——列不可解析走既有 ValueError
        # 白名单降级全 False，非 ResearchError 增捕分支，不在本断言面）
        dna.signal_genes = [
            make_signal_gene(indicator="RSI",
                             condition_type="ufo_type", condition_value=5),
            make_signal_gene(indicator="EMA",
                             condition_type="alien_type", condition_value=5),
            *dna.signal_genes[1:],
        ]
        with caplog.at_level(logging.WARNING, logger="core.strategy.executor"):
            results = batch_signal_sets([dna], df)

        assert len(results) == 1, "整批完成"
        msgs = [r for r in caplog.records
                if "跳过拒绝基因" in r.getMessage()]
        assert len(msgs) == 2, f"逐基因披露条数==坏基因数，got {len(msgs)}"
        for rec in msgs:
            msg = rec.getMessage()
            assert "code=RESEARCH_NOT_EXPRESSIBLE" in msg or \
                "code=RESEARCH_CONDITION_INVALID" in msg
            assert "detail=" in msg
            assert "indicator=" in msg
            assert "condition=" in msg
        # 逐基因披露 + 汇总计数两条都在场
        summary = [r for r in caplog.records
                   if "共拒绝" in r.getMessage()]
        assert len(summary) == 1
        assert "2" in summary[0].getMessage()

    def test_s12b_unknown_type_detail_supplemented(self, caplog):
        """batch 域补注面：未知类型 ResearchError 的 detail 在 batch
        内被补上 indicator（evaluate_condition 无 gene 视野）。"""
        df = make_enhanced_df(n=60)
        dna = make_dna()
        dna.signal_genes = [
            make_signal_gene(indicator="RSI",
                             condition_type="alien_type", condition_value=5),
            *dna.signal_genes[1:],
        ]
        with caplog.at_level(logging.WARNING, logger="core.strategy.executor"):
            batch_signal_sets([dna], df)
        rec = next(r for r in caplog.records
                   if "跳过拒绝基因" in r.getMessage())
        assert "indicator=RSI" in rec.getMessage()

    def test_s12c_two_domains_differ(self, caplog):
        """两域差异锁定：研究链（dna_to_signal_set）422 直达 /
        batch 日志披露不炸批——禁互套（batch 不 raise、直译不吞）。"""
        df = make_enhanced_df(n=60)
        dna = make_dna()
        dna.signal_genes = [
            make_signal_gene(condition_type="omega_bad", condition_value=5),
            *dna.signal_genes[1:],
        ]
        # 研究链：raise 直达
        with pytest.raises(ResearchError):
            dna_to_signal_set(dna, df)
        # batch 域：不 raise、warning 披露
        with caplog.at_level(logging.WARNING, logger="core.strategy.executor"):
            results = batch_signal_sets([dna], df)
        assert len(results) == 1
        assert any("跳过拒绝基因" in r.getMessage() for r in caplog.records)


class TestDegradationLogging:
    """H4-b（Q-4 维持+留痕，dna-strong-typing-and-disclosure-hygiene 件③）：
    两面降级行为零变化（全 False/空信号集维持），纯日志补齐——

    - 基因级白名单 (ValueError, KeyError, TypeError)：单基因求值失败
      →该基因全 False——补逐基因 warning（indicator+异常类名，沿
      ResearchError 跳过基因的 :1100-1103 同款可观测形制）；
    - 个体级组装层 except Exception：→_empty_signal_set——补逐个体
      warning（strategy_id+异常类名——零信号策略可归因）。
    """

    def test_gene_whitelist_degradation_logged(self, caplog):
        """白名单基因降级留痕：未知指标+合法条件类型走 ValueError
        白名单（_get_indicator_column 列缺失——S12a 同款形态）→
        warning 含 indicator 与异常类名（现状静默——红）。"""
        df = make_enhanced_df(n=60)
        dna = make_dna()
        dna.signal_genes = [
            make_signal_gene(indicator="GHOST_X",
                             condition_type="lt", condition_value=30),
            *dna.signal_genes[1:],
        ]
        with caplog.at_level(logging.WARNING, logger="core.strategy.executor"):
            results = batch_signal_sets([dna], df)
        assert len(results) == 1, "行为面：降级不炸批维持"
        msgs = [r for r in caplog.records
                if "基因求值降级" in r.getMessage()]
        assert msgs, "白名单基因降级无留痕（Q-4 +留痕未落地）"
        assert "indicator=GHOST_X" in msgs[0].getMessage()
        assert "exception=" in msgs[0].getMessage()

    def test_individual_assembly_degradation_logged(self, caplog, monkeypatch):
        """个体级组装层降级留痕：MTF 个体组装抛普通异常 →空信号集
        维持+warning 含 strategy_id 与异常类名（现状静默——红）。"""
        from core.strategy import executor as exc_mod
        from core.strategy.dna import (
            ExecutionGenes, LogicGenes, RiskGenes, StrategyDNA,
            TimeframeLayer,
        )

        base = make_enhanced_df(n=120, freq="1h", seed=21)
        hi = make_enhanced_df(n=8, freq="1d", seed=22)
        # 显式 layers 构造（is_mtf 判据=_layers_explicit，构造期标记）
        dna = StrategyDNA(
            signal_genes=make_dna().signal_genes,
            logic_genes=LogicGenes(),
            execution_genes=ExecutionGenes(timeframe="1h"),
            risk_genes=RiskGenes(),
            layers=[TimeframeLayer(
                "1h", make_dna().signal_genes, LogicGenes(), None)],
        )
        dna.strategy_id = "st_deg_log"
        assert dna.is_mtf, "测试前提：MTF 个体走组装分支"

        def _boom(*a, **kw):
            raise RuntimeError("组装意外")

        monkeypatch.setattr(exc_mod, "dna_to_signal_set", _boom)
        with caplog.at_level(logging.WARNING, logger="core.strategy.executor"):
            results = batch_signal_sets(
                [dna], base, dfs_by_timeframe={"1d": hi})
        assert len(results) == 1, "行为面：空信号集降级维持不炸批"
        msgs = [r for r in caplog.records
                if "个体组装降级" in r.getMessage()]
        assert msgs, "个体组装降级无留痕（Q-4 +留痕未落地）"
        assert "strategy_id=st_deg_log" in msgs[0].getMessage()
        assert "exception=RuntimeError" in msgs[0].getMessage()


class TestIndicatorSupplementGuard:
    """补注守卫的 False 侧（覆盖率盲区消解）：ResearchError.detail
    已含 indicator 时不被调用层覆盖——防双写抹掉既有上下文。当前
    调用链 evaluate_condition 恒不带 indicator，故经 monkeypatch
    控制异常形制直证守卫行为。"""

    @staticmethod
    def _raiser():
        from core.analysis.errors import ResearchError

        def _raise(*args, **kwargs):
            raise ResearchError(
                "RESEARCH_NOT_EXPRESSIBLE", "探针",
                detail={"condition_type": "eq", "indicator": "ORIGINAL_CTX"})
        return _raise

    def test_direct_path_keeps_existing_indicator(self, monkeypatch):
        """直译路径：detail 已含 indicator=ORIGINAL_CTX → 原样透出
        （不被 gene.indicator 覆盖为 RSI）。"""
        from core.strategy import executor as executor_mod

        monkeypatch.setattr(executor_mod, "evaluate_condition",
                            self._raiser())
        df = make_enhanced_df(n=30)
        dna = make_dna()
        with pytest.raises(ResearchError) as exc_info:
            dna_to_signal_set(dna, df)
        assert exc_info.value.detail["indicator"] == "ORIGINAL_CTX"

    def test_batch_path_keeps_existing_indicator(self, monkeypatch, caplog):
        """batch 路径同款守卫：detail 内既有 indicator 保留
        ORIGINAL_CTX 不被 gene.indicator 覆盖；warning 的 indicator=
        打 gene 名（披露拒绝发生在哪个基因，与 detail 补注两回事）。"""
        from core.strategy import executor as executor_mod

        monkeypatch.setattr(executor_mod, "evaluate_condition",
                            self._raiser())
        df = make_enhanced_df(n=60)
        dna = make_dna()
        with caplog.at_level(logging.WARNING, logger="core.strategy.executor"):
            results = batch_signal_sets([dna], df)
        assert len(results) == 1, "整批完成"
        # 串台隔离修复（fix-fullrun-baseline-4red ④）：caplog 按
        # propagation 捕获全局日志——全量态取证实证泄漏的
        # evolution-runner 线程（前序测试 lifespan join 超时遗留）
        # 的「跳过拒绝基因」warning 串入本测试记录集且排前，旧检索
        # （next 取第一条）错取串台记录致 RSI 断言摇摆红。按产生线程
        # 归属过滤（fullrun-discipline 测试独立性：断言锚定本测试
        # 上下文产生的记录）——守护两断言语义原样；泄漏线程记录不属
        # 于本测试产物，排除有定性依据（api/app.py:112-118 工程档案
        # 「缺陷④跨域 flaky 实测锁定」+runner daemon api/runner.py:134
        # +join 超时泄漏 api/app.py:133）。泄漏本身不修（TD-③ 在册）。
        import threading
        mine = [r for r in caplog.records
                if "跳过拒绝基因" in r.getMessage()
                and r.thread == threading.get_ident()]
        assert mine, "本测试 batch 调用未产生拒绝基因 warning"
        msg = mine[0].getMessage()
        assert "indicator=RSI" in msg, "warning 披露 gene 名"
        assert "ORIGINAL_CTX" in msg, "detail 内既有上下文保留"

    def test_cross_thread_log_filter_keeps_assertion_stable(
            self, monkeypatch, caplog):
        """④串台确定性承载（fullrun-discipline「泄漏线程日志不
        干扰断言」scenario 的常驻验证）：伪造泄漏线程向同 logger
        打排前的 MACD warning，本测试主线程 batch 调用产 RSI
        warning——按产生线程归属过滤检索必选本线程记录。排前
        断言同时证明「退回旧检索（取第一条）必错取串台记录」
        ——检索若退回不过滤形制，本用例即红。注入与断言同在
        方法体内（caplog setup 期记录在 call 期被清——本 change
        DELIVERY 坑位 2）。"""
        import threading

        from core.strategy import executor as executor_mod

        monkeypatch.setattr(executor_mod, "evaluate_condition",
                            self._raiser())
        logger = logging.getLogger("core.strategy.executor")
        # 伪造串台（先落记录=排前形态）：形制贴 executor.py:1101
        # 生产 warning（「跳过拒绝基因」+ indicator=%s）
        spoofer = threading.Thread(
            target=lambda: logger.warning(
                "batch_signal_sets 跳过拒绝基因 code=%s detail=%r "
                "indicator=%s condition=%r",
                "RESEARCH_NOT_EXPRESSIBLE", {"probe": "串台注入"},
                "MACD", {"type": "eq"}),
            name="evolution-runner")
        df = make_enhanced_df(n=60)
        dna = make_dna()
        with caplog.at_level(logging.WARNING,
                             logger="core.strategy.executor"):
            spoofer.start()
            spoofer.join()
            results = batch_signal_sets([dna], df)
        assert len(results) == 1, "整批完成"
        rejected = [r for r in caplog.records
                    if "跳过拒绝基因" in r.getMessage()]
        assert rejected, "前置：拒绝基因 warning 在场"
        assert any(r.thread != threading.get_ident()
                   for r in rejected), "前置：串台记录确属他线程"
        assert "indicator=MACD" in rejected[0].getMessage(), \
            "前置：串台记录排前（旧检索取第一条必错取——过滤必要性锚）"
        mine = [r for r in rejected
                if r.thread == threading.get_ident()]
        assert mine, "本测试 batch 调用未产生拒绝基因 warning"
        msg = mine[0].getMessage()
        assert "indicator=RSI" in msg, "线程过滤后仍捕本测试记录"
        assert "ORIGINAL_CTX" in msg, "detail 内既有上下文保留"
        assert not any("indicator=MACD" in r.getMessage()
                       for r in mine), "串台记录被线程归属过滤排除"


class TestGeneLevelTimeframeExecution:
    """T3/T4a：基因级周期执行链（unblock-research-validation 任务 3）。"""

    def _mtf_env(self, n_base=400):
        """1h 基准 + 1d 高周期轴（make_enhanced_df 指标双轴齐备）。"""
        from tests.helpers.data_factory import make_enhanced_df
        base = make_enhanced_df(n=n_base, freq="1h", seed=21)
        # 高周期轴：同行情 1d 重采样形制（独立合成即可——对照用同轴）
        hi = make_enhanced_df(n=n_base // 24 + 2, freq="1d", seed=22)
        return base, hi

    def test_t3b_higher_tf_evaluation_independent_reference(self):
        """T3b 高周期求值独立对照：基因 RSI(14)@1d lt 阈值 → 信号 ==
        1d 轴手工求值经 project_completed_bool 投影（mtf_alignment
        权威函数独立构造期望）。"""
        from core.data.mtf_alignment import project_completed_bool
        from core.strategy.dna import ExecutionGenes, LogicGenes, RiskGenes, StrategyDNA
        base, hi = self._mtf_env()
        # 纯 entry DNA（隔离对照——make_dna 自带 exit 基因会经既有
        # 「同 bar 进出清洗」语义改写 entries，非投影面差异）
        dna = StrategyDNA(
            signal_genes=[make_dna().signal_genes[0]],
            logic_genes=LogicGenes(),
            execution_genes=ExecutionGenes(timeframe="1h"),
            risk_genes=RiskGenes())
        dna.signal_genes[0].timeframe = "1d"
        sig = dna_to_signal_set(dna, base, dfs_by_timeframe={"1d": hi})
        # 独立对照：1d 轴上手工求值同条件 → 权威投影
        hi_col = hi["rsi_14"]
        hi_signal = (hi_col < 30).fillna(False)
        expect = project_completed_bool(hi_signal, base.index, "1d")
        pd.testing.assert_series_equal(
            sig.entries.reindex(base.index).fillna(False), expect,
            check_names=False)

    def test_t3c_axis_missing_fail_loud(self):
        """T3c 轴缺失 fail-loud：基因级 1d 但装载集无 1d → 结构化拒绝
        （禁止静默降级为基准周期求值）。"""
        base, hi = self._mtf_env(120)
        dna = make_dna()
        dna.signal_genes[0].timeframe = "1d"
        dna.execution_genes.timeframe = "1h"
        with pytest.raises(ResearchError) as ei:
            dna_to_signal_set(dna, base, dfs_by_timeframe={"4h": hi})
        assert ei.value.code == "RESEARCH_DATA_INSUFFICIENT"
        assert ei.value.detail["timeframe"] == "1d"

    def test_t3a_baseline_genes_byte_identical(self):
        """T3a 疫苗 3 前置：无 tf 基因在 dfs_by_timeframe 在场时仍走
        基准路径（与不传轴逐位一致）。"""
        base, hi = self._mtf_env(200)
        dna = make_dna()
        a = dna_to_signal_set(dna, base)
        b = dna_to_signal_set(dna, base, dfs_by_timeframe={"1d": hi})
        pd.testing.assert_series_equal(a.entries, b.entries, check_names=False)

    def test_t3f_batch_single_point_consistency(self):
        """T3f batch 与主路径单点一致：同 DNA（带基因级 tf）两域
        entries 逐位一致。"""
        base, hi = self._mtf_env(240)
        dna = make_dna()
        dna.signal_genes[0].timeframe = "1d"
        dna.execution_genes.timeframe = "1h"
        solo = dna_to_signal_set(dna, base, dfs_by_timeframe={"1d": hi})
        batched = batch_signal_sets([dna], base, dfs_by_timeframe={"1d": hi})
        pd.testing.assert_series_equal(
            solo.entries, batched[0].entries, check_names=False)

    def test_t4a_collect_needed_tfs_gene_level(self):
        """T4a 装载判据三层并集：layers ∪ 基因级 tf ∪ 执行周期。"""
        from core.analysis.backtest_bridge import _collect_needed_tfs
        d = make_dna()                      # 单层无 tf
        assert _collect_needed_tfs(d, "4h") == set()
        d.signal_genes[0].timeframe = "1d"  # 单层+基因级
        assert _collect_needed_tfs(d, "4h") == {"1d", "4h"}
        from tests.helpers.data_factory import make_mtf_dna
        m = make_mtf_dna(timeframes=("1d", "4h"))
        assert _collect_needed_tfs(m, "1h") == {"1d", "4h", "1h"}

    def test_t3e_evolution_offspring_baseline_path(self):
        """T3e 进化产物恒现路径：无 tf 基因（进化恒产）求值不受
        dfs_by_timeframe 在场影响（进化评估装载传轴场景）。"""
        base, hi = self._mtf_env(150)
        dna = make_dna(entry_value=30, exit_value=70)
        a = batch_signal_sets([dna], base)
        b = batch_signal_sets([dna], base, dfs_by_timeframe={"1d": hi})
        pd.testing.assert_series_equal(
            a[0].entries, b[0].entries, check_names=False)


class TestGeneTfProjectionOrder:
    """T2g-2/T2g-3/T3d/T2f-2 信号层（tester 补——spec delta「多周期
    与事件策略组合」「加仓触发组多周期放行」Scenario 推导：先投影
    后 policy、1d 广播窗 first_true 取首 bar、防前视不早于收盘、
    adds=状态窗逐 bar）。"""

    _ENTRY_ONLY_DNA = None

    def _tf_dna(self, roles_tfs, entry_value=70, exit_value=99):
        """构造带基因级 tf 的 DNA（roles_tfs=[(SignalRole, tf)]）。"""
        from core.strategy.dna import (
            ExecutionGenes, LogicGenes, RiskGenes, SignalRole, StrategyDNA,
        )
        genes = []
        for role, tf in roles_tfs:
            g = make_signal_gene(
                role=role, condition_type=(
                    "gt" if role == SignalRole.EXIT_TRIGGER else "lt"),
                condition_value=(
                    exit_value if role == SignalRole.EXIT_TRIGGER
                    else entry_value))
            g.timeframe = tf
            genes.append(g)
        return StrategyDNA(
            signal_genes=genes, logic_genes=LogicGenes(),
            execution_genes=ExecutionGenes(timeframe="1h"),
            risk_genes=RiskGenes())

    def _hand_hi_signal(self, hi, entry_value=70):
        """独立期望：1d 轴手工求值同条件（不调被测求值实现）。"""
        return (hi["rsi_14"] < entry_value).fillna(False)

    def test_t3d_no_lookahead_not_before_close(self):
        """T3d 防前视：1d 信号投影 4h 基准轴，首个 True 不早于 1d
        首个 True bar 的收盘时点（已收盘才可见——契约前视纪律）。"""
        from core.data.timeframes import TF_SECONDS
        from core.strategy.dna import SignalRole
        base, hi = self._hand_env()
        dna = self._tf_dna([(SignalRole.ENTRY_TRIGGER, "1d"),
                            (SignalRole.EXIT_TRIGGER, None)],
                           entry_value=30, exit_value=99)
        # 独立期望：1d 轴手工求值首 True 的收盘时刻
        hi_sig = self._hand_hi_signal(hi, entry_value=30)
        assert hi_sig.any(), "前提：1d 轴存在 True（构造保证）"
        first_hi = hi_sig[hi_sig].index[0]
        close_ts = first_hi + pd.Timedelta(seconds=TF_SECONDS["1d"])
        sig = dna_to_signal_set(dna, base, dfs_by_timeframe={"1d": hi})
        entries = sig.entries.reindex(base.index).fillna(False)
        assert entries.any(), "投影后有信号（前提）"
        first_base = entries[entries].index[0]
        assert first_base >= close_ts, (
            f"投影信号早于 1d 收盘时点：{first_base} < {close_ts}")

    def _hand_env(self, n_base=400):
        from tests.helpers.data_factory import make_enhanced_df
        base = make_enhanced_df(n=n_base, freq="1h", seed=21)
        hi = make_enhanced_df(n=n_base // 24 + 2, freq="1d", seed=22)
        return base, hi

    def test_t2g_2_first_true_takes_first_bar_of_window(self):
        """T2g-2/T2g-3 顺序契约+同构：1d 广播窗（连续 True）经
        first_true 变换取每窗首 bar——期望=权威投影+权威 policy 函数
        串联独立构造（policy 语义不因投影层漂移）。"""
        from core.analysis.event_policy import apply_policy_to_series
        from core.data.mtf_alignment import project_completed_bool
        from core.strategy.dna import SignalRole
        base, hi = self._hand_env()
        dna = self._tf_dna([(SignalRole.ENTRY_TRIGGER, "1d"),
                            (SignalRole.EXIT_TRIGGER, None)],
                           entry_value=70, exit_value=99)
        dna.entry_trigger_policy = {"mode": "first_true"}
        # 独立期望：手工 1d 求值 → 权威投影（广播窗）→ 权威 policy
        hi_sig = self._hand_hi_signal(hi, entry_value=70)
        broadcast = project_completed_bool(hi_sig, base.index, "1d")
        # 前提鉴别力：存在长度>1 的连续窗（first_true 才有可观察差异）
        runs = (broadcast != broadcast.shift(fill_value=False)).cumsum()
        run_lens = broadcast.groupby(runs).sum()
        assert (run_lens[run_lens.index.map(
            lambda k: broadcast.groupby(runs).first()[k]) > 0] > 1).any(), \
            "前提：存在多 bar 广播窗"
        expected = apply_policy_to_series(
            broadcast, {"mode": "first_true"})
        sig = dna_to_signal_set(dna, base, dfs_by_timeframe={"1d": hi})
        got = sig.entries.reindex(base.index).fillna(False)
        pd.testing.assert_series_equal(
            got, expected.astype(bool), check_names=False)

    def test_t2f_2_adds_broadcast_state_window(self):
        """T2f-2 信号层：ADD_TRIGGER tf=1d → adds=掩码广播状态窗
        （窗内逐 bar True 连续，非边沿脉冲）——引擎步进/封顶机制
        既有锚见 tests/backtest/test_scaling_execution.py。"""
        from core.data.mtf_alignment import project_completed_bool
        from core.strategy.dna import SignalRole
        base, hi = self._hand_env()
        dna = self._tf_dna(
            [(SignalRole.ENTRY_TRIGGER, None),
             (SignalRole.EXIT_TRIGGER, None),
             (SignalRole.ADD_TRIGGER, "1d")],
            entry_value=70, exit_value=99)
        hi_sig = self._hand_hi_signal(hi, entry_value=70)
        expected = project_completed_bool(hi_sig, base.index, "1d")
        sig = dna_to_signal_set(dna, base, dfs_by_timeframe={"1d": hi})
        got = sig.adds.reindex(base.index).fillna(False)
        pd.testing.assert_series_equal(
            got, expected, check_names=False)
        # 状态窗断言：True 段为连续多 bar（广播语义，非单 bar 边沿）
        if got.any():
            runs = (got != got.shift(fill_value=False)).cumsum()
            run_lens = got.groupby(runs).sum()
            assert (run_lens[run_lens > 0] > 1).any(), \
                "广播窗应为连续多 bar 状态窗"


class TestNeededTfsSingleSource:
    """T4e 条件性单点断言（tester 补——任务 3a 采纳合并单源：
    backtest_service.compute_needed_tfs 逐 DNA 委托研究链收集器
    _collect_needed_tfs——patch 收集器后工程域行为跟随即证明委托，
    双实现漂移在结构上不可发生）。"""

    def test_t4e_compute_needed_tfs_delegates_single_collector(self, monkeypatch):
        import core.analysis.backtest_bridge as bridge
        from core.services.backtest_service import compute_needed_tfs
        calls = []
        orig = bridge._collect_needed_tfs

        def spy(dna, exec_tf):
            calls.append((getattr(dna, "strategy_id", None), exec_tf))
            return orig(dna, exec_tf)

        monkeypatch.setattr(bridge, "_collect_needed_tfs", spy)
        d1, d2 = make_dna(), make_dna()
        d1.signal_genes[0].timeframe = "1d"
        out = compute_needed_tfs([d1, d2], "4h")
        assert len(calls) == 2, "逐 DNA 全部经单源收集器"
        assert out == {"1d", "4h"}, "聚合语义保持（任一 DNA 需装载即装载）"


class TestActionSetSingleSource:
    """动作集单一权威源断言网（indicator-and-action-repair B6 卡 D
    4.3，F1 五表三层断言）——任一侧漂移即红。

    五表：①registry supported_conditions ②executor
    EVALUABLE_CONDITION_TYPES ③翻译 _ACTION_MAP ④统计面
    action_map（STATISTICS_ACTION_KEYS 事实源）⑤case_inspection
    _SUPPORTED_ACTIONS；单源=RESEARCH_EXPRESSIBLE_ACTIONS（登记面
    与翻译面同源引用）。
    """

    def test_expressible_subset_evaluable(self):
        """①可表达 ⊆ 执行合法（可表达必可执行——按翻译产物 type
        投影：breakout/breakdown 为翻译层别名，产物 type 归一为
        cross_above/cross_below 后须全部在执行合法集）。"""
        from core.analysis.hypothesis_service import (
            RESEARCH_EXPRESSIBLE_ACTIONS)
        _ALIAS = {"breakout": "cross_above", "breakdown": "cross_below"}
        projected = {_ALIAS.get(a, a) for a in RESEARCH_EXPRESSIBLE_ACTIONS}
        assert projected <= EVALUABLE_CONDITION_TYPES

    def test_action_map_subset_expressible(self):
        """②翻译 _ACTION_MAP ⊆ 可表达集（2.5 时点 10 ⊆ 14；2.7 扩
        至 13 后单源恰等断言生效——本断言恒成立的超集关系保持）。"""
        from core.analysis.hypothesis_service import (
            RESEARCH_EXPRESSIBLE_ACTIONS)
        from core.analysis.hypothesis_to_strategy import _ACTION_MAP
        assert frozenset(_ACTION_MAP) <= RESEARCH_EXPRESSIBLE_ACTIONS

    def test_registry_union_subset_evaluable(self):
        """③registry 并集 ⊆ 执行合法（S6b 主断言为更强双向相等——
        本条为引用性关系断言，防 S6b 语义变更时静默丢失包含关系）。"""
        from core.features.registry import INDICATOR_REGISTRY
        union = set()
        for reg in INDICATOR_REGISTRY.values():
            union.update(reg.supported_conditions)
        assert union <= EVALUABLE_CONDITION_TYPES

    def test_statistics_keys_equal_union(self):
        """④统计面键集 == 可表达 ∪ STATISTICS_ONLY（恰等——差集
        显式化非静默；统计面新增动作不同步两常量即红）。"""
        from core.analysis.hypothesis_service import (
            RESEARCH_EXPRESSIBLE_ACTIONS, STATISTICS_ONLY_ACTIONS)
        from core.validation.engine import STATISTICS_ACTION_KEYS
        assert STATISTICS_ACTION_KEYS == (
            RESEARCH_EXPRESSIBLE_ACTIONS | STATISTICS_ONLY_ACTIONS)

    def test_case_inspection_subset_statistics(self):
        """⑤case_inspection 通道集 ⊆ 统计面键集（可达子集声明）。"""
        from core.analysis.case_inspection import _SUPPORTED_ACTIONS
        from core.validation.engine import STATISTICS_ACTION_KEYS
        assert _SUPPORTED_ACTIONS <= STATISTICS_ACTION_KEYS

    def test_history_heterogeneous_sets_exempt(self):
        """F2 历史异集豁免面固化：两处 ConditionType 枚举（dna.py
        15 成员无 eq/schemas.py 8 成员）不参与断言——豁免纪律锚
        （防后续维护者误拉平两侧枚举触发假红）。"""
        import core.strategy.dna as dna_mod
        ctype = getattr(dna_mod, "ConditionType", None)
        if ctype is not None:  # 枚举在场即固化「无 eq」现状
            members = {m.value for m in ctype}
            assert "eq" not in members, \
                "dna.ConditionType 历史异集（无 eq）——拉平须经裁决"
