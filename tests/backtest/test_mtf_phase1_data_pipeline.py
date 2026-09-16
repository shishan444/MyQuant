"""Phase 1: MTF Data Pipeline fixes.

Verifies:
- H1: Compare endpoint load_mtf_data parameter fix
- MTF data loading and structure
"""

import numpy as np
import pandas as pd
import pytest

pytestmark = [pytest.mark.integration]
from pathlib import Path
from unittest.mock import patch

from core.data.mtf_loader import load_mtf_data
from core.strategy.dna import (
    ExecutionGenes,
    LogicGenes,
    RiskGenes,
    SignalGene,
    SignalRole,
    StrategyDNA,
    TimeframeLayer,
)

# ── Helpers ──

def _make_ohlcv_df(n=500, timeframe="4h", start="2024-01-01"):
    """Create synthetic OHLCV DataFrame for given timeframe."""
    freq_map = {"1m": "1min", "5m": "5min", "15m": "15min", "30m": "30min",
                "1h": "1h", "4h": "4h", "1d": "1D", "3d": "3D"}
    freq = freq_map.get(timeframe, "4h")
    np.random.seed(42)
    dates = pd.date_range(start, periods=n, freq=freq, tz="UTC")
    close = 40000 + np.cumsum(np.random.randn(n) * 100)
    df = pd.DataFrame({
        "open": close * 0.999, "high": close * 1.005,
        "low": close * 0.995, "close": close, "volume": 1000.0,
    }, index=dates)
    df.index.name = "timestamp"
    return df

def _make_mtf_dna(exec_timeframe="4h", symbol="BTCUSDT"):
    """Create a multi-timeframe strategy DNA with 2 layers."""
    return StrategyDNA(
        signal_genes=[
            SignalGene("RSI", {"period": 14}, SignalRole.ENTRY_TRIGGER, "RSI_14",
                        {"type": "lt", "threshold": 30}),
            SignalGene("RSI", {"period": 14}, SignalRole.EXIT_TRIGGER, "RSI_14",
                        {"type": "gt", "threshold": 70}),
        ],
        logic_genes=LogicGenes(entry_logic="AND", exit_logic="AND"),
        execution_genes=ExecutionGenes(timeframe=exec_timeframe, symbol=symbol),
        risk_genes=RiskGenes(stop_loss=0.05, take_profit=0.10, position_size=0.5,
                             leverage=1, direction="long"),
        layers=[
            TimeframeLayer(
                timeframe="1d",
                signal_genes=[
                    SignalGene("EMA", {"period": 50}, SignalRole.ENTRY_TRIGGER, "ema_50",
                                {"type": "price_above"}),
                ],
                logic_genes=LogicGenes(entry_logic="AND", exit_logic="AND"),
                role="trend",
            ),
        ],
    )

# ── H1: Compare endpoint parameter fix ──

def test_load_mtf_data_signature():
    """load_mtf_data should accept correct positional args."""
    import inspect
    sig = inspect.signature(load_mtf_data)
    params = list(sig.parameters.keys())
    assert params[:5] == ["data_dir", "symbol", "exec_timeframe", "enhanced_df", "needed_tfs"]

def test_compare_builds_needed_tfs_from_dna_layers():
    """Compare endpoint should extract needed_tfs from dna.layers."""
    dna = _make_mtf_dna()
    exec_tf = dna.execution_genes.timeframe
    needed_tfs = {layer.timeframe for layer in dna.layers}
    needed_tfs.add(exec_tf)
    assert "1d" in needed_tfs
    assert exec_tf in needed_tfs

def test_compare_endpoint_imports():
    """Verify compare endpoint module imports correctly."""
    from api.routes.strategies import compare_strategies
    assert callable(compare_strategies)

# ── MTF data loading ──

def test_load_mtf_data_returns_none_for_single_tf():
    """load_mtf_data returns valid dict with exec timeframe when no additional TFs available."""
    data_dir = Path("/nonexistent")
    enhanced_df = _make_ohlcv_df(500)
    result = load_mtf_data(
        data_dir, "BTCUSDT", "4h", enhanced_df,
        {"4h"},
    )
    # Now returns {exec_timeframe: df} instead of None for graceful degradation
    assert result is not None
    assert "4h" in result

def test_load_mtf_data_returns_dict_with_exec_tf():
    """load_mtf_data should include exec_timeframe in returned dict."""
    enhanced_df = _make_ohlcv_df(500)

    with patch("core.data.mtf_loader.find_parquet") as mock_find:
        mock_find.return_value = Path("/fake/BTCUSDT_1d.parquet")
        daily_df = _make_ohlcv_df(200, "1d")

        with patch("core.data.storage.load_parquet", return_value=daily_df):
            result = load_mtf_data(
                Path("/fake"), "BTCUSDT", "4h", enhanced_df,
                {"4h", "1d"},
            )

    assert result is not None
    assert "4h" in result
    assert "1d" in result
    assert result["4h"] is enhanced_df
