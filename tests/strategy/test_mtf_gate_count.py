"""MTF 决策门控拦截计数契约单测（tester 覆盖核对补齐——T1-I5）。

覆盖缺口来源：tasks 3.2 完成标注声称「构造三态样本单测（entries
严格档+adds 放宽档+两面同 bar 去重）」但全库不存在；既有集成断言仅
isinstance(int)——0 与 N 不区分，计数正确性无锚。本文件按 design
§2.3 计数公式（契约单源）直接推导期望值：

    entries 面被拦 = entries ∧ ¬(direction_pass ∧ confluence_pass)
    adds 面被拦   = adds ∧ ¬(confluence_score ≥ threshold×0.8)
                     （放宽共振门，无方向门）
    mtf_gated_bars = |entries_gated ∪ adds_gated|（bar 去重——同 bar
                     两面均被拦计 1；语义是「N 根 bar」非事件数）

三态：None（mtf_mode None=无门控面）/0（门控全过）/N（实际拦截）。
期望值全部人工核算，不从实现输出反推。
"""
from __future__ import annotations

import pandas as pd
import pytest

from core.strategy.dna import (
    ExecutionGenes, LogicGenes, RiskGenes, SignalGene, SignalRole,
    StrategyDNA, TimeframeLayer,
)
from core.strategy.executor import SignalSet
from core.strategy.mtf_engine import MTFSynthesis, apply_decision_gate

IDX = pd.DatetimeIndex(
    pd.date_range("2024-01-01", periods=4, freq="4h", tz="UTC"))


def _signal_set(entries, adds) -> SignalSet:
    return SignalSet(
        entries=pd.Series(entries, index=IDX, dtype=bool),
        exits=pd.Series([False] * 4, index=IDX, dtype=bool),
        adds=pd.Series(adds, index=IDX, dtype=bool),
        reduces=pd.Series([False] * 4, index=IDX, dtype=bool),
    )


def _synthesis(direction, confluence) -> MTFSynthesis:
    ones = pd.Series(1.0, index=IDX)
    return MTFSynthesis(
        direction_score=pd.Series(direction, index=IDX),
        confluence_score=pd.Series(confluence, index=IDX),
        momentum_score=ones, strength_multiplier=ones)


def _dna(mtf_mode, threshold=0.5, direction="long") -> StrategyDNA:
    return StrategyDNA(
        layers=[TimeframeLayer(
            timeframe="1d", role="trend",
            signal_genes=[SignalGene("RSI", {"period": 14},
                                     SignalRole.ENTRY_TRIGGER, "rsi_14",
                                     {"type": "lt", "threshold": 45})],
            logic_genes=LogicGenes())],
        execution_genes=ExecutionGenes(timeframe="4h"),
        risk_genes=RiskGenes(direction=direction),
        mtf_mode=mtf_mode, confluence_threshold=threshold,
    )


class TestEntriesStrictDoubleGate:
    """entries 面严格双门：direction∧confluence 双过才放行。"""

    def test_strict_gate_counts_each_side(self):
        """人工核算：threshold=0.5、long。
        bar0 dir=+1 conf=0.6 双过→不拦；bar1 dir=+1 conf=0.3 共振
        拦；bar2 dir=-1 conf=0.6 方向拦；bar3 dir=+1 conf=0.45 共振拦。
        entries 触发=[T,T,F,T]→entries_gated={1,3}（bar2 无原始触发
        不计——「原始触发∧¬门」口径）。"""
        ss = _signal_set([True, True, False, True], [False] * 4)
        syn = _synthesis([1.0, 1.0, -1.0, 1.0], [0.6, 0.3, 0.6, 0.45])
        out = apply_decision_gate(ss, syn, _dna("direction+confluence"))
        assert out.mtf_gated_bars == 2
        # 放行面同口径：仅 bar0 触发且双过
        assert out.entries.tolist() == [True, False, False, False]

    def test_zero_when_all_pass(self):
        """门控全过=0（int 非 None——与无门控面 None 严格区分）。"""
        ss = _signal_set([True, False, False, False], [True, False, False, False])
        syn = _synthesis([1.0] * 4, [0.9] * 4)
        out = apply_decision_gate(ss, syn, _dna("direction+confluence"))
        assert out.mtf_gated_bars == 0
        assert isinstance(out.mtf_gated_bars, int)

    def test_mode_none_is_none_not_zero(self):
        """mtf_mode None=无门控面→None（三态第一态）。"""
        ss = _signal_set([True] * 4, [True] * 4)
        syn = _synthesis([-1.0] * 4, [0.0] * 4)
        out = apply_decision_gate(ss, syn, _dna(None))
        assert out.mtf_gated_bars is None
        # 信号原样透传（向后兼容）
        assert out.entries.tolist() == [True] * 4


class TestAddsRelaxedGate:
    """adds 面放宽共振门（threshold×0.8），无方向门。"""

    def test_relaxed_threshold_boundary(self):
        """恰=threshold×0.8 的 add 不拦（>=含等号）。
        人工核算：threshold=0.5→放宽线 0.4；bar0 conf=0.4 恰等放宽线
        →add 放行；同 bar entry 面仍拦（0.4<0.5 严格门）→计数=1。"""
        ss = _signal_set([True, False, False, False],
                         [True, False, False, False])
        syn = _synthesis([1.0] * 4, [0.4, 0.9, 0.9, 0.9])
        out = apply_decision_gate(ss, syn, _dna("direction+confluence"))
        assert out.mtf_gated_bars == 1
        assert out.adds.tolist() == [True, False, False, False], (
            "conf 恰=threshold×0.8 的 add 须放行（>= 含等号）")

    def test_add_gated_below_relaxed_line(self):
        """conf<放宽线的 add 被拦。bar0 conf=0.3<0.4：entry 拦+add 拦
        ——同 bar 两面被拦计 1（bar 去重：贡献 1 非 2）。"""
        ss = _signal_set([True, False, False, False],
                         [True, False, False, False])
        syn = _synthesis([1.0] * 4, [0.3, 0.9, 0.9, 0.9])
        out = apply_decision_gate(ss, syn, _dna("direction+confluence"))
        assert out.mtf_gated_bars == 1, "同 bar 两面均被拦计 1（bar 去重）"
        assert out.adds.tolist() == [False] * 4

    def test_relaxed_band_entry_gated_add_passes(self):
        """放宽带 conf∈(0.4,0.5)：entry 拦+add 放行——「放宽」语义
        的正反面分立（entry 严格门与 add 放宽门不同档）。"""
        ss = _signal_set([True, False, False, False],
                         [True, False, False, False])
        syn = _synthesis([1.0] * 4, [0.45, 0.9, 0.9, 0.9])
        out = apply_decision_gate(ss, syn, _dna("direction+confluence"))
        assert out.mtf_gated_bars == 1
        assert out.entries.tolist() == [False] * 4, "0.45<0.5 entry 须拦"
        assert out.adds.tolist() == [True, False, False, False], (
            "0.45≥0.4 add 须放行（放宽带）")


class TestBarDedupSemantics:
    """同 bar 两面被拦按 1 根计——「N 根 bar」非事件数。"""

    def test_same_bar_two_faces_counted_once(self):
        """bar0：dir=-1（方向拦 entry）+conf=0.3（严格与放宽门均拦）；
        bar1：同态。entries/adds 全触发→每 bar 两面均被拦，计数=2
        非 4。"""
        ss = _signal_set([True, True, False, False],
                         [True, True, False, False])
        syn = _synthesis([-1.0, -1.0, 1.0, 1.0], [0.3, 0.3, 0.9, 0.9])
        out = apply_decision_gate(ss, syn, _dna("direction+confluence"))
        assert out.mtf_gated_bars == 2

    def test_distinct_bars_sum(self):
        """不同 bar 各自贡献：bar0 entry 拦（dir=-1 conf=0.9）、bar1
        add 拦（dir=+1 conf=0.2<0.4 放宽线）→计数=2。"""
        ss = _signal_set([True, False, False, False],
                         [False, True, False, False])
        syn = _synthesis([-1.0, 1.0, 1.0, 1.0], [0.9, 0.2, 0.9, 0.9])
        out = apply_decision_gate(ss, syn, _dna("direction+confluence"))
        assert out.mtf_gated_bars == 2


class TestExitsReducesNotGated:
    """exits/reduces 不过滤——离场与减仓属风险管理，不因门控阻塞。"""

    def test_exit_reduce_pass_through(self):
        ss = SignalSet(
            entries=pd.Series([True] * 4, index=IDX, dtype=bool),
            exits=pd.Series([True] * 4, index=IDX, dtype=bool),
            adds=pd.Series([True] * 4, index=IDX, dtype=bool),
            reduces=pd.Series([True] * 4, index=IDX, dtype=bool),
        )
        syn = _synthesis([-1.0] * 4, [0.0] * 4)  # 全拦面
        out = apply_decision_gate(ss, syn, _dna("direction+confluence"))
        assert out.exits.tolist() == [True] * 4
        assert out.reduces.tolist() == [True] * 4
        # 全部 entries/adds 触发 bar 均被拦→计数=4 根 bar
        assert out.mtf_gated_bars == 4
