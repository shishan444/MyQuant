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

    def test_preset_indicator_column_not_overwritten(self):
        """df 预置同名列（rsi_14）时指标装配列跳过赋值（幂等分支）：
        分段照常产出、不覆写既有列。"""
        df = _two_regime().assign(rsi_14=0.5)
        p = _split(df, indicator="RSI", condition=_RSI_COND)
        assert p["payload"]["segments"], "预置同名列不阻断分段产出"

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


class TestMtfContextPassthrough:
    """0.2.14 mtf 形制：P-4 修复（c82ec46 三键必备超集校验）后带
    timeframe 键条件走掩码投影；无键 condition 传 ctx 仍零影响
    （ctx 只在高周期分派被消费）。"""

    def test_无timeframe键condition_mtf上下文不被消费(self):
        from pathlib import Path

        from core.analysis.condition_kernel import MtfEvalContext

        df = _two_regime()
        ctx = MtfEvalContext(Path("/nonexistent"), "TESTUSDT", "1d")
        with_ctx = _split(df, indicator="RSI", condition=_RSI_COND,
                          forward_bars=4, mtf=ctx)
        without = _split(df, indicator="RSI", condition=_RSI_COND,
                         forward_bars=4)
        assert with_ctx == without   # ctx 零影响

    def test_条件携带timeframe键_掩码投影求值与condition_stats对照一致(
            self, tmp_path):
        """P-4 修复回归（c82ec46）：带 4h 键条件不再被前置校验拒，
        经 _mask_for 掩码投影求值——与 condition_stats 同数据同条件
        逐数对照（期望由 4h 收盘挂载手工推导，不调被测投影函数）。"""
        from core.analysis.condition_kernel import MtfEvalContext
        from core.analysis.templates.condition_stats import condition_stats
        from tests.helpers.data_factory import make_ohlcv

        data_dir = tmp_path / "market"
        data_dir.mkdir(parents=True, exist_ok=True)
        base = make_ohlcv(n=600, freq="1h", start="2024-01-01", seed=23)
        high = make_ohlcv(n=150, freq="4h", start="2024-01-01", seed=23)
        base.to_parquet(data_dir / "BTCUSDT_1h.parquet")
        high.to_parquet(data_dir / "BTCUSDT_4h.parquet")
        ctx = MtfEvalContext(data_dir, "BTCUSDT", "1h",
                             eval_start=pd.Timestamp("2024-01-02", tz="UTC"))
        ranges = [{"symbol": "BTCUSDT", "timeframe": "1h",
                   "start": "2024-01-02", "end": "2024-01-25"}]
        cond = {"subject": "close", "action": "gt",
                "target": float(high["close"].median()), "timeframe": "4h"}

        rs = _split(base, segmentation="auto", indicator="RSI",
                    condition=cond, forward_bars=12, mtf=ctx)
        rs_total = sum(s["samples"]
                       for s in rs["payload"]["condition_by_segment"])
        cs = condition_stats(
            base.copy(), indicator="EMA", params={"period": 10},
            condition=cond, data_ranges=ranges, forward_bars=12,
            mtf=ctx).to_dict()

        # 独立手算：4h 收盘挂载（+4h 索引平移）backward 对齐 + 尾部
        # 丢窗（命中位置 + forward_bars 超出末端不计）
        closed = high.index + pd.Timedelta(hours=4)
        visible = pd.Series(high["close"].values, index=closed).reindex(
            base.index, method="ffill")
        hit_mask = (visible > cond["target"]).fillna(False)
        locs = [i for i, v in enumerate(hit_mask) if v]
        n_valid = sum(1 for i in locs if i + 12 < len(base))
        assert cs["payload"]["hit_count"] == int(hit_mask.sum())
        assert cs["sample_size"] == n_valid == rs_total

        # 轴分离：同阈值去键（1h 轴求值）样本数不同——掩码投影生效
        cs_base = condition_stats(
            base.copy(), indicator="EMA", params={"period": 10},
            condition={"subject": "close", "action": "gt",
                       "target": cond["target"]},
            data_ranges=ranges, forward_bars=12, mtf=ctx).to_dict()
        assert cs_base["sample_size"] != cs["sample_size"]

    def test_带timeframe键低周期_结构化拒绝(self, tmp_path):
        from pathlib import Path

        from core.analysis.condition_kernel import MtfEvalContext

        # base_tf=1d（_RANGES 请求周期路由形制），1h < 1d → 低周期拒
        ctx = MtfEvalContext(Path("/nonexistent"), "TESTUSDT", "1d")
        with pytest.raises(ResearchError) as ei:
            _split(_two_regime(), indicator="RSI",
                   condition={**_RSI_COND, "timeframe": "1h"},
                   forward_bars=4, mtf=ctx)
        assert ei.value.code == "TIMEFRAME_NOT_HIGHER"


    def test_结构化subject高周期键_掩码投影与内核直调一致(self, tmp_path):
        """P-4/P-5 回归（c82ec46+3b33c4f）：结构化 subject+timeframe 不再
        被三键校验拒、模板层字符串化后高周期轴 decode 现算——regime_split
        逐段 samples 总和 == condition_stats sample_size == 内核 _mask_for
        直调掩码统计（三方逐数一致；修复前该形态静默全 0）。"""
        from core.analysis.condition_kernel import MtfEvalContext, _mask_for
        from core.analysis.templates.condition_stats import condition_stats
        from tests.helpers.data_factory import make_ohlcv

        data_dir = tmp_path / "market"
        data_dir.mkdir(parents=True, exist_ok=True)
        base = make_ohlcv(n=600, freq="1h", start="2024-01-01", seed=23)
        high = make_ohlcv(n=150, freq="4h", start="2024-01-01", seed=23)
        base.to_parquet(data_dir / "BTCUSDT_1h.parquet")
        high.to_parquet(data_dir / "BTCUSDT_4h.parquet")
        # eval_start 距 4h 起点 84 bar ≥ EMA(20) 需求 60（收敛×3）
        ctx = MtfEvalContext(data_dir, "BTCUSDT", "1h",
                             eval_start=pd.Timestamp("2024-01-15", tz="UTC"))
        ranges = [{"symbol": "BTCUSDT", "timeframe": "1h",
                   "start": "2024-01-15", "end": "2024-01-25"}]
        cond = {"subject": {"indicator": "EMA", "params": {"period": 20}},
                "action": "gt", "target": float(high["close"].median()),
                "timeframe": "4h"}

        rs = _split(base, segmentation="auto", indicator="EMA",
                    condition=cond, forward_bars=12, mtf=ctx)
        rs_total = sum(s["samples"]
                       for s in rs["payload"]["condition_by_segment"])
        cs = condition_stats(
            base.copy(), indicator="EMA", params={"period": 20},
            condition=cond, data_ranges=ranges, forward_bars=12,
            mtf=ctx).to_dict()
        mask = _mask_for(base.copy(), cond, ctx)
        locs = [i for i, v in enumerate(mask) if v]
        direct_valid = sum(1 for i in locs if i + 12 < len(base))
        assert rs_total > 0                       # 修复前全 0（静默）
        assert rs_total == cs["sample_size"] == direct_valid


class TestGapClosureSplit3:
    """第三批：condition 形制三拒。"""

    def test_condition带而indicator非字符串_拒(self):
        df = _two_regime()
        with pytest.raises(ResearchError, match="indicator 必填"):
            _split(df, condition=_RSI_COND, indicator=123)

    def test_indicator未知_拒(self):
        df = _two_regime()
        with pytest.raises(ResearchError, match="indicator 未知"):
            _split(df, condition=_RSI_COND, indicator="GHOST")

    def test_subject不属于指标输出列_拒(self):
        df = _two_regime()
        bad = dict(_RSI_COND, subject={"indicator": "RSI",
                                       "params": {"period": 38}})  # 非默认参数列
        with pytest.raises(ResearchError, match="不属于"):
            _split(df, condition=bad, indicator="RSI")
