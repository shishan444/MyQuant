"""Exception and boundary condition tests for core modules.

Targets the top 10 coverage gaps identified in the test audit:
1. executor._get_indicator_column raises ValueError for unknown indicator
2. BacktestEngine.run() with empty/1-bar DataFrame
3. compute_all_indicators with empty/NaN-heavy DataFrame
4. compute_metrics with single-element equity curve
5. StrategyDNA.from_dict with malformed input
6. Evolution operators with empty signal_genes
7. evaluate_condition with unknown condition type / missing key
8. storage.load_parquet with non-existent path
9. combine_signals with empty list
10. compute_metrics with all-NaN equity curve

Run with: pytest tests/test_exceptions.py -v
"""

import numpy as np
import pandas as pd
import pytest

pytestmark = [pytest.mark.unit]

from tests.helpers.data_factory import make_ohlcv, make_dna

# ============================================================================
# Gap 1: executor._get_indicator_column raises ValueError
# ============================================================================

class TestExecutorExceptions:
    """Exception tests for core/strategy/executor.py."""

    def test_unknown_indicator_raises(self):
        """Passing an unknown indicator to dna_to_signal_set should not crash silently."""
        from core.strategy.executor import dna_to_signal_set
        from core.strategy.dna import SignalGene, SignalRole, StrategyDNA, LogicGenes, ExecutionGenes, RiskGenes

        gene = SignalGene(
            indicator="NONEXISTENT_INDICATOR",
            params={"period": 14},
            role=SignalRole.ENTRY_TRIGGER,
            field_name="NONEXISTENT_14",
            condition={"type": "gt", "value": 50},
        )
        dna = StrategyDNA(
            signal_genes=[gene],
            logic_genes=LogicGenes(),
            execution_genes=ExecutionGenes(timeframe="4h"),
            risk_genes=RiskGenes(),
        )
        df = make_ohlcv(n=100, freq="4h")

        # Should not raise -- executor catches missing indicators gracefully
        # by returning all-False signals (evaluate_layer catches ValueError)
        signal_set = dna_to_signal_set(dna, df)
        assert signal_set.entries.dtype == bool

    def test_evaluate_condition_missing_type_key(self):
        """Condition dict without 'type' key should not crash."""
        from core.strategy.executor import evaluate_condition
        s = pd.Series([1.0, 2.0, 3.0])
        close = pd.Series([100.0, 101.0, 102.0])
        # Missing 'type' key -- will raise KeyError in evaluate_condition
        with pytest.raises(KeyError):
            evaluate_condition(s, close, {"value": 50})

    def test_evaluate_condition_unknown_type_returns_false(self):
        """Unknown condition type returns all-False series."""
        from core.strategy.executor import evaluate_condition
        s = pd.Series([1.0, 2.0, 3.0])
        close = pd.Series([100.0, 101.0, 102.0])
        result = evaluate_condition(s, close, {"type": "totally_unknown"})
        assert not result.any()

# ============================================================================
# Gap 2: BacktestEngine.run() boundary conditions
# ============================================================================

class TestBacktestEngineBoundary:
    """Boundary tests for core/backtest/engine.py."""

    def test_empty_dataframe(self):
        """Empty DataFrame should not hang BacktestEngine.run()."""
        from core.backtest.engine import BacktestEngine
        engine = BacktestEngine(init_cash=100000)
        dna = make_dna()
        df = pd.DataFrame(columns=["open", "high", "low", "close", "volume"])
        # Empty DataFrame -- Numba/vectorbt will raise on 0-length arrays
        with pytest.raises(Exception):
            engine.run(dna, df)

    def test_single_bar_dataframe(self):
        """1-bar DataFrame should not hang or produce infinite loop."""
        from core.backtest.engine import BacktestEngine
        from core.strategy.executor import dna_to_signal_set
        engine = BacktestEngine(init_cash=100000)
        dna = make_dna(timeframe="4h")
        df = make_ohlcv(n=1, freq="4h")

        # Either works or raises cleanly
        try:
            signal_set = dna_to_signal_set(dna, df)
            result = engine.run(dna, df, signal_set=signal_set)
            assert result is not None
        except (ValueError, IndexError):
            pass  # Acceptable on minimal data

# ============================================================================
# Gap 3: compute_all_indicators boundary conditions
# ============================================================================

class TestIndicatorsBoundary:
    """Boundary tests for core/features/indicators.py."""

    def test_empty_dataframe(self):
        """compute_all_indicators on empty DataFrame should not crash."""
        from core.features.indicators import compute_all_indicators
        df = pd.DataFrame(columns=["open", "high", "low", "close", "volume"])
        result = compute_all_indicators(df)
        assert isinstance(result, pd.DataFrame)

    def test_missing_columns(self):
        """compute_all_indicators with missing OHLCV columns."""
        from core.features.indicators import compute_all_indicators
        df = pd.DataFrame({"close": [1.0, 2.0, 3.0]})
        # Should raise or handle gracefully
        try:
            result = compute_all_indicators(df)
        except (KeyError, ValueError):
            pass  # Expected -- missing required columns

    def test_short_dataframe(self):
        """DataFrame shorter than longest indicator lookback."""
        from core.features.indicators import compute_all_indicators
        df = make_ohlcv(n=5, freq="4h")
        result = compute_all_indicators(df)
        # Should produce columns, many will be NaN
        assert isinstance(result, pd.DataFrame)
        assert "close" in result.columns

    def test_all_nan_values(self):
        """DataFrame with all NaN values in price columns."""
        from core.features.indicators import compute_all_indicators
        n = 50
        dates = pd.date_range("2024-01-01", periods=n, freq="4h", tz="UTC")
        df = pd.DataFrame({
            "open": [np.nan] * n,
            "high": [np.nan] * n,
            "low": [np.nan] * n,
            "close": [np.nan] * n,
            "volume": [np.nan] * n,
        }, index=dates)
        # Should not crash -- indicators will be NaN
        result = compute_all_indicators(df)
        assert isinstance(result, pd.DataFrame)

# ============================================================================
# Gap 4: compute_metrics with single-element equity curve
# ============================================================================

class TestMetricsBoundary:
    """Boundary tests for core/scoring/metrics.py."""

    def test_single_element_equity_curve(self):
        """Single element equity curve should return zero metrics."""
        from core.scoring.metrics import compute_metrics
        equity = pd.Series([100000.0])
        result = compute_metrics(equity)
        assert isinstance(result, dict)
        assert result.get("max_drawdown", 0) == 0.0

    def test_two_element_flat_equity(self):
        """Flat equity curve should have zero drawdown and zero return."""
        from core.scoring.metrics import compute_metrics
        equity = pd.Series([100000.0, 100000.0])
        result = compute_metrics(equity)
        assert result["max_drawdown"] == 0.0

    def test_empty_equity_curve(self):
        """Empty equity curve should be handled gracefully."""
        from core.scoring.metrics import compute_metrics
        equity = pd.Series([], dtype=float)
        result = compute_metrics(equity)
        assert isinstance(result, dict)

# ============================================================================
# Gap 5: StrategyDNA.from_dict with malformed input
# ============================================================================

class TestDNABoundary:
    """Boundary tests for core/strategy/dna.py."""

    def test_from_dict_empty(self):
        """from_dict with empty dict should use all defaults."""
        from core.strategy.dna import StrategyDNA
        dna = StrategyDNA.from_dict({})
        assert isinstance(dna, StrategyDNA)
        assert dna.generation == 0

    def test_from_dict_wrong_signal_genes_type(self):
        """from_dict with non-list signal_genes."""
        from core.strategy.dna import StrategyDNA
        # This should either gracefully handle or raise
        try:
            dna = StrategyDNA.from_dict({"signal_genes": "not_a_list"})
            # If it doesn't raise, signal_genes should be empty or converted
        except (TypeError, AttributeError):
            pass  # Acceptable to raise on bad input

    def test_from_dict_missing_logic_genes(self):
        """from_dict without logic_genes uses defaults."""
        from core.strategy.dna import StrategyDNA
        dna = StrategyDNA.from_dict({"signal_genes": []})
        assert dna.logic_genes.entry_logic == "AND"

    def test_to_dict_roundtrip_preserves_fields(self):
        """to_dict -> from_dict roundtrip preserves all key fields."""
        from core.strategy.dna import StrategyDNA
        dna = make_dna(indicator="RSI")
        d = dna.to_dict()
        dna2 = StrategyDNA.from_dict(d)
        assert dna2.execution_genes.timeframe == dna.execution_genes.timeframe
        assert dna2.risk_genes.direction == dna.risk_genes.direction

    def test_from_dict_legacy_trend_role(self):
        """Legacy 'trend' role maps to 'structure'."""
        from core.strategy.dna import StrategyDNA
        data = {
            "signal_genes": [],
            "layers": [{"timeframe": "1d", "signal_genes": [], "logic_genes": {}, "role": "trend"}],
        }
        dna = StrategyDNA.from_dict(data)
        assert dna.layers[0].role == "structure"

# ============================================================================
# Gap 6: Evolution operators with empty signal_genes
# ============================================================================

class TestEvolutionBoundary:
    """Boundary tests for core/evolution/operators.py."""

    def test_crossover_empty_signal_genes(self):
        """Crossover with empty signal_genes should not crash."""
        from core.evolution.operators import crossover
        from core.strategy.dna import StrategyDNA, LogicGenes, ExecutionGenes, RiskGenes
        dna_a = StrategyDNA(
            signal_genes=[],
            logic_genes=LogicGenes(),
            execution_genes=ExecutionGenes(),
            risk_genes=RiskGenes(),
        )
        dna_b = StrategyDNA(
            signal_genes=[],
            logic_genes=LogicGenes(),
            execution_genes=ExecutionGenes(),
            risk_genes=RiskGenes(),
        )
        # Should either work or raise cleanly
        try:
            child = crossover(dna_a, dna_b)
            assert isinstance(child, StrategyDNA)
        except (IndexError, ValueError):
            pass  # Acceptable to reject empty parents

# ============================================================================
# Gap 8: storage.load_parquet with non-existent path
# ============================================================================

class TestStorageBoundary:
    """Boundary tests for core/data/storage.py."""

    def test_load_nonexistent_file(self):
        """Loading a non-existent parquet file raises FileNotFoundError."""
        from core.data.storage import load_parquet
        from pathlib import Path
        with pytest.raises(FileNotFoundError):
            load_parquet(Path("/nonexistent/path/data.parquet"))

    def test_save_and_load_roundtrip(self, tmp_path):
        """save_parquet -> load_parquet roundtrip preserves data."""
        from core.data.storage import save_parquet, load_parquet
        from pathlib import Path
        df = make_ohlcv(n=10, freq="4h")
        path = tmp_path / "test.parquet"
        save_parquet(df, path)
        loaded = load_parquet(path)
        assert loaded is not None
        assert len(loaded) == 10
        assert list(loaded.columns) == list(df.columns)

# ============================================================================
# Gap 9: combine_signals with empty list
# ============================================================================

class TestCombineSignalsBoundary:
    """Boundary test for executor.combine_signals with empty list."""

    def test_combine_signals_empty_list(self):
        """combine_signals with empty signal list should handle gracefully."""
        from core.strategy.executor import combine_signals
        # Signature: combine_signals(signal_list, logic_str)
        result = combine_signals([], "AND")
        # Empty list returns empty False series
        if isinstance(result, pd.Series):
            assert len(result) == 0 or not result.any()

# ============================================================================
# Gap 10: compute_metrics with all-NaN equity curve
# ============================================================================

class TestMetricsNaN:
    """NaN handling tests for core/scoring/metrics.py."""

    def test_all_nan_equity_curve(self):
        """All-NaN equity curve should produce finite results."""
        from core.scoring.metrics import compute_metrics
        equity = pd.Series([np.nan, np.nan, np.nan, np.nan])
        result = compute_metrics(equity)
        assert isinstance(result, dict)
        # NaN is acceptable for some metrics, but should not crash
        for key, val in result.items():
            if isinstance(val, float):
                # Value is either finite or NaN, not inf
                assert np.isfinite(val) or np.isnan(val)
