"""hour_utc 通道测试（add-session-structure-expression 任务 7）。

U7a-h：双入口注入/值形制/周期域三面/统计面/全链四层/八列零变化/
action 反面/预热防炸。
"""
from __future__ import annotations

from pathlib import Path
import json as _json

import numpy as np
import pandas as pd
import pytest

pytestmark = [pytest.mark.unit]

from tests.helpers.data_factory import make_ohlcv


class TestInjection:
    """U7a/b：双入口注入+值形制。"""

    def _parquet_env(self, tmp_path: Path):
        d = tmp_path / "market"
        d.mkdir(exist_ok=True)
        make_ohlcv(n=200, freq="1h", start="2024-01-01").to_parquet(
            d / "BTCUSDT_1h.parquet")
        make_ohlcv(n=60, freq="4h", start="2024-01-01").to_parquet(
            d / "BTCUSDT_4h.parquet")
        return d

    def test_u7a_both_loaders_inject(self, tmp_path):
        """U7a 双入口对拍：load_and_prepare_df 与 load_mtf_data 各轴都
        带 hour_utc 列（只挂一处必红）。"""
        from core.data.mtf_loader import load_and_prepare_df, load_mtf_data
        d = self._parquet_env(tmp_path)
        single = load_and_prepare_df(d, "BTCUSDT", "1h")
        assert "hour_utc" in single.columns
        multi = load_mtf_data(d, "BTCUSDT", "1h", single,
                              needed_tfs={"1h", "4h"})
        assert "hour_utc" in multi["1h"].columns
        assert "hour_utc" in multi["4h"].columns

    def test_u7a_daily_frame_not_injected(self, tmp_path):
        """U7a 补（D-1/C3 分周期注入）：>=1d 帧不注入 hour_utc（design
        2.7 双保险的物理列缺失面——日线值恒 0 无意义，域校验拒绝为
        第一保险，物理缺失为第二保险）。"""
        from core.data.mtf_loader import load_and_prepare_df, load_mtf_data
        d = self._parquet_env(tmp_path)
        make_ohlcv(n=60, freq="1d", start="2024-01-01").to_parquet(
            d / "BTCUSDT_1d.parquet")
        single = load_and_prepare_df(d, "BTCUSDT", "1d",
                                     compute_indicators=False)
        assert "hour_utc" not in single.columns, "1d 单周期帧不注入"
        base = load_and_prepare_df(d, "BTCUSDT", "1h")
        multi = load_mtf_data(d, "BTCUSDT", "1h", base,
                              needed_tfs={"4h", "1d"})
        assert "hour_utc" not in multi["1d"].columns, "MTF 1d 轴不注入"
        assert "hour_utc" in multi["4h"].columns, "4h 轴照常注入"

    def test_u7b_value_form(self):
        """U7b 值形制：[0,24) 半开浮点——00:00→0.0、23:59→23.9833。"""
        from core.data.derived_columns import inject_derived_columns
        idx = pd.DatetimeIndex(
            [pd.Timestamp("2024-01-01 00:00", tz="UTC"),
             pd.Timestamp("2024-01-01 23:59", tz="UTC"),
             pd.Timestamp("2024-01-02 12:30", tz="UTC")], name="timestamp")
        df = inject_derived_columns(pd.DataFrame(
            {"close": [1.0, 2.0, 3.0]}, index=idx))
        assert df["hour_utc"].tolist() == pytest.approx(
            [0.0, 23.9833333, 12.5])
        assert ((df["hour_utc"] >= 0) & (df["hour_utc"] < 24)).all()


class TestDomainReject:
    """U7c：周期域拒绝三面（同码同 detail 键集）。"""

    def _detail_keys(self):
        return {"column", "base_tf", "domain", "hint", "ref_tf"}

    def test_u7c_resolve_face_rejection(self):
        """resolve 直调（分析面）拒绝形制：同码同 detail 键集（ref_tf=
        校验所用引用周期，base_tf=回退基准；D-1 修复定稿形制）。登记/
        翻译 canonical 面随 retire-legacy 登记链退役（字符串 subject
        在翻译面不走 resolve）；detail 键集单源锚保留在分析面。"""
        from core.analysis.column_ref import ColumnRefError, resolve_column_ref
        with pytest.raises(ColumnRefError) as e1:
            resolve_column_ref("hour_utc", base_tf="1d")
        assert e1.value.code == "PARAM_INVALID"
        assert set(e1.value.detail) == self._detail_keys()

    def test_u7g_cross_actions_rejected(self, tmp_path):
        """U7g action 域锁反面：cross_above/price_above 拒（翻译面
        _translate_conditions 直断——登记链退役后值域层唯一存活面）。"""
        from core.persistence.db_ext import init_db_ext
        from core.analysis.executor import ensure_research_tables
        from core.analysis.errors import ResearchError
        from core.analysis.hypothesis_to_strategy import _translate_conditions
        from core.strategy.dna import SignalRole
        db = tmp_path / "q.db"
        init_db_ext(db); ensure_research_tables(db)
        for act in ("cross_above", "price_above"):
            with pytest.raises(ResearchError):
                _translate_conditions(
                    [{"subject": "hour_utc", "action": act, "target": 12}],
                    db_path=db, label="when_conditions",
                    role=SignalRole.ENTRY_TRIGGER, base_tf="4h")


class TestFullChain:
    """U7e：全链四层——登记/翻译/执行直读白名单。"""

    def test_u7e_full_chain_gene_and_eval(self, tmp_path):
        """翻译（基因三元组）→执行（直读白名单求值）——登记链退役后
        经 _translate_conditions 翻译面承载全链。"""
        from core.persistence.db_ext import init_db_ext
        from core.analysis.executor import ensure_research_tables
        from core.analysis.hypothesis_to_strategy import _translate_conditions
        from core.strategy.dna import (
            ExecutionGenes, LogicGenes, RiskGenes, StrategyDNA)
        from core.strategy.executor import dna_to_signal_set, clear_indicator_cache

        db = tmp_path / "q.db"
        init_db_ext(db); ensure_research_tables(db)
        d = tmp_path / "market"; d.mkdir()
        make_ohlcv(n=300, freq="1h", start="2024-01-01").to_parquet(
            d / "BTCUSDT_1h.parquet")
        genes, sources = _translate_conditions(
            [{"subject": "hour_utc", "action": "gt", "target": 12}],
            db_path=db, label="when_conditions",
            role=__import__("core.strategy.dna", fromlist=["SignalRole"]).SignalRole.ENTRY_TRIGGER,
            base_tf="1h")
        g = genes[0]
        assert (g.indicator, g.params, g.field_name) == \
            ("hour_utc", {}, None), "基因三元组"
        # 执行直读（装载注入列在场）
        from core.data.mtf_loader import load_and_prepare_df
        df = load_and_prepare_df(d, "BTCUSDT", "1h")
        dna = StrategyDNA(
            signal_genes=list(genes), logic_genes=LogicGenes(),
            execution_genes=ExecutionGenes(timeframe="1h"),
            risk_genes=RiskGenes(sl_mode="pct", stop_loss=0.05),
            signal_sources=[{"type": "rule"},
                            *(dict(s) for s in (sources or []))])
        clear_indicator_cache()
        sig = dna_to_signal_set(dna, df)
        # 13:00-23:00 的 bar 触发（hour>12）
        expect = df["hour_utc"] > 12
        assert bool(sig.entries.any())
        assert int(sig.entries.sum()) == int(expect.sum()), "直读白名单求值一致"

    def test_u7h_warmup_guard(self, tmp_path):
        """U7h 预热防炸：_dna_indicator_names 跳过白名单（derive_warmup
        对 hour_utc 不抛 KeyError——漏跳过必红）。"""
        from core.analysis.validation_service import _dna_indicator_names
        from core.strategy.dna import (
            ExecutionGenes, LogicGenes, RiskGenes, SignalGene, SignalRole,
            StrategyDNA)
        dna = StrategyDNA(
            signal_genes=[SignalGene("hour_utc", {}, SignalRole.ENTRY_TRIGGER,
                                     None, {"type": "gt", "threshold": 12})],
            logic_genes=LogicGenes(),
            execution_genes=ExecutionGenes(timeframe="1h"),
            risk_genes=RiskGenes())
        names = _dna_indicator_names(dna)
        assert "hour_utc" not in names, "白名单成员不进 warmup 引用集"


def test_u7d_stats_mask_reads_hour_utc(tmp_path):
    """U7d 统计面自动获得：mask_for 对 hour_utc 条件按列直读求值
    （期望独立构造=hour_utc 列 ≥12 的 bar 集合——非生产求值器输出）。"""
    from core.analysis.condition_kernel import mask_for
    from core.data.mtf_loader import load_and_prepare_df
    d = tmp_path / "market"
    d.mkdir()
    make_ohlcv(n=200, freq="1h", start="2024-01-01").to_parquet(
        d / "BTCUSDT_1h.parquet")
    df = load_and_prepare_df(d, "BTCUSDT", "1h", compute_indicators=False)
    mask = mask_for(df, {"subject": "hour_utc", "action": "ge", "target": 12})
    expect = (df["hour_utc"] >= 12).fillna(False).astype(bool)
    assert mask.tolist() == expect.tolist(), "统计面掩码与列定义一致"
    assert mask.sum() > 0, "12:00 起的 bar 应命中（非全 False 假绿）"


def test_inject_idempotent_and_naive_utc():
    """注入助手幂等+naive 索引语义（闸门覆盖缺口补锚）：二次注入值
    不变；naive 索引按 UTC 解释（装载层已统一 tz-aware——防御分支）。"""
    from core.data.derived_columns import inject_derived_columns
    idx = pd.DatetimeIndex(
        [pd.Timestamp("2024-01-01 05:30", tz="UTC")], name="timestamp")
    df = pd.DataFrame({"close": [1.0]}, index=idx)
    inject_derived_columns(df)
    v1 = df["hour_utc"].iloc[0]
    inject_derived_columns(df)
    assert df["hour_utc"].iloc[0] == v1 == 5.5, "幂等且值=5.5（5:30）"
    df_naive = pd.DataFrame(
        {"close": [1.0]},
        index=pd.DatetimeIndex([pd.Timestamp("2024-01-01 05:30")]))
    inject_derived_columns(df_naive)
    assert df_naive["hour_utc"].iloc[0] == 5.5, "naive 按 UTC 解释"


def test_executor_direct_read_missing_column_fails_fast():
    """执行直读白名单 fail-fast（闸门覆盖缺口补锚）：帧缺 hour_utc 列
    （绕过装载层的工程接线错误形）显式 ValueError 附指路——不走前缀
    兜底静默取错列。"""
    from core.strategy.executor import _get_indicator_column
    from core.strategy.dna import SignalGene, SignalRole
    df = pd.DataFrame(
        {"close": [1.0]},
        index=pd.DatetimeIndex([pd.Timestamp("2024-01-01", tz="UTC")]))
    gene = SignalGene("hour_utc", {}, SignalRole.ENTRY_TRIGGER, None,
                      {"type": "gt", "threshold": 12})
    with pytest.raises(ValueError, match="loader must inject"):
        _get_indicator_column(df, gene)
