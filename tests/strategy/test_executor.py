"""执行器条件合法集契约与双静默面收口测试（unify-tool-return-contract）。

覆盖任务 1（合法集常量同源）与任务 3（未知类型结构化拒绝 S6 /
列解析收口 S8 / 防线独立性 S9 / batch 增捕 S12）全部断言面。
锚点编号=backend-tester 包一方案（S6b 双向相等主断言等）。
"""
from __future__ import annotations

import logging

import pytest

pytestmark = [pytest.mark.unit]

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

    def test_s6b_constant_is_frozenset_of_16(self):
        """常量形制锚：frozenset、16 类型（执行器实际分派集基数）。"""
        assert isinstance(EVALUABLE_CONDITION_TYPES, frozenset)
        assert len(EVALUABLE_CONDITION_TYPES) == 16

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
        rec = next(r for r in caplog.records
                   if "跳过拒绝基因" in r.getMessage())
        msg = rec.getMessage()
        assert "indicator=RSI" in msg, "warning 披露 gene 名"
        assert "ORIGINAL_CTX" in msg, "detail 内既有上下文保留"
