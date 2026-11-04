"""trace_resolver 纯函数单测（add-vr-signal-trace 任务 4.1——T1-U1/
T1-U2/T1-U3 锚族）。

覆盖面：事件码分配表 10-34 每码至少一测；risk_budget_fallback 两态
（Q1 对照实测裁决）；capped∈(0,1) 比值域（Q2 裁决）；seq 分配（引擎
行先求值层后、层内 event_code 升序、0 起）；幂等键族去重；未映射码
raise ValueError fail-fast；映射表完整性结构断言（T1-U3——CI 层首拦）。
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from core.backtest.trace_resolver import (
    EVENT_REASON_MAP, engine_written_event_codes, resolve_signal_trace)

IDX = pd.DatetimeIndex(
    pd.date_range("2024-01-01", periods=4, freq="4h", tz="UTC"))


def _buf(codes: dict) -> np.ndarray:
    """{pos: code} → 4×1 矩阵。"""
    buf = np.zeros((len(IDX), 1), dtype=np.float64)
    for pos, code in codes.items():
        buf[pos, 0] = float(code)
    return buf


def _trades(rows: list) -> pd.DataFrame:
    """[{entry, exit, size, ep, xp}] → records_readable 形制。"""
    return pd.DataFrame({
        "Entry Timestamp": [pd.Timestamp(r["entry"], tz="UTC") for r in rows],
        "Exit Timestamp": [
            (pd.Timestamp(r["exit"], tz="UTC")
             if r.get("exit") else pd.NaT) for r in rows],
        "Size": [r["size"] for r in rows],
        "Avg Entry Price": [r["ep"] for r in rows],
        "Avg Exit Price": [r["xp"] for r in rows],
    })


RISK_PCT = {
    "position_mode": "fixed", "size_pct": 0.3, "sl_mode": "pct",
    "sl_stop": 0.05, "risk_fraction": None,
}


class TestEventCodeCoverage:
    """分配表 10-34 每码至少一测（T1-U1）。"""

    @pytest.mark.parametrize("code", sorted(EVENT_REASON_MAP.keys()))
    def test_each_code_produces_row(self, code):
        rows = resolve_signal_trace(
            _buf({1: code}), IDX, None, None, exec_ref="t")
        assert len(rows) == 1
        r = rows[0]
        assert r["event_code"] == code and r["exec_ref"] == "t"
        assert r["seq"] == 0
        if code in (30, 31, 33, 34):
            assert r["outcome"] in ("rejected", "swallowed")
            assert r["reason"] is not None
            assert r["size"] is None and r["price"] is None

    def test_code10_filled_with_trade_match(self):
        rows = resolve_signal_trace(
            _buf({1: 10}), IDX, None,
            _trades([{"entry": "2024-01-01 04:00", "exit": None,
                      "size": 3.0, "ep": 100.0, "xp": 0.0}]),
            exec_ref="t")
        assert rows[0]["outcome"] == "filled"
        assert rows[0]["size"] == 3.0 and rows[0]["price"] == 100.0

    def test_code10_no_trade_rejected_reason_null(self):
        """发单码无成交→rejected+reason NULL（vbt 拒单原因黑箱如实缺失）。"""
        rows = resolve_signal_trace(_buf({1: 10}), IDX, None, None)
        assert rows[0]["outcome"] == "rejected"
        assert rows[0]["reason"] is None

    def test_exit_code_matches_exit_timestamp(self):
        rows = resolve_signal_trace(
            _buf({2: 20}), IDX, None,
            _trades([{"entry": "2024-01-01 00:00", "exit": "2024-01-01 08:00",
                      "size": 2.0, "ep": 100.0, "xp": 95.0}]))
        assert rows[0]["outcome"] == "filled"
        assert rows[0]["price"] == 95.0  # 出场行取 Avg Exit Price


class TestRiskBudgetFallbackTwoStates:
    """Q1 裁决（方案 A·对照实测）两态断言落死。"""

    def _params(self):
        return {"position_mode": "risk_budget", "sl_mode": "pct",
                "sl_stop": 0.05, "risk_fraction": 0.1, "size_pct": None}

    def test_fallback_filled_when_trade_exists(self):
        rows = resolve_signal_trace(
            _buf({1: 32}), IDX, None,
            _trades([{"entry": "2024-01-01 04:00", "exit": None,
                      "size": 1.5, "ep": 100.0, "xp": 0.0}]),
            risk_params=self._params())
        r = rows[0]
        assert r["outcome"] == "filled"
        assert r["reason"] == "risk_budget_fallback"
        assert r["size"] == 1.5 and r["price"] == 100.0

    def test_fallback_rejected_when_no_trade(self):
        rows = resolve_signal_trace(
            _buf({1: 32}), IDX, None, None, risk_params=self._params())
        r = rows[0]
        assert r["outcome"] == "rejected"
        assert r["reason"] == "risk_budget_fallback"


class TestCapped:
    """Q2 裁决：capped=实际÷计划比值∈(0,1)。"""

    def test_capped_ratio_in_open_interval(self):
        # risk_budget：plan=value×rf/(price×sl_stop)=100000×0.1/5=2000
        # 实际 1500 → 0.75
        rows = resolve_signal_trace(
            _buf({1: 10}), IDX, None,
            _trades([{"entry": "2024-01-01 04:00", "exit": None,
                      "size": 1500.0, "ep": 100.0, "xp": 0.0}]),
            value_curve=pd.Series([100000.0] * 4, index=IDX),
            risk_params={"position_mode": "risk_budget", "sl_mode": "pct",
                         "sl_stop": 0.05, "risk_fraction": 0.1})
        assert rows[0]["capped"] == pytest.approx(0.75)

    def test_uncapped_full_fill_is_none(self):
        # 实际 2000 == 计划 2000 → 未截断 None
        rows = resolve_signal_trace(
            _buf({1: 10}), IDX, None,
            _trades([{"entry": "2024-01-01 04:00", "exit": None,
                      "size": 2000.0, "ep": 100.0, "xp": 0.0}]),
            value_curve=pd.Series([100000.0] * 4, index=IDX),
            risk_params={"position_mode": "risk_budget", "sl_mode": "pct",
                         "sl_stop": 0.05, "risk_fraction": 0.1})
        assert rows[0]["capped"] is None

    def test_fixed_mode_plan(self):
        # fixed：plan=value×size_pct/price=100000×0.3/100=300；实际 240→0.8
        rows = resolve_signal_trace(
            _buf({1: 10}), IDX, None,
            _trades([{"entry": "2024-01-01 04:00", "exit": None,
                      "size": 240.0, "ep": 100.0, "xp": 0.0}]),
            value_curve=pd.Series([100000.0] * 4, index=IDX),
            risk_params=RISK_PCT)
        assert rows[0]["capped"] == pytest.approx(0.8)

    def test_value_now_uses_previous_bar(self):
        # value_now=订单 bar 前一 bar 期末值（对齐假设）——pos=1 用 [0]
        rows = resolve_signal_trace(
            _buf({1: 10}), IDX, None,
            _trades([{"entry": "2024-01-01 04:00", "exit": None,
                      "size": 150.0, "ep": 100.0, "xp": 0.0}]),
            value_curve=pd.Series([50000.0, 99000.0, 99000.0, 99000.0],
                                  index=IDX),
            risk_params=RISK_PCT)  # plan=50000×0.3/100=150 → 150/150=None
        assert rows[0]["capped"] is None


class TestSeqAssignment:
    """seq 分配：引擎行先求值层后、层内 event_code 升序、同 bar 0 起。"""

    def test_exit_filled_then_entry_swallowed_seq_01(self):
        """同 bar 出场保留两行：exit filled（引擎）seq=0+entry swallowed
        （求值层）seq=1——设计卡 §2.5 分配规则。"""
        rows = resolve_signal_trace(
            _buf({1: 13}), IDX,
            [{"bar_time": str(IDX[1]), "reason": "entry_exit_conflict"}],
            _trades([{"entry": "2024-01-01 00:00", "exit": "2024-01-01 04:00",
                      "size": 2.0, "ep": 100.0, "xp": 110.0}]))
        assert len(rows) == 2
        by_code = {r["event_code"]: r for r in rows}
        assert by_code[13]["seq"] == 0       # 引擎行先
        assert by_code[33]["seq"] == 1       # 求值层行后
        assert by_code[33]["outcome"] == "swallowed"

    def test_layer_internal_event_code_ascending(self):
        # 同 bar 引擎双事件不可达（每 bar 单格）——swallowed 双 reason
        # 可达：33 与 34 同 bar，层内按 event_code 升序
        rows = resolve_signal_trace(
            None, IDX,
            [{"bar_time": str(IDX[2]), "reason": "add_inhibited"},
             {"bar_time": str(IDX[2]), "reason": "entry_exit_conflict"}],
            None)
        assert [r["event_code"] for r in rows] == [33, 34]
        assert [r["seq"] for r in rows] == [0, 1]

    def test_bars_sorted_output(self):
        rows = resolve_signal_trace(
            _buf({2: 10, 0: 13}), IDX, None, None)
        assert [r["bar_time"] for r in rows] == [
            str(IDX[0]), str(IDX[2])]


class TestFailFast:
    """未映射码 raise（禁止静默丢弃禁止直显）+未知吞没原因。"""

    def test_unmapped_code_raises(self):
        with pytest.raises(ValueError, match="未映射事件码 99"):
            resolve_signal_trace(_buf({1: 99}), IDX, None, None,
                                 exec_ref="seg_a")

    def test_unmapped_swallowed_reason_raises(self):
        with pytest.raises(ValueError, match="未映射吞没原因"):
            resolve_signal_trace(
                None, IDX,
                [{"bar_time": str(IDX[0]), "reason": "mystery"}], None)


class TestIdempotentDedup:
    def test_identical_rows_deduped(self):
        # 同 bar 同层同码重复 swallowed 行 → 单行（幂等键族防御）
        rows = resolve_signal_trace(
            None, IDX,
            [{"bar_time": str(IDX[0]), "reason": "add_inhibited"},
             {"bar_time": str(IDX[0]), "reason": "add_inhibited"}],
            None)
        assert len(rows) == 1


class TestMapIntegrity:
    """T1-U3 映射表完整性结构断言——CI 层首拦（引擎新增码未同步即红）。"""

    def test_map_covers_full_allocation_domain(self):
        assert set(EVENT_REASON_MAP.keys()) == (
            set(range(10, 14)) | set(range(20, 26)) | set(range(30, 35)))

    def test_engine_codes_subset_of_map(self):
        assert engine_written_event_codes() <= frozenset(
            EVENT_REASON_MAP.keys())

    def test_reason_domain_two_families(self):
        decision_family = {"exposure_cap", "insufficient_funds",
                           "risk_budget_fallback"}
        eval_family = {"entry_exit_conflict", "add_inhibited"}
        reasons = {v["reason"] for v in EVENT_REASON_MAP.values()
                   if v["reason"] is not None}
        assert reasons == decision_family | eval_family

    def test_empty_inputs_zero_rows(self):
        """零信号记录：管线通而事实空（空列表——三布尔仍打 1 的依据）。"""
        rows = resolve_signal_trace(
            np.zeros((4, 1)), IDX, None, None)
        assert rows == []
