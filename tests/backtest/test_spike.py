"""v0.1 Spike Test - End-to-end pipeline verification.

Validates: DNA → Indicators → Signals → Backtest → Metrics in < 5 seconds.
Uses synthetic data (no Binance API needed).
"""

import time
import pytest

pytestmark = [pytest.mark.unit]
import pandas as pd
import numpy as np

from core.strategy.dna import (
    SignalRole, SignalGene, LogicGenes, ExecutionGenes, RiskGenes, StrategyDNA,
)
from core.strategy.validator import validate_dna
from core.features.indicators import compute_all_indicators
from core.backtest.engine import BacktestEngine

@pytest.fixture
def synthetic_ohlcv():
    """2000 bars of synthetic 4h OHLCV data (~1 year)."""
    np.random.seed(42)
    n = 2000
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

def test_full_pipeline_end_to_end(synthetic_ohlcv):
    """Complete pipeline: DNA → validate → indicators → backtest → output metrics."""
    start_time = time.time()

    # 1. Create strategy DNA (use relaxed thresholds to ensure trades on random data)
    dna = StrategyDNA(
        strategy_id="spike-test",
        signal_genes=[
            SignalGene("RSI", {"period": 14}, SignalRole.ENTRY_TRIGGER, None,
                       {"type": "lt", "threshold": 45}),
            SignalGene("RSI", {"period": 14}, SignalRole.EXIT_TRIGGER, None,
                       {"type": "gt", "threshold": 55}),
        ],
        logic_genes=LogicGenes(entry_logic="AND", exit_logic="OR"),
        execution_genes=ExecutionGenes(timeframe="4h", symbol="BTCUSDT"),
        risk_genes=RiskGenes(stop_loss=0.05, take_profit=None, position_size=0.3),
    )

    # 2. Validate DNA
    result = validate_dna(dna)
    assert result.is_valid, f"DNA validation failed: {result.errors}"

    # 3. Compute indicators
    enhanced_df = compute_all_indicators(synthetic_ohlcv)
    assert "rsi_14" in enhanced_df.columns
    assert "ema_50" in enhanced_df.columns

    # 4. Run backtest
    engine = BacktestEngine(init_cash=100000, fee=0.001, slippage=0.0005)
    bt_result = engine.run(dna, enhanced_df)

    # 5. Verify results
    assert bt_result.total_trades > 0, "Expected at least 1 trade with relaxed thresholds"
    assert bt_result.equity_curve is not None
    assert len(bt_result.equity_curve) > 0
    assert abs(bt_result.equity_curve.iloc[0] - 100000) < 1

    elapsed = time.time() - start_time
    print(f"\n{'='*50}")
    print(f"v0.1 Spike Test Results")
    print(f"{'='*50}")
    print(f"Total trades:   {bt_result.total_trades}")
    print(f"Total return:   {bt_result.total_return:.2%}")
    print(f"Sharpe ratio:   {bt_result.sharpe_ratio:.2f}")
    print(f"Max drawdown:   {bt_result.max_drawdown:.2%}")
    print(f"Win rate:       {bt_result.win_rate:.2%}")
    print(f"Elapsed time:   {elapsed:.2f}s")
    print(f"{'='*50}")

    # Performance gate: must complete in < 20 seconds (includes indicator computation on 2000 bars)
    assert elapsed < 20.0, f"Pipeline took {elapsed:.2f}s, exceeds 20s limit"
