"""Tests for Iteration 1b infrastructure.

Validates:
1. resolve_indicator_column() produces correct names for all 40 indicators
2. _CONTEXT_SCHEMA declarative mapping works correctly
3. Per-series normalization for momentum context
"""
import pytest

pytestmark = [pytest.mark.unit]

import numpy as np
import pandas as pd

from core.features.registry import (
    INDICATOR_REGISTRY,
    resolve_indicator_column,
)


# ---------------------------------------------------------------------------
# 1. resolve_indicator_column — default naming (21 indicators)
# ---------------------------------------------------------------------------

class TestResolveColumnDefaultNaming:
    """Default naming mode: {output_fields[0]}_{period} or {output_fields[0]}."""

    @pytest.mark.parametrize("name,params,expected", [
        # Trend with params (5)
        ("EMA", {"period": 20}, "ema_20"),
        ("SMA", {"period": 50}, "sma_50"),
        ("WMA", {"period": 10}, "wma_10"),
        ("DEMA", {"period": 50}, "dema_50"),
        ("TEMA", {"period": 50}, "tema_50"),
        # Momentum with params (6)
        ("RSI", {"period": 14}, "rsi_14"),
        ("CCI", {"period": 20}, "cci_20"),
        ("ROC", {"period": 12}, "roc_12"),
        ("Williams %R", {"period": 14}, "willr_14"),
        ("CMO", {"period": 14}, "cmo_14"),
        ("TRIX", {"period": 12}, "trix_12"),
        # Volume with params (4)
        ("CMF", {"period": 20}, "cmf_20"),
        ("MFI", {"period": 14}, "mfi_14"),
        ("RVOL", {"period": 20}, "rvol_20"),
        ("VROC", {"period": 14}, "vroc_14"),
        # Other with params (3)
        ("ATR", {"period": 14}, "atr_14"),
        ("ADX", {"period": 14}, "adx_14"),
        ("VWMA", {"period": 20}, "vwma_20"),
        # Volume without params (3)
        ("OBV", {}, "obv"),
        ("AD", {}, "ad"),
        ("CVD", {}, "cvd"),
        # Guard-only trend (1)
        ("VWAP", {}, "vwap"),
    ])
    def test_default_naming(self, name, params, expected):
        result = resolve_indicator_column(name, params, naming="default")
        assert result == expected, f"{name}: expected '{expected}', got '{result}'"


# ---------------------------------------------------------------------------
# 2. resolve_indicator_column — special naming modes
# ---------------------------------------------------------------------------

class TestResolveColumnBB:
    """BB naming: bb_{field}_{period}_{std} (std integer format)."""

    def test_bb_upper_default_std(self):
        assert resolve_indicator_column(
            "BB", {"period": 20, "std": 2.0}, "upper", "bb"
        ) == "bb_upper_20_2"

    def test_bb_middle_default_std(self):
        assert resolve_indicator_column(
            "BB", {"period": 20, "std": 2.0}, "middle", "bb"
        ) == "bb_middle_20_2"

    def test_bb_lower_default_std(self):
        assert resolve_indicator_column(
            "BB", {"period": 20, "std": 2.0}, "lower", "bb"
        ) == "bb_lower_20_2"

    def test_bb_non_integer_std(self):
        assert resolve_indicator_column(
            "BB", {"period": 20, "std": 1.5}, "upper", "bb"
        ) == "bb_upper_20_1.5"

    def test_bb_no_field_defaults_upper(self):
        assert resolve_indicator_column(
            "BB", {"period": 20, "std": 2.0}, "", "bb"
        ) == "bb_upper_20_2"


class TestResolveColumnMACD:
    """MACD naming: macd_{field}_{fast}_{slow}_{signal}."""

    def test_macd_histogram(self):
        assert resolve_indicator_column(
            "MACD", {"fast": 12, "slow": 26, "signal": 9}, "histogram", "macd"
        ) == "macd_histogram_12_26_9"

    def test_macd_default_field_is_histogram(self):
        assert resolve_indicator_column(
            "MACD", {"fast": 12, "slow": 26, "signal": 9}, "", "macd"
        ) == "macd_histogram_12_26_9"

    def test_macd_line(self):
        assert resolve_indicator_column(
            "MACD", {"fast": 12, "slow": 26, "signal": 9}, "macd", "macd"
        ) == "macd_12_26_9"

    def test_macd_signal(self):
        assert resolve_indicator_column(
            "MACD", {"fast": 12, "slow": 26, "signal": 9}, "signal", "macd"
        ) == "macd_signal_12_26_9"


class TestResolveColumnStoch:
    """Stochastic naming: stoch_{k/d}_{k_period}_{d_period}."""

    def test_stoch_k(self):
        assert resolve_indicator_column(
            "Stochastic", {"k_period": 14, "d_period": 3}, "k", "stoch"
        ) == "stoch_k_14_3"

    def test_stoch_d(self):
        assert resolve_indicator_column(
            "Stochastic", {"k_period": 14, "d_period": 3}, "d", "stoch"
        ) == "stoch_d_14_3"

    def test_stoch_default_field_is_k(self):
        assert resolve_indicator_column(
            "Stochastic", {"k_period": 14, "d_period": 3}, "", "stoch"
        ) == "stoch_k_14_3"


class TestResolveColumnKC:
    """Keltner naming: kc_{ema}_{atr}_{mult}_{field} (float mult, no .replace)."""

    def test_kc_upper(self):
        assert resolve_indicator_column(
            "Keltner",
            {"ema_period": 20, "atr_period": 10, "multiplier": 2.0},
            "upper", "kc",
        ) == "kc_20_10_2.0_upper"

    def test_kc_middle(self):
        assert resolve_indicator_column(
            "Keltner",
            {"ema_period": 20, "atr_period": 10, "multiplier": 2.0},
            "middle", "kc",
        ) == "kc_20_10_2.0_middle"

    def test_kc_lower(self):
        assert resolve_indicator_column(
            "Keltner",
            {"ema_period": 20, "atr_period": 10, "multiplier": 2.0},
            "lower", "kc",
        ) == "kc_20_10_2.0_lower"


class TestResolveColumnDC:
    """Donchian naming: dc_{field}_{period}."""

    def test_dc_upper(self):
        assert resolve_indicator_column(
            "Donchian", {"period": 20}, "upper", "dc"
        ) == "dc_upper_20"

    def test_dc_lower(self):
        assert resolve_indicator_column(
            "Donchian", {"period": 20}, "lower", "dc"
        ) == "dc_lower_20"


class TestResolveColumnVP:
    """VolumeProfile naming: {field}_{bins}_{lookback}."""

    def test_vp_poc(self):
        assert resolve_indicator_column(
            "VolumeProfile", {"bins": 50, "lookback": 60}, "vp_poc", "vp"
        ) == "vp_poc_50_60"

    def test_vp_vah(self):
        assert resolve_indicator_column(
            "VolumeProfile", {"bins": 50, "lookback": 60}, "vp_vah", "vp"
        ) == "vp_vah_50_60"

    def test_vp_default_field_is_poc(self):
        assert resolve_indicator_column(
            "VolumeProfile", {"bins": 50, "lookback": 60}, "", "vp"
        ) == "vp_poc_50_60"


class TestResolveColumnPSAR:
    """PSAR naming: fixed 'psar' regardless of params."""

    def test_psar_ignores_params(self):
        assert resolve_indicator_column(
            "PSAR", {"step": 0.02, "max_step": 0.2}, "", "psar"
        ) == "psar"


class TestResolveColumnAroon:
    """Aroon naming: {field}_{period} where field = aroon_up/aroon_down."""

    def test_aroon_up(self):
        assert resolve_indicator_column(
            "Aroon", {"period": 25}, "aroon_up", "aroon"
        ) == "aroon_up_25"

    def test_aroon_down(self):
        assert resolve_indicator_column(
            "Aroon", {"period": 25}, "aroon_down", "aroon"
        ) == "aroon_down_25"

    def test_aroon_default_field(self):
        assert resolve_indicator_column(
            "Aroon", {"period": 25}, "", "aroon"
        ) == "aroon_up_25"


class TestResolveColumnPattern:
    """Pattern naming: pattern_{snake_case} fixed names."""

    @pytest.mark.parametrize("name,expected", [
        ("BearishEngulfing", "pattern_bearish_engulfing"),
        ("EveningStar", "pattern_evening_star"),
        ("ThreeBlackCrows", "pattern_3blackcrows"),
        ("ShootingStar", "pattern_shooting_star"),
        ("ThreeWhiteSoldiers", "pattern_3whitesoldiers"),
        ("MorningStar", "pattern_morning_star"),
        ("BullishReversal", "pattern_bullish_reversal"),
        ("BearishReversal", "pattern_bearish_reversal"),
        ("BullishDivergence", "pattern_bullish_divergence"),
        ("BearishDivergence", "pattern_bearish_divergence"),
    ])
    def test_pattern_column(self, name, expected):
        assert resolve_indicator_column(name, {}, "", "pattern") == expected


# ---------------------------------------------------------------------------
# 3. Registry naming field completeness
# ---------------------------------------------------------------------------

class TestRegistryNamingField:
    """Every registry entry should have a valid naming mode."""

    def test_all_indicators_have_valid_naming(self):
        valid_modes = {"default", "bb", "macd", "stoch", "kc", "dc",
                       "vp", "pattern", "psar", "aroon", "mfe", "mf_osc"}
        for name, defn in INDICATOR_REGISTRY.items():
            assert defn.naming in valid_modes, \
                f"{name} has invalid naming mode: {defn.naming}"

    def test_expected_naming_modes(self):
        """Verify specific naming modes for non-default indicators."""
        expected = {
            "BB": "bb", "MACD": "macd", "Stochastic": "stoch",
            "Keltner": "kc", "Donchian": "dc", "VolumeProfile": "vp",
            "PSAR": "psar", "Aroon": "aroon",
            "BearishEngulfing": "pattern", "EveningStar": "pattern",
            "ThreeBlackCrows": "pattern", "ShootingStar": "pattern",
            "ThreeWhiteSoldiers": "pattern", "MorningStar": "pattern",
            "BullishReversal": "pattern", "BearishReversal": "pattern",
            "BullishDivergence": "pattern", "BearishDivergence": "pattern",
        }
        for name, mode in expected.items():
            assert INDICATOR_REGISTRY[name].naming == mode, \
                f"{name}.naming should be '{mode}'"

    def test_default_indicators_count(self):
        """Exactly 27 indicators should use default naming (22 + 5 derivatives)."""
        default_count = sum(
            1 for d in INDICATOR_REGISTRY.values() if d.naming == "default"
        )
        assert default_count == 27, \
            f"Expected 27 default-naming indicators (22 + 5 derivatives), got {default_count}"


# ---------------------------------------------------------------------------
# 4. _CONTEXT_SCHEMA
# ---------------------------------------------------------------------------

class TestContextSchema:
    """Declarative context mapping schema validation."""

    def test_schema_exists(self):
        from core.strategy.mtf_engine import _CONTEXT_SCHEMA
        assert isinstance(_CONTEXT_SCHEMA, dict)

    def test_trend_provides_direction_and_price_levels(self):
        from core.strategy.mtf_engine import _CONTEXT_SCHEMA
        assert _CONTEXT_SCHEMA["trend"] == {"direction", "price_levels"}

    def test_volatility_provides_price_levels(self):
        from core.strategy.mtf_engine import _CONTEXT_SCHEMA
        assert _CONTEXT_SCHEMA["volatility"] == {"price_levels"}

    def test_momentum_provides_momentum(self):
        from core.strategy.mtf_engine import _CONTEXT_SCHEMA
        assert _CONTEXT_SCHEMA["momentum"] == {"momentum"}

    def test_volume_provides_momentum(self):
        from core.strategy.mtf_engine import _CONTEXT_SCHEMA
        assert _CONTEXT_SCHEMA["volume"] == {"momentum"}

    def test_trend_strength_provides_momentum(self):
        from core.strategy.mtf_engine import _CONTEXT_SCHEMA
        assert _CONTEXT_SCHEMA["trend_strength"] == {"momentum"}

    def test_pattern_not_in_schema(self):
        from core.strategy.mtf_engine import _CONTEXT_SCHEMA
        assert "pattern" not in _CONTEXT_SCHEMA

    def test_structure_not_in_schema(self):
        from core.strategy.mtf_engine import _CONTEXT_SCHEMA
        assert "structure" not in _CONTEXT_SCHEMA


# ---------------------------------------------------------------------------
# 5. Per-series normalization for momentum context
# ---------------------------------------------------------------------------

class TestPerSeriesNormalization:
    """Momentum context is normalized to [-1, +1] via rolling min-max."""

    def _make_df_with_indicator(self, col_name, values):
        n = len(values)
        idx = pd.date_range("2024-01-01", periods=n, freq="4h")
        return pd.DataFrame({
            col_name: values,
            "close": [60000.0] * n,
        }, index=idx)

    def _make_gene(self, indicator, params, condition_type="gt"):
        from core.strategy.dna import SignalGene, SignalRole
        return SignalGene(
            indicator=indicator,
            params=params,
            role=SignalRole.ENTRY_TRIGGER,
            condition={"type": condition_type},
        )

    def test_obv_now_provides_momentum(self):
        """After 1b, OBV provides normalized momentum context."""
        df = self._make_df_with_indicator("obv", list(range(200)))
        gene = self._make_gene("OBV", {})
        from core.strategy.mtf_engine import extract_context
        ctx = extract_context(df, gene, "volume")
        assert "momentum" in ctx, "OBV should provide momentum after 1b normalization"

    def test_normalization_range(self):
        """Normalized momentum should be in [-1, +1]."""
        values = list(np.random.randn(200) * 1000)
        df = self._make_df_with_indicator("rsi_14", values)
        gene = self._make_gene("RSI", {"period": 14})
        from core.strategy.mtf_engine import extract_context
        ctx = extract_context(df, gene, "momentum")
        assert "momentum" in ctx
        mom = ctx["momentum"]
        assert mom.min() >= -1.01, f"Min {mom.min()} < -1"
        assert mom.max() <= 1.01, f"Max {mom.max()} > 1"

    def test_cmf_still_provides_momentum(self):
        """CMF still provides momentum after 1b (backward compat)."""
        df = self._make_df_with_indicator("cmf_20", [0.1, -0.2, 0.3, 0.0, -0.1] * 40)
        gene = self._make_gene("CMF", {"period": 20})
        from core.strategy.mtf_engine import extract_context
        ctx = extract_context(df, gene, "volume")
        assert "momentum" in ctx

    def test_adx_still_provides_momentum(self):
        """ADX still provides momentum after 1b (backward compat)."""
        df = self._make_df_with_indicator("adx_14", [25.0, 30.0, 20.0, 35.0, 28.0] * 40)
        gene = self._make_gene("ADX", {"period": 14})
        from core.strategy.mtf_engine import extract_context
        ctx = extract_context(df, gene, "trend_strength")
        assert "momentum" in ctx

    def test_constant_series_no_division_by_zero(self):
        """Constant indicator values should not cause division by zero."""
        df = self._make_df_with_indicator("obv", [1000.0] * 200)
        gene = self._make_gene("OBV", {})
        from core.strategy.mtf_engine import extract_context
        ctx = extract_context(df, gene, "volume")
        assert "momentum" in ctx
        # Constant series → normalized to 0 (since range=0, we replace with 1)
        mom = ctx["momentum"]
        assert not mom.isna().any(), "Should have no NaN values"

    def test_trend_indicator_no_momentum(self):
        """Trend indicators should NOT provide momentum context."""
        df = self._make_df_with_indicator("ema_20", list(range(200)))
        gene = self._make_gene("EMA", {"period": 20})
        from core.strategy.mtf_engine import extract_context
        ctx = extract_context(df, gene, "trend")
        assert "momentum" not in ctx

    def test_trend_direction_preserved(self):
        """Trend direction extraction still works after schema refactor."""
        close_vals = list(range(200))
        df = self._make_df_with_indicator("ema_20", list(range(100, 300)))
        df["close"] = pd.Series(close_vals, index=df.index)
        gene = self._make_gene("EMA", {"period": 20}, "price_above")
        from core.strategy.mtf_engine import extract_context
        ctx = extract_context(df, gene, "trend")
        assert "direction" in ctx
