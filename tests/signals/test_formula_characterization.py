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


class TestResourceLimits:
    """表达式资源限制双件套（CARD-14·涉安）。

    长度上限 10KB（入口前置，注册关拦截口径）；递归超限三处转译
    FormulaError/错误清单——设计目标是「不裸炸」：10KB 内病态扁平
    长链注册可过、求值被干净转译是契约行为（非「10KB 内全部可求值」）。
    """

    COLS = {"close", "vol"}

    def test_over_limit_rejected_with_actual_length(self) -> None:
        expr = "close" + "+close" * 12000  # ~72KB
        errs = validate_formula(expr, self.COLS)
        assert len(errs) == 1
        assert "表达式超长" in errs[0]
        assert str(len(expr)) in errs[0]  # 文案如实报实际字符数

    def test_boundary_exact_limit_passes(self) -> None:
        expr = "close" + " " * (10 * 1024 - len("close"))  # 恰 10240
        assert len(expr) == 10 * 1024
        assert validate_formula(expr, self.COLS) == []

    def test_boundary_one_char_over_rejected(self) -> None:
        expr = "close" + " " * (10 * 1024 - len("close") + 1)  # 10241
        errs = validate_formula(expr, self.COLS)
        assert any("表达式超长" in e for e in errs)

    def test_deep_nesting_within_limit_eval_translated(self) -> None:
        """10KB 内深嵌套：注册过、求值 _ev 递归超限干净转译（不裸炸）。

        自隔离设计（全量运行教训）：2000 层锚在全量运行时 parse 层先于
        _ev 触顶（转译为「解析递归超限」），match 失配——栈敏感、顺序
        相关。改为探测当前真实栈深并显式压低递归上限，嵌套仅 60 层
        （parse C 级零压力，_ev 必超），与进程运行状态无关。
        """
        import sys

        expr = "-" * 60 + "close"  # 左深 UnaryOp 链，<10KB
        assert validate_formula(expr, self.COLS) == []

        frames = 0
        frame = sys._getframe()
        while frame is not None:
            frames += 1
            frame = frame.f_back

        df = pd.DataFrame({"close": [1.0, 2.0, 3.0]})
        old_limit = sys.getrecursionlimit()
        try:
            # 余量校准：前置调用链 ≈ frames+3 须不触发；60 层 _ev 递归
            # 最深 ≈ frames+64 须触发——取 frames+50 两侧皆有安全边距
            sys.setrecursionlimit(frames + 50)
            with pytest.raises(FormulaError, match="求值递归超限"):
                evaluate_formula(expr, df)
        finally:
            sys.setrecursionlimit(old_limit)

    def test_parse_recursionerror_translated_in_validate(self) -> None:
        """白盒锚（不依赖真实阈值，跨版本稳定）：parse 抛
        RecursionError 时 validate_formula 返回清单不外泄。"""
        from unittest.mock import patch as _patch

        with _patch(
            "core.signals.formula.ast.parse", side_effect=RecursionError
        ):
            errs = validate_formula("close", self.COLS)
        assert any("嵌套过深" in e for e in errs)

    def test_parse_memoryerror_translated_both_layers(self) -> None:
        """白盒锚：CPython C 级解析器栈溢出（MemoryError）双层转译。"""
        from unittest.mock import patch as _patch

        df = pd.DataFrame({"close": [1.0, 2.0, 3.0]})
        with _patch(
            "core.signals.formula.ast.parse",
            side_effect=MemoryError("Parser stack overflowed"),
        ):
            errs = validate_formula("close", self.COLS)
            assert any("嵌套过深" in e for e in errs)
        with _patch(
            "core.signals.formula.ast.parse",
            side_effect=MemoryError("Parser stack overflowed"),
        ):
            with pytest.raises(FormulaError, match="解析递归超限"):
                evaluate_formula("close", df)

    def test_normal_expression_within_limit_unharmed(self) -> None:
        """防误伤：常规组合形态（<10KB）注册与求值均正常。"""
        expr = "rolling_mean(zscore(close, 30), 20) + corr(close, vol, 10)"
        assert validate_formula(expr, self.COLS) == []
        idx = pd.date_range("2024-01-01", periods=60, freq="1h", tz="UTC")
        df = pd.DataFrame(
            {"close": np.arange(1.0, 61.0), "vol": np.arange(2.0, 122.0, 2.0)},
            index=idx,
        )
        out = evaluate_formula(expr, df)
        assert isinstance(out, pd.Series)
        assert len(out) == 60
        assert out.iloc[40:].notna().all()  # 窗口收敛后无 NaN
