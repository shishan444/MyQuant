"""信号源实质判定测试（fix-research-c4-signal-source-substance · C4.3）。

缺陷五（fr_1aee6a6e7e38）：纯插件进场策略被静默作废——组合层的
「有无规则源」判定按 signal_sources 声明（type=rule 字样）而非基因
角色实质，signal_genes 仅 exit_trigger+公式进场源的形制
（st_f86b4295c58b）被误判「有规则源」，空 rule 基因的全 False 基线
把纯插件进场作废成永不入场（0 笔交易静默）。

锚组：
- A7-①：纯插件进场复活——entries 触发数与插件侧统计锚精确一致；
- A7-②：混合形制回归不变（规则进场+公式源过滤）；
- guard-only：仅 ENTRY_GUARD 进场基因时判「有规则进场侧」，guard
  掩码语义不被单位元基线静默丢弃；
- caplog 三件：固定前缀/插件列名/警告不入返回体；
- 两边界：真实零触发（同告警=正确契约）/正常混合态负向无警告；
- 退化形态：无 plugin_sources 且无 composition 走原路径零涉及。

期望值独立手算（插件侧掩码向量化重算），不用被测实现生成。
"""

from pathlib import Path

import pandas as pd
import pytest

pytestmark = [pytest.mark.integration]

from core.analysis.plugin_service import register_formula_plugin
from core.analysis.signal_assembly import assemble_signal_set
from core.persistence.db_ext import init_db_ext
from core.analysis.executor import ensure_research_tables
from core.strategy.dna import SignalGene, SignalRole, StrategyDNA
from tests.helpers.data_factory import make_ohlcv

PLUGIN_EXPR = "(close - shift(close, 3)) / shift(close, 3)"


@pytest.fixture()
def db(tmp_path: Path) -> Path:
    p = tmp_path / "q.db"
    init_db_ext(p)
    ensure_research_tables(p)
    register_formula_plugin(p, name="dip", expression=PLUGIN_EXPR)
    return p


@pytest.fixture()
def df() -> pd.DataFrame:
    return make_ohlcv(n=300, freq="1h", start="2024-01-01", seed=7)


def _plugin_mask(df) -> pd.Series:
    """插件侧掩码独立重算（期望值来源——不用被测实现）。"""
    return ((df["close"] - df["close"].shift(3)) / df["close"].shift(3)) < -0.01


def _dna(genes, sources=None, comp=None):
    return StrategyDNA(
        signal_genes=genes,
        signal_sources=sources if sources is not None else [
            {"type": "formula", "ref": "fx_dip",
             "threshold": -0.01, "operator": "<"}],
        composition=comp if comp is not None else {"mode": "logic", "op": "AND"},
    )


def _exit_gene() -> SignalGene:
    """THEN 出场基因（恒不触发——不干扰进场断言；注意 dna_to_signal_set
    会剔除「进场位∩出场位」（exit 优先），exit 恒 True 会吞掉全部进场）。"""
    return SignalGene(indicator="EMA", params={"period": 10},
                      role=SignalRole.EXIT_TRIGGER, field_name="ema",
                      condition={"type": "gt", "threshold": 1e12})


class TestA7PurePluginRevival:
    """A7-①：纯插件进场复活（st_f86b4295c58b 缺陷形制）。"""

    def test_pure_plugin_entries_match_stat_anchor(self, db, df):
        dna = _dna([_exit_gene()])  # 仅出场基因+公式进场源
        ss = assemble_signal_set(db, dna, df)
        got = int(ss.entries.fillna(False).astype(bool).sum())
        expect = int(_plugin_mask(df).fillna(False).sum())  # 逐 bar 直译（组合层无事件化）
        assert expect > 0
        assert got == expect  # 修复前 got==0（静默作废）


class TestA7MixedRegression:
    """A7-②：混合形制回归不变（规则进场基因+公式源过滤）。"""

    def test_mixed_rule_plus_plugin_filter(self, db, df):
        # 规则进场：close > 300 bar 中位数（约半数 bar）
        med = float(df["close"].median())
        entry = SignalGene(indicator="SMA", params={"period": 5},
                           role=SignalRole.ENTRY_TRIGGER, field_name="sma",
                           condition={"type": "gt", "threshold": med})
        ss = assemble_signal_set(db, _dna([entry, _exit_gene()]), df)
        entries = ss.entries.fillna(False).astype(bool)
        sma5 = df["close"].rolling(5).mean()  # 基因= SMA(5) gt med
        expect = (sma5 > med) & _plugin_mask(df)
        pd.testing.assert_series_equal(
            entries, expect.fillna(False).astype(bool), check_names=False)
        assert int(entries.sum()) > 0  # 非退化：确有混合交集


class TestGuardOnlySubstance:
    """guard-only：仅 ENTRY_GUARD 进场基因——判「有规则进场侧」，
    guard 掩码语义不被单位元基线静默丢弃。"""

    def test_guard_false_blocks_plugin_entries(self, db, df):
        # guard 条件恒 False（close > 天文数字）——修复前（声明面判定
        # 失误时）基线被重置为恒 True，guard 语义丢失；修复后 guard
        # 掩码参与 AND：插件触发点也不进场。
        guard = SignalGene(indicator="SMA", params={"period": 5},
                           role=SignalRole.ENTRY_GUARD, field_name="sma",
                           condition={"type": "gt", "threshold": 1e12})
        ss = assemble_signal_set(db, _dna([guard, _exit_gene()]), df)
        entries = ss.entries.fillna(False).astype(bool)
        assert int(_plugin_mask(df).sum()) > 0  # 插件侧确有触发
        assert int(entries.sum()) == 0  # guard 全 False 拦住全部进场

    def test_guard_true_passes_plugin_entries(self, db, df):
        # 对照：guard 恒 True（close > 0）——插件触发自由进场。
        guard = SignalGene(indicator="SMA", params={"period": 5},
                           role=SignalRole.ENTRY_GUARD, field_name="sma",
                           condition={"type": "gt", "threshold": 0})
        ss = assemble_signal_set(db, _dna([guard, _exit_gene()]), df)
        got = int(ss.entries.fillna(False).astype(bool).sum())
        # guard 掩码恒 True（除 SMA warmup 首 4 bar），插件触发全部放行
        assert got == int(_plugin_mask(df).fillna(False).sum())


class TestCombineSentinelCaplog:
    """C4.2 caplog 三件：固定前缀/插件列名/警告不入返回体。"""

    def test_sentinel_warning_emitted_not_in_payload(
            self, db, df, caplog):
        import logging
        # 触发条件：规则进场基因条件全 False + 插件源在场
        entry = SignalGene(indicator="SMA", params={"period": 5},
                           role=SignalRole.ENTRY_TRIGGER, field_name="sma",
                           condition={"type": "gt", "threshold": 1e12})
        with caplog.at_level(logging.WARNING,
                             logger="core.analysis.signal_assembly"):
            ss = assemble_signal_set(db, _dna([entry, _exit_gene()]), df)
        joined = "\n".join(r.getMessage() for r in caplog.records)
        assert "[signal-assembly]" in joined  # 固定前缀
        assert "fx_dip" in joined  # 插件列名
        assert not hasattr(ss, "warnings") or not getattr(ss, "warnings", None)

    def test_real_zero_trigger_same_warning_is_contract(self, db, df, caplog):
        """边界一：真实零触发（规则基线与插件侧都为零）同样告警——
        spec 字面声明为正确行为契约（可观测优先，不区分误报）。"""
        import logging
        entry = SignalGene(indicator="SMA", params={"period": 5},
                           role=SignalRole.ENTRY_TRIGGER, field_name="sma",
                           condition={"type": "gt", "threshold": 1e12})
        zero_dna = StrategyDNA(
            signal_genes=[entry, _exit_gene()],
            signal_sources=[{"type": "formula", "ref": "fx_dip",
                             "threshold": -99.0, "operator": "<"}],
            composition={"mode": "logic", "op": "AND"})
        with caplog.at_level(logging.WARNING,
                             logger="core.analysis.signal_assembly"):
            assemble_signal_set(db, zero_dna, df)
        assert any("[signal-assembly]" in r.getMessage()
                   for r in caplog.records)

    def test_normal_mixed_no_warning(self, db, df, caplog):
        """边界二：正常混合态（规则基线非全 False）负向无警告。"""
        import logging
        med = float(df["close"].median())
        entry = SignalGene(indicator="SMA", params={"period": 5},
                           role=SignalRole.ENTRY_TRIGGER, field_name="sma",
                           condition={"type": "gt", "threshold": med})
        with caplog.at_level(logging.WARNING,
                             logger="core.analysis.signal_assembly"):
            assemble_signal_set(db, _dna([entry, _exit_gene()]), df)
        assert not any("[signal-assembly]" in r.getMessage()
                       for r in caplog.records)


class TestDegeneratePathUnchanged:
    """退化形态：无 plugin_sources 且无 composition 走原路径零涉及。"""

    def test_no_sources_no_composition_original_path(self, db, df):
        med = float(df["close"].median())
        entry = SignalGene(indicator="SMA", params={"period": 5},
                           role=SignalRole.ENTRY_TRIGGER, field_name="sma",
                           condition={"type": "gt", "threshold": med})
        dna = StrategyDNA(signal_genes=[entry, _exit_gene()])  # 无 sources/composition
        ss = assemble_signal_set(db, dna, df)
        entries = ss.entries.fillna(False).astype(bool)
        # 原路径无 policy 即原样（逐 bar 直译，无事件化）——掩码按
        # 基因 SMA(5) 手算
        sma5 = df["close"].rolling(5).mean()
        assert int(entries.sum()) == int((sma5 > med).fillna(False).sum())
