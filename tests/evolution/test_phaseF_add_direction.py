"""Phase F: Add signal direction fix for mixed mode.

Verifies that add-to-position orders use the current position direction
instead of the DNA direction_val when in mixed mode.
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
from core.strategy.executor import SignalSet

def _make_dna(direction="mixed", leverage=1, sl=0.20, tp=0.0, pos_size=0.5):
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
            stop_loss=sl, take_profit=tp, position_size=pos_size,
            leverage=leverage, direction=direction,
        ),
    )

def _make_df(n=50, close_arr=None):
    dates = pd.date_range('2024-01-01', periods=n, freq='4h', tz='UTC')
    if close_arr is None:
        close_arr = np.ones(n) * 100
    close = np.array(close_arr, dtype=float)
    df = pd.DataFrame({
        'open': close * 0.999, 'high': close * 1.01,
        'low': close * 0.99, 'close': close, 'volume': 1000.0,
    }, index=dates)
    df.index.name = 'timestamp'
    return df

def test_add_long_position_direction_is_long():
    """When holding a long position, add signal should buy more (direction=Buy/0).

    In mixed mode, add must use position direction, not DNA direction_val.
    If direction_val (2) is used directly, vectorbt may reject the order.
    """
    n = 40
    close_arr = np.ones(n) * 100
    # Steady rise for long profits
    close_arr[5:35] = np.linspace(100, 130, 30)
    df = _make_df(n, close_arr)

    dna = _make_dna(direction="mixed", sl=0.0, tp=0.0)

    entries = pd.Series(False, index=df.index)
    entries.iloc[3] = True  # entry at bar 3, shifted to bar 4
    exits = pd.Series(False, index=df.index)
    exits.iloc[30] = True  # exit at bar 30, shifted to bar 31
    adds = pd.Series(False, index=df.index)
    adds.iloc[10] = True  # add at bar 10, shifted to bar 11
    reduces = pd.Series(False, index=df.index)
    # Direction = long (+1)
    direction = pd.Series(1.0, index=df.index)

    sig_set = SignalSet(entries=entries, exits=exits, adds=adds, reduces=reduces,
                        entry_direction=direction)

    engine = BacktestEngine(init_cash=100000)
    result = engine.run(dna, df, signal_set=sig_set)

    assert result.total_trades >= 1
    # Long with add in rising market should be profitable
    assert result.equity_curve.iloc[-1] > 100000

def test_add_short_position_direction_is_short():
    """When holding a short position, add signal should sell more (direction=Sell/1).

    In mixed mode, add must use position direction (short), not DNA direction_val.
    """
    n = 40
    close_arr = np.ones(n) * 100
    # Steady decline for short profits
    close_arr[5:35] = np.linspace(100, 70, 30)
    df = _make_df(n, close_arr)

    dna = _make_dna(direction="mixed", sl=0.0, tp=0.0)

    entries = pd.Series(False, index=df.index)
    entries.iloc[3] = True  # entry at bar 3, shifted to bar 4
    exits = pd.Series(False, index=df.index)
    exits.iloc[30] = True  # exit at bar 30, shifted to bar 31
    adds = pd.Series(False, index=df.index)
    adds.iloc[10] = True  # add at bar 10, shifted to bar 11
    reduces = pd.Series(False, index=df.index)
    # Direction = short (-1)
    direction = pd.Series(-1.0, index=df.index)

    sig_set = SignalSet(entries=entries, exits=exits, adds=adds, reduces=reduces,
                        entry_direction=direction)

    engine = BacktestEngine(init_cash=100000)
    result = engine.run(dna, df, signal_set=sig_set)

    assert result.total_trades >= 1
    # Short with add in falling market should be profitable
    assert result.equity_curve.iloc[-1] > 100000

def test_add_mixed_preserves_position_direction():
    """In mixed mode, adding to a position should keep the same direction as the position.

    This test opens long, adds, then verifies the equity behaves as expected
    for a long position that was enhanced by the add.
    """
    n = 40
    close_arr = np.ones(n) * 100
    close_arr[5:35] = np.linspace(100, 130, 30)
    df = _make_df(n, close_arr)

    dna = _make_dna(direction="mixed", sl=0.0, tp=0.0, pos_size=0.3)

    entries = pd.Series(False, index=df.index)
    entries.iloc[3] = True  # entry at bar 3, shifted to bar 4
    exits = pd.Series(False, index=df.index)
    exits.iloc[30] = True  # exit at bar 30, shifted to bar 31
    adds = pd.Series(False, index=df.index)
    adds.iloc[10] = True  # add at bar 10, shifted to bar 11
    adds.iloc[15] = True  # add at bar 15, shifted to bar 16
    reduces = pd.Series(False, index=df.index)
    direction = pd.Series(1.0, index=df.index)

    sig_set = SignalSet(entries=entries, exits=exits, adds=adds, reduces=reduces,
                        entry_direction=direction)

    engine = BacktestEngine(init_cash=100000)
    result = engine.run(dna, df, signal_set=sig_set)

    assert result.total_trades >= 1
    assert result.add_count >= 1
    # Long with multiple adds in rising market should be very profitable
    assert result.equity_curve.iloc[-1] > 100000
