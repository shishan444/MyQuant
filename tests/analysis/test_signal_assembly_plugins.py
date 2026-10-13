"""插件源信号组装分支特征测试（修复期特征网：记录现状行为）。

signal_assembly 的 weighted_vote 主干 / gate / 退化路径 / 悬空拒绝由
test_strategy_v2 承接；本文件补零覆盖分支：model 源 walk-forward 组装
（_model_column 此前全域零执行）、logic OR 组合、未知 mode 拒绝、
operator 显式映射与阈值语义。

model 数值正确性由 tests/signals/test_model.py 承接（层级分工）；此处
对 walk-forward 期望值用替身注入确定性序列，聚焦组装层的配置装载、
valid 版本选择与错误转译契约。
"""

import json
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

pytestmark = [pytest.mark.integration]

import core.signals.model as signals_model
from core.analysis.hypothesis_service import ResearchError
from core.analysis.plugin_service import (
    enrich_with_plugin_columns,
    register_formula_plugin,
)
from core.analysis.signal_assembly import assemble_signal_set
from core.signals.model import ModelPipelineError
from core.strategy.dna import (
    ExecutionGenes,
    LogicGenes,
    RiskGenes,
    SignalGene,
    SignalRole,
    StrategyDNA,
)


def _df(n: int = 400, seed: int = 6) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    idx = pd.date_range("2024-01-01", periods=n, freq="4h", tz="UTC",
                        name="timestamp")
    close = 60000 + np.cumsum(rng.normal(0, 110, n))
    return pd.DataFrame({
        "open": close, "high": close + 60, "low": close - 60,
        "close": close, "volume": rng.uniform(100, 800, n), "trades": 40,
    }, index=idx)


def _rule_genes() -> list:
    return [SignalGene("RSI", {"period": 14}, SignalRole.ENTRY_TRIGGER,
                       "rsi_14", {"type": "lt", "threshold": 35})]


def _dna(sources, composition=None, genes=None) -> StrategyDNA:
    """genes=None 缺省带规则进场基因（混合形制）；传 [] 为纯插件形制
    （fix-research-c4 判定改基因实质后，两类形制的组合基线不同——
    纯插件走单位元、混合走规则基线）。"""
    return StrategyDNA(
        signal_genes=_rule_genes() if genes is None else genes,
        logic_genes=LogicGenes(),
        execution_genes=ExecutionGenes(timeframe="4h"),
        risk_genes=RiskGenes(),
        signal_sources=sources,
        composition=composition)


@pytest.fixture()
def db(tmp_path: Path) -> Path:
    from core.analysis.executor import ensure_research_tables
    from core.persistence.db_ext import init_db_ext
    d = tmp_path / "q.db"
    init_db_ext(d)
    ensure_research_tables(d)
    return d


def _insert_model_plugin(db: Path, *, version: int, status: str,
                         features: list, name: str = "m") -> None:
    from sqlalchemy import insert
    from core.persistence.orm.models import ModelPlugin
    from core.persistence.repository.dataset_meta import _engine_for
    cfg = json.dumps({"name": name, "features": features})
    with _engine_for(db).begin() as conn:
        conn.execute(insert(ModelPlugin).values(
            plugin_id=f"mpl_{name}_v{version}", name=name, version=version,
            config=cfg, signal_column="model_m", status=status,
            strategy_visible=1, created_at="2026-09-19T00:00:00"))


def _register_model_column(db: Path, name: str = "m") -> None:
    from core.persistence.repository import research as rr
    rr.register_existing_plugin_signal(
        db, source="model", plugin_id=f"mpl_{name}_v2", name=name, version=2,
        column_name="model_m")


@pytest.fixture()
def model_env(db: Path) -> Path:
    _insert_model_plugin(db, version=1, status="rejected",
                         features=["volume"], name="old")
    _insert_model_plugin(db, version=2, status="valid",
                         features=["close"], name="new")
    _register_model_column(db, name="new")
    return db


class TestModelSourceAssembly:
    def test_model源_配置装载与单位元组合(self, model_env, monkeypatch) -> None:
        """valid 版本配置装载（features 进 ModelPluginConfig）+ 无规则源
        AND 单位元：weighted_vote 单源 entries == (signal > 0)。"""
        df = _df()
        received = {}

        def fake_wfw(df_in, cfg):
            received["cfg"] = cfg
            received["columns"] = list(df_in.columns)
            # 确定性信号：半数 bar 为 1、半数为 -1（按位置奇偶）
            sig = pd.Series(np.where(np.arange(len(df_in)) % 2, 1.0, -1.0),
                            index=df_in.index)
            return SimpleNamespace(signal=sig)

        monkeypatch.setattr(signals_model, "fit_predict_walk_forward", fake_wfw)

        # fix-research-c4：纯插件形制（无进场基因）走单位元基线——
        # 单源 weighted_vote 直接透传插件布尔（混合形制见
        # test_signal_source_substance.py）
        dna = _dna([{"type": "model", "ref": "model_m"}], genes=[])
        ss = assemble_signal_set(model_env, dna, df)

        # 版本选择：version=1 rejected 被跳过，version=2 valid 的
        # config 装载（name="new"、features=["close"]）
        assert received["cfg"].name == "new"
        assert received["cfg"].features == ["close"]
        # 组装传入的 df 含 OHLCV 基础列（walk-forward 取数底座）
        assert "close" in received["columns"]

        expected = fake_wfw(df, None).signal > 0.0
        assert list(ss.entries) == list(expected)

    def test_无valid版本_拒绝(self, db: Path) -> None:
        _insert_model_plugin(db, version=1, status="rejected",
                             features=["close"])
        _register_model_column(db)

        with pytest.raises(ResearchError) as ei:
            assemble_signal_set(db, _dna([{"type": "model", "ref": "model_m"}]),
                                _df())
        assert ei.value.code == "RESEARCH_SIGNAL_SOURCE_INVALID"
        assert "无 valid 版本" in ei.value.message

    def test_管道异常_转译为结构化拒绝(self, model_env, monkeypatch) -> None:
        def boom(df_in, cfg):
            raise ModelPipelineError("boom")

        monkeypatch.setattr(signals_model, "fit_predict_walk_forward", boom)

        with pytest.raises(ResearchError) as ei:
            assemble_signal_set(model_env, _dna([{"type": "model",
                                                  "ref": "model_m"}]), _df())
        assert ei.value.code == "RESEARCH_SIGNAL_SOURCE_INVALID"
        assert "模型信号源求值失败" in ei.value.message
        assert "boom" in ei.value.message

    def test_真实walkforward管道_组装成功(self, model_env) -> None:
        """不注入替身的真管道冒烟：model 源经真实 fit_predict_walk_forward
        装配出布尔 entries（数值口径由 tests/signals 承接）。"""
        ss = assemble_signal_set(model_env,
                                 _dna([{"type": "model", "ref": "model_m"}]),
                                 _df())
        assert ss.entries.dtype == bool
        assert len(ss.entries) == 400


class TestCombineBranches:
    @pytest.fixture()
    def formula_col(self, db: Path) -> str:
        register_formula_plugin(db, name="gfx", expression="zscore(close, 20)")
        from core.analysis.registry_meta import available_signal_columns
        return next(c["column_key"] for c in available_signal_columns(db)
                    if c["source"] == "formula")

    def test_logic_OR_基线恒假_并集语义(self, db, formula_col) -> None:
        """无规则源 + logic OR：规则侧单位元恒假（空 rule 基因不产生
        恒 False 陷阱——OR 语义下取恒假使 entries == 插件任意真）。"""
        df = _df()
        enriched = enrich_with_plugin_columns(db, df, {formula_col})
        fx = enriched[formula_col].astype(float)

        # fix-research-c4：纯插件形制（无进场基因）——OR 单位元恒假
        # 使 entries == 插件任意真（混合形制的 OR 并集语义见
        # test_signal_source_substance.py）
        dna = _dna([{"type": "formula", "ref": formula_col}],
                   {"mode": "logic", "op": "OR"}, genes=[])
        ss = assemble_signal_set(db, dna, df)

        assert list(ss.entries) == list(fx > 0.0)

    def test_operator显式映射_lt阈值(self, db, formula_col) -> None:
        """operator "<" + threshold：插件布尔 = (fx < 阈值)，weighted_vote
        单源直接透传该布尔。"""
        df = _df()
        enriched = enrich_with_plugin_columns(db, df, {formula_col})
        fx = enriched[formula_col].astype(float)

        dna = _dna([{"type": "formula", "ref": formula_col,
                     "operator": "<", "threshold": 0.0}], genes=[])
        ss = assemble_signal_set(db, dna, df)

        assert list(ss.entries) == list(fx < 0.0)

    def test_operator缺省_gt零阈值(self, db, formula_col) -> None:
        df = _df()
        enriched = enrich_with_plugin_columns(db, df, {formula_col})
        fx = enriched[formula_col].astype(float)

        dna = _dna([{"type": "formula", "ref": formula_col}], genes=[])
        ss = assemble_signal_set(db, dna, df)

        assert list(ss.entries) == list(fx > 0.0)

    def test_未知组合模式_拒绝(self, db, formula_col) -> None:
        dna = _dna([{"type": "formula", "ref": formula_col}],
                   {"mode": "bogus"})
        with pytest.raises(ResearchError) as ei:
            assemble_signal_set(db, dna, _df())
        assert ei.value.code == "RESEARCH_COMPOSITION_INVALID"
        assert "bogus" in ei.value.message

    def test_有规则源_基线不被单位元替换(self, db, formula_col) -> None:
        """signal_sources 含 rule 型：entries = 规则基线 AND 插件
        （weighted_vote 交集语义；对照=同基因纯规则策略）。"""
        df = _df()
        rule_only = StrategyDNA(
            signal_genes=_rule_genes(), logic_genes=LogicGenes(),
            execution_genes=ExecutionGenes(timeframe="4h"),
            risk_genes=RiskGenes())
        base = assemble_signal_set(db, rule_only, df)
        enriched = enrich_with_plugin_columns(db, df, {formula_col})
        fx = enriched[formula_col].astype(float)

        dna = _dna([{"type": "rule"}, {"type": "formula", "ref": formula_col}])
        ss = assemble_signal_set(db, dna, df)

        assert list(ss.entries) == list(base.entries & (fx > 0.0))


class TestGapClosureAssemble3:
    """第三批：纯 rule 源 + composition 的空插件早退。"""

    def test_纯rule源带组合_空插件早退(self, db: Path) -> None:
        """signal_sources 全为 rule 型 + composition 非空 → plugin_sources
        空 → _combine 的空 bools 早退（entries=规则基线原样）。"""
        df = _df()
        rule_only = StrategyDNA(
            signal_genes=_rule_genes(), logic_genes=LogicGenes(),
            execution_genes=ExecutionGenes(timeframe="4h"),
            risk_genes=RiskGenes())
        with_comp = StrategyDNA(
            signal_genes=_rule_genes(), logic_genes=LogicGenes(),
            execution_genes=ExecutionGenes(timeframe="4h"),
            risk_genes=RiskGenes(),
            signal_sources=[{"type": "rule"}],
            composition={"mode": "gate"})

        base = assemble_signal_set(db, rule_only, df)
        out = assemble_signal_set(db, with_comp, df)
        assert list(out.entries) == list(base.entries)  # 空插件 = 基线原样


class TestGapClosureFormulaFail:
    """第三批：formula 源运行时缺列——求值失败显式拒绝。"""

    def test_formula源运行时缺列_拒绝(self, db: Path) -> None:
        register_formula_plugin(db, name="need_oi",
                                expression="zscore(open_interest, 20)")
        from core.analysis.registry_meta import available_signal_columns
        col = next(c["column_key"] for c in available_signal_columns(db)
                   if c["source"] == "formula")
        dna = _dna([{"type": "formula", "ref": col}])
        with pytest.raises(ResearchError) as ei:
            assemble_signal_set(db, dna, _df())  # _df 无 open_interest 列
        assert ei.value.code == "RESEARCH_SIGNAL_SOURCE_INVALID"
        assert "求值失败" in ei.value.message


class TestCombineMissingPluginColumnSkips:
    """_combine 对 signal_sources 引用列在 plugin_cols 缺失时跳过
    （不炸不产半布尔列——悬空引用防御的组装层侧面）。"""

    def test_missing_ref_skipped_to_empty_bools(self):
        from core.analysis.signal_assembly import _combine
        from core.strategy.executor import SignalSet

        idx = pd.date_range("2024-01-01", periods=8, freq="4h")
        base = SignalSet(
            entries=pd.Series(False, index=idx),
            exits=pd.Series(False, index=idx),
            adds=pd.Series(False, index=idx),
            reduces=pd.Series(False, index=idx))
        dna = StrategyDNA(
            signal_genes=[], logic_genes=LogicGenes(),
            execution_genes=ExecutionGenes(timeframe="4h"),
            risk_genes=RiskGenes(),
            signal_sources=[
                {"type": "formula", "ref": "missing_col",
                 "threshold": 0.5, "operator": ">"}])
        out = _combine(base, dna, {}, "logic")
        # 无插件布尔 → entries 原样返回（缺失源被 continue 跳过）
        assert list(out.keys()) == ["entries"]
        assert not out["entries"].any()


class TestDuplicateFormulaRefSkipsReappend:
    """assemble_signal_set 声明序去重：两条 source 引用同一 formula
    列 → 第二轮跳过重复 append（失效报告序确定性的去重侧）。"""

    @pytest.fixture()
    def formula_col(self, db: Path) -> str:
        register_formula_plugin(db, name="dup_gfx",
                                expression="zscore(close, 20)")
        from core.analysis.registry_meta import available_signal_columns
        return next(c["column_key"] for c in available_signal_columns(db)
                    if c["source"] == "formula")

    def test_duplicate_ref_registered_once(self, db, formula_col) -> None:
        dna = _dna([{"type": "formula", "ref": formula_col},
                    {"type": "formula", "ref": formula_col}],
                   {"mode": "logic", "op": "OR"}, genes=[])
        ss = assemble_signal_set(db, dna, _df())
        # 双引用同一列不炸、entries 仍为该列布尔（去重后单列）
        df = _df()
        enriched = enrich_with_plugin_columns(db, df, {formula_col})
        fx = enriched[formula_col].astype(float)
        assert list(ss.entries) == list(fx > 0.0)


class TestCombineParamValidation:
    """0.3.7 H5：组合参数值域显式拒绝（组装器单点——验证链/回测链同源）。

    键缺省（operator ">" / threshold 0.0 / weights 1.0）是既有契约，
    由 TestCombineBranches.test_operator缺省_gt零阈值 等既有锚守护；
    本类只锚显式坏值的结构化拒绝。"""

    @pytest.fixture()
    def formula_col(self, db: Path) -> str:
        register_formula_plugin(db, name="gfx", expression="zscore(close, 20)")
        from core.analysis.registry_meta import available_signal_columns
        return next(c["column_key"] for c in available_signal_columns(db)
                    if c["source"] == "formula")

    def test_未知operator_结构化拒绝_合法集回显(self, db, formula_col) -> None:
        dna = _dna([{"type": "formula", "ref": formula_col,
                     "operator": "<>", "threshold": 0.0}])
        with pytest.raises(ResearchError) as ei:
            assemble_signal_set(db, dna, _df())
        assert ei.value.code == "RESEARCH_PARAM_INVALID"
        assert ei.value.detail["legal_values"] == ["<", "<=", ">", ">="]
        assert ei.value.detail["param"] == f"signal_sources[{formula_col}].operator"

    def test_operator合法集对拍_防字典漂移(self) -> None:
        # 枚举对拍锚：期望硬编 vs 实现字典键集（字典误改时此锚红——
        # 0.2.8 工具数锚同款纪律，单一事实源防漂移）
        from core.analysis.signal_assembly import _SOURCE_COMPARE_OPS
        assert set(_SOURCE_COMPARE_OPS) == {">", "<", ">=", "<="}

    def test_threshold坏串_422不裸穿500(self, db, formula_col) -> None:
        dna = _dna([{"type": "formula", "ref": formula_col,
                     "operator": ">", "threshold": "bad"}])
        with pytest.raises(ResearchError) as ei:
            assemble_signal_set(db, dna, _df())
        assert ei.value.code == "RESEARCH_PARAM_INVALID"
        assert ei.value.detail["value"] == "bad"

    def test_weighted_vote权重坏值_422含列名(self, db, formula_col) -> None:
        dna = _dna([{"type": "formula", "ref": formula_col}],
                   {"mode": "weighted_vote",
                    "weights": {formula_col: "oops"}})
        with pytest.raises(ResearchError) as ei:
            assemble_signal_set(db, dna, _df())
        assert ei.value.code == "RESEARCH_PARAM_INVALID"
        assert ei.value.detail["param"] == f"composition.weights[{formula_col}]"

    def test_weighted_vote阈值坏串_422(self, db, formula_col) -> None:
        dna = _dna([{"type": "formula", "ref": formula_col}],
                   {"mode": "weighted_vote", "threshold": "bad"})
        with pytest.raises(ResearchError) as ei:
            assemble_signal_set(db, dna, _df())
        assert ei.value.code == "RESEARCH_PARAM_INVALID"
