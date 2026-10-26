"""_get_indicator_column 按需现算机制的特征测试（修复期特征网）。

被测面=executor 列解析链（core/strategy/executor）的按需现算段：
指标已注册但列未预计算（如变异产生非默认参数）时现算并写回 df。
覆盖五组行为锚（S1.1-S1.5）：
- 预计算列直读优先，不触发按需现算（零开销）
- 列缺失触发 _compute_indicator，结果写回 df
- 同 df 后续查找复用现算列，不重算
- 未注册指标显式 raise ValueError
- 子串兜底退役：精确列名 miss 落现算，不子串命中他参数列
"""
import pytest

import pandas as pd
import numpy as np
from unittest.mock import patch, MagicMock

from core.features.indicators import _compute_indicator, _DEFAULT_PARAMS
from core.features.registry import INDICATOR_REGISTRY
from tests.helpers.data_factory import make_ohlcv, make_signal_gene

pytestmark = [pytest.mark.unit]


# ---------------------------------------------------------------------------
# 辅助构造
# ---------------------------------------------------------------------------

def _gene_df_with_rsi_14():
    """返回 (gene, df)，df 已预计算 rsi_14 列。"""
    df = make_ohlcv(n=200)
    df["rsi_14"] = _compute_indicator(df, "RSI", {"period": 14})["rsi_14"]
    gene = make_signal_gene(indicator="RSI", params={"period": 14},
                            condition_type="lt", condition_value=30)
    return gene, df


def _gene_df_without_rsi_99():
    """返回 (gene, df)，df 不含 rsi_99 列（触发按需现算形）。"""
    df = make_ohlcv(n=200)
    gene = make_signal_gene(indicator="RSI", params={"period": 99},
                            condition_type="lt", condition_value=30)
    return gene, df


# ---------------------------------------------------------------------------
# S1.1: 预计算列优先直读（零开销）
# ---------------------------------------------------------------------------

class TestPrecomputedPriority:
    """预计算列必须直读返回，不触发按需现算。"""

    def test_precomputed_column_returned_directly(self):
        gene, df = _gene_df_with_rsi_14()
        from core.strategy.executor import _get_indicator_column
        result = _get_indicator_column(df, gene)
        assert result is not None
        assert len(result) == 200
        assert "rsi_14" in df.columns

    def test_on_demand_not_called_for_precomputed(self):
        gene, df = _gene_df_with_rsi_14()
        with patch("core.features.indicators._compute_indicator") as mock:
            from core.strategy.executor import _get_indicator_column
            _get_indicator_column(df, gene)
            mock.assert_not_called()


# ---------------------------------------------------------------------------
# S1.2: 按需现算兜底
# ---------------------------------------------------------------------------

class TestOnDemandCompute:
    """列缺失应触发 _compute_indicator 并返回结果。"""

    def test_missing_column_triggers_compute(self):
        gene, df = _gene_df_without_rsi_99()
        assert "rsi_99" not in df.columns

        from core.strategy.executor import _get_indicator_column, clear_indicator_cache
        clear_indicator_cache()
        result = _get_indicator_column(df, gene)

        # 现算后列应已在 df 中
        assert "rsi_99" in df.columns
        assert result is not None
        assert len(result) == 200

    def test_compute_result_written_to_df(self):
        gene, df = _gene_df_without_rsi_99()
        from core.strategy.executor import _get_indicator_column, clear_indicator_cache
        clear_indicator_cache()
        _get_indicator_column(df, gene)

        # 现算列必须写回 df 供复用
        assert "rsi_99" in df.columns

    def test_unknown_indicator_still_raises(self):
        df = make_ohlcv(n=200)
        gene = make_signal_gene(indicator="FakeIndicator", params={},
                                condition_type="lt", condition_value=1)
        from core.strategy.executor import _get_indicator_column, clear_indicator_cache
        clear_indicator_cache()
        with pytest.raises(ValueError):
            _get_indicator_column(df, gene)

    def test_compute_returns_empty_still_raises(self):
        """_compute_indicator 返回空 dict 时仍须 raise ValueError。"""
        gene, df = _gene_df_without_rsi_99()
        from core.strategy.executor import _get_indicator_column, clear_indicator_cache
        clear_indicator_cache()

        with patch("core.features.indicators._compute_indicator", return_value={}):
            with pytest.raises(ValueError):
                _get_indicator_column(df, gene)


# ---------------------------------------------------------------------------
# S1.3: 现算列同代内复用
# ---------------------------------------------------------------------------

class TestComputeReuse:
    """按需现算列必须可复用且不重算。"""

    def test_second_lookup_uses_cached_column(self):
        gene, df = _gene_df_without_rsi_99()
        from core.strategy.executor import _get_indicator_column, clear_indicator_cache
        clear_indicator_cache()

        # 首次查找触发现算
        result1 = _get_indicator_column(df, gene)
        assert "rsi_99" in df.columns

        # 二次查找不应重算（走缓存）
        with patch("core.features.indicators._compute_indicator") as mock:
            result2 = _get_indicator_column(df, gene)
            mock.assert_not_called()

        pd.testing.assert_series_equal(result1, result2)

    def test_different_genes_same_params_reuse_column(self):
        """同 (indicator, params) 的两个基因应共享同一 df 列。"""
        gene1, df = _gene_df_without_rsi_99()
        gene2 = make_signal_gene(indicator="RSI", params={"period": 99},
                                  role=gene1.role, condition_type="gt",
                                  condition_value=70)

        from core.strategy.executor import _get_indicator_column, clear_indicator_cache
        clear_indicator_cache()

        result1 = _get_indicator_column(df, gene1)
        assert "rsi_99" in df.columns

        with patch("core.features.indicators._compute_indicator") as mock:
            result2 = _get_indicator_column(df, gene2)
            mock.assert_not_called()

        assert len(result1) == len(result2) == 200


# ---------------------------------------------------------------------------
# S1.4: 多输出指标按需现算（如 MACD、BB）
# ---------------------------------------------------------------------------

class TestMultiOutputOnDemand:
    """多输出指标（MACD、BB）应正常走按需现算。"""

    def test_macd_non_default_params(self):
        df = make_ohlcv(n=300)
        gene = make_signal_gene(indicator="MACD",
                                params={"fast": 8, "slow": 21, "signal": 5},
                                condition_type="cross_above",
                                condition_value=0,
                                field_name="histogram")
        assert "macd_8_21_5_histogram" not in df.columns

        from core.strategy.executor import _get_indicator_column, clear_indicator_cache
        clear_indicator_cache()
        result = _get_indicator_column(df, gene)

        assert result is not None
        assert len(result) == 300

    def test_bb_non_default_std(self):
        df = make_ohlcv(n=200)
        gene = make_signal_gene(indicator="BB",
                                params={"period": 20, "std": 1.5},
                                condition_type="price_below",
                                field_name="lower")
        assert "bb_20_1.5_lower" not in df.columns

        from core.strategy.executor import _get_indicator_column, clear_indicator_cache
        clear_indicator_cache()
        result = _get_indicator_column(df, gene)

        assert result is not None
        assert len(result) == 200


# ---------------------------------------------------------------------------
# S1.5: 子串兜底退役（fix-executor-column-and-exit-guard A1/A2）
# ---------------------------------------------------------------------------

class TestSubstringFallbackRetirement:
    """精确列名 miss 必须落按需现算，不得子串命中他参数列。

    修复前缺陷：请求 rsi_2 时 "rsi_2" in "rsi_21" 子串命中，静默返回
    RSI(21) 列，现算路径永不可达（BUG-1 主证）。
    """

    def test_rsi_2_computed_exact_not_substring_rsi_21(self):
        df = make_ohlcv(n=200)
        for p in (7, 14, 21):
            df[f"rsi_{p}"] = _compute_indicator(df, "RSI", {"period": p})[f"rsi_{p}"]
        gene = make_signal_gene(indicator="RSI", params={"period": 2},
                                condition_type="lt", condition_value=10)

        from core.strategy.executor import (
            _get_indicator_column, clear_indicator_cache,
        )
        clear_indicator_cache()
        result = _get_indicator_column(df, gene)

        expected = _compute_indicator(df, "RSI", {"period": 2})["rsi_2"]
        # check_names=False：pandas-ta 产 Series.name 为库内命名（RSI_2）
        # 与 dict 键/df 列名（rsi_2）不同源——锚的本意是逐元素值一致
        pd.testing.assert_series_equal(
            result.reset_index(drop=True),
            expected.reset_index(drop=True), check_names=False)
        assert result.name == "rsi_2"
        assert "rsi_2" in df.columns

    def test_ema_5_computed_exact_not_substring_ema_50(self):
        df = make_ohlcv(n=200)
        for p in (10, 20, 50, 100, 200):
            df[f"ema_{p}"] = _compute_indicator(df, "EMA", {"period": p})[f"ema_{p}"]
        gene = make_signal_gene(indicator="EMA", params={"period": 5},
                                condition_type="gt", condition_value=0)

        from core.strategy.executor import (
            _get_indicator_column, clear_indicator_cache,
        )
        clear_indicator_cache()
        result = _get_indicator_column(df, gene)

        expected = _compute_indicator(df, "EMA", {"period": 5})["ema_5"]
        pd.testing.assert_series_equal(
            result.reset_index(drop=True),
            expected.reset_index(drop=True), check_names=False)
        assert result.name == "ema_5"
        assert "ema_5" in df.columns

    def test_a9_naming_desync_fails_loud_with_available_columns(self):
        """A9：naming 失同步形态——resolve 产物与现算产列名仅子串
        关系（"macd_8_21_5_histogram" vs 实际 "macd_8_21_5" /
        "macd_histogram_8_21_5"）→ 显式 ValueError 附实际可用列清单
        （互含取列退役：不静默拿错语义分量，fail-loud 暴露失同步）。
        """
        df = make_ohlcv(n=300)
        gene = make_signal_gene(indicator="MACD",
                                params={"fast": 8, "slow": 21, "signal": 5},
                                condition_type="cross_above",
                                condition_value=0,
                                field_name="histogram")
        from core.strategy.executor import (
            _get_indicator_column, clear_indicator_cache,
        )
        clear_indicator_cache()
        # 故障注入：resolve 层失同步产出词序畸形列名（仅与现算产列
        # 成子串关系——旧互含分支会静默取 macd_8_21_5 错语义分量）
        with patch("core.strategy.executor.resolve_indicator_column",
                   return_value="macd_8_21_5_histogram"):
            with pytest.raises(ValueError) as exc_info:
                _get_indicator_column(df, gene)
        msg = str(exc_info.value)
        assert "Available" in msg
        assert "macd_8_21_5" in msg        # 实际可用列清单在案
