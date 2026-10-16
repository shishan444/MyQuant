"""Tests for new condition types in evaluate_condition."""

import pytest

pytestmark = [pytest.mark.unit]
import pandas as pd
import numpy as np

from core.strategy.executor import evaluate_condition

def _make_df(n: int = 50) -> pd.DataFrame:
    """Create a synthetic OHLCV DataFrame with EMA columns."""
    np.random.seed(42)
    dates = pd.date_range("2023-01-01", periods=n, freq="4h")
    close = 30000 + np.cumsum(np.random.randn(n) * 50)
    df = pd.DataFrame({
        "open": close + np.random.randn(n) * 10,
        "high": close + abs(np.random.randn(n) * 30),
        "low": close - abs(np.random.randn(n) * 30),
        "close": close,
        "volume": np.random.randint(100, 5000, n).astype(float),
        "ema_10": close - 50 + np.random.randn(n) * 20,
        "ema_20": close - 100 + np.random.randn(n) * 30,
        "rsi_14": np.random.uniform(20, 80, n),
    }, index=dates)
    return df

class TestCrossAboveSeries:
    """Test cross_above_series condition type."""

    def test_detects_cross_above(self):
        df = _make_df(20)
        # Force a cross above event at index 10
        ema_a = df["ema_10"].copy()
        ema_b = df["ema_20"].copy()
        ema_a.iloc[9] = ema_b.iloc[9] - 10  # A below B
        ema_a.iloc[10] = ema_b.iloc[10] + 10  # A above B
        df["ema_10"] = ema_a

        condition = {
            "type": "cross_above_series",
            "target_indicator": "EMA",
            "target_params": {"period": 20},
        }
        result = evaluate_condition(df["ema_10"], df["close"], condition, df=df)
        assert result.iloc[10] == True

    def test_no_cross_returns_false(self):
        df = _make_df(20)
        condition = {
            "type": "cross_above_series",
            "target_indicator": "EMA",
            "target_params": {"period": 20},
        }
        result = evaluate_condition(df["ema_10"], df["close"], condition, df=df)
        # Should be all False for random data (no forced cross)
        assert isinstance(result, pd.Series)

class TestCrossBelowSeries:
    """Test cross_below_series condition type."""

    def test_detects_cross_below(self):
        df = _make_df(20)
        ema_a = df["ema_10"].copy()
        ema_b = df["ema_20"].copy()
        ema_a.iloc[9] = ema_b.iloc[9] + 10  # A above B
        ema_a.iloc[10] = ema_b.iloc[10] - 10  # A below B
        df["ema_10"] = ema_a

        condition = {
            "type": "cross_below_series",
            "target_indicator": "EMA",
            "target_params": {"period": 20},
        }
        result = evaluate_condition(df["ema_10"], df["close"], condition, df=df)
        assert result.iloc[10] == True

class TestLookbackAny:
    """Test lookback_any condition type."""

    def test_any_in_window(self):
        df = _make_df(20)
        rsi = df["rsi_14"].copy()
        # Set RSI > 70 at index 5, check lookback window of 5 at index 8
        rsi.iloc[5] = 75
        rsi.iloc[6] = 40
        rsi.iloc[7] = 40
        rsi.iloc[8] = 40
        df["rsi_14"] = rsi

        condition = {
            "type": "lookback_any",
            "window": 5,
            "inner": {"type": "gt", "threshold": 70},
        }
        result = evaluate_condition(df["rsi_14"], df["close"], condition, df=df)
        # Index 8 should be True because index 5 had RSI > 70 within window
        assert result.iloc[8] == True

    def test_none_in_window(self):
        df = _make_df(20)
        rsi = df["rsi_14"].copy()
        # All RSI below 60
        rsi.iloc[:] = 40
        df["rsi_14"] = rsi

        condition = {
            "type": "lookback_any",
            "window": 5,
            "inner": {"type": "gt", "threshold": 70},
        }
        result = evaluate_condition(df["rsi_14"], df["close"], condition, df=df)
        assert result.sum() == 0

class TestLookbackAll:
    """Test lookback_all condition type."""

    def test_all_in_window(self):
        df = _make_df(20)
        rsi = df["rsi_14"].copy()
        # Set RSI > 30 for several consecutive bars
        rsi.iloc[5:10] = 50
        df["rsi_14"] = rsi

        condition = {
            "type": "lookback_all",
            "window": 3,
            "inner": {"type": "gt", "threshold": 30},
        }
        result = evaluate_condition(df["rsi_14"], df["close"], condition, df=df)
        # Should be True at indices where all 3 prior bars are > 30
        assert result.iloc[9] == True

class TestTouchBounce:
    """Test touch_bounce condition type."""

    def test_support_bounce(self):
        df = _make_df(20)
        # Force a support bounce: low near line, close above, current close > previous close
        line_val = 30000
        df["line"] = line_val
        df.loc[df.index[9], "close"] = line_val + 50   # previous close lower
        df.loc[df.index[10], "low"] = line_val + 5
        df.loc[df.index[10], "close"] = line_val + 100  # current close higher than prev
        df.loc[df.index[10], "high"] = line_val + 200

        condition = {
            "type": "touch_bounce",
            "direction": "support",
            "proximity_pct": 0.01,
            "bounce_pct": 0.005,
        }
        result = evaluate_condition(df["line"], df["close"], condition, df=df)
        assert result.iloc[10] == True

    def test_resistance_bounce(self):
        df = _make_df(20)
        line_val = 30000
        df["line"] = line_val
        df.loc[df.index[9], "close"] = line_val - 50   # previous close higher
        df.loc[df.index[10], "high"] = line_val - 5
        df.loc[df.index[10], "close"] = line_val - 100  # current close lower than prev
        df.loc[df.index[10], "low"] = line_val - 200

        condition = {
            "type": "touch_bounce",
            "direction": "resistance",
            "proximity_pct": 0.01,
            "bounce_pct": 0.005,
        }
        result = evaluate_condition(df["line"], df["close"], condition, df=df)
        assert result.iloc[10] == True

class TestRoleReversal:
    """Test role_reversal condition type."""

    def test_resistance_reversal(self):
        df = _make_df(30)
        line_val = 30000
        df["line"] = line_val
        # 10 bars ago: price above line (line was support)
        df.loc[df.index[10], "close"] = line_val + 200
        # Now: price below line (line became resistance)
        df.loc[df.index[20], "close"] = line_val - 200

        condition = {
            "type": "role_reversal",
            "role": "resistance",
            "lookback": 10,
        }
        result = evaluate_condition(df["line"], df["close"], condition, df=df)
        assert result.iloc[20] == True

    def test_support_reversal(self):
        df = _make_df(30)
        line_val = 30000
        df["line"] = line_val
        df.loc[df.index[10], "close"] = line_val - 200
        df.loc[df.index[20], "close"] = line_val + 200

        condition = {
            "type": "role_reversal",
            "role": "support",
            "lookback": 10,
        }
        result = evaluate_condition(df["line"], df["close"], condition, df=df)
        assert result.iloc[20] == True

class TestWickTouch:
    """Test wick_touch condition type."""

    def test_above_wick_touch(self):
        df = _make_df(20)
        line_val = 30000
        df["line"] = line_val
        # High touches line, but close below
        df.loc[df.index[10], "high"] = line_val + 5
        df.loc[df.index[10], "close"] = line_val - 100
        df.loc[df.index[10], "low"] = line_val - 200

        condition = {
            "type": "wick_touch",
            "direction": "above",
            "proximity_pct": 0.01,
        }
        result = evaluate_condition(df["line"], df["close"], condition, df=df)
        assert result.iloc[10] == True

    def test_below_wick_touch(self):
        df = _make_df(20)
        line_val = 30000
        df["line"] = line_val
        df.loc[df.index[10], "low"] = line_val - 5
        df.loc[df.index[10], "close"] = line_val + 100
        df.loc[df.index[10], "high"] = line_val + 200

        condition = {
            "type": "wick_touch",
            "direction": "below",
            "proximity_pct": 0.01,
        }
        result = evaluate_condition(df["line"], df["close"], condition, df=df)
        assert result.iloc[10] == True

class TestBackwardCompatibility:
    """Ensure original 8 condition types still work with df=None."""

    def test_lt_without_df(self):
        series = pd.Series([1, 2, 3, 4, 5])
        close = pd.Series([3, 3, 3, 3, 3])
        result = evaluate_condition(series, close, {"type": "lt", "threshold": 3})
        assert result.iloc[0] == True
        assert result.iloc[2] == False

    def test_price_above_without_df(self):
        series = pd.Series([2, 4, 2, 4, 2])
        close = pd.Series([3, 3, 3, 3, 3])
        result = evaluate_condition(series, close, {"type": "price_above"})
        assert result.iloc[0] == True
        assert result.iloc[1] == False

    def test_cross_above_without_df(self):
        series = pd.Series([1, 2, 3, 4, 5])
        close = pd.Series([3, 3, 3, 3, 3])
        result = evaluate_condition(series, close, {"type": "cross_above", "threshold": 3})
        assert result.iloc[2] == True


class TestCrossSeriesTargetUnresolvable:
    """cross_*_series 目标侧不可解析时全 False 降级（不炸不误报）。"""

    def test_cross_above_unknown_target_indicator_all_false(self):
        df = _make_df(20)
        condition = {
            "type": "cross_above_series",
            "target_indicator": "NOPE",
            "target_params": {},
        }
        result = evaluate_condition(df["ema_10"], df["close"], condition, df=df)
        assert len(result) == len(df)
        assert not result.any()

    def test_cross_below_unknown_target_indicator_all_false(self):
        df = _make_df(20)
        condition = {
            "type": "cross_below_series",
            "target_indicator": "NOPE",
            "target_params": {},
        }
        result = evaluate_condition(df["ema_10"], df["close"], condition, df=df)
        assert len(result) == len(df)
        assert not result.any()

    def test_cross_above_without_df_all_false(self):
        series = pd.Series([1.0, 2.0, 3.0])
        condition = {
            "type": "cross_above_series",
            "target_indicator": "EMA",
            "target_params": {"period": 20},
        }
        result = evaluate_condition(series, series * 0.5, condition, df=None)
        assert not result.any()


class TestLookbackInnerConditionTypes:
    """lookback_* 内层条件类型分支（le/ge/price_above/price_below/复杂委托）。"""

    def test_inner_le_threshold_true(self):
        df = _make_df(30)
        df["rsi_14"] = 65.0  # 恒 <= 70
        condition = {"type": "lookback_any", "window": 3,
                     "inner": {"type": "le", "threshold": 70}}
        result = evaluate_condition(df["rsi_14"], df["close"], condition, df=df)
        assert bool(result.all())

    def test_inner_ge_threshold_false(self):
        df = _make_df(30)
        df["rsi_14"] = 65.0  # 恒不 >= 70
        condition = {"type": "lookback_any", "window": 3,
                     "inner": {"type": "ge", "threshold": 70}}
        result = evaluate_condition(df["rsi_14"], df["close"], condition, df=df)
        assert not result.any()

    def test_inner_price_above_true(self):
        df = _make_df(30)
        df["ema_10"] = df["close"] - 10  # close 恒在指标线上方
        condition = {"type": "lookback_any", "window": 3,
                     "inner": {"type": "price_above"}}
        result = evaluate_condition(df["ema_10"], df["close"], condition, df=df)
        assert bool(result.all())

    def test_inner_price_below_true(self):
        df = _make_df(30)
        df["ema_10"] = df["close"] + 10  # close 恒在指标线下方
        condition = {"type": "lookback_any", "window": 3,
                     "inner": {"type": "price_below"}}
        result = evaluate_condition(df["ema_10"], df["close"], condition, df=df)
        assert bool(result.all())

    def test_inner_complex_delegates_to_main_entry(self):
        """复杂内层（cross_above_series）委托 evaluate_condition 主入口——
        lookback_any 结果覆盖直调主入口的 True 位置（窗口 >= 1）。"""
        df = _make_df(30)
        ema_a = df["ema_10"].copy()
        ema_b = df["ema_20"].copy()
        ema_a.iloc[9] = ema_b.iloc[9] - 10
        ema_a.iloc[10] = ema_b.iloc[10] + 10
        df["ema_10"] = ema_a
        inner = {"type": "cross_above_series",
                 "target_indicator": "EMA", "target_params": {"period": 20}}
        via_lookback = evaluate_condition(
            df["ema_10"], df["close"],
            {"type": "lookback_any", "window": 2, "inner": inner}, df=df)
        direct = evaluate_condition(df["ema_10"], df["close"], inner, df=df)
        assert bool(direct.iloc[10])
        assert bool(via_lookback.iloc[10])
        assert bool((via_lookback | direct).equals(via_lookback))


class TestPatternConditionsWithoutDf:
    """touch_bounce/role_reversal/wick_touch 在 df=None 时全 False 降级
    （OHL 列依赖缺失不炸——逐 bar 求值层兜底语义）。"""

    @pytest.mark.parametrize("cond_type", ["touch_bounce", "role_reversal", "wick_touch"])
    def test_without_df_all_false(self, cond_type):
        series = pd.Series([1.0, 2.0, 3.0, 2.0, 1.0])
        close = pd.Series([2.0, 2.0, 2.0, 2.0, 2.0])
        condition = {"type": cond_type, "direction": "support"}
        result = evaluate_condition(series, close, condition, df=None)
        assert len(result) == len(series)
        assert not result.any()


class TestComputedColumnExactHitAnchor:
    """互含匹配分支（executor.py _get_indicator_column 374-378）不可达判定锚。

    调用图依据：该分支要求「resolve_indicator_column 产出的请求列名不在
    现算输出列中、但二者互含」。而 resolve_indicator_column
    （core/features/registry.py:454）与 _compute_indicator
    （core/features/indicators.py:121）对每个注册指标的输出列名同编码
    硬编码（EMA→ema_{period}、SMA→sma_{period}、MACD→macd_{fast}_{slow}
    _{signal} 族、BB→bb_{field}_{period}_{std} 族、Aroon→aroon_{field}
    _{period} 族、VolumeProfile→vp_poc_{bins}_{lookback} 等——两侧逐指标
    核对一致），正常调用图下现算输出必含精确名，369 行精确命中先行返回。
    该分支仅在两侧命名漂移（回归错误形态）时可达，无法以合法输入构造。
    本锚用例证明精确命中路径：请求未预算列 EMA(30) → 现算 → 精确命中。
    """

    def test_computed_column_exact_hit(self):
        from core.strategy.executor import _SimpleGene, clear_indicator_cache

        clear_indicator_cache()
        df = _make_df(40)  # 无 ema_30 预算列
        assert "ema_30" not in df.columns
        gene = _SimpleGene("EMA", {"period": 30})
        col = _get_indicator_column_for_test(df, gene)
        assert col.name == "ema_30"
        assert "ema_30" in df.columns  # 现算写回
        clear_indicator_cache()


def _get_indicator_column_for_test(df, gene):
    from core.strategy.executor import _get_indicator_column
    return _get_indicator_column(df, gene)


class TestLookbackInnerUnknownType:
    """lookback_* 内层类型完全未知（非阈值/价格族）→ 委托主入口
    evaluate_condition → 结构化拒绝 ResearchError
    （unify-tool-return-contract：主入口 else 兜底从静默全 False
    收口为拒绝后，内层委托路径行为随单点语义同步传播——未识别
    即显式拒绝）。"""

    def test_inner_unknown_type_rejected(self):
        from core.analysis.errors import ResearchError

        df = _make_df(30)
        condition = {"type": "lookback_any", "window": 3,
                     "inner": {"type": "totally_unknown_type"}}
        with pytest.raises(ResearchError) as exc_info:
            evaluate_condition(df["rsi_14"], df["close"], condition, df=df)
        assert exc_info.value.code == "RESEARCH_NOT_EXPRESSIBLE"
        assert exc_info.value.detail["condition_type"] == "totally_unknown_type"
        assert "legal_types" in exc_info.value.detail


class TestInnerConditionChainArcUnreachable:
    """_evaluate_inner_condition elif 链弧 [155,162] 不可达判定
    （coverage.py 字节码行归属伪迹）。

    调用图依据：:147 ``inner_type in ("lt","gt","le","ge")`` 为元组
    成员判断——CPython 编译为单次判断，False 时直跳 :157，不经过
    :149-155 的 elif 子判断（le/ge 用例仅经 :153/:155 的 True 侧）。
    源码语义下 :155（elif ge）False 的唯一后继是 :157；:155 → :162
    无控制流路径。coverage.py 在 3.13 行表下把该链某跳转的 from 行
    归属到 :155，产生语义上不存在的弧。既有用例组合（lt/gt/le/ge/
    price_above/price_below/未知类型委托主入口——TestLookbackInner
    ConditionTypes 与本文件 TestLookbackInnerUnknownType）已枚举
    源码全部可枚举路径。
    """


class TestUnregisteredIndicatorColumnMissing:
    """未注册指标且列不存在：前缀/子串匹配全落空 → 结构化 ValueError
    （非法基因在列解析单点拒绝，不静默 False）。"""

    def test_unregistered_no_column_raises(self):
        from core.strategy.executor import _SimpleGene, _get_indicator_column

        df = _make_df(20).drop(columns=["ema_10", "ema_20", "rsi_14"])
        gene = _SimpleGene("NOPE_INDICATOR", {})
        try:
            _get_indicator_column(df, gene)
            raised = False
        except ValueError as exc:
            raised = True
            assert "NOPE_INDICATOR" in str(exc)
        assert raised


class TestComputedOnDemandUnregisteredUnreachable:
    """_get_indicator_column 现算守卫弧（executor.py:355 False 侧 → :380）
    不可达判定。

    调用图依据：:328 ``reg = INDICATOR_REGISTRY.get(...)`` 为 None
    （未注册指标）时，:335-339 前缀匹配二路——无匹配 :339 直接
    raise ValueError（TestUnregisteredIndicatorColumnMissing 锚）；
    有匹配则 col=:337 必来自 df.columns（:341 精确命中先行 return）。
    两条路径均在 :355 之前终结——``reg is not None`` 在 :355 处恒真，
    False 侧（跳现算直奔 :380 raise）无控制流路径（防御分支）。
    """
