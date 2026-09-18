"""Phase I: run_with_portfolio double execution fix.

Verifies that run_with_portfolio only calls _build_portfolio once,
not twice (once directly + once via run()).
"""

import numpy as np
import pandas as pd
import pytest

pytestmark = [pytest.mark.integration]

from core.backtest.engine import BacktestEngine
from core.strategy.dna import (
    ExecutionGenes,
    LogicGenes,
    RiskGenes,
    SignalGene,
    SignalRole,
    StrategyDNA,
)

def _make_dna():
    return StrategyDNA(
        signal_genes=[
            SignalGene('RSI', {'period': 14}, SignalRole.ENTRY_TRIGGER, 'RSI_14',
                        {'type': 'lt', 'threshold': 30}),
            SignalGene('RSI', {'period': 14}, SignalRole.EXIT_TRIGGER, 'RSI_14',
                        {'type': 'gt', 'threshold': 70}),
        ],
        logic_genes=LogicGenes(entry_logic='AND', exit_logic='AND'),
        execution_genes=ExecutionGenes(timeframe='4h', symbol='BTCUSDT'),
        risk_genes=RiskGenes(
            stop_loss=0.05, take_profit=0.10, position_size=0.5,
            leverage=1, direction='long',
        ),
    )

def _make_df():
    np.random.seed(42)
    n = 200
    dates = pd.date_range('2024-01-01', periods=n, freq='4h', tz='UTC')
    close = 100 + np.cumsum(np.random.randn(n) * 0.5)
    df = pd.DataFrame({
        'open': close * 0.999, 'high': close * 1.005,
        'low': close * 0.995, 'close': close, 'volume': 1000.0,
    }, index=dates)
    df.index.name = 'timestamp'
    df['rsi_14'] = np.clip(50 + np.random.randn(n) * 20, 0, 100)
    return df

def test_run_with_portfolio_builds_once():
    """_build_portfolio should be called exactly once by run_with_portfolio.

    Before the fix: run_with_portfolio called _build_portfolio, then called
    run() which called _build_portfolio again = 2 calls total.
    After the fix: only 1 call.
    """
    dna = _make_dna()
    df = _make_df()
    engine = BacktestEngine(init_cash=100000)

    call_count = [0]
    original_build = engine._build_portfolio

    def counting_build(*args, **kwargs):
        call_count[0] += 1
        return original_build(*args, **kwargs)

    engine._build_portfolio = counting_build

    result, portfolio = engine.run_with_portfolio(dna, df)

    assert call_count[0] == 1, f"Expected 1 call, got {call_count[0]}"

def test_run_with_portfolio_result_matches_run():
    """Results from run_with_portfolio should be identical to run().

    Since both use the same data and DNA, the BacktestResult should match.
    """
    dna = _make_dna()
    df = _make_df()
    engine = BacktestEngine(init_cash=100000)

    result_run = engine.run(dna, df)
    result_rwp, portfolio = engine.run_with_portfolio(dna, df)

    # Key metrics should match
    assert abs(result_run.total_return - result_rwp.total_return) < 1e-6
    assert result_run.total_trades == result_rwp.total_trades
    assert result_run.add_count == result_rwp.add_count
    assert result_run.reduce_count == result_rwp.reduce_count
