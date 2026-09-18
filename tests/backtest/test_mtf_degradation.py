"""Phase F: MTF layer degradation diagnostics.

Verifies:
- dna_to_signal_set returns degraded_layers count when layers are skipped
- Executor logs ERROR (not WARNING) when layer data is missing
- BacktestEngine.run propagates degraded_layers to BacktestResult
- Full backtest with missing TF data reports degradation
"""

import logging

import numpy as np
import pandas as pd
import pytest

pytestmark = [pytest.mark.integration]

from core.strategy.dna import (
    ExecutionGenes,
    LogicGenes,
    RiskGenes,
    SignalGene,
    SignalRole,
    StrategyDNA,
    TimeframeLayer,
)
from core.strategy.executor import dna_to_signal_set
from core.backtest.engine import BacktestEngine

def _make_ohlcv(n=200, timeframe="4h", seed=42):
    """Create synthetic OHLCV DataFrame with indicators."""
    np.random.seed(seed)
    freq_map = {"1h": "1h", "4h": "4h", "1d": "1D"}
    freq = freq_map.get(timeframe, "4h")
    dates = pd.date_range("2024-01-01", periods=n, freq=freq, tz="UTC")
    close = 40000 + np.cumsum(np.random.randn(n) * 100)
    df = pd.DataFrame({
        "open": close * 0.999, "high": close * 1.005,
        "low": close * 0.995, "close": close, "volume": 1000.0,
    }, index=dates)
    df.index.name = "timestamp"
    df["rsi_14"] = 50.0
    df["ema_50"] = close.mean()
    return df

def _make_mtf_dna():
    """Create MTF DNA with 3 layers (4h, 1d, 1h)."""
    return StrategyDNA(
        signal_genes=[
            SignalGene("RSI", {"period": 14}, SignalRole.ENTRY_TRIGGER, "rsi_14",
                        {"type": "lt", "threshold": 30}),
            SignalGene("RSI", {"period": 14}, SignalRole.EXIT_TRIGGER, "rsi_14",
                        {"type": "gt", "threshold": 70}),
        ],
        logic_genes=LogicGenes(entry_logic="AND", exit_logic="AND"),
        execution_genes=ExecutionGenes(timeframe="4h", symbol="BTCUSDT"),
        risk_genes=RiskGenes(stop_loss=0.05, take_profit=0.10, position_size=0.5),
        layers=[
            TimeframeLayer(
                timeframe="4h",
                signal_genes=[
                    SignalGene("RSI", {"period": 14}, SignalRole.ENTRY_TRIGGER, "rsi_14",
                                {"type": "lt", "threshold": 30}),
                    SignalGene("RSI", {"period": 14}, SignalRole.EXIT_TRIGGER, "rsi_14",
                                {"type": "gt", "threshold": 70}),
                ],
                logic_genes=LogicGenes(entry_logic="AND", exit_logic="AND"),
                role="execution",
            ),
            TimeframeLayer(
                timeframe="1d",
                signal_genes=[
                    SignalGene("EMA", {"period": 50}, SignalRole.ENTRY_TRIGGER, "ema_50",
                                {"type": "price_above"}),
                ],
                logic_genes=LogicGenes(entry_logic="AND"),
                role="trend",
            ),
            TimeframeLayer(
                timeframe="1h",
                signal_genes=[
                    SignalGene("RSI", {"period": 14}, SignalRole.ENTRY_TRIGGER, "rsi_14",
                                {"type": "lt", "threshold": 35}),
                ],
                logic_genes=LogicGenes(entry_logic="AND"),
                role="execution",
            ),
        ],
    )

# ── dna_to_signal_set degradation reporting ──

def test_dna_to_signal_set_reports_degraded_layers():
    """3-layer DNA with only 2 layers of data should report 1 degraded."""
    dna = _make_mtf_dna()
    enhanced_df = _make_ohlcv(200, "4h")
    daily_df = _make_ohlcv(50, "1d")
    # Only provide 4h and 1d, missing 1h
    dfs_by_timeframe = {"4h": enhanced_df, "1d": daily_df}

    sig_set = dna_to_signal_set(dna, enhanced_df, dfs_by_timeframe=dfs_by_timeframe)

    assert hasattr(sig_set, "degraded_layers"), "SignalSet should have degraded_layers"
    assert sig_set.degraded_layers == 1, f"Expected 1 degraded layer, got {sig_set.degraded_layers}"

def test_dna_to_signal_set_reports_all_degraded():
    """3-layer DNA with no data should report 3 degraded."""
    dna = _make_mtf_dna()
    enhanced_df = _make_ohlcv(200, "4h")
    dfs_by_timeframe = {}  # No data for any layer

    sig_set = dna_to_signal_set(dna, enhanced_df, dfs_by_timeframe=dfs_by_timeframe)

    assert sig_set.degraded_layers == 3, f"Expected 3 degraded layers, got {sig_set.degraded_layers}"

def test_no_degradation_when_all_data_present():
    """All data present should report 0 degraded."""
    dna = _make_mtf_dna()
    enhanced_df = _make_ohlcv(200, "4h")
    daily_df = _make_ohlcv(50, "1d")
    hourly_df = _make_ohlcv(500, "1h")
    dfs_by_timeframe = {"4h": enhanced_df, "1d": daily_df, "1h": hourly_df}

    sig_set = dna_to_signal_set(dna, enhanced_df, dfs_by_timeframe=dfs_by_timeframe)

    assert sig_set.degraded_layers == 0

def test_executor_logs_error_on_missing_data(caplog):
    """Missing layer data should log at ERROR level, not WARNING."""
    dna = _make_mtf_dna()
    enhanced_df = _make_ohlcv(200, "4h")
    dfs_by_timeframe = {"4h": enhanced_df}  # Missing 1d and 1h

    with caplog.at_level(logging.ERROR, logger="core.strategy.executor"):
        dna_to_signal_set(dna, enhanced_df, dfs_by_timeframe=dfs_by_timeframe)

    error_logs = [r for r in caplog.records if r.levelno >= logging.ERROR]
    assert len(error_logs) >= 1, "Expected at least 1 ERROR log for missing layer data"

def test_backtest_result_includes_degraded_layers():
    """BacktestResult should include degraded_layers from signal evaluation."""
    dna = _make_mtf_dna()
    enhanced_df = _make_ohlcv(200, "4h")
    dfs_by_timeframe = {"4h": enhanced_df}  # Missing 1d and 1h

    engine = BacktestEngine()
    result = engine.run(dna, enhanced_df, dfs_by_timeframe=dfs_by_timeframe)

    assert hasattr(result, "degraded_layers"), "BacktestResult should have degraded_layers"
    assert result.degraded_layers >= 1, "Expected at least 1 degraded layer"

def test_signal_set_degraded_layers_default_zero():
    """SignalSet from single-TF path should have degraded_layers=0."""
    dna = StrategyDNA(
        signal_genes=[
            SignalGene("RSI", {"period": 14}, SignalRole.ENTRY_TRIGGER, "rsi_14",
                        {"type": "lt", "threshold": 30}),
            SignalGene("RSI", {"period": 14}, SignalRole.EXIT_TRIGGER, "rsi_14",
                        {"type": "gt", "threshold": 70}),
        ],
        logic_genes=LogicGenes(entry_logic="AND", exit_logic="AND"),
        execution_genes=ExecutionGenes(timeframe="4h"),
        risk_genes=RiskGenes(stop_loss=0.05, take_profit=0.10, position_size=0.5),
    )
    enhanced_df = _make_ohlcv(200, "4h")

    sig_set = dna_to_signal_set(dna, enhanced_df)
    assert sig_set.degraded_layers == 0
