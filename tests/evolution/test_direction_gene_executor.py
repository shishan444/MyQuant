"""Tests for DIRECTION gene handling in executor."""
import numpy as np
import pandas as pd
import pytest

pytestmark = [pytest.mark.unit]

from core.strategy.dna import (
    ExecutionGenes, LogicGenes, RiskGenes, SignalGene, SignalRole, StrategyDNA,
    TimeframeLayer,
)
from core.strategy.executor import dna_to_signal_set, batch_signal_sets, evaluate_layer


def _make_trending_data(n=300):
    """Create synthetic trending data: up first half, down second half."""
    np.random.seed(55)
    dates = pd.date_range("2024-01-01", periods=n, freq="4h", tz="UTC")

    half = n // 2
    up = np.linspace(38000, 42000, half)
    down = np.linspace(42000, 38000, n - half)
    close = np.concatenate([up, down])
    close += np.random.randn(n) * 20

    df = pd.DataFrame({
        "open": close * 0.9999, "high": close * 1.002,
        "low": close * 0.998, "close": close, "volume": 1000.0,
    }, index=dates)
    df.index.name = "timestamp"

    # RSI: trigger entries in both halves
    rsi = np.full(n, 50.0)
    rsi[30] = 25.0
    rsi[60] = 25.0
    rsi[170] = 25.0
    rsi[200] = 25.0
    rsi[45] = 75.0
    rsi[80] = 75.0
    rsi[185] = 75.0
    rsi[220] = 75.0
    df["rsi_14"] = rsi

    # EMA: below price in uptrend, above price in downtrend
    ema = np.full(n, 40000.0)
    ema[:half] = close[:half] * 0.998
    ema[half:] = close[half:] * 1.002
    df["ema_50"] = ema

    # SMA: similar to EMA for additional direction tests
    df["sma_20"] = df["ema_50"] * 0.999

    return df


def _make_direction_dna(direction_condition="price_above", direction_indicator="EMA",
                         direction_params=None):
    """Create a single-TF mixed DNA with a DIRECTION gene."""
    return StrategyDNA(
        signal_genes=[
            SignalGene("RSI", {"period": 14}, SignalRole.ENTRY_TRIGGER,
                       None, {"type": "lt", "threshold": 30}),
            SignalGene("RSI", {"period": 14}, SignalRole.EXIT_TRIGGER,
                       None, {"type": "gt", "threshold": 70}),
            SignalGene(direction_indicator,
                       direction_params or {"period": 50},
                       SignalRole.DIRECTION, None,
                       {"type": direction_condition}),
        ],
        logic_genes=LogicGenes(entry_logic="AND", exit_logic="AND"),
        execution_genes=ExecutionGenes(timeframe="4h", symbol="BTCUSDT"),
        risk_genes=RiskGenes(
            stop_loss=0.05, take_profit=0.10,
            position_size=0.5, leverage=1, direction="mixed",
        ),
    )


class TestSingleTFDirectionGene:
    """Test DIRECTION gene in single-timeframe path."""

    def test_direction_gene_overrides_momentum(self):
        """DIRECTION gene should override momentum fallback."""
        df = _make_trending_data()
        dna = _make_direction_dna("price_above", "EMA")

        sig = dna_to_signal_set(dna, df)

        assert sig.entry_direction is not None
        half = len(df) // 2
        # Uptrend: price > EMA -> direction = +1
        uptrend_dirs = sig.entry_direction.iloc[:half]
        # Downtrend: price < EMA -> direction = -1
        downtrend_dirs = sig.entry_direction.iloc[half:]

        assert (uptrend_dirs == 1.0).any(), "Uptrend should have +1 direction"
        assert (downtrend_dirs == -1.0).any(), "Downtrend should have -1 direction"

    def test_no_direction_gene_neutral_direction(self):
        """Without DIRECTION gene, mixed strategy gets neutral (0.0) direction."""
        df = _make_trending_data()
        dna = StrategyDNA(
            signal_genes=[
                SignalGene("RSI", {"period": 14}, SignalRole.ENTRY_TRIGGER,
                           None, {"type": "lt", "threshold": 30}),
                SignalGene("RSI", {"period": 14}, SignalRole.EXIT_TRIGGER,
                           None, {"type": "gt", "threshold": 70}),
            ],
            logic_genes=LogicGenes(entry_logic="AND", exit_logic="AND"),
            execution_genes=ExecutionGenes(timeframe="4h", symbol="BTCUSDT"),
            risk_genes=RiskGenes(direction="mixed"),
        )

        sig = dna_to_signal_set(dna, df)

        assert sig.entry_direction is not None
        # No DIRECTION gene: neutral direction (0.0)
        assert (sig.entry_direction == 0.0).all(), "Should be all neutral (0.0)"

    def test_direction_gene_not_used_for_long(self):
        """direction='long' should not generate entry_direction even with DIRECTION gene."""
        df = _make_trending_data()
        dna = StrategyDNA(
            signal_genes=[
                SignalGene("RSI", {"period": 14}, SignalRole.ENTRY_TRIGGER,
                           None, {"type": "lt", "threshold": 30}),
                SignalGene("EMA", {"period": 50}, SignalRole.DIRECTION,
                           None, {"type": "price_above"}),
            ],
            logic_genes=LogicGenes(),
            risk_genes=RiskGenes(direction="long"),
        )

        sig = dna_to_signal_set(dna, df)
        # direction="long" generates constant entry_direction=1.0
        assert sig.entry_direction is not None
        assert (sig.entry_direction == 1.0).all()

    def test_direction_gene_not_used_for_short(self):
        """direction='short' generates constant entry_direction=-1.0."""
        df = _make_trending_data()
        dna = StrategyDNA(
            signal_genes=[
                SignalGene("RSI", {"period": 14}, SignalRole.ENTRY_TRIGGER,
                           None, {"type": "lt", "threshold": 30}),
                SignalGene("EMA", {"period": 50}, SignalRole.DIRECTION,
                           None, {"type": "price_above"}),
            ],
            logic_genes=LogicGenes(),
            risk_genes=RiskGenes(direction="short"),
        )

        sig = dna_to_signal_set(dna, df)
        assert sig.entry_direction is not None
        assert (sig.entry_direction == -1.0).all()

    def test_direction_gene_price_above_uptrend(self):
        """EMA price_above: direction=+1 when price > EMA."""
        df = _make_trending_data()
        dna = _make_direction_dna("price_above", "EMA", {"period": 50})

        sig = dna_to_signal_set(dna, df)

        half = len(df) // 2
        # First half: price > EMA -> direction should be +1
        first_half = sig.entry_direction.iloc[:half]
        assert (first_half == 1.0).any()

    def test_direction_gene_price_below_downtrend(self):
        """price_below condition: direction=-1 when price < indicator."""
        df = _make_trending_data()
        dna = _make_direction_dna("price_below", "EMA", {"period": 50})

        sig = dna_to_signal_set(dna, df)

        half = len(df) // 2
        # First half: price > EMA, price_below is False -> direction = -1
        first_half = sig.entry_direction.iloc[:half]
        # Second half: price < EMA, price_below is True -> direction = +1
        second_half = sig.entry_direction.iloc[half:]

        assert (first_half == -1.0).any(), "price_below=False should give -1"
        assert (second_half == 1.0).any(), "price_below=True should give +1"


class TestBatchDirectionGene:
    """Test DIRECTION gene in batch signal path."""

    def test_batch_direction_gene_per_individual(self):
        """Batch: individuals with DIRECTION gene use it, others get neutral."""
        df = _make_trending_data()

        dna_with_dir = _make_direction_dna("price_above", "EMA")
        dna_without_dir = StrategyDNA(
            signal_genes=[
                SignalGene("RSI", {"period": 14}, SignalRole.ENTRY_TRIGGER,
                           None, {"type": "lt", "threshold": 30}),
                SignalGene("RSI", {"period": 14}, SignalRole.EXIT_TRIGGER,
                           None, {"type": "gt", "threshold": 70}),
            ],
            logic_genes=LogicGenes(),
            risk_genes=RiskGenes(direction="mixed"),
        )

        results = batch_signal_sets([dna_with_dir, dna_without_dir], df)

        assert len(results) == 2

        # First individual uses DIRECTION gene
        sig_with = results[0]
        assert sig_with.entry_direction is not None
        half = len(df) // 2
        assert (sig_with.entry_direction.iloc[:half] == 1.0).any()

        # Second individual uses momentum fallback
        sig_without = results[1]
        assert sig_without.entry_direction is not None

    def test_batch_long_no_direction(self):
        """Batch: direction='long' individuals should not have entry_direction."""
        df = _make_trending_data()

        dna_long = StrategyDNA(
            signal_genes=[
                SignalGene("RSI", {"period": 14}, SignalRole.ENTRY_TRIGGER,
                           None, {"type": "lt", "threshold": 30}),
                SignalGene("EMA", {"period": 50}, SignalRole.DIRECTION,
                           None, {"type": "price_above"}),
            ],
            logic_genes=LogicGenes(),
            risk_genes=RiskGenes(direction="long"),
        )

        results = batch_signal_sets([dna_long], df)
        assert results[0].entry_direction is None


class TestEvaluateLayerDirection:
    """Test evaluate_layer handles DIRECTION genes in TimeframeLayer."""

    def test_evaluate_layer_with_direction_gene(self):
        """evaluate_layer should produce entry_direction from DIRECTION gene."""
        df = _make_trending_data()
        layer = TimeframeLayer(
            timeframe="4h",
            signal_genes=[
                SignalGene("RSI", {"period": 14}, SignalRole.ENTRY_TRIGGER,
                           None, {"type": "lt", "threshold": 30}),
                SignalGene("EMA", {"period": 50}, SignalRole.DIRECTION,
                           None, {"type": "price_above"}),
            ],
            logic_genes=LogicGenes(entry_logic="AND", exit_logic="OR"),
        )

        sig = evaluate_layer(layer, df)

        assert sig.entry_direction is not None
        half = len(df) // 2
        # Uptrend: price > EMA -> direction = +1
        assert (sig.entry_direction.iloc[:half] == 1.0).any()
        # Downtrend: price < EMA -> direction = -1
        assert (sig.entry_direction.iloc[half:] == -1.0).any()

    def test_evaluate_layer_without_direction_gene(self):
        """evaluate_layer without DIRECTION gene should have None entry_direction."""
        df = _make_trending_data()
        layer = TimeframeLayer(
            timeframe="4h",
            signal_genes=[
                SignalGene("RSI", {"period": 14}, SignalRole.ENTRY_TRIGGER,
                           None, {"type": "lt", "threshold": 30}),
            ],
            logic_genes=LogicGenes(),
        )

        sig = evaluate_layer(layer, df)
        assert sig.entry_direction is None
