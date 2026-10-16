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

    def test_u7c_three_faces_same_rejection(self, tmp_path):
        """三面：resolve 直调（分析面）/登记 canonical（登记面）/翻译面
        ——同码同 detail 键集（ref_tf=校验所用引用周期，base_tf=回退
        基准；D-1 修复定稿形制）。"""
        from core.analysis.column_ref import ColumnRefError, resolve_column_ref
        # 分析面
        with pytest.raises(ColumnRefError) as e1:
            resolve_column_ref("hour_utc", base_tf="1d")
        assert e1.value.code == "PARAM_INVALID"
        assert set(e1.value.detail) == self._detail_keys()
        # 登记面：detail 证同源（resolve 键集经 ColumnRefError.detail
        # 透传进 ResearchError.detail——不只 message 含列名）
        from core.persistence.db_ext import init_db_ext
        from core.analysis.executor import ensure_research_tables
        from core.analysis.hypothesis_service import (
            ResearchError, register_hypothesis)
        db = tmp_path / "q.db"
        init_db_ext(db); ensure_research_tables(db)
        with pytest.raises(ResearchError) as e2:
            register_hypothesis(
                db,
                conditions={"when_conditions": [
                    {"subject": "hour_utc", "action": "gt", "target": 12}],
                    "then_conditions": [
                        {"subject": "close", "action": "gt", "target": 0}]},
                statement="s",
                data_ranges=[{"symbol": "BTCUSDT", "timeframe": "1d",
                              "start": "2024-01-01T00:00:00Z",
                              "end": "2024-02-01T00:00:00Z"}])
        assert e2.value.code == "RESEARCH_CONDITION_INVALID"
        assert e2.value.detail["code"] == "PARAM_INVALID"
        assert {"column", "base_tf", "domain", "hint", "ref_tf"} <= \
            set(e2.value.detail), "登记面 detail 与 resolve 同源同键集"

    def test_u7c_registration_rejects_1d_ref_on_intraday_base(self, tmp_path):
        """U7c 补（D-6② 引用周期语义）：条件自带 timeframe=1d 而基准
        4h 日内——求值轴=1d 须拒（ref_tf 优先于 base_tf 的校验对象）。"""
        from core.persistence.db_ext import init_db_ext
        from core.analysis.executor import ensure_research_tables
        from core.analysis.hypothesis_service import (
            ResearchError, register_hypothesis)
        db = tmp_path / "q.db"
        init_db_ext(db); ensure_research_tables(db)
        with pytest.raises(ResearchError) as ei:
            register_hypothesis(
                db,
                conditions={"when_conditions": [
                    {"subject": "hour_utc", "action": "gt", "target": 12,
                     "timeframe": "1d"}],
                    "then_conditions": [
                        {"subject": "close", "action": "gt", "target": 0}]},
                statement="s",
                data_ranges=[{"symbol": "BTCUSDT", "timeframe": "4h",
                              "start": "2024-01-01T00:00:00Z",
                              "end": "2024-02-01T00:00:00Z"}])
        assert ei.value.detail["ref_tf"] == "1d", "校验对象=引用周期"

    def test_u7c_translation_rejects_1d_ref(self, tmp_path):
        """U7c 翻译面：登记面防线在先，翻译面为纵深防御（绕过登记的
        库内/手工数据）——直调 _translate_conditions 证翻译面独立拒绝
        （detail={condition, ref_tf, hint} 形制）。"""
        from core.persistence.db_ext import init_db_ext
        from core.analysis.executor import ensure_research_tables
        from core.analysis.hypothesis_service import ResearchError
        from core.analysis.hypothesis_to_strategy import _translate_conditions
        from core.strategy.dna import SignalRole
        db = tmp_path / "q.db"
        init_db_ext(db); ensure_research_tables(db)
        with pytest.raises(ResearchError) as ei:
            _translate_conditions(
                [{"subject": "hour_utc", "action": "gt", "target": 12,
                  "timeframe": "1d"}],
                db_path=db, label="when_conditions",
                role=SignalRole.ENTRY_TRIGGER, base_tf="4h")
        assert {"condition", "ref_tf", "hint"} <= set(ei.value.detail)
        assert ei.value.detail["ref_tf"] == "1d"

    def test_u7f_legacy_columns_zero_change(self):
        """U7f 八列存量零变化（域元数据 dict 兼容——in 判定同语义）。"""
        from core.analysis.column_ref import BASE_SUBJECT_COLUMNS, resolve_column_ref
        legacy = ["open", "high", "low", "close", "volume", "trades",
                  "open_interest", "funding_rate"]
        assert all(c in BASE_SUBJECT_COLUMNS for c in legacy)
        for c in legacy:
            r = resolve_column_ref(c, base_tf="1d")  # 无域元数据不拒
            assert r.kind == "base"

    def test_c4_target_domain_hard_validation(self, tmp_path):
        """C4：hour_utc target 须 [0,24)（与列值域一致半开）——25/24/
        -0.5 拒（RESEARCH_NOT_EXPRESSIBLE）、23.9833/0 放行（期望
        人工核算：23:59→23.9833 为域上确界内值、24 归次日 0:00 出域）。"""
        from core.persistence.db_ext import init_db_ext
        from core.analysis.executor import ensure_research_tables
        from core.analysis.hypothesis_service import ResearchError
        from core.analysis.hypothesis_to_strategy import _translate_conditions
        from core.strategy.dna import SignalRole
        db = tmp_path / "q.db"
        init_db_ext(db); ensure_research_tables(db)
        for bad in (25, 24, -0.5):
            with pytest.raises(ResearchError) as ei:
                _translate_conditions(
                    [{"subject": "hour_utc", "action": "gt", "target": bad}],
                    db_path=db, label="when_conditions",
                    role=SignalRole.ENTRY_TRIGGER, base_tf="4h")
            assert ei.value.code == "RESEARCH_NOT_EXPRESSIBLE", \
                f"target={bad} 应拒"
        for ok in (23.9833, 0):
            genes, _ = _translate_conditions(
                [{"subject": "hour_utc", "action": "gt", "target": ok}],
                db_path=db, label="when_conditions",
                role=SignalRole.ENTRY_TRIGGER, base_tf="4h")
            assert len(genes) == 1, f"target={ok} 应放行承载"

    def test_u7g_cross_actions_rejected(self, tmp_path):
        """U7g action 域锁反面：cross_above/price_above 拒。"""
        from core.persistence.db_ext import init_db_ext
        from core.analysis.executor import ensure_research_tables
        from core.analysis.hypothesis_service import (
            ResearchError, register_hypothesis)
        db = tmp_path / "q.db"
        init_db_ext(db); ensure_research_tables(db)
        # 登记面不校验 base 列 action（值域层归翻译面）——action 域锁
        # 经翻译面断言（登记过、翻译拒）
        conds = lambda act: {"when_conditions": [
            {"subject": "hour_utc", "action": act, "target": 12}],
            "then_conditions": [
                {"subject": {"indicator": "RSI", "params": {"period": 14}},
                 "action": "gt", "target": 70}]}
        from core.analysis.hypothesis_to_strategy import translate_hypothesis
        rng = [{"symbol": "BTCUSDT", "timeframe": "4h",
                "start": "2024-01-01T00:00:00Z",
                "end": "2024-02-01T00:00:00Z"}]
        for act in ("cross_above", "price_above"):
            hid = register_hypothesis(db, conditions=conds(act),
                                      statement="s", data_ranges=rng
                                      )["hypothesis_id"]
            with pytest.raises(ResearchError):
                translate_hypothesis(db, data_dir=tmp_path,
                                     hypothesis_id=hid)


class TestFullChain:
    """U7e：全链四层——登记/翻译/执行直读白名单。"""

    def test_u7e_full_chain_gene_and_eval(self, tmp_path):
        """登记→翻译（基因三元组）→执行（直读白名单求值）。"""
        from core.persistence.db_ext import init_db_ext
        from core.analysis.executor import ensure_research_tables
        from core.analysis.hypothesis_service import register_hypothesis
        from core.analysis.hypothesis_to_strategy import translate_hypothesis
        from core.persistence.repository.strategy import get_strategy
        from core.strategy.dna import StrategyDNA
        from core.strategy.executor import dna_to_signal_set, clear_indicator_cache

        db = tmp_path / "q.db"
        init_db_ext(db); ensure_research_tables(db)
        d = tmp_path / "market"; d.mkdir()
        make_ohlcv(n=300, freq="1h", start="2024-01-01").to_parquet(
            d / "BTCUSDT_1h.parquet")
        hid = register_hypothesis(
            db,
            conditions={"when_conditions": [
                {"subject": "hour_utc", "action": "gt", "target": 12}],
                "then_conditions": [
                    {"subject": {"indicator": "RSI",
                                 "params": {"period": 30}},
                     "action": "gt", "target": 70}]},
            statement="s",
            data_ranges=[{"symbol": "BTCUSDT", "timeframe": "1h",
                          "start": "2024-01-01T00:00:00Z",
                          "end": "2024-02-01T00:00:00Z"}])["hypothesis_id"]
        out = translate_hypothesis(db, hypothesis_id=hid, data_dir=d,
                                   strategy_timeframe="1h")
        genes = _json.loads(
            get_strategy(db, out["strategy_id"])["dna_json"])["signal_genes"]
        g = genes[0]
        assert (g["indicator"], g["params"], g["field"]) == \
            ("hour_utc", {}, None), "基因三元组"
        # 执行直读（装载注入列在场）
        from core.data.mtf_loader import load_and_prepare_df
        df = load_and_prepare_df(d, "BTCUSDT", "1h")
        # dataclass 直构（dict 形构造会 auto-wrap layers 触发 MTF
        # 分支；单周期直调语义等价直构）
        from core.strategy.dna import (
            ExecutionGenes, LogicGenes, RiskGenes, SignalGene, SignalRole)
        dna_genes = _json.loads(
            get_strategy(db, out["strategy_id"])["dna_json"])["signal_genes"]
        dna = StrategyDNA(
            signal_genes=[
                SignalGene(g["indicator"], g["params"],
                           SignalRole(g["role"]), g.get("field"),
                           g["condition"]) for g in dna_genes],
            logic_genes=LogicGenes(),
            execution_genes=ExecutionGenes(timeframe="1h"),
            risk_genes=RiskGenes())
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
