"""求值披露零侵入测试（change paper-signal-log 任务 2）。

SignalSet.hits 可选字段+dna_to_signal_set 的 with_hits 开启参数：
- 缺省关闭零侵入（语义字段逐字节不变、hits 缺省 None）；
- with_hits=True 结构断言（四键、项形六字段与 pt_signal.gene_hits
  同构、命中与基因条件求值独立对照一致）；
- 私有符号稳定性（evaluation.py:273 跨模块延迟 import 的运行时
  捕获锚——E1 用例：真实执行该 import 语句与被引对象）。
"""
from __future__ import annotations

from dataclasses import asdict

import pytest

pytestmark = [pytest.mark.unit]

import numpy as np
import pandas as pd

from core.strategy.executor import (
    SignalSet,
    clear_indicator_cache,
    dna_to_signal_set,
    evaluate_condition,
)
from core.strategy.dna import SignalRole
from tests.helpers.data_factory import (
    make_dna,
    make_enhanced_df,
    make_mtf_dna,
    make_signal_gene,
)


@pytest.fixture(autouse=True)
def _clean_cache():
    clear_indicator_cache()
    yield
    clear_indicator_cache()


class TestZeroIntrusion:
    """①缺省 False 五方零侵入：语义字段逐字节不变+hits 缺省 None。"""

    def test_default_hits_is_none(self):
        dna = make_dna()
        sig = dna_to_signal_set(dna, make_enhanced_df(n=80))
        assert sig.hits is None, "缺省不传参 hits 恒 None（零侵入）"

    def test_semantic_fields_identical_with_and_without_hits(self):
        """with_hits=True/False 两种调用的四布尔信号+entry_direction
        逐 bar 相等（披露通道不触碰信号语义字段）。"""
        dna = make_dna(entry_condition="lt", entry_value=40,
                       exit_condition="gt", exit_value=60)
        df = make_enhanced_df(n=120)
        a = dna_to_signal_set(dna, df)
        b = dna_to_signal_set(dna, df, with_hits=True)
        assert (a.entries == b.entries).all()
        assert (a.exits == b.exits).all()
        assert (a.adds == b.adds).all()
        assert (a.reduces == b.reduces).all()
        pd.testing.assert_series_equal(a.entry_direction, b.entry_direction)

    def test_asdict_serialization_with_new_field(self):
        """⑤新字段缺省 None 不破坏既有序列化（asdict 往返含 hits 键）。"""
        dna = make_dna()
        sig = dna_to_signal_set(dna, make_enhanced_df(n=60))
        d = asdict(sig)
        assert d["hits"] is None
        sig_h = dna_to_signal_set(make_dna(), make_enhanced_df(n=60),
                                  with_hits=True)
        d_h = asdict(sig_h)
        assert set(d_h["hits"]) == {"entry", "exit", "add", "reduce"}


def _aligned_ohlcv(n: int, timeframe: str) -> pd.DataFrame:
    """同起点对齐帧（MTF 层轴对齐前提——沿 test_mtf_degradation
    _make_ohlcv 形制；rsi_14=25 使 make_mtf_dna 缺省 entry 条件
    lt 30 恒命中、exit 条件 gt 70 恒不命中）。"""
    freq_map = {"1h": "1h", "4h": "4h", "1d": "1D"}
    dates = pd.date_range("2024-01-01", periods=n,
                          freq=freq_map[timeframe], tz="UTC")
    close = pd.Series(np.linspace(40000, 42000, n), index=dates)
    df = pd.DataFrame({
        "open": close * 0.999, "high": close * 1.005,
        "low": close * 0.995, "close": close, "volume": 1000.0,
    }, index=dates)
    df["rsi_14"] = 25.0
    return df


class TestHitsStructure:
    """②with_hits=True 结构断言：四键、项形六字段、独立对照命中一致。"""

    def test_four_keys_and_alignment(self):
        dna = make_dna()
        df = make_enhanced_df(n=80)
        sig = dna_to_signal_set(dna, df, with_hits=True)
        assert set(sig.hits) == {"entry", "exit", "add", "reduce"}
        for key, buckets in sig.hits.items():
            assert len(buckets) == len(df), f"{key} 桶数与帧长对齐"

    def test_hit_item_six_fields(self):
        """项形六字段与 pt_signal.gene_hits 同构（gene_index/indicator/
        field/condition/role/timeframe——spec 字段族明细权威字典）。"""
        dna = make_dna()
        df = make_enhanced_df(n=200)
        sig = dna_to_signal_set(dna, df, with_hits=True)
        found = None
        for key in ("entry", "exit"):
            for bucket in sig.hits[key]:
                if bucket:
                    found = (key, bucket[0])
                    break
            if found:
                break
        assert found is not None, "构造数据应产出至少一个命中"
        key, item = found
        assert set(item) == {"gene_index", "indicator", "field",
                             "condition", "role", "timeframe"}
        assert item["indicator"] == "RSI"
        assert item["condition"]["type"] in ("lt", "gt")
        assert key == ("entry" if "entry" in item["role"]
                       else "exit")
        assert item["timeframe"] == dna.execution_genes.timeframe, \
            "单周期基因的求值轴=执行周期"

    def test_hits_match_independent_condition_evaluation(self):
        """命中桶与独立计算的基因条件求值逐 bar 对照（期望独立组装
        ——测试自跑 evaluate_condition 生成期望，不复制生产装配）。"""
        entry = make_signal_gene(condition_type="lt", condition_value=45)
        exit_g = make_signal_gene(
            role=SignalRole.EXIT_TRIGGER, condition_type="gt",
            condition_value=55)
        dna = make_dna()
        dna.signal_genes = [entry, exit_g]
        df = make_enhanced_df(n=150)
        sig = dna_to_signal_set(dna, df, with_hits=True)

        rsi = df["rsi_14"]
        exp_entry = evaluate_condition(
            rsi, df["close"], entry.condition, df=df).fillna(False)
        exp_exit = evaluate_condition(
            rsi, df["close"], exit_g.condition, df=df).fillna(False)
        for i in range(len(df)):
            got_entry = {it["gene_index"]
                         for it in sig.hits["entry"][i]}
            got_exit = {it["gene_index"]
                        for it in sig.hits["exit"][i]}
            assert got_entry == ({0} if bool(exp_entry.iloc[i]) else set()), i
            assert got_exit == ({1} if bool(exp_exit.iloc[i]) else set()), i

    def test_gene_index_is_signal_genes_offset(self):
        """gene_index=dna.signal_genes 下标（DNA 快照可回溯锚）。"""
        genes = [
            make_signal_gene(condition_type="lt", condition_value=99),
            make_signal_gene(role=SignalRole.ENTRY_GUARD,
                             condition_type="lt", condition_value=99),
            make_signal_gene(role=SignalRole.EXIT_TRIGGER,
                             condition_type="gt", condition_value=1),
        ]
        dna = make_dna()
        dna.signal_genes = genes
        df = make_enhanced_df(n=80)
        sig = dna_to_signal_set(dna, df, with_hits=True)
        entry_items = {it["gene_index"]
                       for bucket in sig.hits["entry"] for it in bucket}
        exit_items = {it["gene_index"]
                      for bucket in sig.hits["exit"] for it in bucket}
        # 条件 99 几乎恒真/1 几乎恒假——两 entry 族基因（0/1）与
        # exit 基因（2）各自归桶
        assert entry_items == {0, 1}
        assert exit_items == {2}

    def test_inhibitor_and_direction_not_in_hits(self):
        """ADD_INHIBIT/DIRECTION 基因不进披露（抑制无对应信号键；
        方向非四类族）。"""
        inhibitor = make_signal_gene(
            role=SignalRole.ADD_INHIBIT, condition_type="lt",
            condition_value=99)
        direction = make_signal_gene(
            role=SignalRole.DIRECTION, condition_type="lt",
            condition_value=99)
        entry = make_signal_gene(condition_type="lt",
                                 condition_value=99)
        dna = make_dna()
        dna.signal_genes = [entry, inhibitor, direction]
        df = make_enhanced_df(n=80)
        sig = dna_to_signal_set(dna, df, with_hits=True)
        roles = {it["role"]
                 for key in sig.hits for bucket in sig.hits[key]
                 for it in bucket}
        assert "add_inhibit" not in roles
        assert "direction" not in roles
        assert roles == {"entry_trigger"}

    def test_mtf_legacy_hits_with_layer_projection(self):
        """MTF legacy 组合（mtf_mode=None）：层内基因经同款投影并入
        主轴 hits——桶数与主帧对齐、timeframe=层周期（求值轴）。"""
        dna = make_mtf_dna(timeframes=("1d", "4h"), mtf_mode=None)
        df = _aligned_ohlcv(200, "4h")   # 主周期=执行层
        hi = _aligned_ohlcv(50, "1d")    # 辅周期（同起点对齐）
        frames = {"4h": df, "1d": hi}
        sig = dna_to_signal_set(dna, df, frames, with_hits=True)
        assert sig.hits is not None
        for key, buckets in sig.hits.items():
            assert len(buckets) == len(df)
        tfs = {it["timeframe"]
               for key in sig.hits for bucket in sig.hits[key]
               for it in bucket}
        assert tfs == {"1d", "4h"}, "层基因按层周期披露（求值轴语义）"

    def test_mtf_engine_path_hits_none_disclosed(self):
        """MTF 新引擎路径（mtf_mode 已设置）hits=None——合成门控打破
        逐基因归因，诚实披露边界（dna_to_signal_set docstring 在案）。

        层形制取执行层 1d+辅层 4h（慢执行轴）：mtf_engine 动量回退
        分支在「快执行轴×1d 辅层」形制存在层轴对齐既有边界（本
        change 范围外，见 synthesize_cross_layer 2b），用不触发形态
        锁本用例的断言对象（hits 披露面非引擎内部）。"""
        dna = make_mtf_dna(timeframes=("4h", "1d"), mtf_mode="adaptive")
        df = _aligned_ohlcv(50, "1d")     # 主周期=执行层 1d
        aux = _aligned_ohlcv(300, "4h")   # 辅周期 4h
        sig = dna_to_signal_set(dna, df, {"1d": df, "4h": aux},
                                with_hits=True)
        assert sig.hits is None


class TestPrivateSymbolStability:
    """⑥私有符号稳定性：evaluation.py:273 跨模块延迟 import 运行时
    捕获（E1 锚——静态扫描不覆盖，真实执行 import 语句与被引对象）。"""

    def test_evaluation_delayed_import_capturable(self):
        """真实执行 evaluation 模块内的延迟 import 语句（逐符号可用）。"""
        ns: dict = {}
        exec(
            "from core.strategy.executor import (dna_to_signal_set,\n"
            "    _empty_signal_set, clear_indicator_cache)",
            ns)
        assert callable(ns["_empty_signal_set"])
        assert callable(ns["clear_indicator_cache"])
        # 被引对象行为仍在（_empty_signal_set 返回全 False 信号集）
        df = make_enhanced_df(n=30)
        empty = ns["_empty_signal_set"](df)
        assert isinstance(empty, SignalSet)
        assert not bool(empty.entries.any())
        assert not bool(empty.exits.any())
