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

        不可达判定锚（阶段四盲区清单 formula.py:197-201——lookback
        的 RecursionError/MemoryError 分支）：CPython 3.13（本工程
        venv 实测）下两类构造均触达不了该分支——①括号嵌套 6000 层
        由 C 解析器 200 层上限直接抛 SyntaxError（too many nested
        parentheses，走既有 :194 分支）；②10 万项 BinOp 链（1+1+…）
        迭代解析正常返回不递归。RecursionError 分支属解释器版本
        差异的防御性兜底；MemoryError 需真实内存耗尽不可构造测试。
        该分支保留正确（与 validate_formula :229-231 既有 MemoryError
        面同构），覆盖工具按不可达豁免。
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
    """白名单 26 词与登记表一致性（add-math-formula-vocabulary 扩容后）。"""

    def test_whitelist_count_26(self):
        from core.signals.formula import FORMULA_FUNCTIONS, _FORMULA_ARITY
        assert len(FORMULA_FUNCTIONS) == 26
        assert set(FORMULA_FUNCTIONS) == set(_FORMULA_ARITY)

    def test_new_words_present(self):
        from core.signals.formula import FORMULA_FUNCTIONS
        for w in ("crossover", "crossunder", "max", "min", "gt", "lt",
                  "eq", "logical_and", "logical_or", "logical_not",
                  # ---- add-math-formula-vocabulary 五词 ----
                  "rolling_quantile", "sqrt", "log",
                  "kalman", "kalman_slope"):
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



# ================================================ add-math-formula-vocabulary
# U1-2 登记面恰等 / U2 静态校验拒绝面 / U3 求值语义（design 附录测试方案）


class TestMathVocabularyRegistry:
    """U1-2：五词四表+附加域表登记面恰等。"""

    def test_arity_registry(self):
        from core.signals.formula import _FORMULA_ARITY
        expect = {"rolling_quantile": 3, "sqrt": 1, "log": 1,
                  "kalman": 3, "kalman_slope": 3}
        for w, a in expect.items():
            assert _FORMULA_ARITY[w] == a, w

    def test_window_param_index(self):
        from core.signals.formula import _WINDOW_PARAM_INDEX
        assert _WINDOW_PARAM_INDEX["rolling_quantile"] == 1
        # sqrt/log/kalman 族无窗口位（不在表=贡献 0 或走 fixed 表）
        for w in ("sqrt", "log", "kalman", "kalman_slope"):
            assert w not in _WINDOW_PARAM_INDEX, w

    def test_fixed_lookback(self):
        from core.signals.formula import _FIXED_LOOKBACK
        assert _FIXED_LOOKBACK["kalman"] == 200
        assert _FIXED_LOOKBACK["kalman_slope"] == 200

    def test_extra_const_param_domains(self):
        """附加常量参数域表：rolling_quantile 位 2 [0,1] 闭、
        kalman/kalman_slope 位 1/2 (0,+∞) 开（下界排他）。"""
        from core.signals.formula import _EXTRA_CONST_PARAM_DOMAINS
        assert _EXTRA_CONST_PARAM_DOMAINS["rolling_quantile"][2] == \
            (0.0, 1.0, False, "分位点")
        for w in ("kalman", "kalman_slope"):
            assert _EXTRA_CONST_PARAM_DOMAINS[w][1] == (0.0, None, True, "q")
            assert _EXTRA_CONST_PARAM_DOMAINS[w][2] == (0.0, None, True, "r")


class TestMathVocabularyValidation:
    """U2：新词静态校验拒绝面（附加域表+窗口复用+前视防护不失效）。"""

    def test_u21_quantile_domain(self):
        # q 非常量 → 须为常量
        errs = validate_formula("rolling_quantile(close, 20, close)", COLS)
        assert any("第 3 参（分位点）须为常量" in e for e in errs)
        # q 越界双侧 → 分位点越界 + 合法域披露
        for bad in ("rolling_quantile(close, 20, 1.5)",
                    "rolling_quantile(close, 20, -0.1)"):
            errs = validate_formula(bad, COLS)
            assert any("分位点越界" in e and "0, 1" in e for e in errs), bad
        # 闭端点两侧合法（q=0.0/q=1.0 均过）
        assert validate_formula("rolling_quantile(close, 20, 0.0)", COLS) == []
        assert validate_formula("rolling_quantile(close, 20, 1.0)", COLS) == []

    def test_u22_kalman_qr_domain(self):
        """两词独立登记逐词测：非常量/非正三面拒、极小正数与无上界过。"""
        for w in ("kalman", "kalman_slope"):
            errs = validate_formula(f"{w}(close, close, 25)", COLS)
            assert any("第 2 参（q）须为常量" in e for e in errs), w
            for bad, name in ((f"{w}(close, 0, 25)", "q=0"),
                              (f"{w}(close, 0.01, 0)", "r=0"),
                              (f"{w}(close, 0.01, -25)", "r=-25")):
                errs = validate_formula(bad, COLS)
                assert any("越界" in e and "> 0" in e for e in errs), \
                    f"{w} {name}"
        # 开域边界合法：极小正数与超大 r 均过
        assert validate_formula("kalman(close, 0.0001, 1e6)", COLS) == []
        assert validate_formula("kalman_slope(close, 0.0001, 1e6)", COLS) == []

    def test_u23_window_domain_reused(self):
        """rolling_quantile 窗口位 1 复用既有窗口校验文案。"""
        errs = validate_formula("rolling_quantile(close, 0, 0.5)", COLS)
        assert any("窗口须 >= 1" in e for e in errs)
        errs = validate_formula("rolling_quantile(close, 10001, 0.5)", COLS)
        assert any("窗口超上限" in e for e in errs)

    def test_u24_lookahead_guard_intact(self):
        """扩词不破坏三关：新词内外嵌负向 shift 仍拒。"""
        errs = validate_formula("shift(kalman_slope(close, 0.01, 25), -1)", COLS)
        assert any("负向平移被禁止" in e for e in errs)
        errs = validate_formula("sqrt(shift(close, -1))", COLS)
        assert any("负向平移被禁止" in e for e in errs)

    def test_u25_arity_errors(self):
        """arity 校验对新词生效（域校验不遮蔽参数个数错误）。"""
        errs = validate_formula("sqrt(close, 1)", COLS)
        assert any("需要 1 个参数" in e for e in errs)
        errs = validate_formula("kalman(close, 0.01)", COLS)
        assert any("需要 3 个参数" in e for e in errs)


class TestMathVocabularyEvaluation:
    """U3：五词求值语义（手算锚+独立递归对照+收敛性质+NaN 契约）。"""

    def _df_close(self, values):
        idx = pd.date_range("2024-01-01", periods=len(values), freq="1h",
                            tz="UTC", name="timestamp")
        return pd.DataFrame({"close": pd.Series(values, index=idx,
                                                dtype=float)}, index=idx)

    def test_u31_rolling_quantile_manual(self):
        """手算锚：[1..5] w=3——q=0.5 与 q=0.75 线性插值链
        （min_periods=1 头部部分窗口分位；pos=q*(k-1) 线性插值）。"""
        df = self._df_close([1, 2, 3, 4, 5])
        out50 = evaluate_formula("rolling_quantile(close, 3, 0.5)", df)
        assert out50.tolist() == pytest.approx(
            [1.0, 1.5, 2.0, 3.0, 4.0])
        out75 = evaluate_formula("rolling_quantile(close, 3, 0.75)", df)
        assert out75.tolist() == pytest.approx(
            [1.0, 1.75, 2.5, 3.5, 4.5])

    def test_u31_quantile_extremes_identity(self):
        """性质锚：q=0 ≡ rolling_min、q=1 ≡ rolling_max。"""
        df = _df(n=40)
        q0 = evaluate_formula("rolling_quantile(close, 7, 0)", df)
        q1 = evaluate_formula("rolling_quantile(close, 7, 1)", df)
        rmin = evaluate_formula("rolling_min(close, 7)", df)
        rmax = evaluate_formula("rolling_max(close, 7)", df)
        assert np.allclose(q0.to_numpy(), rmin.to_numpy(), equal_nan=True)
        assert np.allclose(q1.to_numpy(), rmax.to_numpy(), equal_nan=True)

    def test_u31_quantile_nan_semantics(self):
        """NaN 语义按 pandas rolling 实况：窗口含 NaN 时跳过 NaN 用
        有效值计算（min_periods=1 下 {1,NaN} → 1.0——与 oracle 同源
        同调用，非 NaN 传播）。"""
        df = self._df_close([1.0, np.nan, 3.0, 4.0, 5.0])
        out = evaluate_formula("rolling_quantile(close, 2, 0.5)", df)
        assert out.tolist() == pytest.approx([1.0, 1.0, 3.0, 3.5, 4.5])

    def test_u32_sqrt(self):
        df = self._df_close([4.0, 0.0, -1.0, np.nan, 9.0])
        out = evaluate_formula("sqrt(close)", df)
        expected = [2.0, 0.0, np.nan, np.nan, 3.0]
        for got, exp in zip(out.tolist(), expected):
            assert (np.isnan(got) and np.isnan(exp)) or got == exp

    def test_u33_log(self):
        """log 非正值→NaN（有意不取 -inf——spec scenario 直锚）。"""
        df = self._df_close([1.0, float(np.e), 0.0, -1.0, np.nan])
        out = evaluate_formula("log(close)", df)
        expected = [0.0, 1.0, np.nan, np.nan, np.nan]
        for got, exp in zip(out.tolist(), expected):
            assert (np.isnan(got) and np.isnan(exp)) or \
                got == pytest.approx(exp)

    def test_u33_domain_forms_in_dsl(self):
        """表达式形制 sqrt(0 - 1)/log(0) 在 DSL 内合法（BinOp 常量）
        且逐位 NaN。"""
        df = self._df_close([5.0] * 3)
        assert np.isnan(evaluate_formula("sqrt(0 - 1)", df).iloc[0])
        assert np.isnan(evaluate_formula("log(0)", df).iloc[0])

    @staticmethod
    def _reference_kalman(values, q=0.01, r=25.0):
        """U3-4 独立参考递归（按 design §2 六项口径独立编码，不从
        被测实现 import——两处独立编码构成对照）：x=[水平,斜率]，
        x0=[s0,0]、P0=100I、F=[[1,1],[0,1]]、H=[1,0]、Q=diag(q,q)、R=r。"""
        pr = np.asarray(values, dtype=float)
        n = len(pr)
        x = np.zeros((n, 2))
        P = np.eye(2) * 100.0
        x[0] = [pr[0], 0.0]
        for t in range(1, n):
            xp = np.array([x[t - 1, 0] + x[t - 1, 1], x[t - 1, 1]])
            Pp = np.array(
                [[P[0, 0] + P[1, 0] + P[0, 1] + P[1, 1] + q,
                  P[0, 1] + P[1, 1]],
                 [P[1, 0] + P[1, 1], P[1, 1] + q]])
            S = Pp[0, 0] + r
            K = Pp[:, 0] / S
            x[t] = xp + K * (pr[t] - xp[0])
            P = Pp - np.outer(K, [1.0, 0.0]) @ Pp
        return x

    def test_u34_kalman_matches_reference(self):
        """逐位对照：线性趋势+正弦扰动短序列，被测词输出与独立参考
        递归逐点 approx；首元素精确锚 kalman[0]==s[0]、slope[0]==0。"""
        t = np.arange(60)
        values = 100.0 + 0.5 * t + 3.0 * np.sin(t / 5.0)
        df = self._df_close(values)
        level = evaluate_formula("kalman(close, 0.01, 25)", df)
        slope = evaluate_formula("kalman_slope(close, 0.01, 25)", df)
        ref = self._reference_kalman(values, q=0.01, r=25.0)
        assert level.iloc[0] == pytest.approx(values[0])
        assert slope.iloc[0] == pytest.approx(0.0)
        np.testing.assert_allclose(
            level.to_numpy(), ref[:, 0], rtol=1e-9, atol=1e-9)
        np.testing.assert_allclose(
            slope.to_numpy(), ref[:, 1], rtol=1e-9, atol=1e-9)

    def test_u35_convergence_property(self):
        """收敛性质锚：无噪声线性趋势（斜率恒 1）200 根——slope 尾值
        收敛 1（|err|<=1e-3）、level 贴合水平线（|err|<=1e-2）。"""
        values = 100.0 + 1.0 * np.arange(200)
        df = self._df_close(values)
        slope = evaluate_formula("kalman_slope(close, 0.01, 25)", df)
        level = evaluate_formula("kalman(close, 0.01, 25)", df)
        assert abs(slope.iloc[-1] - 1.0) <= 1e-3
        assert abs(level.iloc[-1] - values[-1]) <= 1e-2

    def test_u36_no_nan_head(self):
        """无 NaN 头分锚：无 NaN 输入下 kalman 族输出等长且全非 NaN
        （递归初值机制——与 rolling 族头部部分计算语义刻意分叉）。"""
        values = 100.0 + np.cumsum(np.random.default_rng(7).normal(0, 1, 80))
        df = self._df_close(values)
        for expr in ("kalman(close, 0.01, 25)",
                     "kalman_slope(close, 0.01, 25)"):
            out = evaluate_formula(expr, df)
            assert len(out) == len(values)
            assert not out.isna().any(), expr

    def test_u37_nan_input_propagates_to_tail(self):
        """NaN 传播契约：输入含 NaN → NaN 位起输出 NaN 至尾部
        （递归无清洗——照契约断言，实现呈部分恢复即偏差；含中间位
        逐位断言，非仅首尾两点）。"""
        values = [100.0, 101.0, np.nan, 103.0, 104.0, 105.0, 106.0, 107.0]
        df = self._df_close(values)
        for expr in ("kalman(close, 0.01, 25)",
                     "kalman_slope(close, 0.01, 25)"):
            out = evaluate_formula(expr, df)
            assert not np.isnan(out.iloc[1]), f"{expr} NaN 位前应有值"
            assert out.iloc[2:].isna().all(), f"{expr} NaN 位起至尾部逐位"

    def test_u38_equal_length_contract(self):
        """等长契约：五词输出与输入等长。"""
        df = _df(n=37)
        for expr in ("rolling_quantile(close, 9, 0.4)", "sqrt(close)",
                     "log(close)", "kalman(close, 0.01, 25)",
                     "kalman_slope(close, 0.01, 25)"):
            assert len(evaluate_formula(expr, df)) == 37, expr

    def test_u39_magnitude_anchor(self):
        """量纲锚：level≈价格量纲（与 close 同数量级）、slope≈1/bar
        （趋势 0.5/bar 下收敛到 0.5）。"""
        values = 100.0 + 0.5 * np.arange(150)
        df = self._df_close(values)
        level = evaluate_formula("kalman(close, 0.01, 25)", df)
        slope = evaluate_formula("kalman_slope(close, 0.01, 25)", df)
        assert 0.1 * values[-1] < level.iloc[-1] < 10 * values[-1]
        assert abs(slope.iloc[-1] - 0.5) <= 1e-3


class TestSeriesFirstParamGuard:
    """CARD-01 序列位防御：首参为序列位的词传标量统一收口
    FormulaError（此前 AttributeError 裸穿——不在 except
    (TypeError, ValueError) 收口面）。"""

    def test_scalar_first_param_raises_formula_error(self):
        """双形制主锚：新词 kalman(5,1,25) 与存量 rolling_mean(5,3)。"""
        df = _df()
        for expr in ("kalman(5, 1, 25)", "rolling_mean(5, 3)"):
            with pytest.raises(
                    FormulaError, match="第 1 参须为列引用序列") as ei:
                evaluate_formula(expr, df)
            assert "float" in str(ei.value), expr
            assert expr.split("(")[0] in str(ei.value), expr

    def test_stock_family_all_intercepted(self):
        """存量族全覆盖：shift/diff/rolling 族/zscore/ema/corr/abs
        及新词 rolling_quantile/kalman_slope 首参标量同收口。"""
        df = _df()
        for expr in ("shift(5, 1)", "diff(5, 1)",
                     "rolling_std(5, 3)", "rolling_max(5, 3)",
                     "rolling_min(5, 3)", "rolling_sum(5, 3)",
                     "zscore(5, 3)", "ema(5, 3)", "abs(5)",
                     "corr(5, volume, 3)",
                     "rolling_quantile(5, 3, 0.5)",
                     "kalman_slope(5, 0.01, 25)"):
            with pytest.raises(FormulaError, match="第 1 参须为列引用序列"):
                evaluate_formula(expr, df)

    def test_constant_broadcast_words_not_hurt(self):
        """防误拦对照：max/min/gt/lt/eq/logical_* 常量广播、sqrt/log
        标量输入、crossover/crossunder 常量阈值均合法不受拦。"""
        df = _df()
        assert len(evaluate_formula("max(close, 10)", df)) == 60
        assert len(evaluate_formula("min(close, 10)", df)) == 60
        assert len(evaluate_formula("gt(close, 100)", df)) == 60
        assert len(evaluate_formula("lt(close, 100)", df)) == 60
        assert len(evaluate_formula("eq(close, close)", df)) == 60
        assert len(evaluate_formula(
            "logical_and(gt(close, 0), 1)", df)) == 60
        assert len(evaluate_formula("logical_or(lt(close, 0), 0)", df)) == 60
        assert len(evaluate_formula("logical_not(close)", df)) == 60
        assert len(evaluate_formula("sqrt(4)", df)) == 60
        assert len(evaluate_formula("log(10)", df)) == 60
        assert len(evaluate_formula("crossover(close, 100)", df)) == 60
        assert len(evaluate_formula("crossunder(close, 100)", df)) == 60


class TestInfLiteralDomainRejection:
    """CARD-02（SEC-01）：inf 字面量（1e999 解析即 inf）穿透开上界
    域校验——kalman(close,1e999,25) 静态放行、求值产尾部 NaN 垃圾列
    +RuntimeWarning 日志污染。out_of_domain 并入 not np.isfinite(val)
    后静态拒（文案沿用「越界」形制，:g 格式化 inf 可读）。"""

    def test_inf_q_rejected(self):
        """主锚：开上界域 (0,+∞) 对 inf 的数值比较全放行，修复后拒。"""
        errs = validate_formula("kalman(close, 1e999, 25)", COLS)
        assert any("kalman q越界" in e and "inf" in e for e in errs)

    def test_neg_inf_r_rejected(self):
        """-inf 形态：既有 val < lo 已拒，并入 isfinite 后仍拒（对照）。"""
        errs = validate_formula("kalman_slope(close, 0.01, -1e999)", COLS)
        assert any("kalman_slope r越界" in e for e in errs)

    def test_quantile_inf_still_rejected(self):
        """对照锚：rolling_quantile q=1e999 走 [0,1] 上界既有拒，
        并入 isfinite 不破坏既有拒绝面。"""
        errs = validate_formula("rolling_quantile(close, 20, 1e999)", COLS)
        assert any("分位点越界" in e for e in errs)

    def test_finite_large_r_still_passes(self):
        """防误拦：有限大 r（1e6）合法边界不受 isfinite 影响。"""
        assert validate_formula("kalman(close, 0.01, 1e6)", COLS) == []
