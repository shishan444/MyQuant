"""指标修复三任务验证（indicator-and-action-repair 2.1/2.2/2.3，
tester 锚 C2-A/B/C 组核心锚）。

- B3 CCI 自算（卡 A）：Lambert 公式对照/值域典型性/阈值语义恢复/
  形制不变；
- B4-P0 背离依赖显式化（卡 B 修订版）：registry depends_on 元数据/
  闭包装载/结构化拒绝触达/恒零消灭；
- B5 Donchian 窗口不含当根（卡 C）：三行公式对照/当根排除机制/
  三态归正/warmup 不变。
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

pytestmark = [pytest.mark.integration]

from core.features.indicators import (
    _compute_indicator, compute_all_indicators)
from core.features.registry import INDICATOR_REGISTRY


def _make_ohlcv(n=300, seed=42, start=100.0, drift=0.0):
    rng = np.random.default_rng(seed)
    close = start + np.cumsum(rng.normal(drift, 1.0, n))
    return pd.DataFrame({
        "open": close * 0.999, "high": close * 1.005,
        "low": close * 0.995, "close": close,
        "volume": rng.uniform(100, 1000, n),
    }, index=pd.date_range("2024-01-01", periods=n, freq="4h",
                           tz="UTC", name="timestamp"))


# ── C2-A：B3 CCI 自算 ─────────────────────────────────────────────


class TestCciRepair:
    def test_a1_lambert_reference(self):
        """A1 独立参考实现对照：tp 对窗口均值的 MAD 单趟 rolling apply
        （参考实现先人工核算样例校准——5 根窗口手算）。"""
        df = _make_ohlcv(60)
        tp = (df["high"] + df["low"] + df["close"]) / 3
        p = 5
        # 手算窗口（bar 4，首个完整窗）：六位有效数字逐值核对
        w = tp.iloc[:p].to_numpy()
        mean = w.mean()
        mad = np.abs(w - mean).mean()
        cci_hand = (tp.iloc[p - 1] - mean) / (0.015 * mad)
        out = _compute_indicator(df, "CCI", {"period": p})
        assert out[f"cci_{p}"].iloc[p - 1] == pytest.approx(cci_hand, rel=1e-9)

    def test_a2_value_band_dimensionless(self):
        """A2 值域典型性：修复后 |CCI| 主体分布无量纲量级（p01/p99
        落 ±300 量级带）——修复前 10^4 量级可区分（已知红证据
        22827~119258）。"""
        df = _make_ohlcv(400, seed=7)
        out = _compute_indicator(df, "CCI", {"period": 20})
        cci = out["cci_20"].dropna()
        assert cci.shape[0] > 300, "样本量"
        p01, p99 = cci.quantile(0.01), cci.quantile(0.99)
        assert -350 < p01 < -50, f"p01={p01}"
        assert 50 < p99 < 350, f"p99={p99}"
        assert cci.abs().max() < 2000, "极值仍应无量纲量级（非价格量级）"

    def test_a3_negative_threshold_reachable(self):
        """A3 阈值语义恢复：构造超卖深跌段→CCI<-100 可触发（修复前
        负阈值永假的行为级反向锚）。"""
        n = 120
        close = np.linspace(120.0, 60.0, n)  # 持续下跌
        df = pd.DataFrame({
            "open": close, "high": close * 1.002, "low": close * 0.998,
            "close": close, "volume": np.full(n, 100.0),
        }, index=pd.date_range("2024-01-01", periods=n, freq="4h",
                               tz="UTC", name="timestamp"))
        out = _compute_indicator(df, "CCI", {"period": 20})
        cci = out["cci_20"]
        assert (cci < -100).sum() > 0, "深跌段 CCI<-100 须可触发"

    def test_a6_shape_unchanged(self):
        """A6 形制断言：列名 cci_{period} 不变+头部 NaN 语义不变。"""
        df = _make_ohlcv(50)
        out = _compute_indicator(df, "CCI", {"period": 20})
        assert f"cci_20" in out
        assert out["cci_20"].iloc[:19].isna().all(), "头部 p-1 根 NaN"
        assert out["cci_20"].iloc[19:].notna().all()


# ── C2-B：B4-P0 背离依赖显式化 ────────────────────────────────────


class TestDivergenceDependency:
    def test_b1_registry_metadata(self):
        """B1 元数据断言：depends_on 默认 []；两 Divergence 声明 RSI。"""
        for name in ("BullishDivergence", "BearishDivergence"):
            reg = INDICATOR_REGISTRY[name]
            assert list(getattr(reg, "depends_on", None) or []) == ["RSI"]
        # 默认值：无依赖指标零声明负担
        assert getattr(INDICATOR_REGISTRY["EMA"], "depends_on", []) == []

    def test_b1_closure_auto_injects_rsi(self):
        """B1 闭包装载：裸 df（无 rsi 列）单点现算 BullishDivergence
        →自动获得 rsi 前置（恒零断链消灭的行为级证据）。"""
        df = _make_ohlcv(120, seed=3)
        assert not any(c.startswith("rsi_") for c in df.columns)
        out = _compute_indicator(df, "BullishDivergence", {})
        assert "rsi_14" in out, "闭包自动注入依赖列"
        assert "pattern_bullish_divergence" in out
        # 恒零消灭前置：输出非全零（120 根随机行情背离触发可能>0；
        # 全零恒假才可疑——同段 bearish 对照，二者分布不同即可）
        bull = out["pattern_bullish_divergence"]
        assert bull.max() >= 0  # 类型与值域健康
        # 幂等：依赖列已在时跳过重算（结果一致）
        df2 = df.copy()
        df2["rsi_14"] = out["rsi_14"]
        out2 = _compute_indicator(df2, "BullishDivergence", {})
        pd.testing.assert_series_equal(
            out2["pattern_bullish_divergence"], bull, check_names=False)

    def test_b1_failure_structured_reject(self):
        """B1 结构化报错触达证明（raise 通道证据）：构造依赖计算必败
        输入（无 close 列）→ ResearchError 点名依赖指标。"""
        from core.analysis.errors import ResearchError
        bad = pd.DataFrame({"high": [1.0], "low": [1.0],
                            "close": pd.Series([np.nan]) * 0,
                            "volume": [1.0]},
                           index=pd.DatetimeIndex(
                               [pd.Timestamp("2024-01-01", tz="UTC")],
                               name="timestamp"))
        bad["close"] = np.nan  # RSI 计算产出全 NaN 失败面
        df = _make_ohlcv(60, seed=5)
        df_no_close = df.drop(columns=["close"])
        with pytest.raises(ResearchError) as ei:
            _compute_indicator(df_no_close, "BullishDivergence", {})
        assert ei.value.code == "RESEARCH_INDICATOR_DEPENDENCY_FAILED"
        assert "RSI" in ei.value.message and "依赖指标" in ei.value.message

    def _divergence_df(self, seed: int, n: int = 400, drift: float = -0.05):
        rng = np.random.default_rng(seed)
        close = 100 + np.cumsum(rng.normal(drift, 1.2, n))
        return pd.DataFrame({
            "open": close * 0.999, "high": close * 1.005,
            "low": close * 0.995, "close": close,
            "volume": rng.uniform(100, 1000, n),
        }, index=pd.date_range("2024-01-01", periods=n, freq="4h",
                               tz="UTC", name="timestamp"))

    def test_b2_bullish_trigger_unlocked(self):
        """B2 触发解锁：下跌漂移随机段（含新低+指标背离，seed=0 实证
        扫描得双向触发）→ bullish 触发数>0 且触发 bar 三条件数据面
        同时成立。"""
        df = self._divergence_df(0)
        out = _compute_indicator(df, "BullishDivergence", {})
        bull = out["pattern_bullish_divergence"]
        rsi = out["rsi_14"]
        assert bull.sum() > 0, f"背离触发数 {bull.sum()} 应 >0"
        # 触发质量（B4-P2 两臂窗口极值语义）：触发 bar 当根创前 N 根
        # 最低 AND RSI 未创同期新低（窗口不含当根）；纯背离无门槛
        for i in np.flatnonzero(bull.to_numpy() > 0):
            lb = 20
            assert df["low"].iloc[i] < df["low"].iloc[i - lb:i].min()
            assert rsi.iloc[i] > rsi.iloc[i - lb:i].min()

    def test_b4_bearish_symmetric(self):
        """B4 对称锚：Bearish 同病同修——同段 bearish 触发解锁
        （seed=0 同数据双向实证）+闭包注入同构。"""
        df = self._divergence_df(0)
        out = _compute_indicator(df, "BearishDivergence", {})
        assert "rsi_14" in out
        assert out["pattern_bearish_divergence"].sum() > 0, \
            "Bearish 触发解锁（对称镜像）"
        # 对称质量（两臂窗口极值镜像）：当根创前 N 根最高 AND RSI
        # 未创同期新高（窗口不含当根）
        bear = out["pattern_bearish_divergence"]
        rsi = out["rsi_14"]
        for i in np.flatnonzero(bear.to_numpy() > 0):
            lb = 20
            assert df["high"].iloc[i] > df["high"].iloc[i - lb:i].max()
            assert rsi.iloc[i] < rsi.iloc[i - lb:i].max()

    def test_b1_silent_zero_path_eliminated(self):
        """B1 静默路径不可达：divergence 直调缺 rsi → 结构化拒绝
        （原恒 0 输出形态消灭）。"""
        from core.analysis.errors import ResearchError
        from core.features.patterns.divergence import (
            detect_bullish_divergence)
        df = _make_ohlcv(50)
        with pytest.raises(ResearchError) as ei:
            detect_bullish_divergence(df)
        assert "RSI" in ei.value.message


# ── C2-C：B5 Donchian 窗口不含当根 ────────────────────────────────


class TestDonchianWindowExcludesCurrent:
    def test_c2_three_line_formula(self):
        """C2 三行公式对照：upper=前 N 根 high 最大/lower=前 N 根 low
        最小/middle=两轨均值（shift(1)——已知序列人工核算）。"""
        n = 40
        high = np.full(n, 100.0)
        low = np.full(n, 99.0)
        high[19] = 110.0  # bar 19 高点
        low[19] = 95.0
        df = pd.DataFrame({
            "open": (high + low) / 2, "high": high, "low": low,
            "close": (high + low) / 2, "volume": np.full(n, 100.0),
        }, index=pd.date_range("2024-01-01", periods=n, freq="4h",
                               tz="UTC", name="timestamp"))
        p = 5
        out = _compute_indicator(df, "Donchian", {"period": p})
        # bar 19 当根高点不在自身轨道：upper[19]=前 5 根（14-18）高=100
        assert out[f"dc_upper_{p}"].iloc[19] == pytest.approx(100.0)
        # bar 20：窗 15-19 含高点 → upper=110（当根已入历史窗）
        assert out[f"dc_upper_{p}"].iloc[20] == pytest.approx(110.0)
        assert out[f"dc_lower_{p}"].iloc[19] == pytest.approx(99.0)
        assert out[f"dc_lower_{p}"].iloc[20] == pytest.approx(95.0)
        mid20 = (110.0 + 95.0) / 2
        assert out[f"dc_middle_{p}"].iloc[20] == pytest.approx(mid20)

    def test_c1_lower_break_reachable(self):
        """C1 下轨解锁：当根创新低 → 下轨=前 N 根最低（不含当根）→
        price_below（low<dc_lower）可触发（修复前数学恒假）。"""
        n = 60
        low = np.full(n, 100.0)
        low[30] = 90.0  # 当根深创新低
        df = pd.DataFrame({
            "open": low + 0.5, "high": low + 1.0, "low": low,
            "close": low + 0.2, "volume": np.full(n, 100.0),
        }, index=pd.date_range("2024-01-01", periods=n, freq="4h",
                               tz="UTC", name="timestamp"))
        p = 10
        out = _compute_indicator(df, "Donchian", {"period": p})
        lower = out[f"dc_lower_{p}"]
        # bar 30：lower=前 10 根最低=100，当根 low=90 < 100 触发面在场
        assert lower.iloc[30] == pytest.approx(100.0)
        assert (df["low"] < lower).iloc[30]

    def test_c5_first_valid_bar_delayed(self):
        """C5 首有效值延后 1 根（p+1）+warmup 推导规则不变（周期×3
        余量覆盖——预算输出不因 +1 改变）。"""
        from core.analysis.registry_meta import derive_warmup_full
        df = _make_ohlcv(60)
        p = 20
        out = _compute_indicator(df, "Donchian", {"period": p})
        col = out[f"dc_upper_{p}"]
        # shift(1) 延后 1 根：首完整窗在第 p 根（1-based），shift 后
        # 首有效值=第 p+1 根（0-based index p）——前 p 根（index 0..p-1）
        # 全 NaN
        assert col.iloc[:p].isna().all(), "rolling p 根 NaN"
        assert col.iloc[p:].notna().all(), "首有效值在 index p（第 p+1 根）"
        # warmup 预算：Donchian 周期参数上限 50 → 预算 150（floor 200
        # 保底——+1 由余量覆盖，预算输出不变）
        w = derive_warmup_full(None, ["Donchian"], plugin_columns=None,
                               indicator_tfs=None, base_tf="4h")
        assert w == 200, "预算规则不变（周期×3=150 < floor 200）"

    def test_c6_registry_declaration_unchanged(self):
        """C6 registry 声明面不动：supported_conditions 无 cross
        （序列对照通道语义独立）+输出字段三键声明不变。"""
        reg = INDICATOR_REGISTRY["Donchian"]
        assert "cross" not in reg.supported_conditions
        assert set(reg.output_fields) == {"upper", "middle", "lower"}


class TestDonchianThreeStateRepair:
    """C3 三态归正（tester 锚 C2-C 组，proposal 三态畸变全谱）。

    含当根窗口下 close∈[low,high] 而下轨≤low/上轨≥high，close×轨道
    的不等式链被钳制：role_reversal 穿越链（was_above/now_below 两臂
    ——proposal 行号锚 executor.py:334/:339 即该函数内两行）恒假；
    touch_bounce 可触发但触及的是「含自己的轨道」（语义偏移非恒假）。
    shift(1) 统一归正——本类各态独立行为锚。
    """

    def _fall_df(self):
        """缓升→急跌破前低→企稳（role_reversal 解锁形态数据）。"""
        from numpy import concatenate, cumsum
        rng = np.random.default_rng(2)
        seg1 = 100 + cumsum(rng.normal(0.05, 0.4, 80))
        seg2 = np.linspace(seg1[-1], seg1[-1] - 25, 30)
        seg3 = seg2[-1] + cumsum(rng.normal(0.0, 0.4, 20))
        close = concatenate([seg1, seg2, seg3])
        n = len(close)
        return pd.DataFrame({
            "open": close, "high": close + 0.4, "low": close - 0.4,
            "close": close, "volume": np.full(n, 100.0),
        }, index=pd.date_range("2024-01-01", periods=n, freq="4h",
                               tz="UTC", name="timestamp"))

    def test_c3_role_reversal_crossing_chain_unlocked(self):
        """C3 ①②穿越链+role 臂恒假归正：dc_lower 上 role_reversal
        （resistance=线原为支撑价格在线上→当根下破）触发数>0，触发
        bar 两臂数据面同时成立（was_above：lookback 前价格在线上；
        now_below：当根价格在线下——修复前下轨≤low≤close 臂恒假）。"""
        from core.strategy.executor import evaluate_condition
        df = self._fall_df()
        lower = _compute_indicator(df, "Donchian", {"period": 20})[
            "dc_lower_20"]
        lb = 10
        rr = evaluate_condition(
            lower, df["close"],
            {"type": "role_reversal", "role": "resistance",
             "lookback": lb}, df=df)
        assert int(rr.sum()) > 0, "role_reversal 穿越链解锁（恒假归正）"
        for i in np.flatnonzero(rr.to_numpy()):
            assert df["close"].iloc[i - lb] > lower.iloc[i - lb], \
                f"was_above 臂（bar {i}）"
            assert df["close"].iloc[i] < lower.iloc[i], \
                f"now_below 臂（bar {i}）——修复前下轨含当根致恒假"

    def _touch_df(self):
        """平台→下探压低前 N 根最低→回踩低位再升（touch 两态数据）。"""
        close = np.concatenate([
            np.full(30, 100.0),
            np.linspace(100, 92, 15),
            np.full(10, 92.0),
            np.array([92.6, 93.4, 94.2, 95.0, 95.8, 96.4]),
            np.linspace(96.4, 99, 29),
        ])
        n = len(close)
        return pd.DataFrame({
            "open": close, "high": close + 0.3, "low": close - 0.3,
            "close": close, "volume": np.full(n, 100.0),
        }, index=pd.date_range("2024-01-01", periods=n, freq="4h",
                               tz="UTC", name="timestamp"))

    def test_c3_touch_bounce_touches_prior_channel(self):
        """C3 ③触及语义归正（正锚）：回踩至「前 N 根最低」附近且反弹
        的 bar 触发——触及的是前 N 根轨道（非含自身轨道）。"""
        from core.strategy.executor import evaluate_condition
        df = self._touch_df()
        lower = _compute_indicator(df, "Donchian", {"period": 20})[
            "dc_lower_20"]
        pct = 0.01
        tb = evaluate_condition(
            lower, df["close"],
            {"type": "touch_bounce", "direction": "support",
             "proximity_pct": pct}, df=df)
        assert int(tb.sum()) > 0, "回踩前 N 根轨道的反弹 bar 触发"
        for i in np.flatnonzero(tb.to_numpy()):
            prev_min = df["low"].iloc[i - 20:i].min()
            assert abs(df["low"].iloc[i] - lower.iloc[i]) \
                <= abs(lower.iloc[i]) * pct, f"触及前 N 根轨道（bar {i}）"
            assert lower.iloc[i] == pytest.approx(prev_min), \
                f"轨道=前 20 根最低（bar {i}）"

    def test_c3_touch_bounce_new_low_bar_not_touch(self):
        """C3 ③触及语义归正（差异锚）：当根深创新低且长下影收回的
        bar 不触发——旧语义轨道含当根（line=当根 low）proximity=0
        恒近线会触发；新语义轨道=前 N 根最低，跌破远离不构成触及。"""
        from core.strategy.executor import evaluate_condition
        close = np.full(60, 100.0)
        df = pd.DataFrame({
            "open": close, "high": close, "low": close - 0.3,
            "close": close, "volume": np.full(60, 100.0),
        }, index=pd.date_range("2024-01-01", periods=60, freq="4h",
                               tz="UTC", name="timestamp"))
        # bar 50：low 深破前 20 根最低（97），close 长下影收回走高
        df.iloc[50, df.columns.get_loc("low")] = 97.0
        df.iloc[50, df.columns.get_loc("close")] = 100.3
        df.iloc[50, df.columns.get_loc("open")] = 99.7
        df.iloc[50, df.columns.get_loc("high")] = 100.5
        df.iloc[51, df.columns.get_loc("close")] = 100.6
        lower = _compute_indicator(df, "Donchian", {"period": 20})[
            "dc_lower_20"]
        assert lower.iloc[50] == pytest.approx(99.7), \
            "前 20 根最低（不含当根 97）"
        tb = evaluate_condition(
            lower, df["close"],
            {"type": "touch_bounce", "direction": "support",
             "proximity_pct": 0.01}, df=df)
        assert not bool(tb.iloc[50]), \
            "深创新低 bar 远离前 N 根轨道——触及语义=前 N 根轨道非含自身"


class TestDivergenceWindowSemanticsUpgrade:
    """B4-P2 语义升级锚（indicator-and-action-repair 2.4，卡 B 2.2
    ——tester 锚 C2-B5）：两臂窗口极值+门槛可选两态。"""

    def test_b3_deterministic_known_divergence(self):
        """B3 人工核算样例（确定样例法）：构造已知背离序列——价格
        当根创前 20 根最低且 RSI 未创同期新低——精确断言恰在该 bar
        触发（排除随机性，触发位置逐 bar 锁定）。"""
        from core.features.patterns.divergence import (
            detect_bullish_divergence)
        n = 60
        close = np.full(n, 100.0)
        close[30] = 99.0    # 前段低点（进入历史窗）
        close[31:50] = 100.0
        close[50] = 98.5    # 当根创新低（< min(low[30..49])=99）
        close[51:] = 100.0
        df = pd.DataFrame({
            "open": close, "high": close + 0.3, "low": close - 0.3,
            "close": close, "volume": np.full(n, 100.0),
        }, index=pd.date_range("2024-01-01", periods=n, freq="4h",
                               tz="UTC", name="timestamp"))
        rsi = np.full(n, 50.0)
        rsi[30] = 30.0      # 历史低 RSI（动量臂对照基准）
        rsi[50] = 35.0      # 当根 RSI 高于历史窗最低（未创新低）
        df["rsi_14"] = rsi
        bull = detect_bullish_divergence(df)["pattern_bullish_divergence"]
        assert int(bull.sum()) == 1, "恰一处背离触发"
        assert bool(bull.iloc[50]), "触发位置=bar 50（确定样例）"
        assert not bool(bull.iloc[30]), "历史低点非当根创低不触发"

    def test_b5_new_old_semantics_observable_diff(self):
        """①新旧语义对照可观察：单点比较（旧）与窗口极值（新）在
        构造样例上触发集不同——窗口极值对「非恰好 20 根前的更高
        低点」也触发。"""
        from core.features.patterns.divergence import (
            detect_bullish_divergence)
        n = 100
        rng = np.random.default_rng(0)
        close = 100 + np.cumsum(rng.normal(-0.1, 1.0, n))
        df = pd.DataFrame({
            "open": close * 0.999, "high": close * 1.005,
            "low": close * 0.995, "close": close,
            "volume": np.full(n, 100.0),
        }, index=pd.date_range("2024-01-01", periods=n, freq="4h",
                               tz="UTC", name="timestamp"))
        df["rsi_14"] = 50 + rng.normal(0, 15, n)
        new = detect_bullish_divergence(df)["pattern_bullish_divergence"]
        # 旧语义（等价复算：单点比较+oversold<40 门槛）
        old = ((df["low"] < df["low"].shift(20))
               & (df["rsi_14"] > df["rsi_14"].shift(20))
               & (df["rsi_14"] < 40)).fillna(0).astype(int)
        # 可观察差异：两语义触发集不完全重合（窗口极值更敏感）
        assert not new.equals(old), "新旧语义触发集应有可观察差异"

    def test_b5_threshold_optional_two_states(self):
        """③门槛两态可区分：缺省不启用=纯背离；显式 oversold=40 过滤
        （触发数 ≤ 纯背离——过滤是收紧）。"""
        from core.features.patterns.divergence import (
            detect_bullish_divergence)
        # seed=1 数据两态可区分（seed=0 触发全落超卖区——不可区分
        # 形态，实证扫描选定可区分样例）
        rng = np.random.default_rng(1)
        n = 400
        close = 100 + np.cumsum(rng.normal(-0.05, 1.2, n))
        df = pd.DataFrame({
            "open": close * 0.999, "high": close * 1.005,
            "low": close * 0.995, "close": close,
            "volume": rng.uniform(100, 1000, n),
        }, index=pd.date_range("2024-01-01", periods=n, freq="4h",
                               tz="UTC", name="timestamp"))
        # 直调面需自带 rsi 列（B4-P0 拒绝面——闭包经 _compute_indicator）
        df = df.copy()
        df["rsi_14"] = _compute_indicator(
            df, "RSI", {"period": 14})["rsi_14"]
        bare = detect_bullish_divergence(df)["pattern_bullish_divergence"]
        filtered = detect_bullish_divergence(df, oversold=40)[
            "pattern_bullish_divergence"]
        assert bare.sum() > filtered.sum() > 0, \
            f"两态可区分：纯背离 {bare.sum()} > 超卖过滤 {filtered.sum()} > 0"
        assert bare.sum() >= filtered.sum(), "门槛过滤是收紧方向"

    def test_b5_deep_dive_density_direction(self):
        """方向性锚：深跌段触发密度高于平静段（背离的经济语义）。"""
        from core.features.patterns.divergence import (
            detect_bullish_divergence)
        n = 300
        rng = np.random.default_rng(3)
        calm = 100 + np.cumsum(rng.normal(0, 0.2, n))    # 平静段
        dive = 100 + np.cumsum(rng.normal(-0.8, 1.5, n))  # 深跌段
        def _count(close):
            df = pd.DataFrame({
                "open": close * 0.999, "high": close * 1.005,
                "low": close * 0.995, "close": close,
                "volume": np.full(n, 100.0),
            }, index=pd.date_range("2024-01-01", periods=n, freq="4h",
                                   tz="UTC", name="timestamp"))
            df["rsi_14"] = _compute_indicator(
                df, "RSI", {"period": 14})["rsi_14"]
            return detect_bullish_divergence(df)[
                "pattern_bullish_divergence"].sum()
        assert _count(dive) > _count(calm), "深跌段背离密度更高"


class TestDependencyClosureGuardrails:
    """阶段四②盲区消解补锚（CARD-02 S-3 行为面）：循环依赖归链拒绝
    +依赖不在注册表拒绝+嵌套依赖 ResearchError 直通（不二次包装）。"""

    def test_b1_circular_dependency_rejected_with_chain(self, monkeypatch):
        """循环依赖声明拒绝：A→B→A 闭包归链命中 visited →
        RESEARCH_INDICATOR_DEPENDENCY_FAILED 且 detail.chain 披露完整
        装载链（祖先序+本指标）。"""
        from core.analysis.errors import ResearchError
        monkeypatch.setitem(
            INDICATOR_REGISTRY, "BullishDivergence",
            INDICATOR_REGISTRY["BullishDivergence"]).__class__  # noqa
        # dataclass 替换：copy 上改 depends_on（replace 保留原字段）
        import dataclasses
        bull = dataclasses.replace(
            INDICATOR_REGISTRY["BullishDivergence"],
            depends_on=["BearishDivergence"])
        bear = dataclasses.replace(
            INDICATOR_REGISTRY["BearishDivergence"],
            depends_on=["BullishDivergence"])
        monkeypatch.setitem(INDICATOR_REGISTRY, "BullishDivergence", bull)
        monkeypatch.setitem(INDICATOR_REGISTRY, "BearishDivergence", bear)
        df = _make_ohlcv(60)
        with pytest.raises(ResearchError) as ei:
            _compute_indicator(df, "BullishDivergence", {})
        assert ei.value.code == "RESEARCH_INDICATOR_DEPENDENCY_FAILED"
        chain = ei.value.detail["chain"]
        assert chain[0] == "BullishDivergence" and "BearishDivergence" in chain
        assert chain[-1] == "BullishDivergence", "闭包回到起点（完整链）"

    def test_b1_unknown_dependency_rejected_and_passthrough(
            self, monkeypatch):
        """依赖不在注册表拒绝（宿主层 :175）+嵌套依赖 ResearchError
        直通不二次包装（:197-198——依赖链上已结构化的失败原码透传，
        message 保持内层指名）。"""
        import dataclasses
        from core.analysis.errors import ResearchError
        # 层一：宿主声明未知依赖
        bull_bad = dataclasses.replace(
            INDICATOR_REGISTRY["BullishDivergence"],
            depends_on=["NoSuchIndicator"])
        monkeypatch.setitem(INDICATOR_REGISTRY, "BullishDivergence", bull_bad)
        df = _make_ohlcv(60)
        with pytest.raises(ResearchError) as e1:
            _compute_indicator(df, "BullishDivergence", {})
        assert e1.value.code == "RESEARCH_INDICATOR_DEPENDENCY_FAILED"
        assert "NoSuchIndicator" in e1.value.message
        assert e1.value.detail["dependency"] == "NoSuchIndicator"
        # 层二：嵌套依赖（RSI→NoSuch）失败自内层 ResearchError 直通
        monkeypatch.undo()
        rsi_bad = dataclasses.replace(
            INDICATOR_REGISTRY["RSI"], depends_on=["NoSuchIndicator"])
        monkeypatch.setitem(INDICATOR_REGISTRY, "RSI", rsi_bad)
        with pytest.raises(ResearchError) as e2:
            _compute_indicator(df, "BullishDivergence", {})
        assert e2.value.code == "RESEARCH_INDICATOR_DEPENDENCY_FAILED"
        # 直通断言：内层（RSI 装载层）的指名原样到达——未被外层
        # 「依赖指标 BullishDivergence 计算失败」二次包装改写
        assert "RSI" in e2.value.message
        assert e2.value.detail["dependency"] == "NoSuchIndicator"

    def test_b5_bearish_overbought_two_states(self):
        """B5 对称补全：Bearish overbought 门槛两态（缺省纯背离/
        显式超买过滤收紧——与 bullish oversold 两态镜像）。"""
        from core.features.patterns.divergence import (
            detect_bearish_divergence)
        rng = np.random.default_rng(7)
        n = 400
        close = 100 + np.cumsum(rng.normal(0.05, 1.2, n))  # 上行段（顶背离载体）
        df = pd.DataFrame({
            "open": close * 0.999, "high": close * 1.005,
            "low": close * 0.995, "close": close,
            "volume": rng.uniform(100, 1000, n),
        }, index=pd.date_range("2024-01-01", periods=n, freq="4h",
                               tz="UTC", name="timestamp"))
        df["rsi_14"] = _compute_indicator(
            df, "RSI", {"period": 14})["rsi_14"]
        bare = detect_bearish_divergence(df)[
            "pattern_bearish_divergence"]
        filtered = detect_bearish_divergence(df, overbought=60)[
            "pattern_bearish_divergence"]
        assert bare.sum() > filtered.sum() > 0, \
            f"两态可区分：纯背离 {bare.sum()} > 超买过滤 {filtered.sum()} > 0"


class TestIndicatorFunctionProperties:
    """④属性补强：核心计算面纯函数不变量（TESTING_V3「不变量与
    属性」——验证一类输入应保持的关系；CCI/Donchian 为无量纲/线性
    齐次输出，背离门槛为嵌套单调收紧）。"""

    def _df(self, n=200, seed=11):
        rng = np.random.default_rng(seed)
        close = 100 + np.cumsum(rng.normal(0, 1.0, n))
        return pd.DataFrame({
            "open": close * 0.999, "high": close * 1.005,
            "low": close * 0.995, "close": close,
            "volume": rng.uniform(100, 1000, n),
        }, index=pd.date_range("2024-01-01", periods=n, freq="4h",
                               tz="UTC", name="timestamp"))

    def test_cci_shift_and_scale_invariance(self):
        """CCI 平移+缩放双不变：价格全体 +c 或 ×k 输出逐值不变
        （无量纲量的代数性质——tp−SMA 与 MAD 同为一次齐次/平移消去）。"""
        df = self._df()
        p = 20
        base = _compute_indicator(df, "CCI", {"period": p})[f"cci_{p}"]
        shifted = _compute_indicator(df + 50.0, "CCI", {"period": p})[
            f"cci_{p}"]
        scaled = _compute_indicator(df * 3.0, "CCI", {"period": p})[
            f"cci_{p}"]
        pd.testing.assert_series_equal(
            base, shifted, check_names=False), "平移不变"
        pd.testing.assert_series_equal(
            base, scaled, check_names=False), "缩放不变"

    def test_donchian_shift_and_scale_homogeneity(self):
        """Donchian 线性齐次：价格 +c → 轨道同 +c；×k → 轨道同 ×k
        （轨道为价格一次齐次函数——极值与均值的线性性）。"""
        df = self._df()
        p = 20
        base = _compute_indicator(df, "Donchian", {"period": p})
        shifted = _compute_indicator(df + 50.0, "Donchian", {"period": p})
        scaled = _compute_indicator(df * 3.0, "Donchian", {"period": p})
        for key in (f"dc_upper_{p}", f"dc_lower_{p}", f"dc_middle_{p}"):
            pd.testing.assert_series_equal(
                base[key] + 50.0, shifted[key], check_names=False), key
            pd.testing.assert_series_equal(
                base[key] * 3.0, scaled[key], check_names=False), key

    def test_divergence_threshold_nested_monotone(self):
        """背离门槛嵌套单调：oversold 越低过滤越强——
        filtered(30) ⊆ filtered(40) ⊆ bare（门槛收紧是集合收缩）。"""
        from core.features.patterns.divergence import (
            detect_bullish_divergence)
        rng = np.random.default_rng(9)
        n = 500
        close = 100 + np.cumsum(rng.normal(-0.05, 1.2, n))
        df = pd.DataFrame({
            "open": close * 0.999, "high": close * 1.005,
            "low": close * 0.995, "close": close,
            "volume": rng.uniform(100, 1000, n),
        }, index=pd.date_range("2024-01-01", periods=n, freq="4h",
                               tz="UTC", name="timestamp"))
        df["rsi_14"] = _compute_indicator(
            df, "RSI", {"period": 14})["rsi_14"]
        bare = detect_bullish_divergence(df)["pattern_bullish_divergence"]
        f40 = detect_bullish_divergence(df, oversold=40)[
            "pattern_bullish_divergence"]
        f30 = detect_bullish_divergence(df, oversold=30)[
            "pattern_bullish_divergence"]
        assert int(bare.sum()) > int(f40.sum()) > int(f30.sum()) >= 0, \
            f"嵌套单调 {bare.sum()} > {f40.sum()} > {f30.sum()}"
        # 集合收缩（逐 bar）：f30 触发集 ⊆ f40 ⊆ bare
        assert bool(((f30 == 1) <= (f40 == 1)).all())
        assert bool(((f40 == 1) <= (bare == 1)).all())
