"""Integration tests for core workflows.

Verifies complete processing chains across module boundaries:
- Workflow 1: DNA -> signal -> backtest -> metrics -> score (full pipeline)
- Workflow 2: DNA serialization -> deserialization -> backtest (roundtrip)
- Workflow 3: MTF DNA -> signal set generation (multi-timeframe)
- Workflow 4: Metrics -> scoring with multiple templates
- Workflow 5: Data -> indicators -> backtest (data pipeline)

Run with: pytest tests/test_integration.py -v -m integration
"""

import numpy as np
import pandas as pd
import pytest

pytestmark = [pytest.mark.integration]

from tests.helpers.data_factory import make_ohlcv, make_dna, make_ema_dna, make_mtf_dna

# ============================================================================
# Workflow 1: Full pipeline DNA -> signal -> backtest -> metrics -> score
# ============================================================================

class TestFullPipeline:
    """End-to-end: DNA -> signal -> backtest -> metrics -> score."""

    def test_rsi_long_full_pipeline(self):
        """RSI long strategy through the complete scoring chain."""
        from core.strategy.executor import dna_to_signal_set
        from core.backtest.engine import BacktestEngine
        from core.scoring.metrics import compute_metrics
        from core.scoring.scorer import score_strategy

        dna = make_dna(indicator="RSI", timeframe="4h", direction="long")
        df = make_ohlcv(n=500, freq="4h")

        # Step 1: Generate signals
        signal_set = dna_to_signal_set(dna, df)
        assert signal_set.entries.dtype == bool
        assert signal_set.exits.dtype == bool

        # Step 2: Run backtest
        engine = BacktestEngine(init_cash=100000)
        result = engine.run(dna, df, signal_set=signal_set)
        assert result.equity_curve is not None
        assert result.total_trades >= 0

        # Step 3: Compute metrics
        metrics = compute_metrics(
            result.equity_curve,
            total_trades=result.total_trades,
            trade_win_rate=result.trade_win_rate,
            trade_returns=result.trade_returns,
        )
        assert "annual_return" in metrics
        assert "sharpe_ratio" in metrics
        assert "max_drawdown" in metrics

        # Step 4: Score
        score_result = score_strategy(metrics, template_name="profit_first")
        assert "total_score" in score_result
        assert 0.0 <= score_result["total_score"] <= 100.0
        assert score_result["template_name"] == "explorer"

    def test_ema_short_full_pipeline(self):
        """EMA short strategy through the complete scoring chain."""
        from core.strategy.executor import dna_to_signal_set
        from core.backtest.engine import BacktestEngine
        from core.scoring.metrics import compute_metrics
        from core.scoring.scorer import score_strategy

        dna = make_ema_dna(timeframe="4h", direction="short")
        df = make_ohlcv(n=300, freq="4h")

        signal_set = dna_to_signal_set(dna, df)
        engine = BacktestEngine(init_cash=50000)
        result = engine.run(dna, df, signal_set=signal_set)

        metrics = compute_metrics(result.equity_curve, total_trades=result.total_trades)
        score_result = score_strategy(metrics, template_name="risk_first")
        assert score_result["template_name"] == "optimizer"  # risk_first -> optimizer

    def test_zero_trades_gives_zero_score(self):
        """Strategy with no signals should produce zero score."""
        from core.strategy.executor import dna_to_signal_set
        from core.backtest.engine import BacktestEngine
        from core.scoring.metrics import compute_metrics
        from core.scoring.scorer import score_strategy

        # Very short DataFrame likely produces no trades
        dna = make_dna(indicator="RSI", timeframe="4h")
        df = make_ohlcv(n=20, freq="4h")

        signal_set = dna_to_signal_set(dna, df)
        engine = BacktestEngine(init_cash=100000)
        result = engine.run(dna, df, signal_set=signal_set)

        metrics = compute_metrics(result.equity_curve, total_trades=result.total_trades)
        score_result = score_strategy(metrics)
        if result.total_trades == 0:
            assert score_result["total_score"] == 0.0

# ============================================================================
# Workflow 2: DNA serialization roundtrip
# ============================================================================

@pytest.mark.integration
class TestDNASerializationRoundtrip:
    """Verify DNA to_dict -> from_dict -> backtest produces same results."""

    def test_single_tf_roundtrip(self):
        dna = make_dna(indicator="RSI")
        d = dna.to_dict()
        from core.strategy.dna import StrategyDNA
        dna2 = StrategyDNA.from_dict(d)

        assert dna2.execution_genes.timeframe == dna.execution_genes.timeframe
        assert dna2.risk_genes.direction == dna.risk_genes.direction
        assert len(dna2.signal_genes) == len(dna.signal_genes)

    def test_mtf_roundtrip(self):
        dna = make_mtf_dna(timeframes=("1d", "4h", "15m"), mtf_mode="direction")
        d = dna.to_dict()
        from core.strategy.dna import StrategyDNA
        dna2 = StrategyDNA.from_dict(d)

        assert dna2.is_mtf
        assert len(dna2.layers) == 3
        assert dna2.mtf_mode == "direction"

    def test_roundtrip_preserves_backtest_input(self):
        """After roundtrip, the DNA should still produce valid signals."""
        from core.strategy.executor import dna_to_signal_set
        from core.strategy.dna import StrategyDNA

        dna = make_dna(indicator="RSI", timeframe="4h")
        df = make_ohlcv(n=200, freq="4h")
        sig1 = dna_to_signal_set(dna, df)

        dna2 = StrategyDNA.from_dict(dna.to_dict())
        sig2 = dna_to_signal_set(dna2, df)

        # Same strategy should produce same signals
        assert sig1.entries.equals(sig2.entries)
        assert sig1.exits.equals(sig2.exits)

# ============================================================================
# Workflow 3: MTF signal generation
# ============================================================================

@pytest.mark.integration
class TestMTFSignalGeneration:
    """Multi-timeframe DNA signal generation."""

    def test_mtf_and_produces_signals(self):
        """MTF AND strategy produces valid signals."""
        from core.strategy.executor import dna_to_signal_set

        dna = make_mtf_dna(timeframes=("4h", "15m"), cross_layer_logic="AND")
        df_4h = make_ohlcv(n=300, freq="4h")
        df_15m = make_ohlcv(n=1200, freq="15min")
        signal_set = dna_to_signal_set(dna, df_4h, dfs_by_timeframe={"4h": df_4h, "15m": df_15m})
        assert signal_set.entries.dtype == bool

    def test_mtf_or_produces_signals(self):
        """MTF OR strategy produces valid signals."""
        from core.strategy.executor import dna_to_signal_set

        dna = make_mtf_dna(timeframes=("4h", "15m"), cross_layer_logic="OR")
        df_4h = make_ohlcv(n=300, freq="4h")
        df_15m = make_ohlcv(n=1200, freq="15min")
        signal_set = dna_to_signal_set(dna, df_4h, dfs_by_timeframe={"4h": df_4h, "15m": df_15m})
        assert signal_set.entries.dtype == bool

# ============================================================================
# Workflow 4: Metrics -> Scoring with all templates
# ============================================================================

@pytest.mark.integration
class TestScoringTemplates:
    """Verify all scoring templates produce valid results."""

    @pytest.fixture
    def sample_metrics(self):
        """Realistic metrics from a backtest."""
        return {
            "annual_return": 0.35,
            "sharpe_ratio": 1.8,
            "max_drawdown": 0.15,
            "win_rate": 0.55,
            "calmar_ratio": 2.3,
            "sortino_ratio": 2.5,
            "profit_factor": 1.5,
            "max_consecutive_losses": 3,
            "monthly_consistency": 0.7,
            "r_squared": 0.65,
            "total_trades": 50,
        }

    @pytest.mark.parametrize("template_name", [
        "balanced", "aggressive", "conservative",
        "profit_first", "steady", "risk_first", "custom",
    ])
    def test_template_produces_valid_score(self, sample_metrics, template_name):
        from core.scoring.scorer import score_strategy
        result = score_strategy(sample_metrics, template_name=template_name)
        assert 0.0 <= result["total_score"] <= 100.0
        # Aliases are resolved to core template names
        assert result["template_name"] in ("explorer", "optimizer", "max_return")
        assert isinstance(result["dimension_scores"], dict)

    def test_liquidated_strategy_gets_zero_score(self, sample_metrics):
        from core.scoring.scorer import score_strategy
        result = score_strategy(sample_metrics, liquidated=True)
        assert result["total_score"] == 0.0
        assert result["liquidated"] is True

    def test_zero_trades_gets_zero_score(self):
        from core.scoring.scorer import score_strategy
        metrics = {k: 0.0 for k in [
            "annual_return", "sharpe_ratio", "max_drawdown", "win_rate",
            "calmar_ratio", "sortino_ratio", "profit_factor",
            "monthly_consistency", "r_squared",
        ]}
        metrics["total_trades"] = 0
        metrics["max_consecutive_losses"] = 0
        result = score_strategy(metrics)
        assert result["total_score"] == 0.0

# ============================================================================
# Workflow 5: Data -> indicators -> backtest
# ============================================================================

@pytest.mark.integration
class TestDataIndicatorsBacktest:
    """Data pipeline: raw OHLCV -> indicators -> backtest."""

    def test_raw_data_to_backtest(self):
        """Raw OHLCV data through indicator computation and backtest."""
        from core.features.indicators import compute_all_indicators
        from core.strategy.executor import dna_to_signal_set
        from core.backtest.engine import BacktestEngine

        # Raw data
        df = make_ohlcv(n=500, freq="4h")
        assert "open" in df.columns

        # Compute indicators
        enhanced = compute_all_indicators(df)
        assert "close" in enhanced.columns
        # Should have indicator columns added
        indicator_cols = [c for c in enhanced.columns if c not in df.columns]
        assert len(indicator_cols) > 0

        # Run backtest on enhanced data
        dna = make_dna(indicator="RSI", timeframe="4h")
        signal_set = dna_to_signal_set(dna, enhanced)
        engine = BacktestEngine(init_cash=100000)
        result = engine.run(dna, enhanced, signal_set=signal_set)
        assert result.equity_curve is not None

    def test_different_timeframes(self):
        """Pipeline works with different timeframes."""
        from core.features.indicators import compute_all_indicators
        from core.strategy.executor import dna_to_signal_set
        from core.backtest.engine import BacktestEngine

        for tf in ["1h", "4h"]:
            df = make_ohlcv(n=300, freq=tf)
            enhanced = compute_all_indicators(df)
            dna = make_dna(indicator="RSI", timeframe=tf)
            signal_set = dna_to_signal_set(dna, enhanced)
            engine = BacktestEngine(init_cash=100000)
            result = engine.run(dna, enhanced, signal_set=signal_set)
            assert result.equity_curve is not None
