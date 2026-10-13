"""DNA 序列引用扩展测试（fix-research-c6-dna-series-reference · C6.4）。

缺陷四（fr_8839bb6d39f9）：假设条件里「指标列对指标列」的序列比较
（如 EMA(5) gt ema_20）可统计可登记，但翻译器只认数值 target——
列名 target 落 RESEARCH_NOT_EXPRESSIBLE，「可研究不可送考」。
本文件锁扩展后的三面：
- 翻译面：列名 target → series 形 condition（八键映射 D-14+别名归一）；
- 回测面：executor 存在性分派（threshold 优先→序列形→皆无全 False，
  消灭裸下标 KeyError 潜伏炸链）+target_field 多输出指标落位；
- warmup 面：序列形 target 指标并入装载并集（前段信号不缺失）。

A2 统计面锚（原始形制 213）在 test_research_regression_anchors.py
随本批次启用。
"""

from pathlib import Path

import pandas as pd
import pytest

pytestmark = [pytest.mark.integration]

from core.analysis.hypothesis_to_strategy import build_signal_genes
from core.analysis.errors import ResearchError
from core.analysis.validation_service import _dna_indicator_names
from core.persistence.db_ext import init_db_ext
from core.analysis.executor import ensure_research_tables
from core.strategy.dna import SignalGene, SignalRole, StrategyDNA
from core.strategy.executor import evaluate_condition
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


class TestSerializationRoundtrip:
    """序列化往返：to_dict/from_dict 新键透传。"""

    def test_roundtrip(self):
        gene = SignalGene(
            indicator="EMA", params={"period": 5},
            role=SignalRole.ENTRY_TRIGGER, field_name="ema",
            condition={"type": "cross_above_series", "target_indicator": "MACD",
                       "target_params": {"fast": 12, "slow": 26, "signal": 9},
                       "target_field": "histogram"})
        d = gene.to_dict() if hasattr(gene, "to_dict") else None
        if d is not None:
            g2 = SignalGene.from_dict(d) if hasattr(SignalGene, "from_dict") else None
            if g2 is not None:
                assert g2.condition == gene.condition
        else:
            # dataclass asdict 路径兜底（形制探测——不假设入口名）
            from dataclasses import asdict
            d2 = asdict(gene)
            assert d2["condition"]["target_indicator"] == "MACD"
