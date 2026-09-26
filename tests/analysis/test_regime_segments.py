"""行情分段权威模块测试（add-regime-split 任务 2.2/2.4）。

覆盖：向量化与单点滚动分位双实现互钉（数学一致性——判读提升的等价性
验收对象）；两段构造切段正确（类型与构造意图一致、段界在切换点附近）；
高波动覆盖态优先；快照同源（同一判读两个消费面）；确定性（同数据同
参数逐字节一致）；短段吸收与段数上限合并。
"""
from __future__ import annotations

import json

import pytest

pytestmark = [pytest.mark.unit]

import pandas as pd

from core.analysis.market_snapshot import _adx_state, _window_percentile
from core.analysis.regime_segments import (
    GATES, build_segments, classify_series, rolling_percentile,
)
from core.features.indicators import _compute_indicator
from core.features.registry import resolve_indicator_column
from tests.helpers.regime_factory import (
    make_random_walk_ohlcv as _random_walk,
    make_two_regime_df as _two_regime,
)


def _prep(df: pd.DataFrame) -> tuple[pd.DataFrame, str, str]:
    names = {}
    for ind in ("ADX", "ATR"):
        names[ind] = resolve_indicator_column(ind, {"period": 14})
        for c, s in _compute_indicator(df, ind, {"period": 14}).items():
            df[c] = s
    return df, names["ADX"], names["ATR"]


class TestPercentileParity:
    """任务 2.2：向量化滚动分位与快照单点版双实现互钉。"""

    def test_rolling_matches_pointwise(self):
        df, _, atr_col = _prep(_random_walk(seed=7, n=400))
        series = (df[atr_col] / df["close"] * 100.0).dropna()
        vec = rolling_percentile(series)
        checked = 0
        for pos in range(2, len(series), 13):  # 覆盖窗口内/满窗两种形态
            pt = _window_percentile(series, pos, 90)
            if pt is None:
                continue
            assert pt == pytest.approx(vec.iloc[pos], abs=0.02), pos
            checked += 1
        assert checked >= 20, "互钉采样充分性"


class TestBuildSegments:
    """任务 2.4：切段正确性（受控构造——类型来自构造意图）。"""

    def test_two_regime_construction_typed_correctly(self):
        """前趋势后震荡构造：首段类型=趋势、末段类型=震荡，段界在
        构造切换点附近（ADX 滞后允许偏移），段描述字段在值域内。"""
        df, adx_col, atr_col = _prep(_two_regime())
        segs = build_segments(df, adx_col, atr_col)
        assert segs[0]["type"] == "趋势"
        assert segs[-1]["type"] == "震荡"
        for s in segs:
            assert s["bars"] >= 1
            assert 0.0 <= s["adx_mean"] <= 100.0, "ADX 值域"
        # 构造切换在第 330 根（2024-11-26 附近）；末趋势段应延伸到其后
        assert segs[-1]["start"] > pd.Timestamp("2024-06-01", tz="UTC")

    def test_deterministic_byte_identical(self):
        df, adx_col, atr_col = _prep(_two_regime())
        a = build_segments(df, adx_col, atr_col)
        b = build_segments(df, adx_col, atr_col)
        assert json.dumps(a, default=str) == json.dumps(b, default=str)

    def test_segment_count_cap_merges_not_truncates(self):
        """段数超上限：最小段并入相邻（总 bar 数守恒=合并不截断）。"""
        df, adx_col, atr_col = _prep(_random_walk(seed=42, n=3000))
        segs = build_segments(df, adx_col, atr_col)
        assert len(segs) <= GATES["max_segments"]
        assert sum(s["bars"] for s in segs) == len(df), "bar 数守恒"

    def test_gates_disclosed_shape(self):
        assert set(GATES) == {"adx_trend", "adx_range", "vol_pct",
                              "percentile_window", "min_segment_bars",
                              "max_segments"}


class TestClassification:
    """高波动覆盖态优先与快照同源。"""

    def test_volatility_override_takes_precedence(self):
        """同 bar 同时满足趋势三态与高波动分位 → 判「高波动」。
        ATR 尾段递增构造（无并列——并列值的平均 rank 会衰减分位，
        恒值阶跃构造不适配 rank 语义）。"""
        idx = pd.date_range("2024-01-01", periods=300, freq="1D", tz="UTC")
        adx_name, atr_name = "adx_14", "atr_14"
        import numpy as np
        atr_tail = list(np.linspace(10.0, 60.0, 50))  # 递增：每根刷新窗口最高
        df = pd.DataFrame({
            "close": [100.0] * 300,
            # ADX 恒 30（趋势态），ATR 前低后递增抬升（后段分位趋 1）
            adx_name: [30.0] * 300,
            atr_name: [0.1] * 250 + atr_tail,
        }, index=idx)
        state = classify_series(df, adx_name, atr_name)
        assert (state.iloc[:250] == "趋势").all()
        assert (state.iloc[-40:] == "高波动").all(), "覆盖态优先于方向三态"

    def test_snapshot_same_source(self):
        """快照同源：classify_series 的方向判读与快照 _adx_state 同规则
        （提升别名后同一函数——此处钉住契约：分段状态仅出自同一枚举集）。"""
        df, adx_col, atr_col = _prep(_random_walk(seed=7, n=300))
        state = classify_series(df, adx_col, atr_col)
        assert set(state.unique()) <= {"趋势", "震荡", "过渡", "高波动"}
        # ADX 判读直接对照快照函数（非高波动 bar 上方向三态一致）
        vol = rolling_percentile(df[atr_col] / df["close"] * 100.0) < GATES["vol_pct"]
        for ts in state.index[::20]:
            adx = df.at[ts, adx_col]
            if pd.notna(adx) and bool(vol.loc[ts]):
                assert state.loc[ts] == _adx_state(float(adx)), ts
