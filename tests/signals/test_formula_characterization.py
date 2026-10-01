"""formula 特征测试网（fix/data-signals-review 修复期安全网）。

现状记录语义：期望值从当前实现行为推导钉住。建网时缺口：
corr 白名单函数（此前零覆盖）/ zscore 零 std 填 0 / 求值层守卫
（窗口 0、列缺失、非法运算符、非法节点）/ 纯常量广播 /
校验层 Mod 运算符与关键字参数 / lookback 语法错误与非常量窗口。
"""

import pytest

pytestmark = [pytest.mark.unit]

import numpy as np
import pandas as pd

from core.signals.formula import (
    FormulaError,
    evaluate_formula,
    formula_lookback,
    validate_formula,
)


class TestCorrFunction:
    """corr：min_periods=2 滚动相关，完全线性 ±1。"""

    def test_perfect_linear_correlation(self) -> None:
        df = pd.DataFrame(
            {
                "close": [1.0, 2.0, 3.0, 4.0, 5.0],
                "vol": [2.0, 4.0, 6.0, 8.0, 10.0],
            }
        )
        out = evaluate_formula("corr(close, vol, 3)", df)
        assert pd.isna(out.iloc[0])  # 首点窗口不足 min_periods=2
        assert np.allclose(out.iloc[1:].to_numpy(), 1.0)

    def test_inverse_correlation(self) -> None:
        df = pd.DataFrame(
            {
                "close": [1.0, 2.0, 3.0, 4.0, 5.0, 6.0],
                "vol": [6.0, 5.0, 4.0, 3.0, 2.0, 1.0],
            }
        )
        out = evaluate_formula("corr(close, vol, 4)", df)
        assert pd.isna(out.iloc[0])
        assert np.allclose(out.iloc[1:].to_numpy(), -1.0)

    def test_uncorrelated_middle_window(self) -> None:
        # 窗口 [1,2,3] vs [1,2,3] 的滚动 r=1；窗口 [2,3,5] vs [2,5,7] r<1
        df = pd.DataFrame(
            {"close": [1.0, 2.0, 3.0, 5.0], "vol": [1.0, 2.0, 3.0, 7.0]}
        )
        out = evaluate_formula("corr(close, vol, 3)", df)
        assert out.iloc[2] == pytest.approx(1.0)
        assert out.iloc[3] < 1.0


class TestZscoreZeroStd:
    """常数序列 std=0 → 除法 NaN → fillna(0)，无 inf 逃逸。"""

    def test_constant_series_fills_zero(self) -> None:
        df = pd.DataFrame({"close": [100.0] * 6})
        out = evaluate_formula("zscore(close, 5)", df)
        assert np.allclose(out.to_numpy(), 0.0)


class TestEvalLayerGuards:
    """求值层守卫（evaluate_formula 不做静态校验，直接执行）。"""

    @pytest.fixture
    def df(self) -> pd.DataFrame:
        return pd.DataFrame({"close": [1.0, 2.0, 3.0]})

    def test_rolling_window_zero_eval_error(self, df) -> None:
        with pytest.raises(FormulaError, match="窗口须 >= 1"):
            evaluate_formula("rolling_mean(close, 0)", df)

    def test_unknown_column_eval_error(self, df) -> None:
        with pytest.raises(FormulaError, match="列引用不存在: nope"):
            evaluate_formula("nope + 1", df)

    def test_disallowed_operator_eval_error(self, df) -> None:
        with pytest.raises(FormulaError, match="不允许的运算符 Mod"):
            evaluate_formula("close % 2", df)

    def test_disallowed_node_eval_error(self, df) -> None:
        with pytest.raises(FormulaError, match="不允许的节点 Subscript"):
            evaluate_formula("close[0]", df)

    def test_pure_constant_broadcasts_to_series(self, df) -> None:
        out = evaluate_formula("3.25", df)
        assert isinstance(out, pd.Series)
        assert len(out) == 3
        assert out.index.equals(df.index)
        assert np.allclose(out.to_numpy(), 3.25)


class TestValidateMore:
    """校验层补充：Mod 运算符 / 关键字参数形态。"""

    COLS = {"close", "vol"}

    def test_mod_operator_rejected(self) -> None:
        errs = validate_formula("close % 2", self.COLS)
        assert any("不允许的运算符" in e for e in errs)

    def test_keyword_argument_rejected(self) -> None:
        errs = validate_formula("shift(close, n=1)", self.COLS)
        assert any("不支持关键字参数" in e for e in errs)


class TestValidateCorrWindow:
    """corr 窗口位（第 3 参）校验（CARD-06）。

    修复前硬编码 args[1] 漏检 corr：corr(x, y, 0) 静态放行、注册成功、
    求值才炸 FormulaError（min_periods 2 must be <= window 0）；修复后
    注册时拒绝，经 plugin_service → ResearchError → API 422。
    """

    COLS = {"close", "vol"}

    @pytest.mark.parametrize(
        ("expr", "fragment"),
        [
            pytest.param("corr(close, vol, 0)", "corr 窗口须 >= 1", id="零窗口拒绝"),
            pytest.param("corr(close, vol, -2)", "corr 窗口须 >= 1", id="负窗口拒绝"),
        ],
    )
    def test_bad_cor_window_rejected(self, expr: str, fragment: str) -> None:
        errs = validate_formula(expr, self.COLS)
        assert any(fragment in e for e in errs)

    def test_valid_cor_window_passes(self) -> None:
        assert validate_formula("corr(close, vol, 10)", self.COLS) == []

    def test_non_constant_cor_window_static_pass(self) -> None:
        """窗口位非常量：静态层放行（只拦常量 <1，动态语义由求值层承担）。"""
        assert validate_formula("corr(close, vol, close)", self.COLS) == []

    def test_crossover_threshold_constant_not_hurt(self) -> None:
        """结构族无窗口位：阈值常量 10（>1 亦无碍）不触发窗口校验。"""
        assert validate_formula("crossover(close, 10)", self.COLS) == []


class TestLookbackEdges:
    """回看推导边界：语法错误 0 / 非常量窗口不取。"""

    def test_syntax_error_returns_zero(self) -> None:
        assert formula_lookback("close +") == 0

    def test_non_constant_window_ignored(self) -> None:
        # 静态推导只认字面量：窗口位是列引用时取不到 → 0
        assert formula_lookback("rolling_mean(close, close)") == 0

    def test_window_literal_still_taken(self) -> None:
        assert formula_lookback("rolling_mean(close, 30)") == 30
