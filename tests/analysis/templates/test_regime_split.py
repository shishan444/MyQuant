"""行情分段模板测试（add-regime-split 任务 3.3，三测试类对标 0.2.6）。

覆盖：auto/explicit 切段、参数拒绝面、确定性、gates 披露、判读参数
不可调（不作为输入存在）；逐段统计与 condition_stats 独立复核一致、
短段 insufficient 不判罪；两段构造正确性。
"""
from __future__ import annotations

import json

import pytest

pytestmark = [pytest.mark.unit]

import pandas as pd

from core.analysis.hypothesis_service import ResearchError
from core.analysis.templates.condition_stats import condition_stats
from core.analysis.templates.regime_split import regime_split
from tests.helpers.regime_factory import (
    make_random_walk_ohlcv as _random_walk,
    make_two_regime_df as _two_regime,
)

_RANGES = [{"symbol": "TESTUSDT", "timeframe": "1d",
            "start": "2024-01-01", "end": "2025-09-01"}]
_RSI_COND = {"subject": {"indicator": "RSI", "params": {"period": 14}}, "action": "lt", "target": 40.0}


def _split(df, **kw):
    return regime_split(
        df, data_ranges=_RANGES, timeframe="1d", **kw).to_dict()


class TestRegimeSplitBasics:
    def test_auto_segments_with_gates(self):
        p = _split(_two_regime())["payload"]
        assert p["segmentation"] == "auto"
        assert len(p["segments"]) >= 2
        assert {s["type"] for s in p["segments"]} <= {"趋势", "震荡", "高波动"}
        assert p["gates"]["adx_trend"] == 25.0
        assert p["gates"]["min_segment_samples"] == 30

    def test_explicit_segments_type_defaulted(self):
        df = _two_regime()
        p = _split(df, segmentation=[
            {"start": str(df.index[50].date()), "end": str(df.index[250].date())},
            {"start": str(df.index[300].date()), "end": str(df.index[500].date()),
             "type": "趋势"}])["payload"]
        assert p["segmentation"] == "explicit"
        assert len(p["segments"]) == 2
        assert p["segments"][1]["type"] == "趋势"
        assert p["segments"][0]["type"] in {"趋势", "震荡", "高波动"}, "缺省补标"

    def test_param_rejection_structured(self):
        df = _two_regime()
        cases = [
            {"segmentation": "sometimes"},
            {"segmentation": []},
            {"segmentation": [{"start": "2024-01-01"}]},   # 缺 end
            {"segmentation": 42},
            {"forward_bars": 0},
            {"condition": {"subject": {"indicator": "RSI", "params": {"period": 14}}}},           # 三键缺失
        ]
        for bad in cases:
            with pytest.raises(ResearchError) as ei:
                _split(df, **bad)
            assert ei.value.code == "RESEARCH_PARAM_INVALID", bad
            assert ei.value.detail.get("legal_range"), bad

    def test_explicit_range_outside_data_rejected(self):
        df = _two_regime()
        with pytest.raises(ResearchError):
            _split(df, segmentation=[{"start": "2030-01-01", "end": "2030-02-01"}])

    def test_deterministic_byte_identical(self):
        df = _two_regime()
        a = _split(df)
        b = _split(df)
        assert json.dumps(a, default=str) == json.dumps(b, default=str)

    def test_no_tunable_gates_in_signature(self):
        """判读参数不可调：不作为可传输入存在（内建常量 gates 披露）。"""
        import inspect
        params = set(inspect.signature(regime_split).parameters)
        for banned in ("adx_trend", "vol_pct", "min_segment_bars", "max_segments"):
            assert banned not in params


class TestConditionBySegment:
    def test_by_segment_matches_condition_stats(self):
        """独立对照：逐段统计与 condition_stats 同段同条件同持有期
        逐字段一致（mean/baseline 口径互钉）。对照切片带前向缓冲
        （段末 fb 根）——regime_split 的前向收益跨段界计算（收益发生
        在未来、段只是触发分组），condition_stats 无缓冲会截断段末。"""
        df = _two_regime()
        r = _split(df, indicator="RSI", condition=_RSI_COND, forward_bars=12)
        segs = r["payload"]["segments"]
        by = r["payload"]["condition_by_segment"]
        assert by is not None and len(by) == len(segs)
        for row in by:
            assert row["state"] in {"scored", "insufficient_sample"}
            if row["state"] == "scored":
                seg = segs[row["segment"]]
                seg_start = pd.Timestamp(seg["start"])
                seg_end = pd.Timestamp(seg["end"])
                pos = df.index.get_indexer([seg_end])[0]
                buf_end = df.index[min(pos + 1 + 12, len(df) - 1)]   # 尾部 fb 缓冲
                head = df.index.get_indexer([seg_start])[0]
                buf_start = df.index[max(head - 42, 0)]              # 头部 warmup 缓冲
                cs = condition_stats(
                    df[(df.index >= buf_start) & (df.index <= buf_end)],
                    indicator="RSI", params={"period": 14},
                    condition=_RSI_COND, forward_bars=12,
                    data_ranges=_RANGES, request_start=seg_start).to_dict()
                cp = cs["payload"]
                assert row["forward_return_mean"] == pytest.approx(
                    cp["forward_return_12b"]["mean"], nan_ok=True)
                assert row["samples"] == cp["forward_return_12b"]["count"], \
                    "命中数口径（全 df 指标 vs U3 warmup 前缀只用不算）"

    def test_short_segment_insufficient_not_guilty(self):
        """短段样本不足：标 insufficient_sample、不产统计量（资格门
        语义——证据不足不等于无效）。"""
        df = _two_regime()
        # 极苛刻条件：全数据命中极少 → 各段均不足
        r = _split(df, indicator="RSI",
                   condition={"subject": {"indicator": "RSI", "params": {"period": 14}}, "action": "lt",
                              "target": 1.0})
        by = r["payload"]["condition_by_segment"]
        assert all(row["state"] == "insufficient_sample" for row in by)
        assert all(row["forward_return_mean"] is None for row in by)


class TestDiscipline:
    def test_two_regime_typed_correctly(self):
        p = _split(_two_regime())["payload"]
        assert p["segments"][0]["type"] == "趋势"
        assert p["segments"][-1]["type"] == "震荡"

    def test_gates_disclosed_values(self):
        p = _split(_random_walk(seed=1, n=200))["payload"]
        g = p["gates"]
        assert g["vol_pct"] == 0.80 and g["percentile_window"] == 90
        assert "method" in p and p["method"]["regime"] == "adx_state_x_vol_override"
