"""mtf_alignment 独立复核用例（tester·multi-timeframe-conditions）。

与实施锚（test_mtf_alignment.py）数据与样例错开的独立对照——期望全部
由契约手工推导（design B1 + spec「asof_completed 前视锚」「掩码投影
语义与穿越状态窗」），不调被测实现生成期望，不复用锚测试数据。

推导基线（数据索引=bar 开盘时刻）：高周期 bar T 的值/掩码挂载到收盘
时刻 T+tf，此后对基准轴可见；首段（首根收盘前）不可见。
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

pytestmark = [pytest.mark.unit]

from core.data.mtf_alignment import (
    assert_higher_tf,
    high_tf_warmup_span,
    infer_tf_delta,
    project_completed,
    project_completed_bool,
    pulse_completed,
)
from core.data.timeframes import TF_SECONDS

_T0 = pd.Timestamp("2024-03-05 00:00:00", tz="UTC")


def _grid(start: pd.Timestamp, n: int, tf: str) -> pd.DatetimeIndex:
    step = pd.Timedelta(seconds=TF_SECONDS[tf])
    return pd.DatetimeIndex([start + i * step for i in range(n)])


def _series(values, index):
    return pd.Series(values, index=index, dtype="float64")


# ---------------------------------------------------------------- 值投影

class TestProjectCompletedIndependent:
    def test_值投影_4h对1h_逐窗可见值手工推导(self):
        """4h [5, 15, 25]：v_k 在收盘 T0+4(k+1)h 起可见，被 v_{k+1} 取代。

        基准 1h 12 根（T0..T0+11h）：
        i=0..3 NaN（首根收盘前）；i=4..7 见 5；i=8..11 见 15；
        v_2=25 收盘 T0+12h 超出基准末端不可见。
        """
        high = _series([5.0, 15.0, 25.0], _grid(_T0, 3, "4h"))
        out = project_completed(high, _grid(_T0, 12, "1h"), "4h")
        assert out.iloc[:4].isna().all()
        assert out.iloc[4:8].tolist() == [5.0] * 4
        assert out.iloc[8:12].tolist() == [15.0] * 4

    def test_值投影_1w对1d_跨周换算(self):
        """1w [3, 9]：v_0 收盘 T0+7d 起可见；v_1 收盘 T0+14d 超出。"""
        high = _series([3.0, 9.0], _grid(_T0, 2, "1w"))
        out = project_completed(high, _grid(_T0, 14, "1d"), "1w")
        assert out.iloc[:7].isna().all()
        assert out.iloc[7:].tolist() == [3.0] * 7

    def test_值投影_收盘时刻恰等基准开盘_当根即见(self):
        """闭边界：close == T0+4h 恰为第 5 根基准 bar 开盘——该 bar 见 v_0。"""
        high = _series([5.0], _grid(_T0, 1, "4h"))
        base = _grid(_T0, 5, "1h")
        out = project_completed(high, base, "4h")
        assert out.iloc[:4].isna().all()
        assert out.iloc[4] == pytest.approx(5.0)

    def test_值投影_高周期轴NaN段_投影传播NaN不填充(self):
        """「指标自然 NaN 不做值级填充」——NaN 行经 ffill 按行对齐传播。"""
        high = _series([2.0, float("nan"), 6.0, 8.0], _grid(_T0, 4, "4h"))
        out = project_completed(high, _grid(_T0, 16, "1h"), "4h")
        assert out.iloc[:4].isna().all()        # 首根收盘前
        assert out.iloc[4:8].tolist() == [2.0] * 4
        assert out.iloc[8:12].isna().all()      # v_1=NaN 传播
        assert out.iloc[12:16].tolist() == [6.0] * 4

    def test_值投影_缺口高周期_挂载按tf推导不见后续bar(self):
        """缺口 [T0→10, T0+8h→30, T0+16h→40]：T0+8h 开盘的 bar 收盘
        T0+12h，其值 30 自 T0+12h 起才可见——缺口不得提前暴露 30。"""
        idx = pd.DatetimeIndex(
            [_T0, _T0 + pd.Timedelta(hours=8), _T0 + pd.Timedelta(hours=16)])
        high = pd.Series([10.0, 30.0, 40.0], index=idx)
        out = project_completed(high, _grid(_T0, 20, "1h"), "4h")
        assert out.iloc[:4].isna().all()
        assert out.iloc[4:12].tolist() == [10.0] * 8   # 形成中只见 10
        assert out.iloc[12:20].tolist() == [30.0] * 8  # 40 收盘 T0+20h 外


# ---------------------------------------------------------------- 布尔投影

class TestProjectCompletedBoolIndependent:
    def test_布尔投影_首段False_其后逐窗随掩码(self):
        mask = pd.Series([True, False, True], index=_grid(_T0, 3, "4h"))
        out = project_completed_bool(mask, _grid(_T0, 12, "1h"), "4h")
        assert out.iloc[:4].tolist() == [False] * 4
        assert out.iloc[4:8].tolist() == [True] * 4
        assert out.iloc[8:12].tolist() == [False] * 4

    def test_布尔投影_NaN掩码按False_且dtype为bool(self):
        """掩码含 NaN（不可判）→ False（不可见=条件不成立）。"""
        mask = pd.Series([True, None], index=_grid(_T0, 2, "4h"))
        out = project_completed_bool(mask, _grid(_T0, 8, "1h"), "4h")
        assert out.dtype == bool
        assert out.iloc[:4].tolist() == [False] * 4
        assert out.iloc[4:].tolist() == [True] * 4

    def test_布尔投影_asof前视差分_形成中bar掩码翻转输出不变(self):
        """spec「asof_completed 前视锚」差分断言：末根（形成期，收盘
        T0+12h 晚于投影域末端 T0+11h）掩码 True↔False 翻转，投影不变。"""
        idx = _grid(_T0, 3, "4h")
        a = pd.Series([False, True, False], index=idx)
        b = pd.Series([False, True, True], index=idx)
        base = _grid(_T0, 12, "1h")   # 末端 T0+11h < T0+12h
        pd.testing.assert_series_equal(
            project_completed_bool(a, base, "4h"),
            project_completed_bool(b, base, "4h"))


# ---------------------------------------------------------------- 脉冲投影

class TestPulseCompletedIndependent:
    def test_脉冲_收盘时刻恰等基准开盘_落位该bar(self):
        """4h bar1（开盘 04:00 收盘 08:00）→ 08:00 基准 bar 单点 True。"""
        signal = pd.Series([False, True, False], index=_grid(_T0, 3, "4h"))
        out = pulse_completed(signal, _grid(_T0, 16, "1h"), "4h")
        assert out.sum() == 1
        assert bool(out.loc[_T0 + pd.Timedelta(hours=8)]) is True

    def test_脉冲_收盘落基准bar内部_落位所在bar(self):
        """契约「脉冲落在含 T+tf 的基准 bar」：close=04:30 落 (04:00,
        05:00) 窗内 → 04:00 那根基准 bar True（错位开盘的边界情形）。"""
        sig_idx = pd.DatetimeIndex([_T0 + pd.Timedelta(minutes=30)])
        signal = pd.Series([True], index=sig_idx)   # 收盘 04:30
        out = pulse_completed(signal, _grid(_T0, 6, "1h"), "4h")
        assert out.sum() == 1
        assert bool(out.loc[_T0 + pd.Timedelta(hours=4)]) is True

    def test_脉冲_收盘晚于基准末端不落位_asof不可见(self):
        """末 bar 执行于开盘时刻，close > 末 bar 开盘的高周期 K 线彼时
        未收盘（spec MUST：只来自已收盘 K 线）——不落位。"""
        signal = pd.Series([True], index=[_T0])     # 收盘 T0+4h
        assert pulse_completed(signal, _grid(_T0, 3, "1h"), "4h").sum() == 0
        # 闭边界：末端恰为 T0+4h（=收盘时刻）→ 落位末 bar
        out = pulse_completed(signal, _grid(_T0, 5, "1h"), "4h")
        assert out.sum() == 1
        assert bool(out.loc[_T0 + pd.Timedelta(hours=4)]) is True

    def test_脉冲_收盘早于基准首bar不落位(self):
        sig_idx = pd.DatetimeIndex([_T0 - pd.Timedelta(hours=8)])
        signal = pd.Series([True], index=sig_idx)   # 收盘 T0-4h
        assert pulse_completed(signal, _grid(_T0, 4, "1h"), "4h").sum() == 0

    def test_脉冲_两close落同一基准bar_幂等单True(self):
        """重复落位同一基准 bar 自然幂等（True 而非计数）。"""
        sig_idx = pd.DatetimeIndex(
            [_T0, _T0 + pd.Timedelta(minutes=30)])  # 两 close 同落 T0+1h 窗
        signal = pd.Series([True, True], index=sig_idx)
        out = pulse_completed(signal, _grid(_T0, 3, "1h"), "1h")
        assert out.tolist() == [False, True, False]

    def test_脉冲_空信号全False(self):
        signal = pd.Series([], dtype=bool,
                           index=pd.DatetimeIndex([], tz="UTC"))
        out = pulse_completed(signal, _grid(_T0, 4, "1h"), "4h")
        assert out.tolist() == [False] * 4


# ---------------------------------------------------------------- warmup

class TestWarmupSpanIndependent:
    def test_warmup_EMA20_收敛三倍乘tf秒数(self):
        """B1：max(收敛×3, 20 bar) × TF_SECONDS——EMA(20) 收敛 20 → 60 bar。"""
        span = high_tf_warmup_span([("EMA", {"period": 20})], "4h")
        assert span == pd.Timedelta(seconds=60 * TF_SECONDS["4h"])

    def test_warmup_收敛三倍不足floor_按20bar(self):
        """RSI(5) 收敛 5×3=15 < floor 20 → 20 bar（A-new 工程参数）。"""
        span = high_tf_warmup_span([("RSI", {"period": 5})], "4h")
        assert span == pd.Timedelta(seconds=20 * TF_SECONDS["4h"])

    def test_warmup_多指标取最大(self):
        """RSI14（42）+ EMA20（60）→ 60 bar 前扩。"""
        span = high_tf_warmup_span(
            [("RSI", {"period": 14}), ("EMA", {"period": 20})], "1w")
        assert span == pd.Timedelta(seconds=60 * TF_SECONDS["1w"])

    def test_warmup_纯基础列空清单_零前扩(self):
        """无指标收敛需求（close@4h 类）→ 0 bar——不以 floor 拒绝可求值请求。"""
        assert high_tf_warmup_span([], "3d") == pd.Timedelta(0)


# ---------------------------------------------------------------- 方向判定与推断

class TestAssertAndInferIndependent:
    def test_方向_低周期拒绝附降基准指引(self):
        with pytest.raises(ValueError, match="降基准"):
            assert_higher_tf("15m", "1h")

    def test_方向_未知周期拒绝附合法清单(self):
        with pytest.raises(ValueError, match="合法键集"):
            assert_higher_tf("2d", "1h")

    def test_方向_高周期与等周期通过(self):
        assert assert_higher_tf("12h", "1h") is None
        assert assert_higher_tf("1d", "1d") is None

    def test_推断_等距索引取步长(self):
        assert infer_tf_delta(_grid(_T0, 4, "1h")) == pd.Timedelta(hours=1)

    def test_推断_缺口索引取中位数标称步长(self):
        """缺口（1h, 4h, 1h）→ 中位 1h（策略链索引推断对缺口鲁棒）。"""
        idx = pd.DatetimeIndex([
            _T0, _T0 + pd.Timedelta(hours=1), _T0 + pd.Timedelta(hours=5),
            _T0 + pd.Timedelta(hours=6)])
        assert infer_tf_delta(idx) == pd.Timedelta(hours=1)

    def test_推断_单元素索引零时长退化(self):
        idx = pd.DatetimeIndex([_T0])
        assert infer_tf_delta(idx) == pd.Timedelta(0)
