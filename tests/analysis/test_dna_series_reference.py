"""DNA 序列引用扩展测试（fix-research-c6-dna-series-reference · C6.4）。

缺陷四（fr_8839bb6d39f9）：假设条件里「指标列对指标列」的序列比较
（如 EMA(5) gt ema_20）可统计可登记，但翻译器只认数值 target——
列名 target 落 RESEARCH_NOT_EXPRESSIBLE，「可研究不可送考」。
本文件锁扩展后的五面：
- 翻译面：列名 target → series 形 condition（八键映射 D-14+别名归一）；
- 回测面：executor 存在性分派（threshold 优先→序列形→皆无全 False，
  消灭裸下标 KeyError 潜伏炸链）+target_field 多输出指标落位；
- warmup 面：序列形 target 指标并入装载并集（前段信号不缺失）；
- 进化面（A6 抽验三场景）：交叉搬运新键保留 / 变异不删未知键 /
  随机不产 series 形；
- 穿越族消费面：cross_above_series/cross_below_series 翻译产物在
  executor 的实际消费（含 target_field 透传与信号组装链路闭环）。

A2 统计面锚（原始形制 213）在 test_research_regression_anchors.py
随本批次启用。
"""

from pathlib import Path

import pandas as pd
import pytest

pytestmark = [pytest.mark.integration]

import random

from core.analysis.hypothesis_to_strategy import build_signal_genes
from core.analysis.errors import ResearchError
from core.analysis.validation_service import _dna_indicator_names
from core.evolution import operators
from core.evolution.operators import crossover
from core.features.indicators import INDICATOR_REGISTRY
from core.persistence.db_ext import init_db_ext
from core.analysis.executor import ensure_research_tables
from core.strategy.dna import (
    ExecutionGenes, LogicGenes, RiskGenes, SignalGene, SignalRole,
    StrategyDNA, TimeframeLayer,
)
from core.strategy.executor import dna_to_signal_set, evaluate_condition
from core.features.indicators import _compute_indicator
from tests.helpers.data_factory import make_ohlcv


@pytest.fixture()
def db(tmp_path: Path) -> Path:
    p = tmp_path / "q.db"
    init_db_ext(p)
    ensure_research_tables(p)
    return p


@pytest.fixture()
def df() -> pd.DataFrame:
    return make_ohlcv(n=250, freq="1h", start="2024-01-01", seed=11)


def _gene_cond(db, action, target, subject_period=5):
    when = [{"subject": {"indicator": "EMA", "params": {"period": subject_period},
                         "field": "ema"},
             "action": action, "target": target}]
    genes, _ = build_signal_genes(when, [], db)
    return genes[0].condition


class TestSeriesFormTranslation:
    """翻译面：八键映射+series 形产物+别名归一+真不可表达保留。"""

    @pytest.mark.parametrize("action,expected_type", [
        ("gt", "gt"), ("lt", "lt"), ("ge", "ge"), ("le", "le"),
        ("cross_above", "cross_above_series"),
        ("cross_below", "cross_below_series"),
        ("breakout", "cross_above_series"),   # 别名归一
        ("breakdown", "cross_below_series"),  # 别名归一
    ])
    def test_eight_key_series_form(self, db, action, expected_type):
        cond = _gene_cond(db, action, "ema_20")
        assert cond["type"] == expected_type
        assert cond["target_indicator"] == "EMA"
        assert cond["target_params"] == {"period": 20}
        assert cond["target_field"] == "ema"
        assert "threshold" not in cond

    def test_numeric_target_unchanged(self, db):
        """数值 target → threshold 形逐字节不变（回归锚）。"""
        cond = _gene_cond(db, "gt", 55)
        assert cond == {"type": "gt", "threshold": 55.0}

    def test_numeric_string_target_threshold(self, db):
        cond = _gene_cond(db, "lt", "55.5")
        assert cond == {"type": "lt", "threshold": 55.5}

    def test_base_column_target_rejected(self, db):
        """基础列 target（close）维持不可表达——真边界保留。"""
        with pytest.raises(ResearchError) as ei:
            _gene_cond(db, "gt", "close")
        assert ei.value.code == "RESEARCH_NOT_EXPRESSIBLE"

    def test_unknown_action_with_column_target_rejected(self, db):
        """八键外的 action+列名 target 维持不可表达。"""
        with pytest.raises(ResearchError) as ei:
            _gene_cond(db, "touch", "ema_20")
        assert ei.value.code == "RESEARCH_NOT_EXPRESSIBLE"

    def test_passthrough_actions_unaffected(self, db):
        """price_above/price_below 直通不受 C6 影响（target 占位）。"""
        cond = _gene_cond(db, "price_below", "ema_20")  # 列名 target 也占位
        assert cond == {"type": "price_below"}

    def test_a2_equivalent_form_translates(self, db):
        """A2 翻译面锚：排列（序列形）+回调（price_below 直通——close
        lt ema_20 的等价可表达写法）整组翻译成功。"""
        when = [
            {"subject": {"indicator": "EMA", "params": {"period": 5},
                         "field": "ema"},
             "action": "gt", "target": "ema_20"},
            {"subject": {"indicator": "EMA", "params": {"period": 20},
                         "field": "ema"},
             "action": "price_below", "target": 0},
        ]
        genes, _ = build_signal_genes(when, [], db)
        assert genes[0].condition["type"] == "gt"
        assert genes[0].condition["target_indicator"] == "EMA"
        assert genes[1].condition == {"type": "price_below"}


class TestExecutorDispatch:
    """回测面：存在性分派三路+target_field 多输出指标落位。"""

    def test_threshold_path_unchanged(self, df):
        """threshold 非 None → 现状阈值比较（旧 DNA 逐字节不变）。"""
        ema5 = _compute_indicator(df, "EMA", {"period": 5})["ema_5"]
        got = evaluate_condition(ema5, df["close"], {"type": "gt", "threshold": 60000}, df=df)
        pd.testing.assert_series_equal(got, ema5 > 60000, check_names=False)

    def test_series_path_matches_manual(self, df):
        """threshold 缺失+target_indicator → 逐元素序列比较。"""
        ema5 = _compute_indicator(df, "EMA", {"period": 5})["ema_5"]
        cond = {"type": "gt", "target_indicator": "EMA",
                "target_params": {"period": 20}, "target_field": "ema"}
        got = evaluate_condition(ema5, df["close"], cond, df=df)
        ema20 = _compute_indicator(df, "EMA", {"period": 20})["ema_20"]
        pd.testing.assert_series_equal(
            got, (ema5 > ema20).fillna(False), check_names=False)
        assert int(got.sum()) > 0

    def test_both_missing_all_false_no_crash(self, df):
        """皆无键 → 全 False（消灭原裸下标 KeyError 炸链隐患）。"""
        ema5 = _compute_indicator(df, "EMA", {"period": 5})["ema_5"]
        got = evaluate_condition(ema5, df["close"], {"type": "lt"}, df=df)
        assert not bool(got.any())

    def test_coexistence_threshold_wins(self, df):
        """并存 → threshold 优先、target_* 忽略（进化变异恒写可产）。"""
        ema5 = _compute_indicator(df, "EMA", {"period": 5})["ema_5"]
        cond = {"type": "gt", "threshold": 60000,
                "target_indicator": "EMA",
                "target_params": {"period": 20}, "target_field": "ema"}
        got = evaluate_condition(ema5, df["close"], cond, df=df)
        pd.testing.assert_series_equal(got, ema5 > 60000, check_names=False)

    def test_macd_field_resolution(self, df):
        """多输出指标 target：target_field 落正确列（MACD histogram）。"""
        sma5 = _compute_indicator(df, "SMA", {"period": 5})["sma_5"]
        cond = {"type": "gt", "target_indicator": "MACD",
                "target_params": {"fast": 12, "slow": 26, "signal": 9},
                "target_field": "histogram"}
        got = evaluate_condition(sma5, df["close"], cond, df=df)
        hist = _compute_indicator(df, "MACD",
                                  {"fast": 12, "slow": 26, "signal": 9})[
                                      "macd_histogram_12_26_9"]
        pd.testing.assert_series_equal(
            got, (sma5 > hist).fillna(False), check_names=False)


class TestWarmupUnion:
    """warmup 面：序列形 target 指标并入装载并集。"""

    def test_target_indicator_in_names(self, df):
        dna = StrategyDNA(signal_genes=[
            SignalGene(indicator="EMA", params={"period": 5},
                       role=SignalRole.ENTRY_TRIGGER, field_name="ema",
                       condition={"type": "gt", "target_indicator": "ADX",
                                  "target_params": {"period": 14},
                                  "target_field": "adx"})])
        names = _dna_indicator_names(dna)
        assert "EMA" in names and "ADX" in names

    def test_prefix_signals_not_all_false(self, db, df):
        """target 周期大于 subject 周期：前段信号不因 warmup 不足缺失
        （构造 SMA(5) vs SMA(50)，手动取较长 warmup 后前段有信号）。"""
        d = df.iloc[:120].copy()
        sma5 = _compute_indicator(d, "SMA", {"period": 5})["sma_5"]
        cond = {"type": "lt", "target_indicator": "SMA",
                "target_params": {"period": 50}, "target_field": "sma"}
        got = evaluate_condition(sma5, d["close"], cond, df=d)
        valid = sma5.notna() & _compute_indicator(
            d, "SMA", {"period": 50})["sma_50"].notna()
        assert int(got[valid].sum()) > 0  # 有效域内非全假

    def test_mtf_layer_series_form_regression(self):
        """MTF 面：evaluate_layer 走同一 evaluate_condition——层内
        序列形基因（翻译产物形制）在高周期 df 上正常求值，不降级、
        信号非全假（C6.3 的 MTF 回归断言）。"""
        base = make_ohlcv(n=800, freq="1h", seed=7)
        high = make_ohlcv(n=200, freq="4h", seed=7)
        dna = StrategyDNA(
            layers=[TimeframeLayer(
                timeframe="4h",
                signal_genes=[SignalGene(
                    "EMA", {"period": 5}, SignalRole.ENTRY_TRIGGER,
                    "ema_5", dict(_SERIES_COND))],
                logic_genes=LogicGenes())],
            execution_genes=ExecutionGenes(timeframe="1h"),
            risk_genes=RiskGenes(),
        )
        ss = dna_to_signal_set(dna, base, dfs_by_timeframe={"4h": high})
        assert ss.degraded_layers == 0
        assert int(ss.entries.sum()) > 0


class TestSerializationRoundtrip:
    """序列化往返：to_dict/from_dict 新键透传。"""

    def test_roundtrip(self):
        """to_dict/from_dict 直调（dna.py:115-132 实存——原 hasattr 探测
        恒走第一分支，else 兜底为死代码且存在空转绿路径，F4 收敛）。"""
        gene = SignalGene(
            indicator="EMA", params={"period": 5},
            role=SignalRole.ENTRY_TRIGGER, field_name="ema",
            condition={"type": "cross_above_series", "target_indicator": "MACD",
                       "target_params": {"fast": 12, "slow": 26, "signal": 9},
                       "target_field": "histogram"})
        g2 = SignalGene.from_dict(gene.to_dict())
        assert g2.condition == gene.condition


# ---------------------------------------------------------------------------
# A6 进化抽验三场景（design §4 进化兼容结论——交叉新键保留 / 变异不删
# 未知键 / 随机不产 series 形）
# ---------------------------------------------------------------------------

_SERIES_COND = {"type": "gt", "target_indicator": "EMA",
                "target_params": {"period": 20}, "target_field": "ema"}


def _dna_with_series_gene() -> StrategyDNA:
    """含翻译产物 series 形基因的 DNA（entry）+ 普通阈值形 exit。"""
    return StrategyDNA(
        signal_genes=[
            SignalGene("EMA", {"period": 5}, SignalRole.ENTRY_TRIGGER,
                       "ema_5", dict(_SERIES_COND)),
            SignalGene("RSI", {"period": 14}, SignalRole.EXIT_TRIGGER,
                       "rsi_14", {"type": "gt", "threshold": 70}),
        ],
        logic_genes=LogicGenes(),
        execution_genes=ExecutionGenes(timeframe="4h"),
        risk_genes=RiskGenes(),
    )


class TestEvolutionCrossoverSeriesKeys:
    """进化抽验场景一：交叉整体搬运基因对象——series 形新键保留。"""

    def test_base_layer_entry_carries_series_keys(self):
        """基础层交叉：entry 侧取亲代 a（确定性路径），series 形
        condition 的 target_* 三键逐键保留、无 threshold 混入。"""
        a = _dna_with_series_gene()
        b = StrategyDNA(
            signal_genes=[
                SignalGene("RSI", {"period": 14}, SignalRole.ENTRY_TRIGGER,
                           "rsi_14", {"type": "lt", "threshold": 30}),
                SignalGene("EMA", {"period": 20}, SignalRole.EXIT_TRIGGER,
                           "ema_20", {"type": "gt", "threshold": 0}),
            ],
            logic_genes=LogicGenes(),
            execution_genes=ExecutionGenes(timeframe="4h"),
            risk_genes=RiskGenes(),
        )
        for seed in range(10):
            random.seed(seed)
            child = crossover(a, b)
            entries = [g for g in child.signal_genes
                       if g.role == SignalRole.ENTRY_TRIGGER]
            assert len(entries) == 1
            assert entries[0].condition == _SERIES_COND

    def test_mtf_layer_crossover_carries_series_keys(self):
        """MTF 层交叉（同位层 entry 取 a 方引用搬运）：层内 series 形
        基因的 condition 逐键保留（list 级引用传递不逐字段重建）。"""
        a = StrategyDNA(
            layers=[TimeframeLayer(
                timeframe="1d",
                signal_genes=[SignalGene(
                    "EMA", {"period": 5}, SignalRole.ENTRY_TRIGGER,
                    "ema_5", dict(_SERIES_COND))],
                logic_genes=LogicGenes())],
            execution_genes=ExecutionGenes(timeframe="1d"),
            risk_genes=RiskGenes(),
        )
        b = StrategyDNA(
            layers=[TimeframeLayer(
                timeframe="1d",
                signal_genes=[SignalGene(
                    "RSI", {"period": 14}, SignalRole.EXIT_TRIGGER,
                    "rsi_14", {"type": "gt", "threshold": 70})],
                logic_genes=LogicGenes())],
            execution_genes=ExecutionGenes(timeframe="1d"),
            risk_genes=RiskGenes(),
        )
        random.seed(0)
        child = crossover(a, b)
        assert child.layers is not None and len(child.layers) == 1
        entries = [g for g in child.layers[0].signal_genes
                   if g.role == SignalRole.ENTRY_TRIGGER]
        assert len(entries) == 1
        assert entries[0].condition == _SERIES_COND


class TestEvolutionMutationSeriesKeys:
    """进化抽验场景二：变异按键写入不删除未知键——series 形
    condition 的 target_* 键经各变异算子后不丢。"""

    # 不替换 condition 的变异算子（结构性增删基因的算子不在此列——
    # 它们移除整个基因，与「删 condition 未知键」是不同语义）
    @pytest.mark.parametrize("mut_name", [
        "mutate_params", "mutate_logic", "mutate_risk",
        "mutate_cross_logic", "mutate_confluence_threshold",
        "mutate_proximity_mult",
    ])
    def test_mutations_preserve_series_condition(self, mut_name):
        mut = getattr(operators, mut_name)
        for seed in range(10):
            random.seed(seed)
            child = mut(_dna_with_series_gene())
            series_genes = [g for g in child.signal_genes
                            if g.indicator == "EMA" and g.role == SignalRole.ENTRY_TRIGGER]
            assert len(series_genes) == 1
            assert series_genes[0].condition == _SERIES_COND

    def test_mutate_indicator_replacement_valid_forms(self):
        """mutate_indicator 整体替换 condition：替换产物必为合法形
        （阈值四族带 threshold / cross_*_series 带 target_indicator），
        未被替换的 series 形基因原样保留——不产断裂形（无 threshold
        亦无 target_* 的四族）。"""
        for seed in range(20):
            random.seed(seed)
            child = operators.mutate_indicator(_dna_with_series_gene())
            for g in child.signal_genes:
                c = g.condition
                if c.get("type") in ("lt", "gt", "le", "ge", "eq"):
                    assert ("threshold" in c) or ("target_indicator" in c), (
                        f"断裂形 condition: {c}")


class TestEvolutionRandomNoSeriesForm:
    """进化抽验场景三：generate_random_condition 不主动产 series 形
    （阈值四族恒写 threshold——翻译产物专属形制不被随机生成器倒灌）。"""

    def test_threshold_family_always_has_threshold(self):
        random.seed(0)
        for name in INDICATOR_REGISTRY:  # 注册表全量 47 指标
            for _ in range(10):
                cond = operators.generate_random_condition(name)
                if cond["type"] in ("lt", "gt", "le", "ge", "eq"):
                    assert "threshold" in cond, f"{name}: {cond}"
                    assert "target_indicator" not in cond, f"{name}: {cond}"


# ---------------------------------------------------------------------------
# 穿越族序列形 executor 消费（翻译产物 cross_above_series/cross_below_
# series 的求值与信号组装链路——八键映射的 cross/break 面）
# ---------------------------------------------------------------------------

class TestCrossSeriesExecution:
    """穿越族序列形：翻译产物在 executor 的实际消费。"""

    def test_cross_above_series_semantics_manual_reference(self, db, df):
        """翻译产物 cross_above_series 求值 = 独立手工参考（前一根
        subject 低于 target、当前根不低于 target——上穿语义）。"""
        cond = _gene_cond(db, "cross_above", "ema_20")
        assert cond["type"] == "cross_above_series"
        ema5 = _compute_indicator(df, "EMA", {"period": 5})["ema_5"]
        ema20 = _compute_indicator(df, "EMA", {"period": 20})["ema_20"]
        got = evaluate_condition(ema5, df["close"], cond, df=df)
        expected = ((ema5.shift(1) < ema20.shift(1))
                    & (ema5 >= ema20)).fillna(False)
        pd.testing.assert_series_equal(got, expected, check_names=False)
        assert int(got.sum()) > 0

    def test_cross_below_series_semantics_manual_reference(self, db, df):
        cond = _gene_cond(db, "breakdown", "ema_20")  # 别名归一 cross_below
        assert cond["type"] == "cross_below_series"
        ema5 = _compute_indicator(df, "EMA", {"period": 5})["ema_5"]
        ema20 = _compute_indicator(df, "EMA", {"period": 20})["ema_20"]
        got = evaluate_condition(ema5, df["close"], cond, df=df)
        expected = ((ema5.shift(1) > ema20.shift(1))
                    & (ema5 <= ema20)).fillna(False)
        pd.testing.assert_series_equal(got, expected, check_names=False)
        assert int(got.sum()) > 0

    def test_cross_series_macd_field_resolution(self, db, df):
        """穿越族×多输出列 target：target_field 透传落 histogram 列
        （cross_above_series 分支消费翻译产物的 target_field 键）。
        subject 取 MACD 线（与 histogram 同量级——密度断言可区分
        field 落错列的假等价）。"""
        when = [{"subject": {"indicator": "MACD",
                             "params": {"fast": 12, "slow": 26, "signal": 9},
                             "field": "macd"},
                 "action": "cross_above", "target": "macd_histogram_12_26_9"}]
        genes, _ = build_signal_genes(when, [], db)
        cond = genes[0].condition
        assert cond["target_indicator"] == "MACD"
        assert cond["target_field"] == "histogram"
        out = _compute_indicator(df, "MACD",
                                 {"fast": 12, "slow": 26, "signal": 9})
        macd_line = out["macd_12_26_9"]
        hist = out["macd_histogram_12_26_9"]
        got = evaluate_condition(macd_line, df["close"], cond, df=df)
        expected = ((macd_line.shift(1) < hist.shift(1))
                    & (macd_line >= hist)).fillna(False)
        pd.testing.assert_series_equal(got, expected, check_names=False)
        assert int(got.sum()) > 0

    def test_translation_form_matches_evolution_form(self, df):
        """同列一致性：翻译形（带 target_field="ema"）与进化形（无
        target_field——单输出缺省列语义）对同列 target 求值逐元素
        一致（两形制在 executor 消费面等价）。"""
        ema5 = _compute_indicator(df, "EMA", {"period": 5})["ema_5"]
        translated = {"type": "cross_above_series", "target_indicator": "EMA",
                      "target_params": {"period": 20}, "target_field": "ema"}
        evolution = {"type": "cross_above_series",
                     "target_indicator": "EMA",
                     "target_params": {"period": 20}}
        g1 = evaluate_condition(ema5, df["close"], translated, df=df)
        g2 = evaluate_condition(ema5, df["close"], evolution, df=df)
        pd.testing.assert_series_equal(g1, g2, check_names=False)

    def test_signal_set_assembly_nonzero(self, db, df):
        """信号组装链路：翻译产物 DNA（when→entry cross_above /
        then→exit cross_below）经 dna_to_signal_set 真实组装——
        entries/exits 均非全 False（消费闭环不止于单条件求值）。"""
        when_entry = [{"subject": {"indicator": "EMA",
                                    "params": {"period": 5}, "field": "ema"},
                       "action": "cross_above", "target": "ema_20"}]
        then_exit = [{"subject": {"indicator": "EMA",
                                   "params": {"period": 5}, "field": "ema"},
                      "action": "cross_below", "target": "ema_20"}]
        genes, _ = build_signal_genes(when_entry, then_exit, db)
        roles = {g.role for g in genes}
        assert roles == {SignalRole.ENTRY_TRIGGER, SignalRole.EXIT_TRIGGER}
        dna = StrategyDNA(
            signal_genes=list(genes),
            logic_genes=LogicGenes(),
            execution_genes=ExecutionGenes(timeframe="4h"),
            risk_genes=RiskGenes(),
        )
        ss = dna_to_signal_set(dna, df)
        assert int(ss.entries.sum()) > 0
        assert int(ss.exits.sum()) > 0


# ---------------------------------------------------------------------------
# 旧 DNA 真实回测链路级对照锚（A6 回归——同参数同数据与 main 基线
# 一致，tasks C6.2 完成标准）
# ---------------------------------------------------------------------------

def _series_sha1(s) -> str:
    if s is None:
        return "null"
    import hashlib
    import numpy as np
    if isinstance(s, pd.DataFrame):
        return hashlib.sha1(
            pd.util.hash_pandas_object(s, index=True).values.tobytes()
        ).hexdigest()
    if isinstance(s, pd.Series):
        return hashlib.sha1(
            pd.util.hash_pandas_object(s, index=True).values.tobytes()
        ).hexdigest()
    return hashlib.sha1(s.tobytes()).hexdigest()


def _legacy_backtest_df() -> pd.DataFrame:
    return make_ohlcv(n=1200, freq="4h", base_price=40000.0, seed=11,
                      start="2024-01-01")


def _legacy_dna(tag: str) -> StrategyDNA:
    """四组旧形制 DNA（全部 main 既有能力——C6 改动面的对照对象）：
    A 阈值四族(gt/lt/ge/le)×四角色；B 穿越阈值形；C 进化形序列对照
    （cross_*_series 无 target_field）；D 直通+触碰形态类。"""
    common = dict(logic_genes=LogicGenes(),
                  execution_genes=ExecutionGenes(timeframe="4h"),
                  risk_genes=RiskGenes())
    if tag == "A_threshold":
        genes = [
            SignalGene("RSI", {"period": 14}, SignalRole.ENTRY_TRIGGER,
                       "rsi_14", {"type": "lt", "threshold": 45}),
            SignalGene("RSI", {"period": 14}, SignalRole.ENTRY_GUARD,
                       "rsi_14", {"type": "ge", "threshold": 25}),
            SignalGene("RSI", {"period": 14}, SignalRole.EXIT_TRIGGER,
                       "rsi_14", {"type": "gt", "threshold": 60}),
            SignalGene("RSI", {"period": 14}, SignalRole.REDUCE_TRIGGER,
                       "rsi_14", {"type": "le", "threshold": 75}),
        ]
    elif tag == "B_cross":
        genes = [
            SignalGene("EMA", {"period": 5}, SignalRole.ENTRY_TRIGGER,
                       "ema_5", {"type": "cross_above", "threshold": 40000}),
            SignalGene("EMA", {"period": 5}, SignalRole.EXIT_TRIGGER,
                       "ema_5", {"type": "cross_below", "threshold": 39500}),
        ]
    elif tag == "C_series_evolution":
        genes = [
            SignalGene("EMA", {"period": 5}, SignalRole.ENTRY_TRIGGER,
                       "ema_5", {"type": "cross_above_series",
                                 "target_indicator": "EMA",
                                 "target_params": {"period": 20}}),
            SignalGene("EMA", {"period": 5}, SignalRole.EXIT_TRIGGER,
                       "ema_5", {"type": "cross_below_series",
                                 "target_indicator": "EMA",
                                 "target_params": {"period": 20}}),
        ]
    else:  # D_price_touch
        genes = [
            SignalGene("EMA", {"period": 20}, SignalRole.ENTRY_TRIGGER,
                       "ema_20", {"type": "price_above"}),
            SignalGene("EMA", {"period": 20}, SignalRole.ENTRY_GUARD,
                       "ema_20", {"type": "touch_bounce",
                                  "direction": "support",
                                  "proximity_pct": 0.01}),
            SignalGene("EMA", {"period": 20}, SignalRole.EXIT_TRIGGER,
                       "ema_20", {"type": "price_below"}),
        ]
    return StrategyDNA(signal_genes=genes, **common)


# 基线锚（2026-10-13 采集）：merge-base bebb6e0（=main HEAD，C6 改动
# 前）与 C6 分支各跑同脚本（本文件 _legacy_dna 四组 + BacktestEngine
# 默认参 + make_ohlcv n=1200 seed=11），全部指标与三条 sha1（权益曲线/
# 逐笔明细/逐笔收益）两版逐字节一致——锚值即一致值，防后续无声漂移。
_LEGACY_BASELINE = {
    "A_threshold": {
        "total_trades": 611, "total_return": -0.008565041957426911,
        "sharpe_ratio": -1.0065135408098296,
        "max_drawdown": -0.02449380855097925,
        "win_rate": 0.381342062193126, "liquidated": False,
        "data_bars": 1200,
        "equity_sha1": "94992240bbc3b3d1fc5c912bffbc21dc8c454e67",
        "trades_sha1": "8995f01d7e61311dd3dae087dc8253ddd8af6d72",
        "trade_returns_sha1": "d0afc2f972c26ffd41f31a00e657e64f3c5ba8ae",
    },
    "B_cross": {
        "total_trades": 1, "total_return": 0.07407210303952128,
        "sharpe_ratio": 0.0, "max_drawdown": -0.06764204321144672,
        "win_rate": 1.0, "liquidated": False, "data_bars": 1200,
        "equity_sha1": "9cd953bef3a2fb489aba4e602dc2876285579a75",
        "trades_sha1": "c26e2417cf31f072d35d082bf93095ec423305b3",
        "trade_returns_sha1": "6f65a7b799115d1c4c7d333ca2ec91898756bdd5",
    },
    "C_series_evolution": {
        "total_trades": 38, "total_return": -0.026935079122641592,
        "sharpe_ratio": -0.7515117044689164,
        "max_drawdown": -0.057966010830329776,
        "win_rate": 0.3684210526315789, "liquidated": False,
        "data_bars": 1200,
        "equity_sha1": "06145ca8f4eceb3e3ff968f5173352fe62c710c2",
        "trades_sha1": "c588baffe5573039780602266babe791e641ac55",
        "trade_returns_sha1": "7d3541f8d5d0faf30e660a532a78631d106b5ade",
    },
    "D_price_touch": {
        "total_trades": 92, "total_return": -0.10577790213940741,
        "sharpe_ratio": -2.831023285302675,
        "max_drawdown": -0.11135478878636357,
        "win_rate": 0.32608695652173914, "liquidated": False,
        "data_bars": 1200,
        "equity_sha1": "c480809a77fbb0608c678a5bcd3d8d98edaefb52",
        "trades_sha1": "c9c0e22c6feddc56cff8b933814aa5824abad8e2",
        "trade_returns_sha1": "7bd1ae4dbf7b71dfe572c8590d99d3a263f736a4",
    },
}


class TestLegacyBacktestMainParity:
    """旧 DNA 真实回测链路级对照锚：executor 存在性分派改造后，
    旧形制 DNA 的回测行为与 main 基线逐字节一致。"""

    @pytest.mark.slow
    @pytest.mark.parametrize("tag", list(_LEGACY_BASELINE))
    def test_legacy_backtest_matches_main_baseline(self, tag):
        from core.backtest.engine import BacktestEngine
        df = _legacy_backtest_df()
        result = BacktestEngine().run(_legacy_dna(tag), df)
        got = {
            "total_trades": result.total_trades,
            "total_return": result.total_return,
            "sharpe_ratio": result.sharpe_ratio,
            "max_drawdown": result.max_drawdown,
            "win_rate": result.win_rate,
            "liquidated": result.liquidated,
            "data_bars": result.data_bars,
            "equity_sha1": _series_sha1(result.equity_curve),
            "trades_sha1": _series_sha1(result.trades_df),
            "trade_returns_sha1": _series_sha1(result.trade_returns),
        }
        assert got == _LEGACY_BASELINE[tag]


class TestSandboxBacktestSeriesForm:
    """沙盒回测链路级：翻译产物序列形 DNA 经 run_isolated_backtest
    全链（warmup 并集推导 → 前扩装载 → 回测）触发密度非零（C6.2
    完成标准「回测不得为零」+C6.3 warmup 并集链路级观测）。"""

    @pytest.fixture()
    def sandbox(self, tmp_path: Path):
        data_dir = tmp_path / "market"
        data_dir.mkdir()
        df = make_ohlcv(n=2800, freq="1h", base_price=40000.0, seed=11,
                        start="2024-01-01")
        df.to_parquet(data_dir / "BTCUSDT_1h.parquet")
        db = tmp_path / "q.db"
        init_db_ext(db)
        ensure_research_tables(db)
        return {"data_dir": data_dir, "db": db, "df": df}

    def _register(self, db) -> str:
        from core.analysis.hypothesis_service import register_hypothesis
        out = register_hypothesis(
            db,
            conditions={"when_conditions": [
                {"subject": {"indicator": "EMA", "params": {"period": 5},
                             "field": "ema"},
                 "action": "gt", "target": "ema_20"}],
                "then_conditions": [
                    {"subject": "close", "action": "gt", "target": 0}]},
            statement="C6 序列形沙盒链路测试假设",
            # 自报数据区间与请求区间不重叠（判定并集求交前置——
            # data_ranges 是研究自报，回测请求区间须在其外）
            data_ranges=[{"symbol": "BTCUSDT", "timeframe": "1h",
                          "start": "2020-01-01T00:00:00Z",
                          "end": "2020-01-10T00:00:00Z"}])
        return out["hypothesis_id"]

    def test_series_form_sandbox_nonzero_and_warmup_union(self, sandbox):
        """序列形 DNA 沙盒回测全链：total_trades 非零（触发密度不为
        零）+ warmup derived 并入 target 指标。

        区分度构造（lookback 取注册表参数上限之和而非 DNA 实参）：
        subject RSI lookback 50 → 50×3=150 < floor 200，并集失效时
        derived 停在 200；target MACD lookback 110（fast 30+slow 60
        +signal 20 注册表上限和）→ 110×3=330 > 200，并集生效时
        derived == 330（target 指标进推导的链路级观测）。"""
        from core.analysis.validation_service import run_isolated_backtest
        from core.persistence.repository.strategy import save_strategy
        db, data_dir, df = (sandbox["db"], sandbox["data_dir"],
                            sandbox["df"])
        when = [{"subject": {"indicator": "RSI", "params": {"period": 14},
                             "field": "rsi"},
                 "action": "gt", "target": "macd_12_26_9"}]
        then = [{"subject": {"indicator": "RSI", "params": {"period": 14},
                             "field": "rsi"},
                 "action": "lt", "target": "macd_12_26_9"}]
        genes, _ = build_signal_genes(when, then, db)
        dna = StrategyDNA(
            signal_genes=list(genes),
            logic_genes=LogicGenes(),
            execution_genes=ExecutionGenes(timeframe="1h"),
            risk_genes=RiskGenes(),
        )
        save_strategy(db, strategy_id="c6_series", dna_json=dna.to_json(),
                      symbol="BTCUSDT", timeframe="1h")
        hyp_id = self._register(db)
        # 请求区间从第 500 根起（> warmup 330，余 2300 bar 请求段）
        out = run_isolated_backtest(
            db, data_dir, hypothesis_id=hyp_id, strategy_id="c6_series",
            request_range={"symbol": "BTCUSDT", "timeframe": "1h",
                           "start": df.index[500].isoformat(),
                           "end": df.index[-1].isoformat()})
        assert out["metrics"]["total_trades"] > 0
        assert out["warmup"]["derived"] == 330
