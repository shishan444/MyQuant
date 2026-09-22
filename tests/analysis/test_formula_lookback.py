"""公式表达式 lookback 静态推导测试（fix-plugin-column-translation 2.1）。

覆盖：AST 白名单窗口字面量取 max（rolling 嵌套/shift/ema/corr 三参）、
无窗口项返回 0（floor 兜底在调用处）、依赖闭包递归（插件引用插件）。
供验证回测 warmup 扩展接入（批次 2）。
"""

from pathlib import Path

import pytest

pytestmark = [pytest.mark.integration]

from core.analysis.plugin_service import (
    plugin_columns_lookback, register_formula_plugin)
from core.signals.formula import formula_lookback


@pytest.fixture()
def db(tmp_path: Path) -> Path:
    d = tmp_path / "q.db"
    from core.persistence.db_ext import init_db_ext
    from core.analysis.executor import ensure_research_tables
    init_db_ext(d)
    ensure_research_tables(d)
    return d


class TestFormulaLookback:
    """公式表达式窗口静态推导（2.1）：AST 白名单窗口字面量取 max。"""

    @pytest.mark.parametrize(
        ("expression", "expected"),
        [
            ("rolling_mean(rolling_std(close, 30), 60)", 60),  # 嵌套取 max
            ("shift(close, 5)", 5),
            ("diff(close, 12)", 12),
            ("ema(close, 26)", 26),
            ("zscore(close, 20)", 20),
            ("corr(close, volume, 40)", 40),  # corr 第三参
            ("abs(close - open)", 0),  # 无窗口项
            ("close", 0),  # 纯列名
            ("zscore(close, 20) + ema(close, 200)", 200),  # 多窗口取 max
        ],
    )
    def test_lookback_values(self, expression, expected):
        assert formula_lookback(expression) == expected

    def test_syntax_error_returns_zero(self):
        assert formula_lookback("rolling_mean(close,,)") == 0

    def test_plugin_columns_lookback_closure(self, db):
        """依赖闭包递归：fx_b 引用 fx_a → fx_a 的窗口一并计入。"""
        register_formula_plugin(db, name="a",
                                expression="rolling_mean(close, 50)")
        register_formula_plugin(db, name="b", expression="zscore(fx_a, 10)")
        assert plugin_columns_lookback(db, {"fx_a"}) == 50
        assert plugin_columns_lookback(db, {"fx_b"}) == 50, \
            "叠层引用：fx_b(10) 依赖 fx_a(50) → 取闭包 max"
        assert plugin_columns_lookback(db, {"fx_b", "fx_a"}) == 50

    def test_plugin_columns_lookback_unknown_column_zero(self, db):
        """列不存在贡献 0（求值端显式拒绝兜底；floor 兜底在调用处）。"""
        assert plugin_columns_lookback(db, {"fx_ghost"}) == 0
        assert plugin_columns_lookback(db, set()) == 0

    def test_plugin_columns_lookback_no_window(self, db):
        register_formula_plugin(db, name="raw", expression="abs(close - open)")
        assert plugin_columns_lookback(db, {"fx_raw"}) == 0
