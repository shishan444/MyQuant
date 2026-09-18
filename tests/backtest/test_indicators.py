"""Tests for indicator registry and computation engine."""

import pytest

pytestmark = [pytest.mark.unit]
import pandas as pd
import numpy as np
from MyQuant.core.features.indicators import (
    INDICATOR_REGISTRY,
    IndicatorDef,
    ParamDef,
    get_interchangeable,
    compute_all_indicators,
    _compute_indicator,
)

class TestIndicatorRegistry:
    """INDICATOR_REGISTRY structure and completeness tests."""

    def test_registry_has_expected_count(self):
        # 21 original + 9 new = 30 indicators
        assert len(INDICATOR_REGISTRY) >= 30

    def test_all_categories_present(self):
        categories = {defn.category for defn in INDICATOR_REGISTRY.values()}
        assert "trend" in categories
        assert "momentum" in categories
        assert "volatility" in categories
        assert "volume" in categories
        assert "trend_strength" in categories
        assert "structure" in categories

    def test_trend_indicators(self):
        trend = {name for name, d in INDICATOR_REGISTRY.items() if d.category == "trend"}
        assert "EMA" in trend
        assert "SMA" in trend
        assert "VWAP" in trend

    def test_momentum_indicators(self):
        mom = {name for name, d in INDICATOR_REGISTRY.items() if d.category == "momentum"}
        assert "RSI" in mom
        assert "MACD" in mom
        assert "Stochastic" in mom

    def test_new_volume_indicators(self):
        vol = {name for name, d in INDICATOR_REGISTRY.items() if d.category == "volume"}
        assert "RVOL" in vol
        assert "VROC" in vol
        assert "AD" in vol
        assert "CVD" in vol
        assert "VWMA" in vol

    def test_new_momentum_indicators(self):
        mom = {name for name, d in INDICATOR_REGISTRY.items() if d.category == "momentum"}
        assert "Aroon" in mom
        assert "CMO" in mom
        assert "TRIX" in mom

    def test_structure_indicators(self):
        struct = {name for name, d in INDICATOR_REGISTRY.items() if d.category == "structure"}
        assert "VolumeProfile" in struct

    def test_guard_only_indicators(self):
        guard_only = {name for name, d in INDICATOR_REGISTRY.items() if d.guard_only}
        assert "ATR" in guard_only
        assert "ADX" in guard_only
        assert "VolumeProfile" in guard_only

    def test_rsi_has_valid_param_range(self):
        rsi = INDICATOR_REGISTRY["RSI"]
        period_def = rsi.params["period"]
        assert period_def.min == 2
        assert period_def.max == 50
        assert period_def.default == 14
        assert period_def.step == 2

    def test_ema_has_valid_param_range(self):
        ema = INDICATOR_REGISTRY["EMA"]
        period_def = ema.params["period"]
        assert period_def.min == 5
        assert period_def.max == 200

    def test_macd_multi_output(self):
        macd = INDICATOR_REGISTRY["MACD"]
        assert "macd" in macd.output_fields
        assert "signal" in macd.output_fields
        assert "histogram" in macd.output_fields

    def test_bb_multi_output(self):
        bb = INDICATOR_REGISTRY["BB"]
        assert "upper" in bb.output_fields
        assert "lower" in bb.output_fields

    def test_aroon_multi_output(self):
        aroon = INDICATOR_REGISTRY["Aroon"]
        assert "aroon_up" in aroon.output_fields
        assert "aroon_down" in aroon.output_fields
        assert "aroon_osc" in aroon.output_fields

    def test_volume_profile_multi_output(self):
        vp = INDICATOR_REGISTRY["VolumeProfile"]
        assert "vp_poc" in vp.output_fields
        assert "vp_vah" in vp.output_fields
        assert "vp_val" in vp.output_fields

class TestGetInterchangeable:
    """Test same-category indicator lookup."""

    def test_rsi_interchangeable_with_momentum(self):
        interchangeable = get_interchangeable("RSI")
        assert "MACD" in interchangeable
        assert "RSI" not in interchangeable

    def test_ema_interchangeable_with_trend(self):
        interchangeable = get_interchangeable("EMA")
        assert "SMA" in interchangeable
        assert "EMA" not in interchangeable

    def test_atr_interchangeable_with_volatility(self):
        interchangeable = get_interchangeable("ATR")
        assert "BB" in interchangeable

    def test_rvol_interchangeable_with_volume(self):
        interchangeable = get_interchangeable("RVOL")
        assert "VROC" in interchangeable or "OBV" in interchangeable
        assert "RVOL" not in interchangeable

class TestComputeAllIndicators:
    """Test indicator computation on sample DataFrame."""

    @pytest.fixture
    def sample_ohlcv(self):
        """Generate synthetic OHLCV data for testing."""
        np.random.seed(42)
        n = 500
        dates = pd.date_range("2023-01-01", periods=n, freq="4h")
        close = 30000 + np.cumsum(np.random.randn(n) * 200)
        df = pd.DataFrame({
            "open": close + np.random.randn(n) * 50,
            "high": close + abs(np.random.randn(n) * 100),
            "low": close - abs(np.random.randn(n) * 100),
            "close": close,
            "volume": np.random.randint(100, 10000, n).astype(float),
        }, index=dates)
        return df

    def test_computes_rsi(self, sample_ohlcv):
        result = compute_all_indicators(sample_ohlcv)
        rsi_cols = [c for c in result.columns if c.startswith("rsi_")]
        assert len(rsi_cols) > 0
        valid_rsi = result[rsi_cols[0]].dropna()
        assert valid_rsi.min() >= 0
        assert valid_rsi.max() <= 100

    def test_computes_ema(self, sample_ohlcv):
        result = compute_all_indicators(sample_ohlcv)
        ema_cols = [c for c in result.columns if c.startswith("ema_")]
        assert len(ema_cols) > 0

    def test_computes_rvol(self, sample_ohlcv):
        result = compute_all_indicators(sample_ohlcv)
        rvol_cols = [c for c in result.columns if c.startswith("rvol_")]
        assert len(rvol_cols) > 0
        valid_rvol = result[rvol_cols[0]].dropna()
        assert valid_rvol.min() >= 0

    def test_computes_vroc(self, sample_ohlcv):
        result = compute_all_indicators(sample_ohlcv)
        vroc_cols = [c for c in result.columns if c.startswith("vroc_")]
        assert len(vroc_cols) > 0

    def test_computes_ad(self, sample_ohlcv):
        result = compute_all_indicators(sample_ohlcv)
        assert "ad" in result.columns

    def test_computes_cvd(self, sample_ohlcv):
        result = compute_all_indicators(sample_ohlcv)
        assert "cvd" in result.columns

    def test_computes_vwma(self, sample_ohlcv):
        result = compute_all_indicators(sample_ohlcv)
        vwma_cols = [c for c in result.columns if c.startswith("vwma_")]
        assert len(vwma_cols) > 0

    def test_computes_aroon(self, sample_ohlcv):
        result = compute_all_indicators(sample_ohlcv)
        aroon_cols = [c for c in result.columns if c.startswith("aroon_")]
        assert len(aroon_cols) > 0

    def test_computes_cmo(self, sample_ohlcv):
        result = compute_all_indicators(sample_ohlcv)
        cmo_cols = [c for c in result.columns if c.startswith("cmo_")]
        assert len(cmo_cols) > 0

    def test_computes_trix(self, sample_ohlcv):
        result = compute_all_indicators(sample_ohlcv)
        trix_cols = [c for c in result.columns if c.startswith("trix_")]
        assert len(trix_cols) > 0

    def test_preserves_original_columns(self, sample_ohlcv):
        result = compute_all_indicators(sample_ohlcv)
        for col in sample_ohlcv.columns:
            assert col in result.columns

    def test_no_nan_in_last_60_percent(self, sample_ohlcv):
        result = compute_all_indicators(sample_ohlcv)
        cutoff = int(len(result) * 0.4)
        tail = result.iloc[cutoff:]
        indicator_cols = [c for c in result.columns if c not in sample_ohlcv.columns]
        skip_cols = {"psar", "cvd"}
        for col in indicator_cols:
            if col in skip_cols:
                continue
            nan_pct = tail[col].isna().mean()
            assert nan_pct < 0.05, f"Column {col} has {nan_pct:.1%} NaN in tail"

    def test_volume_profile_compute(self, sample_ohlcv):
        """VolumeProfile is guard_only, compute it explicitly."""
        new_cols = _compute_indicator(sample_ohlcv, "VolumeProfile", {"bins": 50, "lookback": 60})
        poc_col = "vp_poc_50_60"
        assert poc_col in new_cols
        # Should have some non-NaN values in the tail
        valid = new_cols[poc_col].dropna()
        assert len(valid) > 0
