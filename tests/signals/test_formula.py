"""受限公式语言单测（5.1）：三关静态校验 + 求值正确性。"""

import numpy as np
import pandas as pd
import pytest

pytestmark = [pytest.mark.unit]

from core.signals.formula import FormulaError, evaluate_formula, validate_formula

COLS = {"close", "volume", "open_interest"}


def _df(n=60, seed=1):
    rng = np.random.default_rng(seed)
    idx = pd.date_range("2024-01-01", periods=n, freq="1h", tz="UTC", name="timestamp")
    close = pd.Series(100 + np.cumsum(rng.normal(0, 1, n)), index=idx)
    return pd.DataFrame({"close": close,
                         "volume": rng.uniform(10, 100, n),
                         "open_interest": rng.uniform(1000, 2000, n)}, index=idx)


class TestValidationThreeGates:
    def test_valid_formula_passes(self) -> None:
        assert validate_formula("zscore(close, 20)", COLS) == []
        assert validate_formula("(close - ema(close, 20)) / ema(close, 20)", COLS) == []
        assert validate_formula("corr(close, volume, 10)", COLS) == []

    def test_gate1_external_access_rejected(self) -> None:
        """第一关：无外部访问。"""
        errs = validate_formula("__import__('os').system('x')", COLS)
        assert any("语法" in e or "不允许" in e for e in errs)
        errs = validate_formula("close.__class__", COLS)
        assert any("外部访问" in e for e in errs)
        errs = validate_formula("(lambda x: x)(close)", COLS)
        assert any("不允许的语法构造" in e for e in errs)

    def test_gate2_unknown_column_rejected(self) -> None:
        """第二关：列引用合法。"""
        errs = validate_formula("zscore(nope_col, 20)", COLS)
        assert any("列引用不存在: nope_col" in e for e in errs)

    def test_gate3_negative_shift_rejected(self) -> None:
        """第三关：禁负向 shift（负值=看未来）。"""
        errs = validate_formula("shift(close, -3)", COLS)
        assert any("负向平移被禁止" in e for e in errs)
        errs = validate_formula("diff(close, -1)", COLS)
        assert any("负向平移被禁止" in e for e in errs)

    def test_non_whitelisted_function_rejected(self) -> None:
        errs = validate_formula("eval('x')", COLS)
        assert any("非白名单函数" in e for e in errs)

    def test_bad_arity_rejected(self) -> None:
        errs = validate_formula("shift(close)", COLS)
        assert any("参数" in e for e in errs)

    def test_non_constant_shift_rejected(self) -> None:
        """shift 参数须常量（静态可校验——变量可能藏负值）。"""
        errs = validate_formula("shift(close, close)", COLS)
        assert any("第二参数必须是常量" in e for e in errs)

    def test_window_below_one_rejected(self) -> None:
        errs = validate_formula("rolling_mean(close, 0)", COLS)
        assert any("窗口须 >= 1" in e for e in errs)


class TestEvaluation:
    def test_shift_correctness(self) -> None:
        df = _df()
        out = evaluate_formula("shift(close, 2)", df)
        assert out.iloc[5] == pytest.approx(df["close"].iloc[3])

    def test_diff_correctness(self) -> None:
        df = _df()
        out = evaluate_formula("diff(close, 1)", df)
        expected = df["close"] - df["close"].shift(1)
        assert out.iloc[10] == pytest.approx(expected.iloc[10])

    def test_zscore_matches_manual(self) -> None:
        df = _df()
        out = evaluate_formula("zscore(close, 20)", df)
        m = df["close"].rolling(20, min_periods=1).mean()
        s = df["close"].rolling(20, min_periods=1).std().replace(0, np.nan)
        expected = ((df["close"] - m) / s).fillna(0)
        assert out.iloc[30] == pytest.approx(expected.iloc[30])

    def test_arithmetic_combination(self) -> None:
        df = _df()
        out = evaluate_formula("(close - ema(close, 5)) / ema(close, 5)", df)
        ema5 = df["close"].ewm(span=5, min_periods=1).mean()
        expected = (df["close"] - ema5) / ema5
        assert out.iloc[20] == pytest.approx(expected.iloc[20])

    def test_extra_columns_env(self) -> None:
        """求值上下文附加列（插件输出引用另一插件输出的场景）。"""
        df = _df()
        extra = {"my_col": df["close"] * 2}
        out = evaluate_formula("my_col / close", df, extra_columns=extra)
        assert out.iloc[7] == pytest.approx(2.0)

    def test_constant_expression_broadcasts(self) -> None:
        df = _df()
        out = evaluate_formula("close * 0 + 1", df)
        assert (out == 1.0).all()

    def test_dynamic_negative_shift_blocked(self) -> None:
        """动态第二道：即使静态漏网，运行时负 shift 也被拒。"""
        df = _df()
        with pytest.raises(FormulaError, match="看向未来|非负"):
            evaluate_formula("shift(close, -1)", df)
