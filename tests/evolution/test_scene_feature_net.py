"""core/validation/scene/ 特征测试网（修复期临时安全网）。

背景：下游审查战（fix/mcp-research-downstream-review）建网补薄，实测
tests/evolution/ 基线 586 用例下 scene/ 各模块行覆盖 38%-100%，
cross_timeframe / scene_engine / pattern_match 的主路径从未被执行。

语义约定：本文件为「现状记录」特征锚——断言值按当前实现行为钉住，
不臆断应然行为；实现行为变化时此网报警即为生效。阶段四将按协议
升级为规格测试。数据全部确定性构造（无随机或固定种子），保证可复现。

Run with: venv/bin/python -m pytest tests/evolution/test_scene_feature_net.py -v
"""

import numpy as np
import pandas as pd
import pytest
from unittest.mock import patch

pytestmark = [pytest.mark.unit]

from tests.helpers.data_factory import make_ohlcv


# ---------------------------------------------------------------------------
# 通用数据构造
# ---------------------------------------------------------------------------

def _base_frame(values: dict, n: int, freq: str = "4h") -> pd.DataFrame:
    """以列为单位的确定性 OHLCV 构造：values 中缺省项用平坦价格补齐。"""
    dates = pd.date_range("2024-01-01", periods=n, freq=freq, tz="UTC")
    flat = np.full(n, 100.0)
    data = {
        "open": values.get("open", flat.copy()),
        "high": values.get("high", flat + 0.5),
        "low": values.get("low", flat - 0.5),
        "close": values.get("close", flat.copy()),
        "volume": values.get("volume", np.full(n, 1000.0)),
    }
    data.update({k: v for k, v in values.items() if k not in data})
    df = pd.DataFrame(data, index=dates)
    df.index.name = "timestamp"
    return df


def _make_pattern_df(peaks: list, troughs: list, n: int,
                     decline_from: int | None = None,
                     decline_to: float = 95.0) -> pd.DataFrame:
    """按 (bar, high, volume) 峰与 (bar, low) 谷构造形态数据。

    峰/谷两侧各 6 根做线性攀爬/下坡，保证 detect_pivots(6,6) 窗口内
    中心点是唯一极值；非形态区保持平坦（high=100.5 / low=99.5）。
    decline_from 不为 None 时，从该 bar 起向 decline_to 阴跌（确认段，
    用于 neckline/level intact 校验；注意起点必须放在最后一个形态点
    的下坡区之后，否则会覆盖后续形态形状）。
    """
    closes = np.full(n, 100.0)
    highs = np.full(n, 100.5)
    lows = np.full(n, 99.5)
    volume = np.full(n, 500.0)

    for bar, price, vol in peaks:
        for i in range(max(0, bar - 6), bar):
            closes[i] = 100.0 + (price - 100.0) * (i - (bar - 6)) / 6.0
            highs[i] = closes[i] + 0.5
            lows[i] = closes[i] - 0.5
        closes[bar] = price
        highs[bar] = price
        lows[bar] = price - 1.5
        volume[bar] = vol
        for i in range(bar + 1, min(n, bar + 7)):
            closes[i] = price - (price - 100.0) * (i - bar) / 6.0
            highs[i] = closes[i] + 0.5
            lows[i] = closes[i] - 0.5

    for bar, price in troughs:
        for i in range(max(0, bar - 6), bar):
            closes[i] = 100.0 + (price - 100.0) * (i - (bar - 6)) / 6.0
            highs[i] = closes[i] + 0.5
            lows[i] = closes[i] - 0.5
        closes[bar] = price
        lows[bar] = price
        highs[bar] = price + 1.5
        for i in range(bar + 1, min(n, bar + 7)):
            closes[i] = price + (100.0 - price) * (i - bar) / 6.0
            highs[i] = closes[i] + 0.5
            lows[i] = closes[i] - 0.5

    if decline_from is not None:
        for i in range(decline_from, n):
            frac = (i - decline_from) / max(1, n - 1 - decline_from)
            target = 100.0 + (decline_to - 100.0) * min(1.0, frac)
            closes[i] = target
            highs[i] = target + 0.5
            lows[i] = target - 0.5

    return _base_frame(
        {"open": closes, "high": highs, "low": lows, "close": closes, "volume": volume},
        n,
    )


# ===========================================================================
# 1. CrossTimeframeDetector（基线覆盖 38%，主循环从未执行）
# ===========================================================================

class TestCrossTimeframeFeature:
    """cross_timeframe.py 特征锚：金叉判定/NaN 跳过/入场确认/间距/列名回退。"""

    @staticmethod
    def _make_ctf_df(n=80, crosses=(50,), slow=100.0, below=99.0, above=101.0,
                     base_fast_col="ema_20", base_fast_val=90.0):
        """构造高周期 EMA 列：crosses 各点发生 fast 上穿 slow 的金叉。

        每个上穿点 c 前一根强制 below、c 起置 above，形成 prev<=0<curr
        的穿越；相邻两次金叉之间留一根 below（制造一次死叉再上穿）。
        """
        fast = np.full(n, below)
        for c in sorted(crosses):
            if c >= 1:
                fast[c - 1] = below
            fast[c:] = above
        return _base_frame(
            {
                "ema_20_1d": fast,
                "ema_50_1d": np.full(n, slow),
                base_fast_col: np.full(n, base_fast_val),
            },
            n,
        )

    def test_name_returns_cross_timeframe(self):
        from core.validation.scene.cross_timeframe import CrossTimeframeDetector
        assert CrossTimeframeDetector().name == "cross_timeframe"

    def test_detect_golden_cross_triggers_with_htf_snapshot(self):
        from core.validation.scene.cross_timeframe import CrossTimeframeDetector
        df = self._make_ctf_df(crosses=(50,))
        triggers = CrossTimeframeDetector().detect(df, {})
        assert len(triggers) == 1
        tp = triggers[0]
        assert tp.bar_index == 50
        assert tp.trigger_price == pytest.approx(100.0)
        assert tp.id == 1
        assert tp.timestamp == str(df.index[50])
        # 快照带高周期快慢线值（round 4 位现状）
        assert tp.indicator_snapshot["htf_fast"] == pytest.approx(101.0, abs=1e-4)
        assert tp.indicator_snapshot["htf_slow"] == pytest.approx(100.0, abs=1e-4)

    def test_detect_no_cross_returns_empty(self):
        from core.validation.scene.cross_timeframe import CrossTimeframeDetector
        # fast 恒在 slow 之下，无穿越
        df = self._make_ctf_df(crosses=())
        df["ema_20_1d"] = 99.0
        assert CrossTimeframeDetector().detect(df, {}) == []

    def test_detect_skips_cross_when_current_htf_value_is_nan(self):
        from core.validation.scene.cross_timeframe import CrossTimeframeDetector
        df = self._make_ctf_df(crosses=(50,))
        df.loc[df.index[50], "ema_20_1d"] = np.nan
        assert CrossTimeframeDetector().detect(df, {}) == []

    def test_detect_skips_cross_when_previous_htf_value_is_nan(self):
        from core.validation.scene.cross_timeframe import CrossTimeframeDetector
        df = self._make_ctf_df(crosses=(50,))
        df.loc[df.index[49], "ema_20_1d"] = np.nan
        assert CrossTimeframeDetector().detect(df, {}) == []

    def test_detect_entry_confirmation_rejects_close_below_base_fast(self):
        from core.validation.scene.cross_timeframe import CrossTimeframeDetector
        df = self._make_ctf_df(crosses=(50,), base_fast_val=200.0)
        assert CrossTimeframeDetector().detect(df, {}) == []

    def test_detect_entry_confirmation_allows_close_equal_to_base_fast(self):
        from core.validation.scene.cross_timeframe import CrossTimeframeDetector
        # 边界：close == base_fast 不满足 "<" 拒绝条件，仍触发
        df = self._make_ctf_df(crosses=(50,), base_fast_val=100.0)
        triggers = CrossTimeframeDetector().detect(df, {})
        assert len(triggers) == 1

    @pytest.mark.parametrize(
        ("min_spacing", "expected_count"),
        [
            pytest.param(5, 2, id="间距充足两次金叉都触发"),
            pytest.param(15, 1, id="间隔10小于15只触发第一次"),
        ],
    )
    def test_detect_min_spacing_filters_close_crosses(self, min_spacing, expected_count):
        from core.validation.scene.cross_timeframe import CrossTimeframeDetector
        df = self._make_ctf_df(crosses=(30, 40))
        triggers = CrossTimeframeDetector().detect(df, {"min_spacing": min_spacing})
        assert len(triggers) == expected_count

    def test_detect_falls_back_to_ema_column_naming(self):
        from core.validation.scene.cross_timeframe import CrossTimeframeDetector
        # signal_type=sma_cross 首词为 sma，sma_20_1d 列缺失时回退 ema_20_1d
        df = self._make_ctf_df(crosses=(50,))
        triggers = CrossTimeframeDetector().detect(df, {"signal_type": "sma_cross"})
        assert len(triggers) == 1

    def test_detect_base_fast_falls_back_to_sma_column(self):
        from core.validation.scene.cross_timeframe import CrossTimeframeDetector
        df = self._make_ctf_df(crosses=(50,), base_fast_col="sma_20", base_fast_val=90.0)
        triggers = CrossTimeframeDetector().detect(df, {})
        assert len(triggers) == 1

    def test_detect_missing_slow_column_returns_empty(self):
        from core.validation.scene.cross_timeframe import CrossTimeframeDetector
        df = self._make_ctf_df(crosses=(50,))
        df = df.drop(columns=["ema_50_1d"])
        assert CrossTimeframeDetector().detect(df, {}) == []


# ===========================================================================
# 2. VolumeSpikeDetector（基线 71%，检出主路径 L46-60 未执行）
# ===========================================================================

class TestVolumeSpikeFeature:
    """volume_spike.py 特征锚：rvol 阈值/滚动均量含当前根/间距/零成交量。"""

    def test_name_returns_volume_spike(self):
        from core.validation.scene.volume_spike import VolumeSpikeDetector
        assert VolumeSpikeDetector().name == "volume_spike"

    def test_detect_spike_triggers_with_rvol_snapshot(self):
        from core.validation.scene.volume_spike import VolumeSpikeDetector
        n = 60
        volume = np.full(n, 1000.0)
        volume[40] = 4000.0
        df = _base_frame({"volume": volume}, n)
        triggers = VolumeSpikeDetector().detect(df, {})
        assert len(triggers) == 1
        tp = triggers[0]
        assert tp.bar_index == 40
        assert tp.trigger_price == pytest.approx(100.0)
        # rvol = 4000 / mean(volume[21..40]) = 4000/1150 ≈ 3.48（滚动窗含当前根）
        assert tp.indicator_snapshot["rvol"] == pytest.approx(4000 / 1150, abs=0.02)

    def test_detect_below_multiplier_no_trigger(self):
        from core.validation.scene.volume_spike import VolumeSpikeDetector
        n = 60
        volume = np.full(n, 1000.0)
        volume[40] = 2000.0  # rvol ≈ 2000/1050 ≈ 1.9 < 2.5
        df = _base_frame({"volume": volume}, n)
        assert VolumeSpikeDetector().detect(df, {}) == []

    def test_detect_min_spacing_suppresses_adjacent_spikes(self):
        from core.validation.scene.volume_spike import VolumeSpikeDetector
        n = 60
        volume = np.full(n, 1000.0)
        volume[40] = 4000.0
        volume[42] = 4000.0
        df = _base_frame({"volume": volume}, n)
        triggers = VolumeSpikeDetector().detect(df, {})
        assert [tp.bar_index for tp in triggers] == [40]

    def test_detect_all_zero_volume_returns_empty(self):
        from core.validation.scene.volume_spike import VolumeSpikeDetector
        df = _base_frame({"volume": np.zeros(60)}, 60)
        assert VolumeSpikeDetector().detect(df, {}) == []

    def test_detect_ignores_spike_before_avg_period(self):
        from core.validation.scene.volume_spike import VolumeSpikeDetector
        n = 60
        volume = np.full(n, 1000.0)
        volume[5] = 10000.0  # 扫描起点为 avg_period=20，bar 5 不在扫描范围
        df = _base_frame({"volume": volume}, n)
        assert VolumeSpikeDetector().detect(df, {}) == []


# ===========================================================================
# 3. MeanReversionDetector（基线 81%，above/both 方向与 MA 回退计算未执行）
# ===========================================================================

class TestMeanReversionFeature:
    """mean_reversion.py 特征锚：方向分支/偏离阈值/MA 列缺失回退计算。"""

    @staticmethod
    def _make_mr_df(n=80, dip_at=60, dip_to=96.0, ma_col="ema_50", ma_val=100.0):
        closes = np.full(n, 100.0)
        closes[dip_at:dip_at + 6] = dip_to
        extra = {ma_col: np.full(n, ma_val)} if ma_col else {}
        return _base_frame({"close": closes, **extra}, n)

    def test_name_returns_mean_reversion(self):
        from core.validation.scene.mean_reversion import MeanReversionDetector
        assert MeanReversionDetector().name == "mean_reversion"

    def test_detect_below_deviation_triggers_with_snapshot(self):
        from core.validation.scene.mean_reversion import MeanReversionDetector
        df = self._make_mr_df(dip_to=96.0)  # deviation = -4% <= -3%
        triggers = MeanReversionDetector().detect(df, {})
        assert len(triggers) == 1
        tp = triggers[0]
        assert tp.bar_index == 60
        assert tp.indicator_snapshot["ma_value"] == pytest.approx(100.0, abs=1e-4)
        assert tp.indicator_snapshot["deviation_pct"] == pytest.approx(-4.0, abs=0.01)

    def test_detect_above_direction_triggers_on_upside(self):
        from core.validation.scene.mean_reversion import MeanReversionDetector
        df = self._make_mr_df(dip_to=104.0)
        triggers = MeanReversionDetector().detect(df, {"direction": "above"})
        assert len(triggers) == 1
        assert triggers[0].indicator_snapshot["deviation_pct"] == pytest.approx(4.0, abs=0.01)

    @pytest.mark.parametrize(
        ("dip_to", "expected_count"),
        [
            pytest.param(96.0, 1, id="both方向下偏离触发"),
            pytest.param(104.0, 1, id="both方向上偏离触发"),
        ],
    )
    def test_detect_both_direction_triggers_either_side(self, dip_to, expected_count):
        from core.validation.scene.mean_reversion import MeanReversionDetector
        df = self._make_mr_df(dip_to=dip_to)
        triggers = MeanReversionDetector().detect(df, {"direction": "both"})
        assert len(triggers) == expected_count

    def test_detect_below_direction_ignores_upside(self):
        from core.validation.scene.mean_reversion import MeanReversionDetector
        df = self._make_mr_df(dip_to=104.0)
        assert MeanReversionDetector().detect(df, {}) == []

    def test_detect_within_threshold_no_trigger(self):
        from core.validation.scene.mean_reversion import MeanReversionDetector
        df = self._make_mr_df(dip_to=99.0)  # -1% 未达 3%
        assert MeanReversionDetector().detect(df, {}) == []

    def test_detect_computes_missing_ema_column_inplace(self):
        """现状记录：ma 列缺失时按 close 现场计算 EMA 并原位写入 df。"""
        from core.validation.scene.mean_reversion import MeanReversionDetector
        n = 100
        closes = np.full(n, 100.0)
        closes[60:] = 80.0  # 深跌，ewm(span=50) 滞后使偏离超过 3%
        df = _base_frame({"close": closes}, n)
        assert "ema_50" not in df.columns
        triggers = MeanReversionDetector().detect(df, {})
        assert len(triggers) >= 1
        assert "ema_50" in df.columns  # 副作用现状：调用方 df 被加列

    def test_detect_ma_type_sma_falls_back_to_rolling(self):
        from core.validation.scene.mean_reversion import MeanReversionDetector
        n = 100
        closes = np.full(n, 100.0)
        closes[60:] = 80.0
        df = _base_frame({"close": closes}, n)
        triggers = MeanReversionDetector().detect(df, {"ma_type": "sma"})
        assert len(triggers) >= 1
        assert "sma_50" in df.columns

    def test_detect_zero_ma_values_skipped(self):
        from core.validation.scene.mean_reversion import MeanReversionDetector
        df = self._make_mr_df(ma_val=0.0)
        assert MeanReversionDetector().detect(df, {}) == []

    def test_detect_min_spacing_suppresses_repeat_triggers(self):
        from core.validation.scene.mean_reversion import MeanReversionDetector
        df = self._make_mr_df(dip_to=96.0)  # 60-65 连续 6 根满足偏离
        triggers = MeanReversionDetector().detect(df, {})
        assert [tp.bar_index for tp in triggers] == [60]


# ===========================================================================
# 4. VolumeBreakoutDetector（基线 82%，列缺失/NaN/未突破/间距分支未执行）
# ===========================================================================

class TestVolumeBreakoutFeature:
    """volume_breakout.py 特征锚：level 解析回退与触发负路径。"""

    @staticmethod
    def _make_vb_df(n=60, bb_upper_val=None, spike_at=40, spike_vol=4000.0):
        volume = np.full(n, 1000.0)
        volume[spike_at] = spike_vol
        bb = np.full(n, 95.0) if bb_upper_val is None else np.asarray(bb_upper_val, dtype=float)
        return _base_frame(
            {"volume": volume, "bb_upper_20_2.0": bb}, n,
        )

    def test_name_returns_volume_breakout(self):
        from core.validation.scene.volume_breakout import VolumeBreakoutDetector
        assert VolumeBreakoutDetector().name == "volume_breakout"

    def test_detect_unknown_level_type_returns_empty(self):
        from core.validation.scene.volume_breakout import VolumeBreakoutDetector
        df = self._make_vb_df()
        out = VolumeBreakoutDetector().detect(df, {"level_type": "nonexistent"})
        assert out == []

    def test_detect_all_zero_volume_skips_every_bar(self):
        """现状记录：滚动均量为 0 时逐根跳过（L50-51），level 列存在也不触发。"""
        from core.validation.scene.volume_breakout import VolumeBreakoutDetector
        n = 60
        df = _base_frame(
            {"volume": np.zeros(n), "bb_upper_20_2.0": np.full(n, 95.0)}, n,
        )
        assert VolumeBreakoutDetector().detect(df, {}) == []

    def test_detect_level_nan_at_spike_bar_skips(self):
        from core.validation.scene.volume_breakout import VolumeBreakoutDetector
        bb = np.full(60, 95.0)
        bb[40] = np.nan
        df = self._make_vb_df(bb_upper_val=bb)
        assert VolumeBreakoutDetector().detect(df, {}) == []

    def test_detect_close_not_above_level_skips(self):
        from core.validation.scene.volume_breakout import VolumeBreakoutDetector
        # close=100 > level=105 不成立（未突破），量再大也无触发
        df = self._make_vb_df(bb_upper_val=np.full(60, 105.0))
        assert VolumeBreakoutDetector().detect(df, {}) == []

    def test_detect_min_spacing_suppresses_adjacent_breakouts(self):
        from core.validation.scene.volume_breakout import VolumeBreakoutDetector
        n = 60
        volume = np.full(n, 1000.0)
        volume[40] = 4000.0
        volume[42] = 4000.0
        df = _base_frame({"volume": volume, "bb_upper_20_2.0": np.full(n, 95.0)}, n)
        triggers = VolumeBreakoutDetector().detect(df, {})
        assert [tp.bar_index for tp in triggers] == [40]

    def test_detect_snapshot_contains_breakout_fields(self):
        from core.validation.scene.volume_breakout import VolumeBreakoutDetector
        df = self._make_vb_df()
        triggers = VolumeBreakoutDetector().detect(df, {})
        assert len(triggers) == 1
        snap = triggers[0].indicator_snapshot
        assert "rvol" in snap
        assert snap["level_value"] == pytest.approx(95.0, abs=1e-4)
        assert snap["breakout_pct"] == pytest.approx((100.0 - 95.0) / 95.0 * 100, abs=0.02)

    def test_resolve_level_column_matches_keltner_by_suffix(self):
        """现状记录：keltner 语义段在列尾，按 (kc_, _upper) 前后缀兜底匹配。"""
        from core.validation.scene.volume_breakout import _resolve_level_column
        cols = pd.Index(["close", "kc_20_10_2.0_upper", "kc_20_10_2.0_lower"])
        assert _resolve_level_column("keltner_upper", cols) == "kc_20_10_2.0_upper"
        assert _resolve_level_column("keltner_lower", cols) == "kc_20_10_2.0_lower"

    def test_resolve_level_column_no_match_returns_none(self):
        from core.validation.scene.volume_breakout import _resolve_level_column
        assert _resolve_level_column("bb_upper", pd.Index(["close", "volume"])) is None


# ===========================================================================
# 5. SupportResistanceDetector（基线 86%，resistance 方向与解析未命中未执行）
# ===========================================================================

class TestSupportResistanceFeature:
    """support_resistance.py 特征锚：resistance/both 方向、解析未命中、距离阈值。"""

    def test_name_returns_support_resistance(self):
        from core.validation.scene.support_resistance import SupportResistanceDetector
        assert SupportResistanceDetector().name == "support_resistance"

    def test_detect_resistance_direction_triggers_on_upper_touch(self):
        from core.validation.scene.support_resistance import SupportResistanceDetector
        n = 40
        df = _base_frame(
            {"high": np.full(n, 105.0), "bb_upper_20_2.0": np.full(n, 105.0)}, n,
        )
        triggers = SupportResistanceDetector().detect(
            df, {"level_source": "bb", "direction": "resistance"},
        )
        # 扫描从 bar 1 起，min_spacing=10 → 每 10 根触发一次（现状钉住）
        assert [tp.bar_index for tp in triggers] == [1, 11, 21, 31]
        assert triggers[0].indicator_snapshot["level_value"] == pytest.approx(105.0, abs=1e-4)
        assert triggers[0].indicator_snapshot["proximity_pct"] == pytest.approx(0.0, abs=1e-4)

    def test_detect_both_direction_checks_both_sides(self):
        """CARD-02 后语义：both 时双侧各查（support 配 lows、resistance
        配 highs），任一侧邻近即触发。本例只有 support 侧邻近。"""
        from core.validation.scene.support_resistance import SupportResistanceDetector
        n = 40
        df = _base_frame(
            {"low": np.full(n, 95.5), "bb_lower_20_2.0": np.full(n, 95.0)}, n,
        )
        triggers = SupportResistanceDetector().detect(
            df, {"level_source": "bb", "direction": "both"},
        )
        assert [tp.bar_index for tp in triggers] == [1, 11, 21, 31]
        assert triggers[0].indicator_snapshot["proximity_pct"] == pytest.approx(
            0.5 / 95.0 * 100, abs=0.02,
        )

    @staticmethod
    def _make_both_sides_df(n=10):
        """双侧数据：bar3/4/6 low 邻近支撑 95；bar5 high 邻近阻力 125。

        bar5 的 low 远离支撑（80），确保 resistance 侧独立命中。
        """
        low = np.full(n, 80.0)
        low[[3, 4, 6]] = 95.5          # |95.5-95|/95 ≈ 0.53%
        high = np.full(n, 100.0)
        high[5] = 126.0                # |126-125|/125 = 0.8%
        return _base_frame(
            {
                "low": low, "high": high,
                "bb_lower_20_2.0": np.full(n, 95.0),
                "bb_upper_20_2.0": np.full(n, 125.0),
            },
            n,
        )

    def test_detect_both_triggers_each_side_independently(self):
        """CARD-02 锚：both 下 resistance 侧独立触发（旧实现被
        `direction == "resistance"` 挡死漏检 bar5）。"""
        from core.validation.scene.support_resistance import SupportResistanceDetector
        df = self._make_both_sides_df()
        triggers = SupportResistanceDetector().detect(
            df, {"level_source": "bb", "direction": "both",
                 "proximity_pct": 2.0, "min_spacing": 1},
        )
        got = [(tp.bar_index, tp.indicator_snapshot["level_value"]) for tp in triggers]
        assert got == [(3, 95.0), (4, 95.0), (5, 125.0), (6, 95.0)]
        # bar5 为 resistance 侧命中：level_value 记触发侧列值
        bar5 = next(tp for tp in triggers if tp.bar_index == 5)
        assert bar5.indicator_snapshot["level_value"] == pytest.approx(125.0)
        assert bar5.indicator_snapshot["proximity_pct"] == pytest.approx(0.8, abs=0.01)

    def test_detect_single_side_on_both_sides_data(self):
        """同数据单方向：support 只出支撑侧，resistance 只出阻力侧。"""
        from core.validation.scene.support_resistance import SupportResistanceDetector
        df = self._make_both_sides_df()
        params = {"level_source": "bb", "proximity_pct": 2.0, "min_spacing": 1}
        support_only = SupportResistanceDetector().detect(
            df, {**params, "direction": "support"},
        )
        resistance_only = SupportResistanceDetector().detect(
            df, {**params, "direction": "resistance"},
        )
        assert [tp.bar_index for tp in support_only] == [3, 4, 6]
        assert [tp.bar_index for tp in resistance_only] == [5]

    def test_detect_both_with_one_side_nan_still_triggers_other(self):
        """现状记录：单侧列全 NaN 时另一侧仍可触发。"""
        from core.validation.scene.support_resistance import SupportResistanceDetector
        df = self._make_both_sides_df()
        df["bb_lower_20_2.0"] = np.nan
        triggers = SupportResistanceDetector().detect(
            df, {"level_source": "bb", "direction": "both",
                 "proximity_pct": 2.0, "min_spacing": 1},
        )
        assert [tp.bar_index for tp in triggers] == [5]

    def test_detect_both_support_side_evaluated_first(self):
        """现状记录：双侧同 bar 同时邻近时 support 侧先评估命中，
        level_value 记 support 列值（side_levels 的字典顺序）。"""
        from core.validation.scene.support_resistance import SupportResistanceDetector
        df = self._make_both_sides_df()
        # bar3 同时让 high 邻近阻力：high=125.8（0.64%）
        df.iloc[3, df.columns.get_loc("high")] = 125.8
        triggers = SupportResistanceDetector().detect(
            df, {"level_source": "bb", "direction": "both",
                 "proximity_pct": 2.0, "min_spacing": 1},
        )
        bar3 = next(tp for tp in triggers if tp.bar_index == 3)
        assert bar3.indicator_snapshot["level_value"] == pytest.approx(95.0)

    def test_detect_unknown_source_returns_empty(self):
        from core.validation.scene.support_resistance import SupportResistanceDetector
        n = 40
        df = _base_frame({"bb_lower_20_2.0": np.full(n, 95.0)}, n)
        out = SupportResistanceDetector().detect(df, {"level_source": "nonexistent"})
        assert out == []

    def test_detect_far_from_level_no_trigger(self):
        from core.validation.scene.support_resistance import SupportResistanceDetector
        n = 40
        df = _base_frame(
            {"low": np.full(n, 90.0), "bb_lower_20_2.0": np.full(n, 95.0)}, n,
        )
        assert SupportResistanceDetector().detect(df, {}) == []

    def test_resolve_sr_column_no_match_returns_none(self):
        from core.validation.scene.support_resistance import _resolve_sr_column
        assert _resolve_sr_column("bb", "support", pd.Index(["close", "volume"])) is None


# ===========================================================================
# 6. TopPatternDetector（基线 91%，间距过滤与快照容错未执行）
# ===========================================================================

class TestTopPatternFeature:
    """top_pattern.py 特征锚：多形态间距过滤、快照非数值容错。"""

    def test_name_returns_top_pattern(self):
        from core.validation.scene.top_pattern import TopPatternDetector
        assert TopPatternDetector().name == "top_pattern"

    @staticmethod
    def _two_double_tops_df():
        """两组 double_top：completion 分别在 bar 55 与 bar 115（间隔 60）。"""
        peaks = [
            (15, 110.0, 1000.0), (55, 110.0, 900.0),
            (75, 120.0, 1000.0), (115, 120.0, 900.0),
        ]
        troughs = [(35, 90.0), (95, 95.0)]
        return _make_pattern_df(peaks, troughs, n=140, decline_from=122, decline_to=85.0)

    @pytest.mark.parametrize(
        ("min_spacing", "expected_count"),
        [
            pytest.param(10, 2, id="默认间距两形态都触发"),
            pytest.param(100, 1, id="间距100大于60只触发第一形态"),
        ],
    )
    def test_detect_min_spacing_filters_patterns(self, min_spacing, expected_count):
        from core.validation.scene.top_pattern import TopPatternDetector
        df = self._two_double_tops_df()
        triggers = TopPatternDetector().detect(df, {"min_spacing": min_spacing})
        assert len(triggers) == expected_count
        subtypes = {tp.pattern_subtype for tp in triggers}
        assert subtypes == {"double_top"}

    def test_detect_trigger_carries_pattern_metadata(self):
        from core.validation.scene.top_pattern import TopPatternDetector
        df = self._two_double_tops_df()
        triggers = TopPatternDetector().detect(df, {})
        assert triggers[0].pattern_metadata["pattern_type"] == "double_top"
        labels = [kp["label"] for kp in triggers[0].pattern_metadata["key_points"]]
        assert labels == ["peak_1", "trough_1", "peak_2"]

    def test_extract_snapshot_skips_non_numeric_values(self):
        """现状记录：priority 列非数值（如字符串）时静默跳过，不抛异常。"""
        from core.validation.scene.top_pattern import _extract_snapshot
        row = pd.Series({"rsi_14": "not-a-number", "atr_14": 123.45678})
        snap = _extract_snapshot(row)
        assert "rsi_14" not in snap
        assert snap["atr_14"] == pytest.approx(123.4568, abs=1e-4)  # round 4 位现状


# ===========================================================================
# 7. pattern_match（基线 62%，head_shoulders_top 与 triple_top 检出路径从未执行）
# ===========================================================================

class TestPatternMatchFeature:
    """pattern_match.py 特征锚：三种顶部形态检出与关键约束负路径。"""

    @staticmethod
    def _run_match(df, bars_left=6, bars_right=6, atr_series=None):
        from core.validation.scene.pattern_match import match_patterns
        from core.validation.scene.pivot import detect_pivots
        pivots = detect_pivots(df, bars_left=bars_left, bars_right=bars_right)
        return match_patterns(pivots, df=df, atr_series=atr_series), pivots

    def test_match_patterns_head_shoulders_top_detected(self):
        """左肩105/头115/右肩105，腋窝均90；头肩顶主体路径（L323-358）。"""
        peaks = [(15, 105.0, 800.0), (55, 115.0, 1000.0), (95, 105.0, 800.0)]
        troughs = [(35, 90.0), (75, 90.0)]
        df = _make_pattern_df(peaks, troughs, n=130, decline_from=102, decline_to=95.0)
        results, pivots = self._run_match(df)
        hs = [m for m in results if m.pattern_type == "head_shoulders_top"]
        assert hs, (
            f"Expected head_shoulders_top, got {[m.pattern_type for m in results]}; "
            f"pivots: {[(p.pivot_type, p.index, p.price) for p in pivots]}"
        )
        m = hs[0]
        assert m.completion_bar == 95  # 右肩 bar 为完成点
        labels = [kp["label"] for kp in m.key_points]
        assert labels == ["left_shoulder", "neckline_1", "head", "neckline_2", "right_shoulder"]
        by_label = {kp["label"]: kp for kp in m.key_points}
        assert by_label["head"]["index"] == 55
        assert by_label["head"]["price"] == pytest.approx(115.0)

    def test_match_patterns_triple_top_detected(self):
        """三峰均110、两谷均95；三重顶主体路径（L451-492）。"""
        peaks = [(15, 110.0, 1000.0), (45, 110.0, 900.0), (75, 110.0, 800.0)]
        troughs = [(30, 95.0), (60, 95.0)]
        df = _make_pattern_df(peaks, troughs, n=110)
        results, pivots = self._run_match(df)
        tt = [m for m in results if m.pattern_type == "triple_top"]
        assert tt, (
            f"Expected triple_top, got {[m.pattern_type for m in results]}; "
            f"pivots: {[(p.pivot_type, p.index, p.price) for p in pivots]}"
        )
        m = tt[0]
        assert m.completion_bar == 75
        labels = [kp["label"] for kp in m.key_points]
        assert labels == ["peak_1", "trough_1", "peak_2", "trough_2", "peak_3"]
        assert [kp["price"] for kp in m.key_points if "peak" in kp["label"]] == [
            pytest.approx(110.0)] * 3

    def test_match_patterns_head_not_highest_rejected(self):
        """三峰等高（头不高于肩）不构成头肩顶，仅可能落入 triple_top。"""
        peaks = [(15, 110.0, 1000.0), (45, 110.0, 900.0), (75, 110.0, 800.0)]
        troughs = [(30, 95.0), (60, 95.0)]
        df = _make_pattern_df(peaks, troughs, n=110)
        results, _ = self._run_match(df)
        assert all(m.pattern_type != "head_shoulders_top" for m in results)

    def test_match_patterns_double_top_rejected_by_level_breach(self):
        """第二峰后 10 根内 high 上破 C 峰价 + abl → C level 已破，不检出。"""
        df = _make_pattern_df(
            [(15, 110.0, 1000.0), (55, 110.0, 900.0)], [(35, 90.0)],
            n=80, decline_from=62, decline_to=85.0,
        )
        # 在第二峰后 3 根制造一根更高的 high，破坏 level intact
        df.iloc[58, df.columns.get_loc("high")] = 112.0
        results, _ = self._run_match(df)
        assert all(m.pattern_type != "double_top" for m in results)

    def test_match_patterns_df_none_returns_empty(self):
        from core.validation.scene.pattern_match import match_patterns
        from core.validation.scene.pivot import PivotPoint
        pivots = [
            PivotPoint(index=0, price=100.0, pivot_type="peak"),
            PivotPoint(index=5, price=90.0, pivot_type="trough"),
            PivotPoint(index=10, price=100.0, pivot_type="peak"),
        ]
        assert match_patterns(pivots, df=None) == []

    def test_match_patterns_results_ordered_by_completion_bar(self):
        from core.validation.scene.pattern_match import match_patterns
        from core.validation.scene.pivot import detect_pivots
        df = _make_pattern_df(
            [(15, 110.0, 1000.0), (55, 110.0, 900.0),
             (75, 120.0, 1000.0), (115, 120.0, 900.0)],
            [(35, 90.0), (95, 95.0)],
            n=140, decline_from=122, decline_to=85.0,
        )
        pivots = detect_pivots(df, bars_left=6, bars_right=6)
        results = match_patterns(pivots, df=df)
        bars = [m.completion_bar for m in results]
        assert bars == sorted(bars)


class TestPatternMatchNegativeFeature:
    """pattern_match.py 约束拒绝负路径锚：几何/ATR/量能/锚定校验。

    用手工 PivotPoint（不经 detect_pivots）精确控制几何量，df 仅在
    对应 bar 上满足 anchor 匹配（peak.price==high / trough.price==low）。
    """

    @staticmethod
    def _hand_pivots(spec: list) -> list:
        """[(index, price, 'peak'|'trough', volume)] → PivotPoint 列表。"""
        from core.validation.scene.pivot import PivotPoint
        return [
            PivotPoint(index=i, price=p, pivot_type=t, volume=v)
            for i, p, t, v in spec
        ]

    @staticmethod
    def _anchor_df(n: int, highs: dict, lows: dict) -> pd.DataFrame:
        """平坦 df（high=100.5/low=99.5），仅覆盖给定 bar 的极值。"""
        high_arr = np.full(n, 100.5)
        low_arr = np.full(n, 99.5)
        for bar, val in highs.items():
            high_arr[bar] = val
        for bar, val in lows.items():
            low_arr[bar] = val
        return _base_frame({"high": high_arr, "low": low_arr}, n)

    def test_insufficient_peaks_returns_empty(self):
        """3 个 pivot 但仅 1 峰：三个 finder 均因峰数不足直接返回。"""
        from core.validation.scene.pattern_match import match_patterns
        pivots = self._hand_pivots([
            (10, 110.0, "peak", 1000.0),
            (20, 95.0, "trough", 0.0),
            (30, 96.0, "trough", 0.0),
        ])
        df = self._anchor_df(40, {10: 110.0}, {20: 95.0, 30: 96.0})
        assert match_patterns(pivots, df=df) == []

    def test_zero_amplitude_rejects_all_patterns(self):
        """high==low（abl=0）时三形态均因 avgBarLength<=0 拒绝。"""
        from core.validation.scene.pattern_match import match_patterns
        pivots = self._hand_pivots([
            (10, 100.0, "peak", 0.0), (20, 100.0, "trough", 0.0),
            (30, 100.0, "peak", 0.0), (40, 100.0, "trough", 0.0),
            (50, 100.0, "peak", 0.0),
        ])
        n = 60
        df = _base_frame(
            {"high": np.full(n, 100.0), "low": np.full(n, 100.0)}, n,
        )
        assert match_patterns(pivots, df=df) == []

    def test_anchor_mismatch_rejects_head_shoulders_and_double(self):
        """pivot 价与 df 极值不符（全平坦 df）→ anchor 校验拒绝。"""
        from core.validation.scene.pattern_match import match_patterns
        pivots = self._hand_pivots([
            (10, 110.0, "peak", 0.0), (20, 95.0, "trough", 0.0),
            (30, 115.0, "peak", 0.0), (40, 95.0, "trough", 0.0),
            (50, 110.0, "peak", 0.0),
        ])
        df = self._anchor_df(60, {}, {})  # 全平坦，anchor 全不匹配
        assert match_patterns(pivots, df=df) == []

    def test_anchor_mismatch_rejects_triple_top(self):
        from core.validation.scene.pattern_match import match_patterns
        pivots = self._hand_pivots([
            (10, 110.0, "peak", 0.0), (20, 95.0, "trough", 0.0),
            (30, 110.0, "peak", 0.0), (40, 95.0, "trough", 0.0),
            (50, 110.0, "peak", 0.0),
        ])
        df = self._anchor_df(60, {}, {})
        assert match_patterns(pivots, df=df) == []

    def test_head_shoulders_rejected_by_shoulder_gap(self):
        """两肩价差 10 > abl*0.5 → 拒（几何约束 L327-328）。"""
        from core.validation.scene.pattern_match import match_patterns
        pivots = self._hand_pivots([
            (10, 110.0, "peak", 0.0), (20, 95.0, "trough", 0.0),
            (30, 115.0, "peak", 0.0), (40, 95.0, "trough", 0.0),
            (50, 100.0, "peak", 0.0),
        ])
        df = self._anchor_df(60, {10: 110.0, 30: 115.0, 50: 100.0}, {20: 95.0, 40: 95.0})
        results = match_patterns(pivots, df=df)
        assert all(m.pattern_type != "head_shoulders_top" for m in results)

    def test_head_shoulders_rejected_by_neckline_tilt(self):
        """两腋窝价差 3 >= abl=1 → 颈线不水平，拒（L331-332）。"""
        from core.validation.scene.pattern_match import match_patterns
        pivots = self._hand_pivots([
            (10, 110.0, "peak", 0.0), (20, 95.0, "trough", 0.0),
            (30, 115.0, "peak", 0.0), (40, 98.0, "trough", 0.0),
            (50, 110.0, "peak", 0.0),
        ])
        df = self._anchor_df(60, {10: 110.0, 30: 115.0, 50: 110.0}, {20: 95.0, 40: 98.0})
        results = match_patterns(pivots, df=df)
        assert all(m.pattern_type != "head_shoulders_top" for m in results)

    def test_head_shoulders_rejected_by_flat_head(self):
        """头突出 0.5 < abl*0.6=0.6 → 结构不明显，拒（L336-337）。"""
        from core.validation.scene.pattern_match import match_patterns
        pivots = self._hand_pivots([
            (10, 101.0, "peak", 0.0), (20, 95.0, "trough", 0.0),
            (30, 101.5, "peak", 0.0), (40, 95.0, "trough", 0.0),
            (50, 101.0, "peak", 0.0),
        ])
        df = self._anchor_df(60, {10: 101.0, 30: 101.5, 50: 101.0}, {20: 95.0, 40: 95.0})
        results = match_patterns(pivots, df=df)
        assert all(m.pattern_type != "head_shoulders_top" for m in results)

    def test_head_shoulders_rejected_by_atr_upper_bound(self):
        """头肩形态数据 + ATR=2：头高 25 > 8*ATR=16 → 拒（L342-345）。"""
        peaks = [(15, 105.0, 800.0), (55, 115.0, 1000.0), (95, 105.0, 800.0)]
        troughs = [(35, 90.0), (75, 90.0)]
        df = _make_pattern_df(peaks, troughs, n=130, decline_from=102, decline_to=95.0)
        from core.validation.scene.pattern_match import match_patterns
        from core.validation.scene.pivot import detect_pivots
        pivots = detect_pivots(df, bars_left=6, bars_right=6)
        atr = pd.Series(np.full(len(df), 2.0), index=df.index)
        results = match_patterns(pivots, df=df, atr_series=atr)
        assert all(m.pattern_type != "head_shoulders_top" for m in results)

    def test_triple_top_rejected_by_atr_upper_bound(self):
        """三重顶数据 + ATR=1.8：形态高 15 > 8*1.8=14.4 → 拒（L463-465）。"""
        peaks = [(15, 110.0, 1000.0), (45, 110.0, 900.0), (75, 110.0, 800.0)]
        troughs = [(30, 95.0), (60, 95.0)]
        df = _make_pattern_df(peaks, troughs, n=110)
        from core.validation.scene.pattern_match import match_patterns
        from core.validation.scene.pivot import detect_pivots
        pivots = detect_pivots(df, bars_left=6, bars_right=6)
        atr = pd.Series(np.full(len(df), 1.8), index=df.index)
        results = match_patterns(pivots, df=df, atr_series=atr)
        assert all(m.pattern_type != "triple_top" for m in results)

    def test_double_top_rejected_by_atr_upper_bound(self):
        """double_top 数据 + ATR=2：峰谷高 20 > 16 → 拒（L207-209）。"""
        peaks = [(15, 110.0, 1000.0), (55, 110.0, 900.0)]
        troughs = [(35, 90.0)]
        df = _make_pattern_df(peaks, troughs, n=80, decline_from=62, decline_to=85.0)
        from core.validation.scene.pattern_match import match_patterns
        from core.validation.scene.pivot import detect_pivots
        pivots = detect_pivots(df, bars_left=6, bars_right=6)
        atr = pd.Series(np.full(len(df), 2.0), index=df.index)
        results = match_patterns(pivots, df=df, atr_series=atr)
        assert all(m.pattern_type != "double_top" for m in results)

    def test_double_top_rejected_by_second_peak_volume(self):
        """第二峰量 2000 >= 首峰 1000*1.5 → 量能约束拒（L213-215）。"""
        peaks = [(15, 110.0, 1000.0), (55, 110.0, 2000.0)]
        troughs = [(35, 90.0)]
        df = _make_pattern_df(peaks, troughs, n=80, decline_from=62, decline_to=85.0)
        from core.validation.scene.pattern_match import match_patterns
        from core.validation.scene.pivot import detect_pivots
        pivots = detect_pivots(df, bars_left=6, bars_right=6)
        results = match_patterns(pivots, df=df)
        assert all(m.pattern_type != "double_top" for m in results)

    def test_double_top_rejected_by_valley_above_peaks(self):
        """谷 111 >= 峰 110 → 谷不在两峰之下，拒（L201-202）。"""
        from core.validation.scene.pattern_match import match_patterns
        pivots = self._hand_pivots([
            (10, 110.0, "peak", 0.0), (20, 111.0, "trough", 0.0),
            (30, 110.0, "peak", 0.0),
        ])
        df = self._anchor_df(40, {10: 110.0, 30: 110.0}, {20: 111.0})
        results = match_patterns(pivots, df=df)
        assert all(m.pattern_type != "double_top" for m in results)

    def test_double_top_rejected_by_post_peak_breach(self):
        """C 峰后 10 根内 high 上破 C+abl → level intact 失败，拒（L226-227）。"""
        from core.validation.scene.pattern_match import match_patterns
        pivots = self._hand_pivots([
            (10, 110.0, "peak", 0.0), (20, 95.0, "trough", 0.0),
            (30, 110.0, "peak", 0.0),
        ])
        highs = {10: 110.0, 30: 110.0, 31: 112.0}  # 31 上破 110+abl=111
        df = self._anchor_df(45, highs, {20: 95.0})
        results = match_patterns(pivots, df=df)
        assert all(m.pattern_type != "double_top" for m in results)

    def test_head_shoulders_rejected_by_armpit_above_shoulder(self):
        """腋窝 106 >= 较低肩 105 → 腋不低于肩，拒（L323-324）。"""
        from core.validation.scene.pattern_match import match_patterns
        pivots = self._hand_pivots([
            (10, 110.0, "peak", 0.0), (20, 106.0, "trough", 0.0),
            (30, 115.0, "peak", 0.0), (40, 95.0, "trough", 0.0),
            (50, 105.0, "peak", 0.0),
        ])
        df = self._anchor_df(
            60, {10: 110.0, 30: 115.0, 50: 105.0}, {20: 106.0, 40: 95.0},
        )
        results = match_patterns(pivots, df=df)
        assert all(m.pattern_type != "head_shoulders_top" for m in results)

    def test_head_shoulders_rejected_by_neckline_breach(self):
        """右肩后 10 根内 low 下破颈线-abl → 确认失败，拒（L353-355）。"""
        from core.validation.scene.pattern_match import match_patterns
        pivots = self._hand_pivots([
            (10, 110.0, "peak", 0.0), (20, 95.0, "trough", 0.0),
            (30, 115.0, "peak", 0.0), (40, 95.0, "trough", 0.0),
            (50, 110.0, "peak", 0.0),
        ])
        lows = {20: 95.0, 40: 95.0, 51: 93.0}  # 51 下破 95-abl=94
        df = self._anchor_df(65, {10: 110.0, 30: 115.0, 50: 110.0}, lows)
        results = match_patterns(pivots, df=df)
        assert all(m.pattern_type != "head_shoulders_top" for m in results)

    def test_triple_top_rejected_by_staircase_prices(self):
        """p1=110/p2=110.3/p3=110.6：相邻差过、首尾差 0.6>0.5 → 拒（L451-452）。"""
        from core.validation.scene.pattern_match import match_patterns
        pivots = self._hand_pivots([
            (10, 110.0, "peak", 0.0), (20, 95.0, "trough", 0.0),
            (30, 110.3, "peak", 0.0), (40, 95.0, "trough", 0.0),
            (50, 110.6, "peak", 0.0),
        ])
        df = self._anchor_df(
            65, {10: 110.0, 30: 110.3, 50: 110.6}, {20: 95.0, 40: 95.0},
        )
        results = match_patterns(pivots, df=df)
        assert all(m.pattern_type != "triple_top" for m in results)

    def test_triple_top_rejected_by_trough_above_avg_peak(self):
        """谷 t1=110.5 >= 平均峰 110 → 谷不低于峰，拒（L456-457）。"""
        from core.validation.scene.pattern_match import match_patterns
        pivots = self._hand_pivots([
            (10, 110.0, "peak", 0.0), (20, 110.5, "trough", 0.0),
            (30, 110.0, "peak", 0.0), (40, 95.0, "trough", 0.0),
            (50, 110.0, "peak", 0.0),
        ])
        df = self._anchor_df(
            65, {10: 110.0, 30: 110.0, 50: 110.0}, {20: 110.5, 40: 95.0},
        )
        results = match_patterns(pivots, df=df)
        assert all(m.pattern_type != "triple_top" for m in results)

    def test_triple_top_rejected_by_third_peak_volume(self):
        """p3 量 2000 >= p1 量 1000*1.5 → 量能约束拒（L468-470）。"""
        from core.validation.scene.pattern_match import match_patterns
        pivots = self._hand_pivots([
            (10, 110.0, "peak", 1000.0), (20, 95.0, "trough", 0.0),
            (30, 110.0, "peak", 0.0), (40, 95.0, "trough", 0.0),
            (50, 110.0, "peak", 2000.0),
        ])
        df = self._anchor_df(
            65, {10: 110.0, 30: 110.0, 50: 110.0}, {20: 95.0, 40: 95.0},
        )
        results = match_patterns(pivots, df=df)
        assert all(m.pattern_type != "triple_top" for m in results)

    def test_triple_top_rejected_by_post_peak_breach(self):
        """p3 峰后 10 根内 high 上破 p3+abl → level intact 失败，拒（L478-479）。"""
        from core.validation.scene.pattern_match import match_patterns
        pivots = self._hand_pivots([
            (10, 110.0, "peak", 0.0), (20, 95.0, "trough", 0.0),
            (30, 110.0, "peak", 0.0), (40, 95.0, "trough", 0.0),
            (50, 110.0, "peak", 0.0),
        ])
        highs = {10: 110.0, 30: 110.0, 50: 110.0, 51: 112.0}
        df = self._anchor_df(65, highs, {20: 95.0, 40: 95.0})
        results = match_patterns(pivots, df=df)
        assert all(m.pattern_type != "triple_top" for m in results)


class TestPatternMatchHelpersFeature:
    """pattern_match.py 内部纯函数现状锚（直接调用钉住边界行为）。"""

    @staticmethod
    def _small_df():
        return pd.DataFrame(
            {"high": [100.0, 101.0, 102.0, 101.5],
             "low": [99.0, 98.0, 97.0, 97.5]},
        )

    def test_check_level_intact_from_last_bar_returns_true(self):
        from core.validation.scene.pattern_match import _check_level_intact
        df = self._small_df()
        assert _check_level_intact(100.0, len(df) - 1, df, "above", 1.0) == True  # noqa: E712

    def test_check_level_intact_below_direction_detects_breach(self):
        from core.validation.scene.pattern_match import _check_level_intact
        df = self._small_df()
        # 后续 low 最低 97；below 判定为 lowest > level - abl（严格大于）
        # level=98：97 > 97 不成立 → 边界相等判破（现状钉住）
        assert _check_level_intact(98.0, 0, df, "below", 1.0) == False  # noqa: E712
        # level=97：97 > 96 成立 → intact
        assert _check_level_intact(97.0, 0, df, "below", 1.0) == True  # noqa: E712

    def test_check_level_intact_above_direction_detects_breach(self):
        from core.validation.scene.pattern_match import _check_level_intact
        df = self._small_df()
        # 后续 high 最高 102；above 判定为 highest < level + abl（严格小于）
        # level=101：102 < 102 不成立 → 边界相等判破（现状钉住）
        assert _check_level_intact(101.0, 0, df, "above", 1.0) == False  # noqa: E712
        # level=100.5：102 < 101.5 不成立 → 破位
        assert _check_level_intact(100.5, 0, df, "above", 1.0) == False  # noqa: E712
        # level=100：102 < 101 不成立 → 破位
        assert _check_level_intact(100.0, 0, df, "above", 1.0) == False  # noqa: E712

    def test_check_level_intact_above_direction_intact_when_far_below(self):
        from core.validation.scene.pattern_match import _check_level_intact
        df = self._small_df()
        # level 足够高（105 > 102+1）→ 未上破
        assert _check_level_intact(105.0, 0, df, "above", 1.0) == True  # noqa: E712

    def test_get_atr_nan_returns_none(self):
        from core.validation.scene.pattern_match import _get_atr
        atr = pd.Series([10.0, np.nan, 12.0])
        assert _get_atr(atr, 1) is None
        assert _get_atr(atr, 0) == 10.0
        assert _get_atr(atr, 99) is None  # 越界返回 None
        assert _get_atr(None, 0) is None

    def test_compute_avg_bar_length_out_of_range_returns_zero(self):
        from core.validation.scene.pattern_match import _compute_avg_bar_length
        arr = np.array([1.0, 2.0, 3.0])
        assert _compute_avg_bar_length(arr, 5, 10) == 0.0
        assert _compute_avg_bar_length(arr, 0, 2) == 2.0  # median([1,2,3])

    def test_validate_pivot_anchor_out_of_bounds_false(self):
        from core.validation.scene.pattern_match import _validate_pivot_anchor
        from core.validation.scene.pivot import PivotPoint
        df = self._small_df()
        assert _validate_pivot_anchor(PivotPoint(index=-1, price=100.0, pivot_type="peak"), df) == False  # noqa: E712
        assert _validate_pivot_anchor(PivotPoint(index=99, price=100.0, pivot_type="peak"), df) == False  # noqa: E712

    def test_validate_pivot_anchor_requires_ohlc_component_match(self):
        from core.validation.scene.pattern_match import _validate_pivot_anchor
        from core.validation.scene.pivot import PivotPoint
        df = self._small_df()
        # peak 锚定到 high（100.0）→ True；锚到 low（99.0）→ False
        assert _validate_pivot_anchor(PivotPoint(index=0, price=100.0, pivot_type="peak"), df) == True  # noqa: E712
        assert _validate_pivot_anchor(PivotPoint(index=0, price=99.0, pivot_type="peak"), df) == False  # noqa: E712
        assert _validate_pivot_anchor(PivotPoint(index=0, price=99.0, pivot_type="trough"), df) == True  # noqa: E712

    def test_pct_diff_zero_average_returns_zero(self):
        """现状记录：_pct_diff 当前无调用方（遗留函数），钉住其零均值行为。"""
        from core.validation.scene.pattern_match import _pct_diff
        assert _pct_diff(0.0, 0.0) == 0.0
        assert _pct_diff(100.0, 90.0) == pytest.approx(10.0 / 95.0 * 100)

    def test_height_pct_zero_trough_returns_zero(self):
        """现状记录：_height_pct 当前无调用方（遗留函数），钉住其零谷值行为。"""
        from core.validation.scene.pattern_match import _height_pct
        assert _height_pct(100.0, 0.0) == 0.0
        assert _height_pct(110.0, 100.0) == pytest.approx(10.0)


# ===========================================================================
# 8. scene_engine.run_scene_verification（基线 39%，主流程 L130-227 从未执行）
# ===========================================================================

class TestRunSceneVerificationFeature:
    """scene_engine.py 特征锚：主路径/空数据/日期过滤/子型过滤/MTF 分支。"""

    @staticmethod
    def _write_parquet(tmp_path, symbol="TESTUSDT", timeframe="4h", df=None):
        from core.data.storage import save_parquet
        df = df if df is not None else make_ohlcv(n=200, freq="4h")
        path = tmp_path / f"{symbol}_{timeframe}.parquet"
        save_parquet(df, path)
        return str(tmp_path)

    @staticmethod
    def _spiked_enhanced(raw, spike_bar=50, factor=100):
        """patch compute_all_indicators 的返回：注入量能异动制造 volume_spike 触发。"""
        enhanced = raw.copy()
        enhanced.iloc[spike_bar, enhanced.columns.get_loc("volume")] *= factor
        enhanced["atr_14"] = 100.0
        return enhanced

    def test_run_main_path_produces_triggers_and_statistics(self, tmp_path):
        """主路径：真 parquet 读取 → 指标注入 → 检测 → 前瞻统计 → 聚合。"""
        from core.validation.scene.scene_engine import run_scene_verification
        raw = make_ohlcv(n=200, freq="4h")
        data_dir = self._write_parquet(tmp_path, df=raw)
        enhanced = self._spiked_enhanced(raw)

        with patch(
            "core.validation.scene.scene_engine.compute_all_indicators",
            return_value=enhanced,
        ):
            result = run_scene_verification(
                symbol="TESTUSDT", timeframe="4h",
                scene_type="volume_spike", data_dir=data_dir,
            )

        assert result.scene_type == "volume_spike"
        assert result.total_triggers >= 1
        assert len(result.trigger_details) == result.total_triggers
        detail = result.trigger_details[0]
        assert detail["bar_index"] if "bar_index" in detail else True  # 详情键集合现状记录
        assert set(detail.keys()) >= {
            "id", "timestamp", "trigger_price", "indicator_snapshot",
            "pattern_subtype", "pattern_metadata", "forward_stats",
        }
        assert "6" in detail["forward_stats"]  # 默认 horizons [6,12,24,48]
        fs6 = detail["forward_stats"]["6"]
        assert {"close_pct", "max_gain_pct", "max_loss_pct",
                "bars_to_peak", "bars_to_trough", "is_partial"} <= set(fs6.keys())
        assert result.statistics_by_horizon, "聚合统计不应为空"
        agg = result.statistics_by_horizon[0]
        assert agg.total_triggers == result.total_triggers
        assert hasattr(agg, "win_rate") and hasattr(agg, "win_rate_ci")

    def test_run_empty_dataset_returns_warning(self, tmp_path):
        from core.validation.scene.scene_engine import run_scene_verification
        empty = pd.DataFrame(
            columns=["open", "high", "low", "close", "volume"],
            index=pd.DatetimeIndex([], name="timestamp"),
        )
        data_dir = self._write_parquet(tmp_path, df=empty)
        result = run_scene_verification(
            symbol="TESTUSDT", timeframe="4h",
            scene_type="volume_spike", data_dir=data_dir,
        )
        assert result.total_triggers == 0
        assert any("Empty dataset" in w for w in result.warnings)

    def test_run_date_range_without_data_returns_warning(self, tmp_path):
        from core.validation.scene.scene_engine import run_scene_verification
        data_dir = self._write_parquet(tmp_path)  # 数据在 2024-01 起
        result = run_scene_verification(
            symbol="TESTUSDT", timeframe="4h", scene_type="volume_spike",
            data_start="2025-06-01", data_end="2025-07-01", data_dir=data_dir,
        )
        assert result.total_triggers == 0
        assert any("No data in range" in w for w in result.warnings)

    def test_run_date_range_filters_input(self, tmp_path):
        """现状记录：data_start/data_end 对输入做边界包含过滤（>= / <=）。"""
        from core.validation.scene.scene_engine import run_scene_verification
        raw = make_ohlcv(n=200, freq="4h")
        data_dir = self._write_parquet(tmp_path, df=raw)
        enhanced = self._spiked_enhanced(raw)

        with patch(
            "core.validation.scene.scene_engine.compute_all_indicators",
            return_value=enhanced,
        ) as mock_ind:
            run_scene_verification(
                symbol="TESTUSDT", timeframe="4h", scene_type="volume_spike",
                data_start=str(raw.index[50]), data_dir=data_dir,
            )
        filtered = mock_ind.call_args[0][0]
        assert filtered.index[0] >= raw.index[50]

    def test_run_no_triggers_returns_warning(self, tmp_path):
        from core.validation.scene.scene_engine import run_scene_verification
        raw = make_ohlcv(n=200, freq="4h")
        data_dir = self._write_parquet(tmp_path, df=raw)

        with patch(
            "core.validation.scene.scene_engine.compute_all_indicators",
            return_value=raw,  # 无注入，随机数据下 volume_spike 未达阈值
        ):
            result = run_scene_verification(
                symbol="TESTUSDT", timeframe="4h",
                scene_type="volume_spike", data_dir=data_dir,
            )
        assert result.total_triggers == 0
        assert any("No scene occurrences" in w for w in result.warnings)

    @staticmethod
    def _double_top_raw():
        peaks = [(15, 110.0, 1000.0), (55, 110.0, 900.0)]
        troughs = [(35, 90.0)]
        return _make_pattern_df(peaks, troughs, n=80, decline_from=62, decline_to=85.0)

    def test_run_sub_pattern_filter_keeps_matching_subtype(self, tmp_path):
        from core.validation.scene.scene_engine import run_scene_verification
        raw = self._double_top_raw()
        data_dir = self._write_parquet(tmp_path, df=raw)
        with patch(
            "core.validation.scene.scene_engine.compute_all_indicators",
            side_effect=lambda df: df,
        ):
            result = run_scene_verification(
                symbol="TESTUSDT", timeframe="4h",
                scene_type="double_top", data_dir=data_dir,
            )
        assert result.total_triggers >= 1
        assert all(
            d["pattern_subtype"] == "double_top" for d in result.trigger_details
        )

    def test_run_sub_pattern_filter_drops_other_subtypes(self, tmp_path):
        """double_top 数据按 head_shoulders_top 请求 → 过滤后空 + 无场景告警。"""
        from core.validation.scene.scene_engine import run_scene_verification
        raw = self._double_top_raw()
        data_dir = self._write_parquet(tmp_path, df=raw)
        with patch(
            "core.validation.scene.scene_engine.compute_all_indicators",
            side_effect=lambda df: df,
        ):
            result = run_scene_verification(
                symbol="TESTUSDT", timeframe="4h",
                scene_type="head_shoulders_top", data_dir=data_dir,
            )
        assert result.total_triggers == 0
        assert any("No scene occurrences" in w for w in result.warnings)

    def test_run_cross_timeframe_mtf_failure_returns_warning(self, tmp_path):
        from core.validation.scene.scene_engine import run_scene_verification
        data_dir = self._write_parquet(tmp_path)

        def _boom(*args, **kwargs):
            raise RuntimeError("mtf data unavailable")

        with patch("core.validation.mtf.load_mtf_data", side_effect=_boom), \
             patch(
                 "core.validation.scene.scene_engine.compute_all_indicators",
                 side_effect=lambda df: df,
             ):
            result = run_scene_verification(
                symbol="TESTUSDT", timeframe="4h",
                scene_type="cross_timeframe", data_dir=data_dir,
            )
        assert result.total_triggers == 0
        assert any("MTF loading failed" in w for w in result.warnings)

    def test_run_cross_timeframe_mtf_merge_success(self, tmp_path):
        """现状记录：MTF 成功路径——高周期指标合并后交由检测器扫描。"""
        from core.validation.scene.scene_engine import run_scene_verification
        raw = make_ohlcv(n=200, freq="4h")
        data_dir = self._write_parquet(tmp_path, df=raw)

        def _fake_merge(base_df, mtf_data, base_minutes, tf_minutes_map):
            out = base_df.copy()
            n = len(out)
            fast = np.full(n, 99.0)
            fast[100:] = 101.0
            out["ema_20_1d"] = fast
            out["ema_50_1d"] = 100.0
            out["ema_20"] = 90.0
            return out

        with patch(
            "core.validation.mtf.load_mtf_data",
            return_value={"1d": make_ohlcv(n=60, freq="1d")},
        ), patch("core.validation.mtf.merge_to_base", side_effect=_fake_merge), \
             patch(
                 "core.validation.scene.scene_engine.compute_all_indicators",
                 side_effect=lambda df: df,
             ):
            result = run_scene_verification(
                symbol="TESTUSDT", timeframe="4h",
                scene_type="cross_timeframe", data_dir=data_dir,
            )
        assert result.total_triggers == 1
        assert not any("MTF loading failed" in w for w in result.warnings)

    def test_run_without_data_dir_uses_default_market_path(self):
        """现状记录：data_dir 缺省解析到仓库 data/market（L118 分支）。"""
        from core.validation.scene.scene_engine import run_scene_verification
        result = run_scene_verification(
            symbol="NOEXIST',;'SYMBOL",  # 含非法字符验证 safe_symbol 清洗现状
            timeframe="4h",
            scene_type="volume_spike",
        )
        assert result.total_triggers == 0
        assert any("No data found" in w for w in result.warnings)

    def test_run_unknown_timeframe_raises_research_error(self, tmp_path):
        """CARD-03 锚：未知 tf 结构化拒绝（旧实现静默空结果）。

        注意：API 端点 /api/validate/scene 对该错误的兜底属 CARD-05，
        此处只锚核心层行为。
        """
        from core.analysis.hypothesis_service import ResearchError
        from core.validation.scene.scene_engine import run_scene_verification
        with pytest.raises(ResearchError) as exc_info:
            run_scene_verification(
                symbol="TESTUSDT", timeframe="7h",
                scene_type="volume_spike", data_dir=str(tmp_path),
            )
        assert "timeframe 未知" in str(exc_info.value)

    def test_run_alias_fallback_detects_on_60m_file(self, tmp_path):
        """CARD-03 锚：只有 60m 文件时请求 1h 经别名兜底可检测。"""
        from core.validation.scene.scene_engine import run_scene_verification
        raw = make_ohlcv(n=200, freq="60min")
        from core.data.storage import save_parquet
        save_parquet(raw, tmp_path / "TESTUSDT_60m.parquet")
        enhanced = raw.copy()
        enhanced.iloc[50, enhanced.columns.get_loc("volume")] *= 100
        enhanced["atr_14"] = 100.0

        with patch(
            "core.validation.scene.scene_engine.compute_all_indicators",
            return_value=enhanced,
        ):
            result = run_scene_verification(
                symbol="TESTUSDT", timeframe="1h",
                scene_type="volume_spike", data_dir=str(tmp_path),
            )
        assert result.total_triggers >= 1


# ===========================================================================
# 9. forward_stats 补锚（基线 94%，空列表分支未执行）
# ===========================================================================

class TestForwardStatsFeature:
    """forward_stats.py 特征锚：空样本跳过、空分布。"""

    def test_aggregate_skips_horizon_with_empty_stats(self):
        from core.validation.scene.forward_stats import aggregate_by_horizon
        assert aggregate_by_horizon({6: []}) == []
        assert aggregate_by_horizon({6: [], 12: []}) == []

    def test_build_distribution_empty_returns_empty(self):
        from core.validation.scene.forward_stats import _build_distribution
        assert _build_distribution([]) == []

    def test_compute_forward_stats_zero_length_window_all_partial(self):
        """现状记录：触发点在末根时 end<=start，各 horizon 全额零值 + partial。"""
        from core.validation.scene.forward_stats import compute_forward_stats
        df = make_ohlcv(n=20, freq="4h")
        result = compute_forward_stats(df, trigger_bar=19, trigger_price=100.0, horizons=[6])
        s = result[6]
        assert s.is_partial is True
        assert s.close_pct == 0.0 and s.max_gain_pct == 0.0 and s.max_loss_pct == 0.0
        assert s.bars_to_peak == 0 and s.bars_to_trough == 0
