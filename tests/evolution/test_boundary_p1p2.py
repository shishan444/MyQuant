"""Boundary and exception tests for mtf_engine, metrics, operators, storage, csv_importer.

Covers edge cases:
- Zero / NaN inputs for compute_s_pct, build_price_zone, compute_proximity_score
- Empty collections for intersect_intervals, confluence, resolve_direction_conflict
- Degenerate trade data for metrics
- Empty gene pools for mutation/crossover operators
- Corrupt/empty parquet files, column mismatches for storage
- Ambiguous CSV formats, missing columns, empty batch for csv_importer

Run with: pytest tests/test_boundary_p1p2.py -v
"""

import math

import numpy as np
import pandas as pd
import pytest

pytestmark = [pytest.mark.unit]

from tests.helpers.data_factory import make_dna, make_ohlcv


# ============================================================================
# mtf_engine boundary tests
# ============================================================================

class TestComputeSPct:
    """Tests for compute_s_pct edge cases."""

    def test_compute_s_pct_zero_close(self):
        """close=0 should return 0.0 to avoid division by zero."""
        from core.strategy.mtf_engine import compute_s_pct

        result = compute_s_pct(atr=1.5, close=0.0, proximity_mult=1.5)
        assert result == 0.0

    def test_compute_s_pct_negative_close(self):
        """Negative close should also return 0.0 (close <= 0 guard)."""
        from core.strategy.mtf_engine import compute_s_pct

        result = compute_s_pct(atr=1.5, close=-100.0, proximity_mult=1.5)
        assert result == 0.0

    def test_compute_s_pct_zero_atr(self):
        """ATR=0 should return 0.0 (no volatility -> no proximity zone)."""
        from core.strategy.mtf_engine import compute_s_pct

        result = compute_s_pct(atr=0.0, close=40000.0, proximity_mult=1.5)
        assert result == 0.0

    def test_compute_s_pct_nan_values(self):
        """NaN in close or atr should propagate as NaN (not crash)."""
        from core.strategy.mtf_engine import compute_s_pct

        result_close_nan = compute_s_pct(atr=1.0, close=float("nan"), proximity_mult=1.5)
        # NaN close fails the <= 0 check (NaN comparisons return False), so falls through
        assert math.isnan(result_close_nan) or result_close_nan == 0.0

        # NaN close goes past the guard since NaN <= 0 is False
        result_atr_nan = compute_s_pct(atr=float("nan"), close=100.0, proximity_mult=1.5)
        assert math.isnan(result_atr_nan)


class TestBuildPriceZone:
    """Tests for build_price_zone edge cases."""

    def test_build_price_zone_zero_price(self):
        """price=0 should produce zone (0, 0)."""
        from core.strategy.mtf_engine import build_price_zone

        lo, hi = build_price_zone(price=0.0, s_pct=0.05)
        assert lo == 0.0
        assert hi == 0.0

    def test_build_price_zone_zero_s_pct(self):
        """s_pct=0 should produce zone equal to the price itself."""
        from core.strategy.mtf_engine import build_price_zone

        lo, hi = build_price_zone(price=40000.0, s_pct=0.0)
        assert lo == 40000.0
        assert hi == 40000.0


class TestIntersectIntervals:
    """Tests for intersect_intervals edge cases."""

    def test_intersect_intervals_one_empty(self):
        """One empty input list should return empty result."""
        from core.strategy.mtf_engine import intersect_intervals

        result = intersect_intervals([(10.0, 20.0)], [])
        assert result == []

    def test_intersect_intervals_both_empty(self):
        """Both empty lists should return empty result."""
        from core.strategy.mtf_engine import intersect_intervals

        result = intersect_intervals([], [])
        assert result == []

    def test_intersect_intervals_no_overlap(self):
        """Non-overlapping intervals should produce empty intersection."""
        from core.strategy.mtf_engine import intersect_intervals

        result = intersect_intervals([(10.0, 20.0)], [(30.0, 40.0)])
        assert result == []


class TestComputeConfluenceScore:
    """Tests for compute_confluence_score edge cases."""

    def test_compute_confluence_score_single_layer(self):
        """Only 1 layer of zones should return 0.0 (need >= 2)."""
        from core.strategy.mtf_engine import compute_confluence_score

        result = compute_confluence_score(
            layer_zones=[[(100.0, 110.0)]],
            current_price=105.0,
            max_zone_width=20.0,
        )
        assert result == 0.0

    def test_compute_confluence_score_all_empty(self):
        """All empty zone lists should return 0.0."""
        from core.strategy.mtf_engine import compute_confluence_score

        result = compute_confluence_score(
            layer_zones=[[], []],
            current_price=100.0,
            max_zone_width=20.0,
        )
        assert result == 0.0


class TestComputeProximityScore:
    """Tests for compute_proximity_score edge cases."""

    def test_compute_proximity_score_empty_levels(self):
        """Empty price_levels should return all-zero Series."""
        from core.strategy.mtf_engine import compute_proximity_score

        prices = pd.Series([100.0, 200.0, 300.0])
        result = compute_proximity_score(
            price_levels=[],
            current_price=prices,
            s_pct=0.05,
        )
        assert (result == 0.0).all()

    def test_compute_proximity_score_zero_s_pct(self):
        """s_pct=0 should return all-zero Series."""
        from core.strategy.mtf_engine import compute_proximity_score

        prices = pd.Series([100.0, 200.0, 300.0])
        levels = [pd.Series([105.0, 210.0, 315.0])]
        result = compute_proximity_score(
            price_levels=levels,
            current_price=prices,
            s_pct=0.0,
        )
        assert (result == 0.0).all()


class TestResolveDirectionConflict:
    """Tests for resolve_direction_conflict edge cases."""

    def test_resolve_direction_conflict_empty(self):
        """Empty layers list should return scalar 0.0 Series."""
        from core.strategy.mtf_engine import resolve_direction_conflict

        result = resolve_direction_conflict([])
        assert isinstance(result, pd.Series)
        assert len(result) == 1
        assert result.iloc[0] == 0.0

    def test_resolve_direction_conflict_single_layer(self):
        """Single layer should return its direction unchanged."""
        from core.strategy.mtf_engine import resolve_direction_conflict

        direction = pd.Series([1.0, -1.0, 1.0])
        result = resolve_direction_conflict([("4h", direction)])
        pd.testing.assert_series_equal(result, direction)

    def test_resolve_direction_conflict_highest_tf_wins(self):
        """Highest timeframe should win when multiple layers disagree."""
        from core.strategy.mtf_engine import resolve_direction_conflict

        dir_4h = pd.Series([1.0, 1.0, 1.0])
        dir_1d = pd.Series([-1.0, -1.0, -1.0])
        result = resolve_direction_conflict([("4h", dir_4h), ("1d", dir_1d)])
        assert (result == -1.0).all()


# ============================================================================
# metrics.py boundary tests
# ============================================================================

class TestMetricsBoundary:
    """Tests for compute_metrics edge cases."""

    def test_metrics_all_losing_trades(self):
        """All negative returns should produce valid metrics with negative annual return."""
        from core.scoring.metrics import compute_metrics

        n = 100
        equity = pd.Series(np.linspace(100000, 50000, n))
        trade_returns = np.array([-0.02, -0.03, -0.01, -0.04, -0.02] * 10)

        result = compute_metrics(
            equity_curve=equity,
            total_trades=50,
            trade_returns=trade_returns,
        )
        assert result["annual_return"] < 0.0
        assert result["max_drawdown"] < 0.0
        assert result["win_rate"] == 0.0
        assert result["total_trades"] == 50
        assert result["profit_factor"] == 0.0
        assert result["max_consecutive_losses"] >= 10

    def test_metrics_single_trade(self):
        """Only 1 trade with equity curve should return valid metrics."""
        from core.scoring.metrics import compute_metrics

        equity = pd.Series([100000, 101000, 100500, 102000])
        trade_returns = np.array([0.02])

        result = compute_metrics(
            equity_curve=equity,
            total_trades=1,
            trade_returns=trade_returns,
        )
        assert result["total_trades"] == 1
        assert result["annual_return"] != 0.0
        # Sharpe requires > 1 trade return to compute from trade_returns
        assert isinstance(result["sharpe_ratio"], float)

    def test_metrics_negative_equity_curve(self):
        """Consistently declining equity should have large negative max_drawdown."""
        from core.scoring.metrics import compute_metrics

        equity = pd.Series(np.linspace(100000, 10000, 200))
        result = compute_metrics(
            equity_curve=equity,
            total_trades=20,
            trade_returns=np.array([-0.01] * 20),
        )
        assert result["annual_return"] < -0.5
        assert result["max_drawdown"] < -0.5
        assert result["calmar_ratio"] > 0  # ratio of negative/negative

    def test_metrics_extreme_short_period(self):
        """Very short equity curve (2 points) with minimal trades."""
        from core.scoring.metrics import compute_metrics

        equity = pd.Series([100000, 100500])
        result = compute_metrics(
            equity_curve=equity,
            total_trades=1,
            trade_win_rate=1.0,
        )
        assert result["total_trades"] == 1
        assert result["annual_return"] > 0.0
        assert result["win_rate"] == 1.0

    def test_metrics_too_short_returns_zeros(self):
        """Equity curve < 2 points or 0 trades should return all zeros."""
        from core.scoring.metrics import compute_metrics

        result = compute_metrics(
            equity_curve=pd.Series([100000]),
            total_trades=0,
        )
        assert result["annual_return"] == 0.0
        assert result["sharpe_ratio"] == 0.0
        assert result["total_trades"] == 0


# ============================================================================
# operators.py boundary tests
# ============================================================================

class TestMutateParams:
    """Tests for mutate_params edge cases."""

    def test_mutate_params_empty_genes(self):
        """DNA with empty signal_genes should return unchanged (new id)."""
        from core.evolution.operators import mutate_params
        from core.strategy.dna import (
            StrategyDNA, LogicGenes, ExecutionGenes, RiskGenes,
        )

        dna = StrategyDNA(
            signal_genes=[],
            logic_genes=LogicGenes(),
            execution_genes=ExecutionGenes(),
            risk_genes=RiskGenes(),
        )
        result = mutate_params(dna)
        assert isinstance(result, StrategyDNA)
        # New ID is assigned even if no mutation happens
        assert result.strategy_id != dna.strategy_id

    def test_mutate_params_preserves_structure(self):
        """mutate_params should not change gene count or DNA topology."""
        from core.evolution.operators import mutate_params

        dna = make_dna()
        result = mutate_params(dna)
        assert len(result.signal_genes) == len(dna.signal_genes)


class TestMutateIndicator:
    """Tests for mutate_indicator edge cases."""

    def test_mutate_indicator_empty_genes(self):
        """DNA with empty signal_genes should not crash."""
        from core.evolution.operators import mutate_indicator
        from core.strategy.dna import (
            StrategyDNA, LogicGenes, ExecutionGenes, RiskGenes,
        )

        dna = StrategyDNA(
            signal_genes=[],
            logic_genes=LogicGenes(),
            execution_genes=ExecutionGenes(),
            risk_genes=RiskGenes(),
        )
        result = mutate_indicator(dna)
        assert isinstance(result, StrategyDNA)

    def test_mutate_indicator_preserves_gene_count(self):
        """mutate_indicator should swap indicator, not add/remove genes."""
        from core.evolution.operators import mutate_indicator

        dna = make_dna()
        result = mutate_indicator(dna)
        assert len(result.signal_genes) == len(dna.signal_genes)


class TestCrossover:
    """Tests for crossover edge cases."""

    def test_crossover_asymmetric_parents(self):
        """One parent with genes, other empty should produce valid child."""
        from core.evolution.operators import crossover

        parent_with_genes = make_dna()
        # Create empty parent via from_dict to ensure proper structure
        from core.strategy.dna import StrategyDNA
        parent_empty = StrategyDNA()

        child = crossover(parent_with_genes, parent_empty)
        assert isinstance(child, StrategyDNA)
        assert len(child.parent_ids) == 2

    def test_crossover_both_empty(self):
        """Both parents empty should produce valid (empty) child."""
        from core.evolution.operators import crossover
        from core.strategy.dna import StrategyDNA

        child = crossover(StrategyDNA(), StrategyDNA())
        assert isinstance(child, StrategyDNA)
        assert child.signal_genes == []


# ============================================================================
# storage.py boundary tests
# ============================================================================

class TestStorageBoundary:
    """Tests for storage edge cases using tmp_path fixture."""

    def test_load_corrupt_parquet(self, tmp_path):
        """Loading a non-parquet file should raise an exception."""
        from core.data.storage import load_parquet

        bad_file = tmp_path / "corrupt.parquet"
        bad_file.write_text("this is not a parquet file at all")

        with pytest.raises(Exception):
            load_parquet(bad_file)

    def test_get_latest_timestamp_empty_df(self, tmp_path):
        """Parquet file with 0 rows should return None."""
        from core.data.storage import save_parquet, get_latest_timestamp

        empty_df = pd.DataFrame(columns=["open", "high", "low", "close", "volume"])
        path = tmp_path / "empty.parquet"
        save_parquet(empty_df, path)

        result = get_latest_timestamp(path)
        assert result is None

    def test_get_latest_timestamp_nonexistent(self, tmp_path):
        """Non-existent file should return None."""
        from core.data.storage import get_latest_timestamp

        result = get_latest_timestamp(tmp_path / "does_not_exist.parquet")
        assert result is None

    def test_merge_parquet_column_mismatch(self, tmp_path):
        """Merging DataFrames with different columns should succeed (NaN fill)."""
        from core.data.storage import save_parquet, merge_parquet, load_parquet

        dates_a = pd.date_range("2024-01-01", periods=3, freq="4h", tz="UTC")
        df_a = pd.DataFrame(
            {"close": [100.0, 101.0, 102.0]},
            index=dates_a,
        )
        path = tmp_path / "mismatch.parquet"
        save_parquet(df_a, path)

        # Merge a DataFrame with a different column
        dates_b = pd.date_range("2024-01-01 12:00", periods=2, freq="4h", tz="UTC")
        df_b = pd.DataFrame(
            {"close": [103.0, 104.0], "extra_col": [1.0, 2.0]},
            index=dates_b,
        )
        merge_parquet(df_b, path)

        result = load_parquet(path)
        # Should have both columns; original rows get NaN in extra_col
        assert "extra_col" in result.columns
        assert len(result) >= 3

    def test_save_and_load_roundtrip(self, tmp_path):
        """Basic save -> load roundtrip should preserve data values."""
        from core.data.storage import save_parquet, load_parquet

        df = make_ohlcv(n=50)
        path = tmp_path / "roundtrip.parquet"
        save_parquet(df, path)

        loaded = load_parquet(path)
        # Compare values (ignore freq attribute which parquet does not preserve)
        pd.testing.assert_frame_equal(
            df, loaded, check_freq=False,
        )


# ============================================================================
# csv_importer.py boundary tests
# ============================================================================

class TestDetectFormat:
    """Tests for detect_format edge cases."""

    def test_detect_format_ambiguous(self, tmp_path):
        """CSV that is neither clearly Binance nor generic should fall back to GENERIC."""
        from core.data.csv_importer import detect_format, ImportFormat

        # 3 columns, first field is not a recognized column name, not 12 columns
        csv_file = tmp_path / "ambiguous.csv"
        csv_file.write_text("foo,bar,baz\n1,2,3\n")

        result = detect_format(csv_file)
        assert result == ImportFormat.GENERIC_OHLCV

    def test_detect_format_empty_file(self, tmp_path):
        """Empty CSV file should raise ValueError."""
        from core.data.csv_importer import detect_format

        csv_file = tmp_path / "empty.csv"
        csv_file.write_text("")

        with pytest.raises(ValueError, match="empty"):
            detect_format(csv_file)

    def test_detect_format_binance(self, tmp_path):
        """12-column numeric CSV should be detected as BINANCE_OFFICIAL."""
        from core.data.csv_importer import detect_format, ImportFormat

        csv_file = tmp_path / "binance.csv"
        csv_file.write_text(
            "1700000000000,40000.0,40100.0,39900.0,40050.0,100.0,"
            "1700000035999,5000000.0,500,250.0,10000000.0,0\n"
        )
        result = detect_format(csv_file)
        assert result == ImportFormat.BINANCE_OFFICIAL

    def test_detect_format_generic(self, tmp_path):
        """CSV with header containing 'timestamp' should be GENERIC_OHLCV."""
        from core.data.csv_importer import detect_format, ImportFormat

        csv_file = tmp_path / "generic.csv"
        csv_file.write_text("timestamp,open,high,low,close,volume\n"
                            "2024-01-01,40000,40100,39900,40050,100\n")
        result = detect_format(csv_file)
        assert result == ImportFormat.GENERIC_OHLCV


class TestReadCsv:
    """Tests for read_csv edge cases."""

    def test_read_csv_missing_timestamp(self, tmp_path):
        """Generic CSV without timestamp column should raise KeyError."""
        from core.data.csv_importer import read_csv

        csv_file = tmp_path / "no_ts.csv"
        csv_file.write_text("open,high,low,close,volume\n"
                            "40000,40100,39900,40050,100\n")

        with pytest.raises((KeyError, ValueError)):
            read_csv(csv_file)

    def test_read_csv_binance_format(self, tmp_path):
        """Binance 12-column format should be read correctly."""
        from core.data.csv_importer import read_csv

        csv_file = tmp_path / "BTCUSDT-4h-2024-01.csv"
        csv_file.write_text(
            "1700006400000,42000.0,42100.0,41900.0,42050.0,150.0,"
            "1700006400599,6300000.0,600,300.0,12600000.0,0\n"
            "1700020800000,42050.0,42200.0,42000.0,42150.0,200.0,"
            "1700020800599,8400000.0,700,400.0,16800000.0,0\n"
        )
        df = read_csv(csv_file)
        assert len(df) == 2
        assert set(df.columns) >= {"open", "high", "low", "close", "volume"}

    def test_read_csv_generic_with_timestamp(self, tmp_path):
        """Generic CSV with proper timestamp should parse correctly."""
        from core.data.csv_importer import read_csv

        csv_file = tmp_path / "generic.csv"
        csv_file.write_text(
            "timestamp,open,high,low,close,volume\n"
            "2024-01-01 00:00,42000,42100,41900,42050,100\n"
            "2024-01-01 04:00,42050,42200,42000,42150,200\n"
        )
        df = read_csv(csv_file)
        assert len(df) == 2
        assert df.index.name == "timestamp"


class TestImportCsvBatch:
    """Tests for import_csv_batch edge cases."""

    def test_import_csv_batch_empty_list(self, tmp_path):
        """Empty path list should raise ValueError."""
        from core.data.csv_importer import import_csv_batch

        with pytest.raises(ValueError, match="No files provided"):
            import_csv_batch(
                paths=[],
                data_dir=tmp_path,
                symbol="BTCUSDT",
                interval="4h",
            )


class TestValidateOhlcv:
    """Tests for validate_ohlcv edge cases."""

    def test_validate_ohlcv_clean_data(self):
        """Clean OHLCV data should return no errors."""
        from core.data.csv_importer import validate_ohlcv

        # Build guaranteed-valid OHLCV: high >= max(O,C,L), low <= min(O,C,H)
        dates = pd.date_range("2024-01-01", periods=20, freq="4h", tz="UTC")
        close_arr = np.linspace(40000, 41000, 20)
        opn_arr = close_arr - 10
        high_arr = close_arr + 50
        low_arr = close_arr - 50
        volume_arr = np.full(20, 1000.0)
        df = pd.DataFrame(
            {
                "open": opn_arr,
                "high": high_arr,
                "low": low_arr,
                "close": close_arr,
                "volume": volume_arr,
            },
            index=dates,
        )
        errors = validate_ohlcv(df)
        assert errors == []

    def test_validate_ohlcv_negative_volume(self):
        """Negative volume should produce an error."""
        from core.data.csv_importer import validate_ohlcv

        df = make_ohlcv(n=10)
        df.loc[df.index[0], "volume"] = -100.0
        errors = validate_ohlcv(df)
        assert any("negative" in e for e in errors)

    def test_validate_ohlcv_high_below_close(self):
        """High < close should produce a high-consistency error."""
        from core.data.csv_importer import validate_ohlcv

        df = make_ohlcv(n=10)
        df.loc[df.index[0], "high"] = df.loc[df.index[0], "close"] - 100
        errors = validate_ohlcv(df)
        assert any("high" in e.lower() for e in errors)

    def test_validate_ohlcv_nan_values(self):
        """NaN in OHLCV columns should produce NaN errors."""
        from core.data.csv_importer import validate_ohlcv

        df = make_ohlcv(n=10)
        df.loc[df.index[0], "close"] = float("nan")
        errors = validate_ohlcv(df)
        assert any("NaN" in e for e in errors)
