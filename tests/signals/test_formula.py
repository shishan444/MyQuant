"""受限公式语言单测（5.1）：三关静态校验 + 求值正确性。"""

import numpy as np
import pandas as pd
import pytest

pytestmark = [pytest.mark.unit]

from core.signals.formula import (
    FormulaError,
    evaluate_formula,
    formula_lookback,
    validate_formula,
)

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

    @pytest.mark.parametrize(
        "expr", ["a.b(1)", "a(1)(2)"],
        ids=["func=Attribute", "func=Call"])
    def test_non_name_call_func_raises_formula_error(
            self, expr: str) -> None:
        """SEC-4 防御：绕过静态校验直调求值器，func 非 Name 的 Call
        统一为 FormulaError（防御契约面=func 非 ast.Name 的任何 Call，
        双形态防只防 Attribute 不防 Call 的窄实现——裸 AttributeError
        不匹配 raises(FormulaError) 即红）。"""
        df = _df()
        with pytest.raises(FormulaError, match="Attribute|Call"):
            evaluate_formula(expr, df)

    @pytest.mark.parametrize(
        ("expr", "match"),
        [
            pytest.param("shift(close, 1", "语法错误",
                         id="语法错误收口"),
            pytest.param("'abc'", r"非常量类型: str", id="str 常量收口"),
            pytest.param("None", r"非常量类型: NoneType",
                         id="None 常量收口"),
        ])
    def test_bypass_defense_forms_raise_formula_error(
            self, expr: str, match: str) -> None:
        """CARD-01 防御收口：不经 validate_formula 直调求值器时，
        语法错误与非常量类型统一为 FormulaError（文案与静态关
        :227/:244 同形制），不裸穿 SyntaxError/TypeError。正常入口
        由静态校验先行拦截，此三形态仅存在于直调面。
        """
        df = _df()
        with pytest.raises(FormulaError, match=match):
            evaluate_formula(expr, df)

    def test_bypass_defense_deep_nesting_no_leak(self) -> None:
        """CARD-01 深嵌套双入口不裸穿：ast.parse 解析面对深嵌套
        抛出的错误（C 解析器 200 层上限 SyntaxError 或解析递归
        RecursionError——两分支均已收口）统一为 FormulaError；
        formula_lookback 同形态返回 0（非法表达式到不了求值层）。
        """
        deep = "(" * 6000 + "1" + ")" * 6000
        df = _df()
        with pytest.raises(FormulaError, match="语法错误|嵌套过深"):
            evaluate_formula(deep, df)
        assert formula_lookback(deep) == 0

    def test_bypass_defense_keywords_rejected(self) -> None:
        """CARD-02 防御收口：不经 validate_formula 直调求值器时，
        关键字参数不再被静默丢弃（此前 shift(close, 1, bogus=2)
        无声算出正常结果——卡实锤形态），统一收口 FormulaError
        （文案与静态关 :263-264 逐字对齐）。"""
        df = _df()
        with pytest.raises(FormulaError, match="不支持关键字参数"):
            evaluate_formula("shift(close, 1, bogus=2)", df)


# ================================================================ 0.2.12 扩充
# T1 白名单函数 / T2 静态校验与推导（TESTING 轮锚定）


class TestWhitelistVocabulary:
    """白名单 21 词与三表一致性。"""

    def test_whitelist_count_21(self):
        from core.signals.formula import FORMULA_FUNCTIONS, _FORMULA_ARITY
        assert len(FORMULA_FUNCTIONS) == 21
        assert set(FORMULA_FUNCTIONS) == set(_FORMULA_ARITY)

    def test_new_words_present(self):
        from core.signals.formula import FORMULA_FUNCTIONS
        for w in ("crossover", "crossunder", "max", "min", "gt", "lt",
                  "eq", "logical_and", "logical_or", "logical_not"):
            assert w in FORMULA_FUNCTIONS


class TestCrossoverTimingAnchor:
    """T1 时序锚：先跌后涨分段序列——穿越时点唯一确定。"""

    def _df(self):
        idx = pd.date_range("2024-01-01", periods=8, freq="D",
                            name="timestamp")
        return pd.DataFrame(
            {"close": [10, 9, 8, 9, 11, 13, 12, 14],
             "vol": [1, 2, 3, 2, 1, 2, 3, 2]}, index=idx)

    def test_crossover_fires_exactly_at_cross_bar(self):
        out = evaluate_formula("crossover(close, 10)", self._df())
        vals = out.tolist()
        assert vals == [False, False, False, False, True, False, False, False]
        assert not vals[0], "首元素（shift 头部 NaN）不误触发"

    def test_crossunder_symmetric(self):
        df = self._df()
        out = evaluate_formula("crossunder(close, 12.5)", df)
        # idx[6]：cur=12<12.5 且 prev=13>=12.5（严格下穿）
        vals = out.tolist()
        assert vals == [False, False, False, False, False, False, True, False]

    def test_crossover_series_series(self):
        """双序列穿越（EMA 金叉的最小形态）。"""
        idx = pd.date_range("2024-01-01", periods=6, freq="D", name="timestamp")
        df = pd.DataFrame({"fast": [1, 1, 1, 2, 2, 2],
                           "slow": [2, 2, 2, 1, 1, 1]}, index=idx)
        out = evaluate_formula("crossover(fast, slow)", df)
        assert out.tolist() == [False, False, False, True, False, False]

    def test_max_not_confused_with_rolling_max(self):
        """max/min 逐元素与窗口聚合不混同。"""
        df = self._df()
        elem = evaluate_formula("max(close, 10)", df)
        roll = evaluate_formula("rolling_max(close, 3)", df)
        # idx[1]（close=9）：max(9,10)=10 逐元素抬底；rolling_max 头部=9
        assert elem.tolist()[1] == 10.0
        assert roll.tolist()[1] == 10.0 or roll.tolist()[1] == 9.0  # min_periods=1
        assert not elem.equals(roll)


class TestElementwiseFamily:
    """T1 逐元素族：比较/逻辑布尔直出 + 常量广播 + NaN 保守（R5）。"""

    def _df(self):
        idx = pd.date_range("2024-01-01", periods=5, freq="D", name="timestamp")
        return pd.DataFrame({"a": [1.0, 2.0, np.nan, 4.0, 5.0],
                             "b": [0.5, 0.0, 2.0, 0.0, 3.0]}, index=idx)

    def test_compare_boolean_out(self):
        out = evaluate_formula("gt(a, 1)", self._df())
        assert out.tolist()[:2] == [False, True]

    def test_logical_nan_conservative(self):
        """R5：NaN 参与逻辑族保守不触发（fillna(False) 语义）。"""
        out = evaluate_formula("logical_and(gt(b, 0), gt(a, 0))", self._df())
        # idx[2] a=NaN → 不触发（尽管 b>0）
        assert out.tolist()[2] is False or out.tolist()[2] == False  # noqa: E712

    def test_logical_numeric_nonzero_truthy(self):
        idx = pd.date_range("2024-01-01", periods=3, freq="D", name="timestamp")
        df = pd.DataFrame({"v": [0.0, 0.5, -2.0]}, index=idx)
        out = evaluate_formula("logical_not(v)", df)
        assert out.tolist() == [True, False, False]

    def test_bool_constant_participates_as_01(self):
        """布尔常量按 0/1 参与（logical_and(a, 1) 合法且等价恒真掩码）。"""
        out = evaluate_formula("logical_and(gt(a, 3), 1)", self._df())
        assert out.tolist() == [False, False, False, True, True]


class TestStaticValidationGates:
    """T2：R1 双向锚 + R6 三态语法锚 + 三关存续。"""

    COLS = {"close", "vol"}

    def test_r1_elementwise_second_param_small_constant_passes(self):
        """R1 修正锚：逐元素族第二参常量 <1 放行（原 arity 笼统推定误拒）。"""
        for expr in ("gt(close, 0.5)", "max(close, 0)", "eq(close, 0)",
                     "min(close, 0.25)", "lt(vol, 0.9)"):
            assert validate_formula(expr, self.COLS) == [], expr

    def test_r1_window_zero_still_rejected(self):
        """R1 修正不误伤既有拒绝：窗口位函数窗口 <1 仍拒。"""
        for expr in ("rolling_mean(close, 0)", "rolling_mean(close, 0.5)",
                     "zscore(close, 0)", "ema(close, 0)"):
            errs = validate_formula(expr, self.COLS)
            assert any("窗口须 >= 1" in e for e in errs), expr

    def test_negative_shift_still_rejected(self):
        errs = validate_formula("shift(close, -1)", self.COLS)
        assert any("负向" in e for e in errs)

    def test_r6_keyword_forms_rejected(self):
        """R6：and 语法错（SyntaxError 归入校验错误清单）；not(a) 是
        UnaryOp 被拒。"""
        errs = validate_formula("and(close, vol)", self.COLS)
        assert errs, "and(a,b) 须被拒"
        errs2 = validate_formula("not(close)", self.COLS)
        assert errs2, "not(a) 被解析为 UnaryOp 须被拒"

    def test_r6_logical_forms_pass(self):
        assert validate_formula("logical_and(close, vol)", self.COLS) == []
        assert validate_formula("logical_not(close)", self.COLS) == []

    def test_operator_syntax_rejected(self):
        assert validate_formula("close > vol", self.COLS), "比较运算符须拒"
        assert validate_formula("close and vol", self.COLS), "布尔运算符须拒"


class TestLookbackDerivation:
    """T2：_FIXED_LOOKBACK 结构固有贡献（R2）。"""

    def test_crossover_contributes_one(self):
        assert formula_lookback("crossover(close, 10)") == 1

    def test_nested_takes_max(self):
        assert formula_lookback(
            "crossover(ema(close, 12), ema(close, 26))") == 26

    def test_elementwise_zero(self):
        assert formula_lookback("gt(close, 5)") == 0
        assert formula_lookback("max(close, 10)") == 0

