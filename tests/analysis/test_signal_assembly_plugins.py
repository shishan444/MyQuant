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
from core.analysis.errors import ResearchError
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

    def test_model源_dict形特征_解析装载与列名替换(self, db, monkeypatch) -> None:
        """0.2.13 生产形制回归锚：库内 config 的 features 为 canonical
        dict 形（指标 {indicator, params, field}）——执行侧须解析回
        ColumnDef、现算装载特征列、替换为精确列名后进管线（直喂 dict
        触发 unhashable，模型列执行链全断——RR-034 沙盒首航实证）。"""
        _insert_model_plugin(
            db, version=1, status="valid",
            features=[{"indicator": "RSI", "params": {"period": 14},
                       "field": "rsi"}])
        _register_model_column(db)
        df = _df()
        received = {}

        def fake_wfw(df_in, cfg):
            received["features"] = cfg.features
            received["columns"] = list(df_in.columns)
            return SimpleNamespace(signal=pd.Series(
                np.where(np.arange(len(df_in)) % 2, 1.0, -1.0),
                index=df_in.index))

        monkeypatch.setattr(signals_model, "fit_predict_walk_forward", fake_wfw)
        assemble_signal_set(
            db, _dna([{"type": "model", "ref": "model_m"}], genes=[]), df)

        # 特征已替换为精确列名（非 dict），且该列被现算装载进 df
        assert len(received["features"]) == 1
        feat = received["features"][0]
        assert isinstance(feat, str)
        assert feat in received["columns"]

    def test_model源_dict形特征_真管道冒烟(self, db) -> None:
        """dict 形特征不注入替身的端到端：RSI 现算注入 + 真实
        walk-forward 管线装配出布尔 entries（生产 500 的直接复现锚）。"""
        _insert_model_plugin(
            db, version=1, status="valid",
            features=[{"indicator": "RSI", "params": {"period": 14},
                       "field": "rsi"}])
        _register_model_column(db)
        ss = assemble_signal_set(
            db, _dna([{"type": "model", "ref": "model_m"}], genes=[]), _df())
        assert ss.entries.dtype == bool
        assert len(ss.entries) == 400

    def test_model源特征引用不可用_结构化拒绝(self, db) -> None:
        """dict 形特征解析失败（未知指标）：显式拒绝而非静默缺列——
        引用失败显式拒绝契约（模块头）在特征维度的延伸。"""
        _insert_model_plugin(
            db, version=1, status="valid",
            features=[{"indicator": "NOPE", "params": {"period": 14},
                       "field": "nope"}])
        _register_model_column(db)
        with pytest.raises(ResearchError) as ei:
            assemble_signal_set(
                db, _dna([{"type": "model", "ref": "model_m"}], genes=[]),
                _df())
        assert ei.value.code == "RESEARCH_SIGNAL_SOURCE_INVALID"
        assert "特征引用不可用" in ei.value.message


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

    def test_未知组合模式_消息渲染单源(self, db, formula_col) -> None:
        """求值期兜底拒绝消息改引 COMPOSITION_MODES 常量渲染后的
        现状锚（H4-a 件②，signal_assembly.py 改动行）：消息以「合法集」
        引出三模式成员——validator 结构判据消息与求值期兜底同源渲染
        （改域单点生效），此锚钉住渲染形制不回退为硬编码异形。"""
        dna = _dna([{"type": "formula", "ref": formula_col}],
                   {"mode": "bogus"})
        with pytest.raises(ResearchError) as ei:
            assemble_signal_set(db, dna, _df())
        assert ei.value.code == "RESEARCH_COMPOSITION_INVALID"
        assert "合法集" in ei.value.message
        for m in ("weighted_vote", "gate", "logic"):
            assert m in ei.value.message, (
                f"消息未含白名单成员 {m}：{ei.value.message}")

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


class TestR2RoleChannelBuckets:
    """R2 组装器分桶通道锚（indicator-and-action-repair 卡 F 6.2
    ——tester 锚 C2-E 组装器字段级：四桶 AND 组合逐桶断言+无规则侧
    独立生效+composition 仅入场）。

    源级 role 键分桶：exit→exits、exit_partial→reduces、add_trigger
    →adds、add_suppress→取反 AND；无规则侧基因时插件组独立生效
    （base 通道恒 False 不作废插件——AND 单位元先例对称）。
    """

    @pytest.fixture()
    def fx_true_col(self, db: Path) -> str:
        """恒真插件列（close 恒正 → gt(close, 0) 全段 True）。"""
        return register_formula_plugin(
            db, name="always", expression="gt(close, 0)")["column"]

    @pytest.fixture()
    def fx_part_col(self, db: Path) -> str:
        """分段插件列（close>55000——seed=5 数据 92 段 True，与规则
        出场条件既有交集（37 bar）又有差集（61 bar），可区分 AND 与
        替换语义）。"""
        return register_formula_plugin(
            db, name="part", expression="gt(close, 55000)")["column"]

    @staticmethod
    def _enhanced() -> pd.DataFrame:
        from core.features.indicators import compute_all_indicators
        from tests.helpers.data_factory import make_ohlcv
        return compute_all_indicators(
            make_ohlcv(300, seed=5, base_price=60000.0))

    @staticmethod
    def _genes(*sides) -> list:
        """入场基因 + 各侧规则基因（rsi 阈值取分段可触发形态）。"""
        by_role = {
            "exit": lambda: SignalGene(
                "RSI", {"period": 14}, SignalRole.EXIT_TRIGGER,
                "rsi_14", {"type": "gt", "threshold": 55}),
            "reduce": lambda: SignalGene(
                "RSI", {"period": 14}, SignalRole.REDUCE_TRIGGER,
                "rsi_14", {"type": "gt", "threshold": 50}),
            "add": lambda: SignalGene(
                "RSI", {"period": 14}, SignalRole.ADD_TRIGGER,
                "rsi_14", {"type": "gt", "threshold": 45}),
        }
        genes = [SignalGene("RSI", {"period": 14}, SignalRole.ENTRY_TRIGGER,
                            "rsi_14", {"type": "lt", "threshold": 35})]
        for s in sides:
            genes.append(by_role[s]())
        return genes

    @staticmethod
    def _src(col: str, role: str) -> dict:
        return {"type": "formula", "ref": col, "threshold": 0.5,
                "operator": ">", "role": role}

    def test_exit_bucket_and_rule_side(self, db, fx_part_col):
        """exit 桶 AND 规则出场：exits == 规则出场 & 插件布尔（分段
        插件列区分 AND 与替换——两集合不等的实数据对照）。"""
        df = self._enhanced()
        dna = _dna([self._src(fx_part_col, "exit")], genes=self._genes("exit"))
        sig = assemble_signal_set(db, dna, df)
        expect = (df["rsi_14"] > 55) & (df["close"] > 55000)
        assert bool((sig.exits.fillna(False) == expect).all()), "exit AND 组合"
        # 构造前提：交集与差集双向非空（AND 语义可区分——替换/OR 必红）
        assert int(expect.sum()) == 37 and int(((df["rsi_14"] > 55) & ~(df["close"] > 55000)).sum()) == 61

    def test_exit_partial_bucket_reduces(self, db, fx_true_col):
        """exit_partial 桶 → reduces 通道（有规则侧 AND；恒真插件下
        reduces == 规则 reduce 信号）。"""
        df = self._enhanced()
        dna = _dna([self._src(fx_true_col, "exit_partial")],
                   genes=self._genes("reduce"))
        sig = assemble_signal_set(db, dna, df)
        expect = (df["rsi_14"] > 50).fillna(False)
        assert bool((sig.reduces.fillna(False) == expect).all()), \
            "exit_partial AND reduce 通道"

    def test_add_trigger_bucket_adds(self, db, fx_true_col):
        """add_trigger 桶 → adds 通道（有规则侧 AND）。"""
        df = self._enhanced()
        dna = _dna([self._src(fx_true_col, "add_trigger")],
                   genes=self._genes("add"))
        sig = assemble_signal_set(db, dna, df)
        expect = (df["rsi_14"] > 45).fillna(False)
        assert bool((sig.adds.fillna(False) == expect).all()), \
            "add_trigger AND add 通道"

    def test_add_suppress_negation_and(self, db, fx_true_col):
        """add_suppress 取反 AND（spec Scenario「插件加仓抑制语义」）：
        恒真抑制条件下规则加仓信号全灭；无抑制源对照照常触发。"""
        df = self._enhanced()
        genes = self._genes("add")
        sig_off = assemble_signal_set(
            db, _dna([self._src(fx_true_col, "add_suppress")], genes=genes),
            df)
        sig_free = assemble_signal_set(db, _dna([], genes=genes), df)
        assert bool(sig_free.adds.any()), "构造前提：规则加仓有触发段"
        assert not bool(sig_off.adds.any()), \
            "fx=True 段加仓信号被抑制（取反 AND）"

    def test_no_rule_side_plugin_independent(self, db, fx_true_col):
        """无规则侧独立生效：仅入场基因+各非 entry 插件源（无对应
        规则基因）→ reduces/adds 由插件独立驱动（base 通道恒 False
        不作废插件——AND 单位元对称）。"""
        df = self._enhanced()
        dna = _dna([self._src(fx_true_col, "exit_partial"),
                    self._src(fx_true_col, "add_trigger")],
                   genes=self._genes())
        sig = assemble_signal_set(db, dna, df)
        assert bool(sig.reduces.all()), "纯插件 reduce 独立生效（恒真列全段）"
        assert bool(sig.adds.all()), "纯插件 add 独立生效"

    def test_composition_mode_not_polluting_exit(self, db, fx_true_col):
        """composition 仅入场（spec Scenario「组合模式不污染出场」）：
        weighted_vote 策略含插件出场源——出场仍固定 AND（恒真插件下
        exits == 规则出场，非投票语义）。"""
        df = self._enhanced()
        dna = _dna([self._src(fx_true_col, "exit")],
                   composition={"mode": "weighted_vote", "threshold": 0.5},
                   genes=self._genes("exit"))
        sig = assemble_signal_set(db, dna, df)
        expect = (df["rsi_14"] > 55).fillna(False)
        assert bool((sig.exits.fillna(False) == expect).all()), \
            "出场固定 AND——投票/权重语义不适用出场"

    @pytest.mark.parametrize(("mode", "op"), [
        ("weighted_vote", None), ("gate", None),
        ("logic", "AND"), ("logic", "OR"),
    ], ids=["weighted_vote", "gate", "logic_and", "logic_or"])
    def test_card01_mixed_sources_survive_composition(
            self, db, fx_true_col, fx_part_col, mode, op):
        """混合形态×composition 四模式矩阵（CARD-01 修复锚）：
        entry 插件与非 entry 插件**并存**时走 composition 三模式分支
        ——修复前分支内新建字典 `return {"entries": ...}` 丢弃已积累
        的非 entry 桶，无规则侧基因时 exits/reduces 通道静默恒 0
        （纯非 entry 形态不触发该分支，原 TestR2RoleChannelBuckets
        覆盖缺口）。修复后非 entry 桶在四模式下全部进通道合并：
        无规则侧=插件独立生效（恒真 exit 全段/分段 exit_partial=92），
        有规则侧=AND 交集（恒真元下 exits==base）。"""
        df = self._enhanced()
        comp = {"mode": mode}
        if op:
            comp["op"] = op
        entry_src = self._src(fx_true_col, "entry")
        exit_true = self._src(fx_true_col, "exit")
        exit_part_seg = self._src(fx_part_col, "exit_partial")
        # 两态：无/有 exit 侧规则基因（reduces 侧两态同构取无规则态）
        dna_no_rule = _dna([entry_src, exit_true, exit_part_seg],
                           composition=comp, genes=self._genes())
        sig = assemble_signal_set(db, dna_no_rule, df)
        assert bool(sig.exits.all()), \
            f"{mode}{op or ''}：混合形态 exit 桶独立生效（修复前恒 0）"
        seg_expect = df["close"] > 55000
        assert int(sig.reduces.sum()) == int(seg_expect.sum()) == 92, \
            f"{mode}{op or ''}：分段 exit_partial 桶独立生效（AND 单位元）"
        dna_with_rule = _dna([entry_src, exit_true, exit_part_seg],
                             composition=comp, genes=self._genes("exit"))
        sig2 = assemble_signal_set(db, dna_with_rule, df)
        base_expect = (df["rsi_14"] > 55).fillna(False)
        assert bool((sig2.exits.fillna(False) == base_expect).all()), \
            f"{mode}{op or ''}：有规则侧 exit 桶 AND（恒真元=base 交集）"

    def test_card01_mixed_and_semantics_with_segmented_exit(
            self, db, fx_true_col, fx_part_col):
        """混合形态 AND 语义区分锚（weighted_vote）：entry 恒真插件+
        exit **分段**插件并存——exits == base.exits & 分段布尔（AND
        交集非替换；与纯非 entry 形态的 test_exit_bucket_and_rule_side
        构成混合/纯两形制对照）。"""
        df = self._enhanced()
        dna = _dna([self._src(fx_true_col, "entry"),
                    self._src(fx_part_col, "exit")],
                   composition={"mode": "weighted_vote", "threshold": 0.5},
                   genes=self._genes("exit"))
        sig = assemble_signal_set(db, dna, df)
        expect = (df["rsi_14"] > 55) & (df["close"] > 55000)
        assert bool((sig.exits.fillna(False) == expect).all()), \
            "混合形态 AND 交集（37 bar——替换/OR 必红）"
        assert int(expect.sum()) == 37


class TestR2BucketInternalConjunction:
    """阶段四②盲区消解补锚：非 entry 桶内多插件 AND 合取
    （_combine 桶内 for b in bools[1:] 循环——单插件用例不触达）。"""

    @pytest.fixture()
    def fx_true_col(self, db: Path) -> str:
        return register_formula_plugin(
            db, name="always2", expression="gt(close, 0)")["column"]

    @pytest.fixture()
    def fx_part_col(self, db: Path) -> str:
        return register_formula_plugin(
            db, name="part2", expression="gt(close, 55000)")["column"]

    def test_exit_bucket_two_plugins_and(self, db, fx_true_col, fx_part_col):
        """exit 桶两个插件（恒真+分段）→ exits == 规则出场 & 恒真 &
        分段（桶内合取+通道 AND 两层同时验证——任一插件 False 段即灭）。"""
        from core.features.indicators import compute_all_indicators
        from tests.helpers.data_factory import make_ohlcv
        df = compute_all_indicators(
            make_ohlcv(300, seed=5, base_price=60000.0))
        genes = [
            SignalGene("RSI", {"period": 14}, SignalRole.ENTRY_TRIGGER,
                       "rsi_14", {"type": "lt", "threshold": 35}),
            SignalGene("RSI", {"period": 14}, SignalRole.EXIT_TRIGGER,
                       "rsi_14", {"type": "gt", "threshold": 55}),
        ]
        dna = _dna([{"type": "formula", "ref": fx_true_col,
                     "threshold": 0.5, "operator": ">", "role": "exit"},
                    {"type": "formula", "ref": fx_part_col,
                     "threshold": 0.5, "operator": ">", "role": "exit"}],
                   genes=genes)
        sig = assemble_signal_set(db, dna, df)
        expect = (df["rsi_14"] > 55) & (df["close"] > 55000)
        assert bool((sig.exits.fillna(False) == expect).all()), \
            "桶内两插件 AND 合取（交集 37 bar）"
        assert int(expect.sum()) == 37


class TestR2ExitPluginEngineChain:
    """⑤层级补全：R2 fx 出场源引擎链端到端（spec strategy-expression
    「插件出场端到端」THEN 落在回测端——组装层锚（E6/四桶）之外的
    引擎消费段：run_backtest_on_range 装载→组装→引擎全链，fx=True 段
    平仓行为可观测）。"""

    @pytest.fixture()
    def env(self, tmp_path: Path):
        from core.analysis.executor import ensure_research_tables
        from core.persistence.db_ext import init_db_ext
        from tests.helpers.data_factory import make_ohlcv
        data_dir = tmp_path / "market"
        data_dir.mkdir()
        df = make_ohlcv(n=420, freq="1h", start="2024-01-01", seed=7)
        df.to_parquet(data_dir / "BTCUSDT_1h.parquet")
        db = tmp_path / "q.db"
        init_db_ext(db)
        ensure_research_tables(db)
        q25 = float(df["close"].quantile(0.25))
        register_formula_plugin(
            db, name="hi", expression=f"gt(close, {q25})")
        register_formula_plugin(
            db, name="never", expression="gt(close, 1e12)")
        return {"data_dir": data_dir, "db": db, "df": df,
                "start": df.index[90].isoformat(),
                "end": df.index[-1].isoformat()}

    @staticmethod
    def _dna(env, ref) -> StrategyDNA:
        """恒真入场+高止损：出场差异唯一归因 fx 源（无止损/规则出场
        干扰——恒假 fx 下持有到期恰 1 笔）。"""
        entry = SignalGene("RSI", {"period": 14}, SignalRole.ENTRY_TRIGGER,
                           "rsi_14", {"type": "lt", "threshold": 100})
        return StrategyDNA(
            signal_genes=[entry], logic_genes=LogicGenes(),
            execution_genes=ExecutionGenes(timeframe="1h"),
            risk_genes=RiskGenes(stop_loss=0.5),
            signal_sources=[{"type": "formula", "ref": ref,
                             "threshold": 0.5, "operator": ">",
                             "role": "exit"}],
            composition={"mode": "logic", "op": "AND"})

    def test_fx_exit_drives_closes_in_engine(self, env):
        """fx 出场源引擎链：fx=True（close>q25 段）平仓→多轮交易；
        恒假 fx 对照持有到期恰 1 笔——fx 源经组装器 SignalSet.exits
        到引擎的平仓消费真实贯通（笔数差异唯一归因 fx 出场）。"""
        from core.analysis.backtest_bridge import run_backtest_on_range
        m_fx, _, _, _ = run_backtest_on_range(
            env["db"], env["data_dir"], self._dna(env, "fx_hi"),
            "BTCUSDT", "1h", env["start"], env["end"], warmup_bars=60)
        m_never, _, _, _ = run_backtest_on_range(
            env["db"], env["data_dir"], self._dna(env, "fx_never"),
            "BTCUSDT", "1h", env["start"], env["end"], warmup_bars=60)
        assert m_never["total_trades"] == 1, \
            "恒假 fx：恒真入场持有到期恰 1 笔（构造前提）"
        assert m_fx["total_trades"] > 10, \
            f"fx=True 段平仓驱动多轮交易（实测 {m_fx['total_trades']}）"


class TestR2ChannelBooleanAlgebra:
    """④属性补强：组装器通道布尔代数（零元/幂等/交换律——恒等元
    由既有恒真插件用例承载）。"""

    @pytest.fixture()
    def cols(self, db: Path):
        q25 = None
        return register_formula_plugin(
            db, name="always3", expression="gt(close, 0)")["column"]

    @staticmethod
    def _df():
        from core.features.indicators import compute_all_indicators
        from tests.helpers.data_factory import make_ohlcv
        return compute_all_indicators(
            make_ohlcv(300, seed=5, base_price=60000.0))

    @staticmethod
    def _genes():
        return [
            SignalGene("RSI", {"period": 14}, SignalRole.ENTRY_TRIGGER,
                       "rsi_14", {"type": "lt", "threshold": 35}),
            SignalGene("RSI", {"period": 14}, SignalRole.EXIT_TRIGGER,
                       "rsi_14", {"type": "gt", "threshold": 55}),
        ]

    def test_annihilator_false_plugin_exits_never(self, db, cols):
        """零元律：恒假 exit 插件 AND 规则出场 → exits 恒 False
        （x AND 0 == 0——插件面恒假时规则出场被灭）。"""
        never = register_formula_plugin(
            db, name="never3", expression="gt(close, 1e12)")["column"]
        dna = _dna([{"type": "formula", "ref": never,
                     "threshold": 0.5, "operator": ">", "role": "exit"}],
                   genes=self._genes())
        sig = assemble_signal_set(db, dna, self._df())
        assert not bool(sig.exits.any()), "恒假插件=AND 零元"

    def test_idempotency_same_column_twice(self, db, cols):
        """幂等律：同一插件列在 exit 桶两条目 == 单条目（x AND x ==
        x——同列重复声明不改变语义）。"""
        one = _dna([{"type": "formula", "ref": cols,
                     "threshold": 0.5, "operator": ">", "role": "exit"}],
                   genes=self._genes())
        two = _dna([{"type": "formula", "ref": cols,
                     "threshold": 0.5, "operator": ">", "role": "exit"},
                    {"type": "formula", "ref": cols,
                     "threshold": 0.5, "operator": ">", "role": "exit"}],
                   genes=self._genes())
        df = self._df()
        s1 = assemble_signal_set(db, one, df)
        s2 = assemble_signal_set(db, two, df)
        assert bool((s1.exits == s2.exits).all()), "同列两条目幂等"

    def test_commutativity_source_order(self, db, cols):
        """交换律：exit 桶两不同插件源声明顺序交换结果不变（A AND B
        == B AND A）。"""
        part = register_formula_plugin(
            db, name="part3", expression="gt(close, 55000)")["column"]
        ab = [dict(type="formula", ref=cols, threshold=0.5, operator=">",
                   role="exit"),
              dict(type="formula", ref=part, threshold=0.5, operator=">",
                   role="exit")]
        df = self._df()
        s1 = assemble_signal_set(db, _dna(ab, genes=self._genes()), df)
        s2 = assemble_signal_set(db, _dna(ab[::-1], genes=self._genes()), df)
        assert bool((s1.exits == s2.exits).all()), "源声明序不影响通道"
