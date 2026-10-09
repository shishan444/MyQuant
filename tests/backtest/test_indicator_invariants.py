"""技术指标输出不变量守护（fix-indicator-output-mapping 阶段 0）。

断言-错位对应表（防止误删「看似多余」的断言）：

+--------------------+---------------------------------------------+
| 断言               | 能抓住的错位                                 |
+--------------------+---------------------------------------------+
| BB/KC/DC 序关系    | 上/下轨互换（BB、Keltner、Donchian）         |
| Aroon osc 恒等式   | up/down 互换（互换致恒等式符号翻转）         |
| MACD hist 恒等式   | 仅防公式回归；互换下代数自洽，抓不住互换     |
| MACD signal oracle | signal/histogram 互换（互换必失配）          |
| SMA oracle         | 均值公式回归                                 |
| 值域断言           | 公式错误/数据污染（抓不住互换，防其他病）    |
| 预热区断言         | 库升级悄悄改变 min_periods 行为              |
+--------------------+---------------------------------------------+

预热区（窗口未满的头部样本）不参与值域与恒等式断言：NaN 参与任何
比较均为 False，会把预热行误判为违反。MACD oracle 另需跳过 100 行
EMA 种子区——pandas_ta 的 ta.macd 与 ta.ema 对 NaN 头部的 EMA 种子
处理不同，偏差按 0.8/行几何衰减，100 行后收敛到浮点精度以下。
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pandas_ta as ta
import pytest

pytestmark = [pytest.mark.unit]

from core.features.indicators import _extract_outputs, compute_all_indicators


N_BARS = 300
# 确定窗口指标的最小预热长度（= 窗口参数；生成层默认参数下）
FIXED_WARMUP_BB = 20  # BB(20)
FIXED_WARMUP_KC = 20  # Keltner ema=20
FIXED_WARMUP_DC = 20  # Donchian(20)
FIXED_WARMUP_SMA = 20  # SMA(20)


def make_ohlcv(n: int = N_BARS, seed: int = 42) -> pd.DataFrame:
    """种子固定的合成 OHLCV，保证 low<=min(o,c)<=max(o,c)<=high。"""
    rng = np.random.default_rng(seed)
    idx = pd.date_range("2026-01-01", periods=n, freq="h")
    close = pd.Series(np.abs(np.cumsum(rng.standard_normal(n))) + 100, index=idx)
    spread = pd.Series(np.abs(rng.standard_normal(n)), index=idx) + 0.1
    high = close + spread
    low = close - spread
    open_ = (close + rng.standard_normal(n) * 0.1).clip(lower=low, upper=high)
    return pd.DataFrame(
        {
            "open": open_,
            "high": high,
            "low": low,
            "close": close,
            "volume": pd.Series(rng.random(n) * 1000, index=idx),
        },
        index=idx,
    )


@pytest.fixture(scope="module")
def enhanced() -> pd.DataFrame:
    """模块级共享：同数据全断言可比。"""
    return compute_all_indicators(make_ohlcv())


def _first_valid(s: pd.Series) -> int:
    """首个非 NaN 位置（int 位置），全 NaN 返回 -1。"""
    nz = s.notna()
    return int(nz.to_numpy().nonzero()[0][0]) if nz.any() else -1


class TestSyntheticData:
    """合成数据自检：坏数据不进被测链路。"""

    def test_ohlcv_relations(self):
        df = make_ohlcv()
        assert (df["low"] <= df[["open", "close"]].min(axis=1)).all()
        assert (df[["open", "close"]].max(axis=1) <= df["high"]).all()
        assert df.index.is_monotonic_increasing
        assert not df[["open", "high", "low", "close", "volume"]].isna().any().any()

    def test_reproducible(self):
        assert make_ohlcv(seed=7)["close"].equals(make_ohlcv(seed=7)["close"])


class TestChannelOrdering:
    """通道指标序关系：upper>=middle>=lower，抓上/下轨互换。"""

    def test_bb_ordering(self, enhanced):
        valid = enhanced["bb_middle_20_2"].notna()
        u = enhanced["bb_upper_20_2"][valid]
        m = enhanced["bb_middle_20_2"][valid]
        l = enhanced["bb_lower_20_2"][valid]
        assert (u >= m).all()
        assert (m >= l).all()

    def test_keltner_ordering(self, enhanced):
        valid = enhanced["kc_20_10_2.0_middle"].notna()
        u = enhanced["kc_20_10_2.0_upper"][valid]
        m = enhanced["kc_20_10_2.0_middle"][valid]
        l = enhanced["kc_20_10_2.0_lower"][valid]
        assert (u >= m).all()
        assert (m >= l).all()

    def test_donchian_ordering(self, enhanced):
        valid = enhanced["dc_middle_20"].notna()
        u = enhanced["dc_upper_20"][valid]
        m = enhanced["dc_middle_20"][valid]
        l = enhanced["dc_lower_20"][valid]
        assert (u >= m).all()
        assert (m >= l).all()

    def test_donchian_middle_identity(self, enhanced):
        """DC middle 恒等式：仅防公式回归（互换下自洽，靠序关系抓）。"""
        recomputed = (enhanced["dc_upper_20"] + enhanced["dc_lower_20"]) / 2
        diff = (enhanced["dc_middle_20"] - recomputed).abs().dropna()
        assert diff.max() < 1e-9


class TestAroonIdentity:
    """Aroon 恒等式：osc == up - down，up/down 互换致符号翻转。"""

    def test_osc_identity(self, enhanced):
        diff = (
            enhanced["aroon_osc_25"]
            - (enhanced["aroon_up_25"] - enhanced["aroon_down_25"])
        ).abs().dropna()
        assert diff.max() < 1e-9


class TestMacdOracle:
    """MACD 断言：hist 恒等式防公式回归，signal oracle 抓互换。"""

    def test_hist_identity(self, enhanced):
        """hist == macd - signal；互换下代数自洽，此断言抓不住互换。"""
        recomputed = enhanced["macd_12_26_9"] - enhanced["macd_signal_12_26_9"]
        diff = (enhanced["macd_histogram_12_26_9"] - recomputed).abs().dropna()
        assert diff.max() < 1e-9

    def test_signal_is_ema_of_macd(self, enhanced):
        """oracle：signal == EMA9(macd)，跳过 EMA 种子区后精确相等。

        oracle 与被测共享 pandas_ta（同库异路径）：本测试对象是列
        映射而非数值算法，只需与位置取列异构，互换时必失配。
        """
        ema9 = ta.ema(enhanced["macd_12_26_9"], length=9)
        diff = (enhanced["macd_signal_12_26_9"] - ema9).abs().dropna()
        assert len(diff) > 0
        # EMA9 衰减因子 0.8/行，种子差经 100 行收敛到浮点精度以下
        assert diff.iloc[100:].max() < 1e-9

    def test_signal_not_swapped_with_histogram(self, enhanced):
        """互换直接证据：signal 与 hist 逐值不等（互换时 hist==旧 signal）。"""
        swapped = (
            enhanced["macd_signal_12_26_9"] == enhanced["macd_histogram_12_26_9"]
        ).dropna()
        assert not swapped.all()


class TestSmaOracle:
    """SMA oracle：与滚动均值逐值相等，且必在窗口极值内。"""

    def test_sma_matches_rolling_mean(self, enhanced):
        df = make_ohlcv()
        manual = df["close"].rolling(20).mean()
        diff = (enhanced["sma_20"] - manual).abs().dropna()
        assert len(diff) > 0
        assert diff.max() < 1e-9

    def test_sma_within_window_extremes(self, enhanced):
        df = make_ohlcv()
        wmin = df["close"].rolling(20).min()
        wmax = df["close"].rolling(20).max()
        valid = enhanced["sma_20"].notna()
        assert (enhanced["sma_20"][valid] >= wmin[valid] - 1e-9).all()
        assert (enhanced["sma_20"][valid] <= wmax[valid] + 1e-9).all()


class TestValueRanges:
    """值域断言：抓公式错误/数据污染（抓不住互换，防其他病）。"""

    def test_rsi_range(self, enhanced):
        s = enhanced["rsi_14"].dropna()
        assert ((s >= 0) & (s <= 100)).all()

    def test_willr_range(self, enhanced):
        s = enhanced["willr_14"].dropna()
        assert ((s >= -100) & (s <= 0)).all()

    def test_stoch_range(self, enhanced):
        for col in ["stoch_k_14_3", "stoch_d_14_3"]:
            s = enhanced[col].dropna()
            assert ((s >= 0) & (s <= 100)).all(), col

    def test_atr_nonnegative(self, enhanced):
        assert (enhanced["atr_14"].dropna() >= 0).all()

    def test_adx_range(self, enhanced):
        s = enhanced["adx_14"].dropna()
        assert ((s >= 0) & (s <= 100)).all()

    def test_cmo_range(self, enhanced):
        s = enhanced["cmo_14"].dropna()
        assert ((s >= -100) & (s <= 100)).all()

    def test_aroon_range(self, enhanced):
        for col in ["aroon_up_25", "aroon_down_25"]:
            s = enhanced[col].dropna()
            assert ((s >= 0) & (s <= 100)).all(), col

    def test_rvol_nonnegative(self, enhanced):
        assert (enhanced["rvol_20"].dropna() >= 0).all()


class TestWarmupBoundary:
    """预热区界定：确定窗口指标断言固定预热全 NaN，RMA 类动态探测。

    固定断言把库的窗口行为固化为契约，库升级改变 min_periods 时
    此处变红即暴露；RMA 类（递归均值）预热极短且非项目契约，仅
    断言存在有效起点。
    """

    def test_fixed_window_warmup_all_nan(self, enhanced):
        for col, warmup in [
            ("bb_upper_20_2", FIXED_WARMUP_BB),
            ("kc_20_10_2.0_upper", FIXED_WARMUP_KC),
            ("dc_upper_20", FIXED_WARMUP_DC),
            ("sma_20", FIXED_WARMUP_SMA),
        ]:
            assert enhanced[col].iloc[: warmup - 1].isna().all(), col
            assert enhanced[col].iloc[warmup - 1:].notna().all(), col

    def test_rma_indicators_have_valid_start(self, enhanced):
        """RMA 类（RSI/CMO/ADX）不硬编码预热契约，只探测有效起点。

        pandas_ta 0.4.x 下 RSI 首个非 NaN 位置极小（部分窗口值），
        记录现状但不固化为契约；统一 NaN 化另行立项（design Non-Goals）。
        """
        for col in ["rsi_14", "cmo_14", "adx_14"]:
            assert _first_valid(enhanced[col]) >= 0, col


class TestExtractOutputsFailFast:
    """取列失败显式化：缺失分量抛 ValueError，不静默错位。"""

    def test_missing_prefix_raises(self):
        df = pd.DataFrame({"BBL_20_2": [1.0], "BBM_20_2": [2.0]})
        with pytest.raises(ValueError, match="upper"):
            _extract_outputs(
                df, {"upper": "BBU_", "middle": "BBM_", "lower": "BBL_"}
            )

    def test_prefix_extraction(self):
        df = pd.DataFrame({"BBB_20_2": [0.1], "BBL_20_2": [1.0], "BBM_20_2": [2.0], "BBU_20_2": [3.0]})
        out = _extract_outputs(
            df, {"upper": "BBU_", "middle": "BBM_", "lower": "BBL_"}
        )
        assert out["upper"].iloc[0] == 3.0
        assert out["middle"].iloc[0] == 2.0
        assert out["lower"].iloc[0] == 1.0


class TestContractConsistency:
    """列名契约一致性：生成层/registry/消费层三层逐项相等。

    同一语义列名在 indicators.py（生成）、registry.resolve_indicator_column
    （解析）、factors.py（硬编码）、volume_breakout（场景引用）必须一致；
    命名分裂曾使 fuzzy 兜底把 bb_lower 解析到 bb_upper 列。
    """

    # registry (指标名, 参数, 语义分量) -> 期望列名（= 生成层规则）
    REGISTRY_CASES = [
        ("BB", {"period": 20, "std": 2.0}, "upper", "bb_upper_20_2"),
        ("BB", {"period": 20, "std": 2.0}, "middle", "bb_middle_20_2"),
        ("BB", {"period": 20, "std": 2.0}, "lower", "bb_lower_20_2"),
        ("Keltner", {"ema_period": 20, "atr_period": 10, "multiplier": 2.0},
         "upper", "kc_20_10_2.0_upper"),
        ("Keltner", {"ema_period": 20, "atr_period": 10, "multiplier": 2.0},
         "lower", "kc_20_10_2.0_lower"),
        ("Donchian", {"period": 20}, "upper", "dc_upper_20"),
        ("Donchian", {"period": 20}, "lower", "dc_lower_20"),
        ("MACD", {"fast": 12, "slow": 26, "signal": 9}, "signal",
         "macd_signal_12_26_9"),
        ("MACD", {"fast": 12, "slow": 26, "signal": 9}, "histogram",
         "macd_histogram_12_26_9"),
        ("Aroon", {"period": 25}, "aroon_up", "aroon_up_25"),
        ("Aroon", {"period": 25}, "aroon_down", "aroon_down_25"),
    ]

    def test_registry_columns_exist_in_generated(self, enhanced):
        """registry 解析的列名必须真实存在于生成层产出。"""
        from core.features.registry import resolve_indicator_column

        for name, params, field, expect in self.REGISTRY_CASES:
            got = resolve_indicator_column(name, params, field)
            assert got == expect, f"{name}.{field}: registry={got} expect={expect}"
            assert expect in enhanced.columns, f"{expect} 未由生成层产出"

    def test_factors_hardcoded_columns_exist(self, enhanced):
        """factors.py 硬编码引用的列名与生成层一致。"""
        for col in ["bb_upper_20_2", "bb_lower_20_2", "atr_14"]:
            assert col in enhanced.columns, col

    def test_volume_breakout_levels_distinct(self, enhanced):
        """场景层解析：bb/keltner 的 upper 与 lower 必须解析到不同列。"""
        from core.validation.scene.volume_breakout import _resolve_level_column

        cols = enhanced.columns
        for upper_lt, lower_lt in [
            ("bb_upper", "bb_lower"),
            ("keltner_upper", "keltner_lower"),
        ]:
            upper_col = _resolve_level_column(upper_lt, cols)
            lower_col = _resolve_level_column(lower_lt, cols)
            assert upper_col is not None and lower_col is not None, upper_lt
            assert upper_col != lower_col, f"{upper_lt} 与 {lower_lt} 解析到同一列"
            # 解析到的列语义正确：upper 列值 >= lower 列值（预热区外）
            valid = enhanced[upper_col].notna()
            assert (
                enhanced[upper_col][valid] >= enhanced[lower_col][valid]
            ).all(), upper_lt
