"""Tests for MTF engine: L1 DNA extension, L2 core algorithms, L3 layer evaluator, L4 synthesis."""

import pytest

pytestmark = [pytest.mark.integration]
import pandas as pd
import numpy as np

from core.strategy.dna import (
    StrategyDNA, TimeframeLayer, SignalGene, SignalRole,
    LogicGenes, ExecutionGenes, RiskGenes, derive_role,
)

# =====================================================================
# L1: DNA Data Structure Extension
# =====================================================================

class TestLayerRoleDerivation:
    """derive_role(timeframe) maps timeframe to structure/zone/execution."""

    def test_derive_role_3d_returns_structure(self):
        assert derive_role("3d") == "structure"

    def test_derive_role_1d_returns_structure(self):
        assert derive_role("1d") == "structure"

    def test_derive_role_4h_returns_zone(self):
        assert derive_role("4h") == "zone"

    def test_derive_role_1h_returns_zone(self):
        assert derive_role("1h") == "zone"

    def test_derive_role_15m_returns_execution(self):
        assert derive_role("15m") == "execution"

    def test_derive_role_30m_returns_execution(self):
        assert derive_role("30m") == "execution"

class TestStrategyDNAMTFFields:
    """StrategyDNA gains mtf_mode, confluence_threshold, proximity_mult."""

    def test_dna_has_mtf_mode_field(self):
        dna = StrategyDNA()
        assert hasattr(dna, "mtf_mode")

    def test_dna_has_confluence_threshold_field(self):
        dna = StrategyDNA()
        assert hasattr(dna, "confluence_threshold")

    def test_dna_has_proximity_mult_field(self):
        dna = StrategyDNA()
        assert hasattr(dna, "proximity_mult")

    def test_dna_new_fields_default_values(self):
        dna = StrategyDNA()
        assert dna.mtf_mode is None
        assert dna.confluence_threshold == 0.3
        assert dna.proximity_mult == 1.5

class TestDNABackwardCompatibility:
    """Old DNA records deserialize correctly with new fields."""

    def test_dna_from_dict_trend_maps_to_structure(self):
        data = {
            "strategy_id": "test-1",
            "signal_genes": [],
            "logic_genes": {"entry_logic": "AND", "exit_logic": "AND",
                            "add_logic": "AND", "reduce_logic": "AND"},
            "execution_genes": {"timeframe": "15m", "symbol": "BTCUSDT"},
            "risk_genes": {"stop_loss": 0.05, "take_profit": None,
                           "position_size": 0.3, "leverage": 1, "direction": "long"},
            "layers": [
                {
                    "timeframe": "1d",
                    "signal_genes": [],
                    "logic_genes": {"entry_logic": "AND", "exit_logic": "AND",
                                    "add_logic": "AND", "reduce_logic": "AND"},
                    "role": "trend",
                },
                {
                    "timeframe": "15m",
                    "signal_genes": [],
                    "logic_genes": {"entry_logic": "AND", "exit_logic": "AND",
                                    "add_logic": "AND", "reduce_logic": "AND"},
                },
            ],
            "cross_layer_logic": "AND",
        }
        dna = StrategyDNA.from_dict(data)
        assert dna.layers[0].role == "structure"  # "trend" -> "structure"

    def test_dna_from_dict_missing_new_fields_use_defaults(self):
        data = {
            "strategy_id": "test-2",
            "signal_genes": [],
            "logic_genes": {"entry_logic": "AND", "exit_logic": "AND",
                            "add_logic": "AND", "reduce_logic": "AND"},
            "execution_genes": {"timeframe": "4h", "symbol": "BTCUSDT"},
            "risk_genes": {"stop_loss": 0.05, "take_profit": None,
                           "position_size": 0.3, "leverage": 1, "direction": "long"},
        }
        dna = StrategyDNA.from_dict(data)
        assert dna.mtf_mode is None
        assert dna.confluence_threshold == 0.3
        assert dna.proximity_mult == 1.5

    def test_dna_to_dict_includes_new_fields(self):
        dna = StrategyDNA(
            mtf_mode="direction",
            confluence_threshold=0.5,
            proximity_mult=2.0,
        )
        d = dna.to_dict()
        assert d["mtf_mode"] == "direction"
        assert d["confluence_threshold"] == 0.5
        assert d["proximity_mult"] == 2.0

    def test_dna_serialization_roundtrip_with_new_fields(self):
        dna = StrategyDNA(
            mtf_mode="direction+confluence",
            confluence_threshold=0.7,
            proximity_mult=1.2,
        )
        roundtripped = StrategyDNA.from_dict(dna.to_dict())
        assert roundtripped.mtf_mode == "direction+confluence"
        assert roundtripped.confluence_threshold == 0.7
        assert roundtripped.proximity_mult == 1.2

class TestValidatorMTFUpdates:
    """Validator accepts new roles and MTF control parameters."""

    def test_validator_accepts_structure_zone_execution_roles(self):
        from core.strategy.validator import validate_dna
        for role in ("structure", "zone", "execution"):
            dna = StrategyDNA(
                layers=[
                    TimeframeLayer(
                        timeframe="1d",
                        signal_genes=[
                            SignalGene("EMA", {"period": 20}, SignalRole.ENTRY_TRIGGER,
                                       condition={"type": "price_above"}),
                            SignalGene("EMA", {"period": 20}, SignalRole.EXIT_TRIGGER,
                                       condition={"type": "price_below"}),
                        ],
                        role=role,
                    ),
                    TimeframeLayer(
                        timeframe="15m",
                        signal_genes=[
                            SignalGene("RSI", {"period": 14}, SignalRole.ENTRY_TRIGGER,
                                       condition={"type": "gt", "threshold": 50}),
                            SignalGene("RSI", {"period": 14}, SignalRole.EXIT_TRIGGER,
                                       condition={"type": "lt", "threshold": 50}),
                        ],
                    ),
                ],
            )
            result = validate_dna(dna)
            assert result.is_valid, f"Role '{role}' should be valid, errors: {result.errors}"

    def test_validator_accepts_mtf_mode_values(self):
        from core.strategy.validator import validate_dna
        for mode in (None, "direction", "confluence", "direction+confluence"):
            dna = StrategyDNA(
                mtf_mode=mode,
                signal_genes=[
                    SignalGene("RSI", {"period": 14}, SignalRole.ENTRY_TRIGGER,
                               condition={"type": "gt", "threshold": 50}),
                    SignalGene("RSI", {"period": 14}, SignalRole.EXIT_TRIGGER,
                               condition={"type": "lt", "threshold": 50}),
                ],
            )
            result = validate_dna(dna)
            assert result.is_valid, f"mtf_mode='{mode}' should be valid, errors: {result.errors}"

    def test_validator_rejects_invalid_mtf_mode(self):
        from core.strategy.validator import validate_dna
        dna = StrategyDNA(
            mtf_mode="invalid",
            signal_genes=[
                SignalGene("RSI", {"period": 14}, SignalRole.ENTRY_TRIGGER,
                           condition={"type": "gt", "threshold": 50}),
                SignalGene("RSI", {"period": 14}, SignalRole.EXIT_TRIGGER,
                           condition={"type": "lt", "threshold": 50}),
            ],
        )
        result = validate_dna(dna)
        assert not result.is_valid

    def test_validator_checks_confluence_threshold_range(self):
        from core.strategy.validator import validate_dna
        base_genes = [
            SignalGene("RSI", {"period": 14}, SignalRole.ENTRY_TRIGGER,
                       condition={"type": "gt", "threshold": 50}),
            SignalGene("RSI", {"period": 14}, SignalRole.EXIT_TRIGGER,
                       condition={"type": "lt", "threshold": 50}),
        ]
        # Too low
        dna = StrategyDNA(confluence_threshold=0.05, signal_genes=base_genes[:])
        result = validate_dna(dna)
        assert not result.is_valid
        # Too high
        dna = StrategyDNA(confluence_threshold=0.95, signal_genes=base_genes[:])
        result = validate_dna(dna)
        assert not result.is_valid
        # Valid boundary
        dna = StrategyDNA(confluence_threshold=0.1, signal_genes=base_genes[:])
        result = validate_dna(dna)
        assert result.is_valid

    def test_validator_checks_proximity_mult_range(self):
        from core.strategy.validator import validate_dna
        base_genes = [
            SignalGene("RSI", {"period": 14}, SignalRole.ENTRY_TRIGGER,
                       condition={"type": "gt", "threshold": 50}),
            SignalGene("RSI", {"period": 14}, SignalRole.EXIT_TRIGGER,
                       condition={"type": "lt", "threshold": 50}),
        ]
        # Too low
        dna = StrategyDNA(proximity_mult=0.3, signal_genes=base_genes[:])
        result = validate_dna(dna)
        assert not result.is_valid
        # Too high
        dna = StrategyDNA(proximity_mult=4.0, signal_genes=base_genes[:])
        result = validate_dna(dna)
        assert not result.is_valid
        # Valid boundary
        dna = StrategyDNA(proximity_mult=0.5, signal_genes=base_genes[:])
        result = validate_dna(dna)
        assert result.is_valid

# =====================================================================
# L2: Confluence Engine Core Algorithms
# =====================================================================

class TestComputeSPct:
    """s% = (ATR / close) * proximity_mult"""

    def test_compute_s_pct_basic(self):
        from core.strategy.mtf_engine import compute_s_pct
        # ATR=800, close=60000, mult=1.5 -> s% = (800/60000)*1.5 = 0.02
        result = compute_s_pct(atr=800.0, close=60000.0, proximity_mult=1.5)
        assert abs(result - 0.02) < 1e-6

    def test_compute_s_pct_high_volatility(self):
        from core.strategy.mtf_engine import compute_s_pct
        s_high = compute_s_pct(atr=2000.0, close=60000.0, proximity_mult=1.5)
        s_low = compute_s_pct(atr=400.0, close=60000.0, proximity_mult=1.5)
        assert s_high > s_low

    def test_compute_s_pct_low_volatility(self):
        from core.strategy.mtf_engine import compute_s_pct
        s = compute_s_pct(atr=100.0, close=60000.0, proximity_mult=1.5)
        assert s < 0.005

    def test_compute_s_pct_different_proximity_mult(self):
        from core.strategy.mtf_engine import compute_s_pct
        s1 = compute_s_pct(atr=800.0, close=60000.0, proximity_mult=1.0)
        s2 = compute_s_pct(atr=800.0, close=60000.0, proximity_mult=2.0)
        assert abs(s2 - 2 * s1) < 1e-6

class TestBuildPriceZone:
    """Build price zone [P*(1-s%), P*(1+s%)] from price level and s%."""

    def test_build_price_zone_single_level(self):
        from core.strategy.mtf_engine import build_price_zone
        # P=60000, s%=0.02 -> [58800, 61200]
        low, high = build_price_zone(60000.0, 0.02)
        assert abs(low - 58800.0) < 1e-6
        assert abs(high - 61200.0) < 1e-6

    def test_build_price_zone_with_buffer(self):
        from core.strategy.mtf_engine import build_price_zone
        low, high = build_price_zone(50000.0, 0.05)
        assert abs(low - 47500.0) < 1e-6
        assert abs(high - 52500.0) < 1e-6

class TestMergeIntervals:
    """Merge overlapping intervals into union."""

    def test_merge_intervals_single_interval(self):
        from core.strategy.mtf_engine import merge_intervals
        result = merge_intervals([(1.0, 5.0)])
        assert result == [(1.0, 5.0)]

    def test_merge_intervals_two_overlapping(self):
        from core.strategy.mtf_engine import merge_intervals
        result = merge_intervals([(1.0, 4.0), (3.0, 6.0)])
        assert result == [(1.0, 6.0)]

    def test_merge_intervals_two_disjoint(self):
        from core.strategy.mtf_engine import merge_intervals
        result = merge_intervals([(1.0, 3.0), (5.0, 8.0)])
        assert result == [(1.0, 3.0), (5.0, 8.0)]

    def test_merge_intervals_three_with_gap(self):
        from core.strategy.mtf_engine import merge_intervals
        result = merge_intervals([(1.0, 3.0), (2.0, 5.0), (7.0, 9.0)])
        assert result == [(1.0, 5.0), (7.0, 9.0)]

class TestIntersectIntervals:
    """Compute intersection of two interval sets."""

    def test_intersect_intervals_overlap(self):
        from core.strategy.mtf_engine import intersect_intervals
        result = intersect_intervals([(1.0, 5.0)], [(3.0, 8.0)])
        assert result == [(3.0, 5.0)]

    def test_intersect_intervals_no_overlap(self):
        from core.strategy.mtf_engine import intersect_intervals
        result = intersect_intervals([(1.0, 3.0)], [(5.0, 8.0)])
        assert result == []

    def test_intersect_intervals_contained(self):
        from core.strategy.mtf_engine import intersect_intervals
        result = intersect_intervals([(2.0, 4.0)], [(1.0, 5.0)])
        assert result == [(2.0, 4.0)]

class TestConfluenceScore:
    """Confluence score from interval overlap and price position."""

    def test_confluence_score_full_overlap(self):
        from core.strategy.mtf_engine import compute_confluence_score
        layer_zones = [
            [(59000.0, 61000.0)],
            [(59200.0, 60800.0)],
        ]
        score = compute_confluence_score(layer_zones, current_price=60000.0,
                                          max_zone_width=2000.0)
        assert score >= 0.8

    def test_confluence_score_partial_overlap(self):
        from core.strategy.mtf_engine import compute_confluence_score
        layer_zones = [
            [(58000.0, 60000.0)],
            [(59500.0, 61500.0)],
        ]
        score = compute_confluence_score(layer_zones, current_price=59750.0,
                                          max_zone_width=4000.0)
        assert 0.0 < score < 1.0

    def test_confluence_score_no_overlap(self):
        from core.strategy.mtf_engine import compute_confluence_score
        layer_zones = [
            [(57000.0, 58000.0)],
            [(62000.0, 63000.0)],
        ]
        score = compute_confluence_score(layer_zones, current_price=60000.0,
                                          max_zone_width=6000.0)
        assert score == 0.0

    def test_confluence_score_price_outside_overlap(self):
        from core.strategy.mtf_engine import compute_confluence_score
        # Price is far from overlap region
        layer_zones = [
            [(57000.0, 58000.0)],
            [(57500.0, 58500.0)],
        ]
        score = compute_confluence_score(layer_zones, current_price=62000.0,
                                          max_zone_width=3000.0)
        assert score == 0.0

    def test_confluence_score_price_inside_overlap(self):
        from core.strategy.mtf_engine import compute_confluence_score
        layer_zones = [
            [(59000.0, 61000.0)],
            [(59500.0, 60500.0)],
        ]
        score = compute_confluence_score(layer_zones, current_price=60000.0,
                                          max_zone_width=2000.0)
        assert score > 0.0

    def test_confluence_score_btc_example(self):
        from core.strategy.mtf_engine import compute_confluence_score
        # 3d EMA at 58000, s%=0.02 -> zone [56840, 59160]
        # 4h BB upper at 61000, s%=0.03 -> zone [59170, 62830]
        # Overlap is tiny/none -> score should be low/0
        layer_zones = [
            [(56840.0, 59160.0)],
            [(59170.0, 62830.0)],
        ]
        score = compute_confluence_score(layer_zones, current_price=60000.0,
                                          max_zone_width=6000.0)
        assert score < 0.1  # Almost no overlap

class TestProximityScore:
    """Single-layer proximity fallback (Type B gap B)."""

    def test_proximity_score_single_layer(self):
        from core.strategy.mtf_engine import compute_proximity_score
        levels = [pd.Series([58000.0, 59000.0, 60000.0])]
        price = pd.Series([59800.0, 59900.0, 60100.0])
        s_pct = 0.03
        scores = compute_proximity_score(levels, price, s_pct)
        assert len(scores) == 3
        # At bar 2, price=60100, level=60000, distance=100/60000=0.00167 < 0.03 -> close
        assert scores.iloc[2] > 0.0

class TestDirectionConflict:
    """Resolve direction conflicts among multiple structure layers."""

    def test_direction_score_highest_tf_wins(self):
        from core.strategy.mtf_engine import resolve_direction_conflict
        idx = pd.date_range("2024-01-01", periods=5, freq="4h")
        dir_3d = pd.Series([1.0, 1.0, 1.0, 1.0, 1.0], index=idx)
        dir_1d = pd.Series([-1.0, -1.0, -1.0, -1.0, -1.0], index=idx)
        layers = [("3d", dir_3d), ("1d", dir_1d)]
        result = resolve_direction_conflict(layers)
        # 3d has higher timeframe, should win
        assert (result == 1.0).all()

    def test_direction_score_single_structure(self):
        from core.strategy.mtf_engine import resolve_direction_conflict
        idx = pd.date_range("2024-01-01", periods=5, freq="4h")
        direction = pd.Series([1.0, 1.0, -1.0, 1.0, 1.0], index=idx)
        layers = [("1d", direction)]
        result = resolve_direction_conflict(layers)
        pd.testing.assert_series_equal(result, direction)

# =====================================================================
# L3: Layer Evaluator + Context Extraction
# =====================================================================

class TestGetAllColumns:
    """_get_all_columns extracts all output columns for an indicator."""

    def test_get_all_columns_bb(self):
        from core.strategy.mtf_engine import _get_all_columns
        df = pd.DataFrame({
            "bb_upper_20_2": [61000.0, 61500.0],
            "bb_middle_20_2": [60000.0, 60500.0],
            "bb_lower_20_2": [59000.0, 59500.0],
            "close": [60000.0, 60500.0],
        })
        result = _get_all_columns(df, "BB", {"period": 20, "std": 2.0})
        assert len(result) == 3  # upper, middle, lower

    def test_get_all_columns_ema(self):
        from core.strategy.mtf_engine import _get_all_columns
        df = pd.DataFrame({
            "ema_50": [60000.0, 60500.0],
            "close": [60000.0, 60500.0],
        })
        result = _get_all_columns(df, "EMA", {"period": 50})
        assert len(result) == 1

    def test_get_all_columns_missing_column_returns_empty(self):
        from core.strategy.mtf_engine import _get_all_columns
        df = pd.DataFrame({"close": [60000.0]})
        result = _get_all_columns(df, "NONEXISTENT", {})
        assert result == []

class TestExtractContext:
    """extract_context pulls direction, price_levels, momentum from a gene."""

    def test_extract_context_ema_trend(self):
        from core.strategy.mtf_engine import extract_context
        idx = pd.date_range("2024-01-01", periods=10, freq="4h")
        df = pd.DataFrame({
            "ema_20": [59000.0] * 10,
            "close": [60000.0] * 10,
        }, index=idx)
        gene = SignalGene("EMA", {"period": 20}, SignalRole.ENTRY_TRIGGER,
                          condition={"type": "price_above"})
        ctx = extract_context(df, gene, "trend")
        assert "direction" in ctx
        assert ctx["direction"] is not None
        # price_above + close > ema -> direction = +1
        assert ctx["direction"].iloc[0] == 1.0

    def test_extract_context_ema_price_below(self):
        from core.strategy.mtf_engine import extract_context
        idx = pd.date_range("2024-01-01", periods=10, freq="4h")
        df = pd.DataFrame({
            "ema_20": [61000.0] * 10,
            "close": [60000.0] * 10,
        }, index=idx)
        gene = SignalGene("EMA", {"period": 20}, SignalRole.ENTRY_TRIGGER,
                          condition={"type": "price_below"})
        ctx = extract_context(df, gene, "trend")
        # price_below + close < ema -> direction = -1
        assert ctx["direction"].iloc[0] == -1.0

    def test_extract_context_bb_price(self):
        from core.strategy.mtf_engine import extract_context
        idx = pd.date_range("2024-01-01", periods=5, freq="4h")
        df = pd.DataFrame({
            "bb_upper_20_2": [61000.0] * 5,
            "bb_middle_20_2": [60000.0] * 5,
            "bb_lower_20_2": [59000.0] * 5,
            "close": [60000.0] * 5,
        }, index=idx)
        gene = SignalGene("BB", {"period": 20, "std": 2.0}, SignalRole.ENTRY_TRIGGER,
                          condition={"type": "price_above"})
        ctx = extract_context(df, gene, "volatility")
        assert "price_levels" in ctx
        assert len(ctx["price_levels"]) == 3  # upper, middle, lower

    def test_extract_context_rsi_momentum(self):
        from core.strategy.mtf_engine import extract_context
        idx = pd.date_range("2024-01-01", periods=5, freq="4h")
        df = pd.DataFrame({
            "rsi_14": [55.0, 60.0, 45.0, 50.0, 65.0],
            "close": [60000.0] * 5,
        }, index=idx)
        gene = SignalGene("RSI", {"period": 14}, SignalRole.ENTRY_TRIGGER,
                          condition={"type": "gt", "threshold": 50})
        ctx = extract_context(df, gene, "momentum")
        # RSI with gt/lt has no price levels, but has momentum
        assert "price_levels" not in ctx or len(ctx.get("price_levels", [])) == 0

    def test_extract_context_no_price_condition(self):
        from core.strategy.mtf_engine import extract_context
        idx = pd.date_range("2024-01-01", periods=5, freq="4h")
        df = pd.DataFrame({
            "rsi_14": [55.0] * 5,
            "close": [60000.0] * 5,
        }, index=idx)
        gene = SignalGene("RSI", {"period": 14}, SignalRole.ENTRY_TRIGGER,
                          condition={"type": "gt", "threshold": 50})
        ctx = extract_context(df, gene, "momentum")
        # gt/lt conditions don't produce price levels
        assert "price_levels" not in ctx or len(ctx.get("price_levels", [])) == 0

class TestResampleValues:
    """resample_values correctly forward-fills numeric series without bool conversion."""

    def test_resample_values_forward_fill(self):
        from core.strategy.mtf_engine import resample_values
        idx_4h = pd.date_range("2024-01-01", periods=3, freq="4h")
        idx_15m = pd.date_range("2024-01-01", periods=48, freq="15min")
        series = pd.Series([60000.0, 60500.0, 61000.0], index=idx_4h)
        result = resample_values(series, idx_15m)
        assert len(result) == 48
        # First 16 15-min bars should have value 60000.0
        assert result.iloc[0] == 60000.0
        assert result.iloc[15] == 60000.0

    def test_resample_values_preserves_float(self):
        from core.strategy.mtf_engine import resample_values
        idx_4h = pd.date_range("2024-01-01", periods=2, freq="4h")
        idx_15m = pd.date_range("2024-01-01", periods=8, freq="15min")
        series = pd.Series([60000.5, 61000.3], index=idx_4h)
        result = resample_values(series, idx_15m)
        assert result.dtype == np.float64 or str(result.dtype).startswith("float")

    def test_resample_values_handles_nan(self):
        from core.strategy.mtf_engine import resample_values
        idx_4h = pd.date_range("2024-01-01", periods=3, freq="4h")
        idx_15m = pd.date_range("2024-01-01", periods=12, freq="15min")
        series = pd.Series([60000.0, float("nan"), 61000.0], index=idx_4h)
        result = resample_values(series, idx_15m)
        assert len(result) == 12

class TestEvaluateLayerWithContext:
    """evaluate_layer_with_context produces LayerResult with context data."""

    def test_evaluate_layer_with_levels_structure(self):
        from core.strategy.mtf_engine import evaluate_layer_with_context, LayerResult
        idx = pd.date_range("2024-01-01", periods=10, freq="4h")
        df = pd.DataFrame({
            "ema_20": [59000.0] * 10,
            "close": [60000.0] * 10,
        }, index=idx)
        layer = TimeframeLayer(
            timeframe="1d",
            signal_genes=[
                SignalGene("EMA", {"period": 20}, SignalRole.ENTRY_TRIGGER,
                           condition={"type": "price_above"}),
                SignalGene("EMA", {"period": 20}, SignalRole.EXIT_TRIGGER,
                           condition={"type": "price_below"}),
            ],
            role="structure",
        )
        result = evaluate_layer_with_context(layer, df)
        assert isinstance(result, LayerResult)
        assert result.direction is not None
        assert len(result.price_levels) > 0

    def test_evaluate_layer_with_levels_zone(self):
        from core.strategy.mtf_engine import evaluate_layer_with_context, LayerResult
        idx = pd.date_range("2024-01-01", periods=10, freq="4h")
        df = pd.DataFrame({
            "bb_upper_20_2": [61000.0] * 10,
            "bb_middle_20_2": [60000.0] * 10,
            "bb_lower_20_2": [59000.0] * 10,
            "close": [60000.0] * 10,
        }, index=idx)
        layer = TimeframeLayer(
            timeframe="4h",
            signal_genes=[
                SignalGene("BB", {"period": 20, "std": 2.0}, SignalRole.ENTRY_TRIGGER,
                           condition={"type": "price_above"}),
                SignalGene("BB", {"period": 20, "std": 2.0}, SignalRole.EXIT_TRIGGER,
                           condition={"type": "price_below"}),
            ],
            role="zone",
        )
        result = evaluate_layer_with_context(layer, df)
        assert isinstance(result, LayerResult)
        assert len(result.price_levels) > 0

    def test_evaluate_layer_with_levels_execution(self):
        from core.strategy.mtf_engine import evaluate_layer_with_context, LayerResult
        idx = pd.date_range("2024-01-01", periods=10, freq="15min")
        df = pd.DataFrame({
            "rsi_14": [55.0] * 10,
            "close": [60000.0] * 10,
        }, index=idx)
        layer = TimeframeLayer(
            timeframe="15m",
            signal_genes=[
                SignalGene("RSI", {"period": 14}, SignalRole.ENTRY_TRIGGER,
                           condition={"type": "gt", "threshold": 50}),
                SignalGene("RSI", {"period": 14}, SignalRole.EXIT_TRIGGER,
                           condition={"type": "lt", "threshold": 50}),
            ],
            role="execution",
        )
        result = evaluate_layer_with_context(layer, df)
        assert isinstance(result, LayerResult)
        assert result.signal_set is not None

    def test_layer_result_dataclass_fields(self):
        from core.strategy.mtf_engine import LayerResult
        from core.strategy.executor import SignalSet
        idx = pd.date_range("2024-01-01", periods=5, freq="4h")
        ss = SignalSet(
            entries=pd.Series([True, False, False, False, False], index=idx),
            exits=pd.Series([False, False, True, False, False], index=idx),
            adds=pd.Series([False] * 5, index=idx),
            reduces=pd.Series([False] * 5, index=idx),
        )
        lr = LayerResult(
            signal_set=ss,
            direction=None,
            price_levels=[],
            momentum=None,
            strength=None,
            volatility=None,
        )
        assert lr.signal_set is ss
        assert lr.direction is None
        assert lr.price_levels == []

# =====================================================================
# L4: Cross-layer Synthesis + Decision Gate
# =====================================================================

class TestMTFSynthesisDataclass:
    """MTFSynthesis holds cross-layer synthesis scores."""

    def test_mtf_synthesis_dataclass_fields(self):
        from core.strategy.mtf_engine import MTFSynthesis
        idx = pd.date_range("2024-01-01", periods=5, freq="15min")
        synthesis = MTFSynthesis(
            direction_score=pd.Series([1.0] * 5, index=idx),
            confluence_score=pd.Series([0.8] * 5, index=idx),
            momentum_score=pd.Series([0.5] * 5, index=idx),
            strength_multiplier=pd.Series([1.0] * 5, index=idx),
        )
        assert synthesis.direction_score is not None
        assert synthesis.confluence_score is not None

class TestCrossLayerSynthesis:
    """synthesize_cross_layer computes direction, confluence, momentum, strength."""

    def test_synthesize_direction_from_structure(self):
        from core.strategy.mtf_engine import synthesize_cross_layer, LayerResult
        from core.strategy.executor import SignalSet
        idx = pd.date_range("2024-01-01", periods=10, freq="15min")
        exec_idx = idx

        layer_results = [
            ("1d", LayerResult(
                signal_set=SignalSet(
                    entries=pd.Series([True] * 10, index=idx),
                    exits=pd.Series([False] * 10, index=idx),
                    adds=pd.Series([False] * 10, index=idx),
                    reduces=pd.Series([False] * 10, index=idx),
                ),
                direction=pd.Series([1.0] * 10, index=idx),
                price_levels=[pd.Series([59000.0] * 10, index=idx)],
                momentum=None,
                strength=None,
                volatility=None,
            )),
        ]
        exec_close = pd.Series([60000.0] * 10, index=idx)
        exec_atr = pd.Series([800.0] * 10, index=idx)
        dna = StrategyDNA(proximity_mult=1.5, confluence_threshold=0.3)
        synthesis = synthesize_cross_layer(
            layer_results, exec_idx, exec_close, exec_atr, 1.5, dna,
        )
        assert synthesis.direction_score.iloc[0] == 1.0

    def test_synthesize_direction_no_structure(self):
        from core.strategy.mtf_engine import synthesize_cross_layer, LayerResult
        from core.strategy.executor import SignalSet
        idx = pd.date_range("2024-01-01", periods=10, freq="15min")
        dna = StrategyDNA(
            risk_genes=RiskGenes(direction="long"),
            proximity_mult=1.5,
            confluence_threshold=0.3,
        )
        layer_results = [
            ("4h", LayerResult(
                signal_set=SignalSet(
                    entries=pd.Series([True] * 10, index=idx),
                    exits=pd.Series([False] * 10, index=idx),
                    adds=pd.Series([False] * 10, index=idx),
                    reduces=pd.Series([False] * 10, index=idx),
                ),
                direction=None,
                price_levels=[pd.Series([60000.0] * 10, index=idx)],
                momentum=None,
                strength=None,
                volatility=None,
            )),
        ]
        exec_close = pd.Series([60000.0] * 10, index=idx)
        exec_atr = pd.Series([800.0] * 10, index=idx)
        synthesis = synthesize_cross_layer(
            layer_results, idx, exec_close, exec_atr, 1.5, dna,
        )
        # No structure layer -> direction from DNA (long=+1)
        assert synthesis.direction_score.iloc[0] == 1.0

    def test_synthesize_confluence_two_layers(self):
        from core.strategy.mtf_engine import synthesize_cross_layer, LayerResult
        from core.strategy.executor import SignalSet
        idx = pd.date_range("2024-01-01", periods=10, freq="15min")
        dna = StrategyDNA(proximity_mult=1.5, confluence_threshold=0.3)
        layer_results = [
            ("1d", LayerResult(
                signal_set=SignalSet(
                    entries=pd.Series([True] * 10, index=idx),
                    exits=pd.Series([False] * 10, index=idx),
                    adds=pd.Series([False] * 10, index=idx),
                    reduces=pd.Series([False] * 10, index=idx),
                ),
                direction=pd.Series([1.0] * 10, index=idx),
                price_levels=[pd.Series([59800.0] * 10, index=idx)],
                momentum=None,
                strength=None,
                volatility=None,
            )),
            ("4h", LayerResult(
                signal_set=SignalSet(
                    entries=pd.Series([True] * 10, index=idx),
                    exits=pd.Series([False] * 10, index=idx),
                    adds=pd.Series([False] * 10, index=idx),
                    reduces=pd.Series([False] * 10, index=idx),
                ),
                direction=None,
                price_levels=[pd.Series([60200.0] * 10, index=idx)],
                momentum=None,
                strength=None,
                volatility=None,
            )),
        ]
        exec_close = pd.Series([60000.0] * 10, index=idx)
        exec_atr = pd.Series([800.0] * 10, index=idx)
        synthesis = synthesize_cross_layer(
            layer_results, idx, exec_close, exec_atr, 1.5, dna,
        )
        # Two layers with close price levels -> positive confluence
        assert synthesis.confluence_score.iloc[0] > 0.0

    def test_synthesize_no_confluence_layers(self):
        from core.strategy.mtf_engine import synthesize_cross_layer, LayerResult
        from core.strategy.executor import SignalSet
        idx = pd.date_range("2024-01-01", periods=10, freq="15min")
        dna = StrategyDNA(proximity_mult=1.5, confluence_threshold=0.3)
        layer_results = []
        exec_close = pd.Series([60000.0] * 10, index=idx)
        exec_atr = pd.Series([800.0] * 10, index=idx)
        synthesis = synthesize_cross_layer(
            layer_results, idx, exec_close, exec_atr, 1.5, dna,
        )
        assert synthesis.confluence_score.iloc[0] == 0.0

class TestDecisionGate:
    """apply_decision_gate filters signals based on MTF synthesis."""

    def _make_signal_set(self, idx, entries=None, exits=None, adds=None, reduces=None):
        from core.strategy.executor import SignalSet
        n = len(idx)
        return SignalSet(
            entries=entries if entries is not None else pd.Series([True] * n, index=idx),
            exits=exits if exits is not None else pd.Series([False] * n, index=idx),
            adds=adds if adds is not None else pd.Series([False] * n, index=idx),
            reduces=reduces if reduces is not None else pd.Series([False] * n, index=idx),
        )

    def test_gate_entry_passes_all_conditions(self):
        from core.strategy.mtf_engine import apply_decision_gate, MTFSynthesis
        idx = pd.date_range("2024-01-01", periods=5, freq="15min")
        ss = self._make_signal_set(idx)
        synthesis = MTFSynthesis(
            direction_score=pd.Series([1.0] * 5, index=idx),
            confluence_score=pd.Series([0.8] * 5, index=idx),
            momentum_score=pd.Series([0.5] * 5, index=idx),
            strength_multiplier=pd.Series([1.0] * 5, index=idx),
        )
        dna = StrategyDNA(
            mtf_mode="direction+confluence",
            confluence_threshold=0.3,
            risk_genes=RiskGenes(direction="long"),
        )
        result = apply_decision_gate(ss, synthesis, dna)
        assert result.entries.iloc[0] == True

    def test_gate_entry_fails_direction(self):
        from core.strategy.mtf_engine import apply_decision_gate, MTFSynthesis
        idx = pd.date_range("2024-01-01", periods=5, freq="15min")
        ss = self._make_signal_set(idx)
        synthesis = MTFSynthesis(
            direction_score=pd.Series([-1.0] * 5, index=idx),
            confluence_score=pd.Series([0.8] * 5, index=idx),
            momentum_score=pd.Series([0.5] * 5, index=idx),
            strength_multiplier=pd.Series([1.0] * 5, index=idx),
        )
        dna = StrategyDNA(
            mtf_mode="direction+confluence",
            confluence_threshold=0.3,
            risk_genes=RiskGenes(direction="long"),
        )
        result = apply_decision_gate(ss, synthesis, dna)
        # direction=-1 but DNA says long -> entry blocked
        assert result.entries.iloc[0] == False

    def test_gate_entry_fails_confluence(self):
        from core.strategy.mtf_engine import apply_decision_gate, MTFSynthesis
        idx = pd.date_range("2024-01-01", periods=5, freq="15min")
        ss = self._make_signal_set(idx)
        synthesis = MTFSynthesis(
            direction_score=pd.Series([1.0] * 5, index=idx),
            confluence_score=pd.Series([0.1] * 5, index=idx),
            momentum_score=pd.Series([0.5] * 5, index=idx),
            strength_multiplier=pd.Series([1.0] * 5, index=idx),
        )
        dna = StrategyDNA(
            mtf_mode="direction+confluence",
            confluence_threshold=0.3,
            risk_genes=RiskGenes(direction="long"),
        )
        result = apply_decision_gate(ss, synthesis, dna)
        # confluence 0.1 < threshold 0.3 -> entry blocked
        assert result.entries.iloc[0] == False

    def test_gate_entry_no_timing_signal(self):
        from core.strategy.mtf_engine import apply_decision_gate, MTFSynthesis
        idx = pd.date_range("2024-01-01", periods=5, freq="15min")
        ss = self._make_signal_set(idx, entries=pd.Series([False] * 5, index=idx))
        synthesis = MTFSynthesis(
            direction_score=pd.Series([1.0] * 5, index=idx),
            confluence_score=pd.Series([0.8] * 5, index=idx),
            momentum_score=pd.Series([0.5] * 5, index=idx),
            strength_multiplier=pd.Series([1.0] * 5, index=idx),
        )
        dna = StrategyDNA(
            mtf_mode="direction+confluence",
            confluence_threshold=0.3,
            risk_genes=RiskGenes(direction="long"),
        )
        result = apply_decision_gate(ss, synthesis, dna)
        assert result.entries.iloc[0] == False

    def test_gate_exit_not_filtered(self):
        from core.strategy.mtf_engine import apply_decision_gate, MTFSynthesis
        idx = pd.date_range("2024-01-01", periods=5, freq="15min")
        ss = self._make_signal_set(
            idx,
            entries=pd.Series([False] * 5, index=idx),
            exits=pd.Series([True] * 5, index=idx),
        )
        synthesis = MTFSynthesis(
            direction_score=pd.Series([-1.0] * 5, index=idx),
            confluence_score=pd.Series([0.1] * 5, index=idx),
            momentum_score=pd.Series([0.5] * 5, index=idx),
            strength_multiplier=pd.Series([1.0] * 5, index=idx),
        )
        dna = StrategyDNA(
            mtf_mode="direction+confluence",
            confluence_threshold=0.3,
            risk_genes=RiskGenes(direction="long"),
        )
        result = apply_decision_gate(ss, synthesis, dna)
        # Exits should not be filtered by confluence/direction
        assert result.exits.iloc[0] == True

    def test_gate_add_filtered_by_confluence(self):
        from core.strategy.mtf_engine import apply_decision_gate, MTFSynthesis
        idx = pd.date_range("2024-01-01", periods=5, freq="15min")
        ss = self._make_signal_set(
            idx,
            entries=pd.Series([False] * 5, index=idx),
            adds=pd.Series([True] * 5, index=idx),
        )
        # Low confluence -> add should be filtered
        synthesis = MTFSynthesis(
            direction_score=pd.Series([1.0] * 5, index=idx),
            confluence_score=pd.Series([0.1] * 5, index=idx),
            momentum_score=pd.Series([0.5] * 5, index=idx),
            strength_multiplier=pd.Series([1.0] * 5, index=idx),
        )
        dna = StrategyDNA(
            mtf_mode="direction+confluence",
            confluence_threshold=0.3,
            risk_genes=RiskGenes(direction="long"),
        )
        result = apply_decision_gate(ss, synthesis, dna)
        # add needs confluence >= threshold * 0.8 = 0.24, we have 0.1 -> blocked
        assert result.adds.iloc[0] == False

    def test_gate_reduce_not_filtered(self):
        from core.strategy.mtf_engine import apply_decision_gate, MTFSynthesis
        idx = pd.date_range("2024-01-01", periods=5, freq="15min")
        ss = self._make_signal_set(
            idx,
            entries=pd.Series([False] * 5, index=idx),
            reduces=pd.Series([True] * 5, index=idx),
        )
        synthesis = MTFSynthesis(
            direction_score=pd.Series([-1.0] * 5, index=idx),
            confluence_score=pd.Series([0.1] * 5, index=idx),
            momentum_score=pd.Series([0.5] * 5, index=idx),
            strength_multiplier=pd.Series([1.0] * 5, index=idx),
        )
        dna = StrategyDNA(
            mtf_mode="direction+confluence",
            confluence_threshold=0.3,
            risk_genes=RiskGenes(direction="long"),
        )
        result = apply_decision_gate(ss, synthesis, dna)
        # Reduces should not be filtered
        assert result.reduces.iloc[0] == True

class TestMTFMode:
    """mtf_mode controls which dimensions are active."""

    def _make_context(self, idx):
        from core.strategy.mtf_engine import apply_decision_gate, MTFSynthesis
        from core.strategy.executor import SignalSet
        ss = SignalSet(
            entries=pd.Series([True] * len(idx), index=idx),
            exits=pd.Series([False] * len(idx), index=idx),
            adds=pd.Series([False] * len(idx), index=idx),
            reduces=pd.Series([False] * len(idx), index=idx),
        )
        synthesis = MTFSynthesis(
            direction_score=pd.Series([1.0] * len(idx), index=idx),
            confluence_score=pd.Series([0.1] * len(idx), index=idx),
            momentum_score=pd.Series([0.5] * len(idx), index=idx),
            strength_multiplier=pd.Series([1.0] * len(idx), index=idx),
        )
        return ss, synthesis

    def test_mtf_mode_direction_only(self):
        from core.strategy.mtf_engine import apply_decision_gate
        idx = pd.date_range("2024-01-01", periods=5, freq="15min")
        ss, synthesis = self._make_context(idx)
        dna = StrategyDNA(
            mtf_mode="direction",
            confluence_threshold=0.3,
            risk_genes=RiskGenes(direction="long"),
        )
        result = apply_decision_gate(ss, synthesis, dna)
        # Only direction active, confluence 0.1 doesn't matter
        assert result.entries.iloc[0] == True

    def test_mtf_mode_confluence_only(self):
        from core.strategy.mtf_engine import apply_decision_gate
        idx = pd.date_range("2024-01-01", periods=5, freq="15min")
        ss, synthesis = self._make_context(idx)
        # direction_score=1.0 but mtf_mode=confluence, so direction gate not applied
        # but confluence=0.1 < threshold=0.3 -> blocked
        dna = StrategyDNA(
            mtf_mode="confluence",
            confluence_threshold=0.3,
            risk_genes=RiskGenes(direction="long"),
        )
        result = apply_decision_gate(ss, synthesis, dna)
        assert result.entries.iloc[0] == False

    def test_mtf_mode_direction_plus_confluence(self):
        from core.strategy.mtf_engine import apply_decision_gate
        idx = pd.date_range("2024-01-01", periods=5, freq="15min")
        ss, synthesis = self._make_context(idx)
        dna = StrategyDNA(
            mtf_mode="direction+confluence",
            confluence_threshold=0.3,
            risk_genes=RiskGenes(direction="long"),
        )
        result = apply_decision_gate(ss, synthesis, dna)
        # Both active, confluence 0.1 < 0.3 -> blocked
        assert result.entries.iloc[0] == False

    def test_mtf_mode_none_skips_gating(self):
        from core.strategy.mtf_engine import apply_decision_gate
        idx = pd.date_range("2024-01-01", periods=5, freq="15min")
        ss, synthesis = self._make_context(idx)
        dna = StrategyDNA(mtf_mode=None, risk_genes=RiskGenes(direction="long"))
        result = apply_decision_gate(ss, synthesis, dna)
        # No gating -> all entries pass
        assert result.entries.iloc[0] == True

class TestDiagnostics:
    """Decision gate includes diagnostics information."""

    def test_diagnostics_included_in_signal_set(self):
        from core.strategy.mtf_engine import apply_decision_gate, MTFSynthesis
        from core.strategy.executor import SignalSet
        idx = pd.date_range("2024-01-01", periods=5, freq="15min")
        ss = SignalSet(
            entries=pd.Series([True] * 5, index=idx),
            exits=pd.Series([False] * 5, index=idx),
            adds=pd.Series([False] * 5, index=idx),
            reduces=pd.Series([False] * 5, index=idx),
        )
        synthesis = MTFSynthesis(
            direction_score=pd.Series([1.0] * 5, index=idx),
            confluence_score=pd.Series([0.8] * 5, index=idx),
            momentum_score=pd.Series([0.5] * 5, index=idx),
            strength_multiplier=pd.Series([1.0] * 5, index=idx),
        )
        dna = StrategyDNA(
            mtf_mode="direction+confluence",
            confluence_threshold=0.3,
            risk_genes=RiskGenes(direction="long"),
        )
        result = apply_decision_gate(ss, synthesis, dna)
        assert result.mtf_diagnostics is not None

    def test_diagnostics_contain_dimension_scores(self):
        from core.strategy.mtf_engine import apply_decision_gate, MTFSynthesis
        from core.strategy.executor import SignalSet
        idx = pd.date_range("2024-01-01", periods=5, freq="15min")
        ss = SignalSet(
            entries=pd.Series([True] * 5, index=idx),
            exits=pd.Series([False] * 5, index=idx),
            adds=pd.Series([False] * 5, index=idx),
            reduces=pd.Series([False] * 5, index=idx),
        )
        synthesis = MTFSynthesis(
            direction_score=pd.Series([1.0] * 5, index=idx),
            confluence_score=pd.Series([0.8] * 5, index=idx),
            momentum_score=pd.Series([0.5] * 5, index=idx),
            strength_multiplier=pd.Series([1.0] * 5, index=idx),
        )
        dna = StrategyDNA(
            mtf_mode="direction+confluence",
            confluence_threshold=0.3,
            risk_genes=RiskGenes(direction="long"),
        )
        result = apply_decision_gate(ss, synthesis, dna)
        diag = result.mtf_diagnostics
        assert "direction_score" in diag
        assert "confluence_score" in diag
        assert "momentum_score" in diag
        assert "strength_multiplier" in diag

# =====================================================================
# Bug Fix Tests: C1, C2, M2, M3, M4
# =====================================================================

class TestC1MomentumConfluence:
    """C1: Confluence should not permanently block when momentum indicators
    are used in structure/zone layers.

    Root cause: extract_context only extracts price_levels for
    {'trend', 'volatility'} categories, so momentum indicators in
    non-execution layers produce empty price_levels, making
    confluence_score=0 and blocking all entries.
    """

    def test_synthesize_with_momentum_layers_gets_confluence(self):
        """Two structure/zone layers with only momentum data should still
        produce a non-zero confluence score via momentum agreement."""
        from core.strategy.mtf_engine import synthesize_cross_layer, LayerResult
        from core.strategy.executor import SignalSet

        idx = pd.date_range("2024-01-01", periods=10, freq="15min")
        dna = StrategyDNA(
            proximity_mult=1.5,
            confluence_threshold=0.3,
            layers=[
                TimeframeLayer(timeframe="1d", signal_genes=[], role="structure"),
                TimeframeLayer(timeframe="4h", signal_genes=[], role="zone"),
            ],
        )
        # Both layers have momentum (same direction) but NO price_levels
        layer_results = [
            ("1d", LayerResult(
                signal_set=SignalSet(
                    entries=pd.Series([True] * 10, index=idx),
                    exits=pd.Series([False] * 10, index=idx),
                    adds=pd.Series([False] * 10, index=idx),
                    reduces=pd.Series([False] * 10, index=idx),
                ),
                direction=pd.Series([1.0] * 10, index=idx),
                price_levels=[],  # No price levels - momentum indicator
                momentum=pd.Series([0.7] * 10, index=idx),
            )),
            ("4h", LayerResult(
                signal_set=SignalSet(
                    entries=pd.Series([True] * 10, index=idx),
                    exits=pd.Series([False] * 10, index=idx),
                    adds=pd.Series([False] * 10, index=idx),
                    reduces=pd.Series([False] * 10, index=idx),
                ),
                direction=None,
                price_levels=[],  # No price levels - momentum indicator
                momentum=pd.Series([0.8] * 10, index=idx),
            )),
        ]
        exec_close = pd.Series([60000.0] * 10, index=idx)
        exec_atr = pd.Series([800.0] * 10, index=idx)
        synthesis = synthesize_cross_layer(
            layer_results, idx, exec_close, exec_atr, 1.5, dna,
        )
        # Momentum agreement should give non-zero confluence
        assert synthesis.confluence_score.iloc[0] > 0.0, \
            "Momentum-only layers should produce non-zero confluence score"

    def test_momentum_only_layers_pass_decision_gate(self):
        """Entries should NOT be blocked when only momentum data is available."""
        from core.strategy.mtf_engine import apply_decision_gate, MTFSynthesis

        idx = pd.date_range("2024-01-01", periods=5, freq="15min")
        from core.strategy.executor import SignalSet
        ss = SignalSet(
            entries=pd.Series([True] * 5, index=idx),
            exits=pd.Series([False] * 5, index=idx),
            adds=pd.Series([False] * 5, index=idx),
            reduces=pd.Series([False] * 5, index=idx),
        )
        # Momentum-derived confluence score > threshold
        synthesis = MTFSynthesis(
            direction_score=pd.Series([1.0] * 5, index=idx),
            confluence_score=pd.Series([0.5] * 5, index=idx),
            momentum_score=pd.Series([0.7] * 5, index=idx),
            strength_multiplier=pd.Series([1.0] * 5, index=idx),
        )
        dna = StrategyDNA(
            mtf_mode="direction+confluence",
            confluence_threshold=0.3,
            risk_genes=RiskGenes(direction="long"),
        )
        result = apply_decision_gate(ss, synthesis, dna)
        assert result.entries.iloc[0] == True, \
            "Entry should pass when momentum confluence > threshold"

    def test_momentum_conflicting_directions_zero_confluence(self):
        """When momentum directions conflict across layers, confluence should be 0."""
        from core.strategy.mtf_engine import synthesize_cross_layer, LayerResult
        from core.strategy.executor import SignalSet

        idx = pd.date_range("2024-01-01", periods=10, freq="15min")
        dna = StrategyDNA(
            proximity_mult=1.5,
            confluence_threshold=0.3,
            layers=[
                TimeframeLayer(timeframe="1d", signal_genes=[], role="structure"),
                TimeframeLayer(timeframe="4h", signal_genes=[], role="zone"),
            ],
        )
        layer_results = [
            ("1d", LayerResult(
                signal_set=SignalSet(
                    entries=pd.Series([True] * 10, index=idx),
                    exits=pd.Series([False] * 10, index=idx),
                    adds=pd.Series([False] * 10, index=idx),
                    reduces=pd.Series([False] * 10, index=idx),
                ),
                direction=pd.Series([1.0] * 10, index=idx),
                price_levels=[],
                momentum=pd.Series([0.8] * 10, index=idx),  # Positive
            )),
            ("4h", LayerResult(
                signal_set=SignalSet(
                    entries=pd.Series([True] * 10, index=idx),
                    exits=pd.Series([False] * 10, index=idx),
                    adds=pd.Series([False] * 10, index=idx),
                    reduces=pd.Series([False] * 10, index=idx),
                ),
                direction=None,
                price_levels=[],
                momentum=pd.Series([-0.6] * 10, index=idx),  # Negative - conflicts
            )),
        ]
        exec_close = pd.Series([60000.0] * 10, index=idx)
        exec_atr = pd.Series([800.0] * 10, index=idx)
        synthesis = synthesize_cross_layer(
            layer_results, idx, exec_close, exec_atr, 1.5, dna,
        )
        # Conflicting momenta should produce low/zero confluence
        assert synthesis.confluence_score.iloc[0] < 0.3, \
            "Conflicting momentum directions should produce low confluence"

class TestM2DirectionZeroMixedMode:
    """M2: direction_score=0.0 in mixed mode should not route to short."""

    def test_direction_zero_blocks_entry_in_mixed_mode(self):
        """When direction_score=0.0 and mode is mixed, entry should be blocked."""
        from core.strategy.mtf_engine import apply_decision_gate, MTFSynthesis
        from core.strategy.executor import SignalSet

        idx = pd.date_range("2024-01-01", periods=5, freq="15min")
        ss = SignalSet(
            entries=pd.Series([True] * 5, index=idx),
            exits=pd.Series([False] * 5, index=idx),
            adds=pd.Series([False] * 5, index=idx),
            reduces=pd.Series([False] * 5, index=idx),
        )
        synthesis = MTFSynthesis(
            direction_score=pd.Series([0.0] * 5, index=idx),  # Neutral
            confluence_score=pd.Series([0.8] * 5, index=idx),
            momentum_score=pd.Series([0.5] * 5, index=idx),
            strength_multiplier=pd.Series([1.0] * 5, index=idx),
        )
        dna = StrategyDNA(
            mtf_mode="direction+confluence",
            confluence_threshold=0.3,
            risk_genes=RiskGenes(direction="mixed"),
        )
        result = apply_decision_gate(ss, synthesis, dna)
        assert result.entries.iloc[0] == False, \
            "direction_score=0.0 (neutral) should block entry in mixed mode"

    def test_direction_positive_allows_entry_in_mixed_mode(self):
        """When direction_score > 0 and mode is mixed, entry should pass."""
        from core.strategy.mtf_engine import apply_decision_gate, MTFSynthesis
        from core.strategy.executor import SignalSet

        idx = pd.date_range("2024-01-01", periods=5, freq="15min")
        ss = SignalSet(
            entries=pd.Series([True] * 5, index=idx),
            exits=pd.Series([False] * 5, index=idx),
            adds=pd.Series([False] * 5, index=idx),
            reduces=pd.Series([False] * 5, index=idx),
        )
        synthesis = MTFSynthesis(
            direction_score=pd.Series([1.0] * 5, index=idx),
            confluence_score=pd.Series([0.8] * 5, index=idx),
            momentum_score=pd.Series([0.5] * 5, index=idx),
            strength_multiplier=pd.Series([1.0] * 5, index=idx),
        )
        dna = StrategyDNA(
            mtf_mode="direction+confluence",
            confluence_threshold=0.3,
            risk_genes=RiskGenes(direction="mixed"),
        )
        result = apply_decision_gate(ss, synthesis, dna)
        assert result.entries.iloc[0] == True

    def test_direction_negative_allows_entry_in_mixed_mode(self):
        """When direction_score < 0 and mode is mixed, entry should pass."""
        from core.strategy.mtf_engine import apply_decision_gate, MTFSynthesis
        from core.strategy.executor import SignalSet

        idx = pd.date_range("2024-01-01", periods=5, freq="15min")
        ss = SignalSet(
            entries=pd.Series([True] * 5, index=idx),
            exits=pd.Series([False] * 5, index=idx),
            adds=pd.Series([False] * 5, index=idx),
            reduces=pd.Series([False] * 5, index=idx),
        )
        synthesis = MTFSynthesis(
            direction_score=pd.Series([-1.0] * 5, index=idx),
            confluence_score=pd.Series([0.8] * 5, index=idx),
            momentum_score=pd.Series([0.5] * 5, index=idx),
            strength_multiplier=pd.Series([1.0] * 5, index=idx),
        )
        dna = StrategyDNA(
            mtf_mode="direction+confluence",
            confluence_threshold=0.3,
            risk_genes=RiskGenes(direction="mixed"),
        )
        result = apply_decision_gate(ss, synthesis, dna)
        assert result.entries.iloc[0] == True
