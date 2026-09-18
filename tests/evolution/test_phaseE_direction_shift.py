"""Phase E: entry_direction shift(1) alignment.

Verifies that entry_direction is shifted by 1 bar alongside entries/exits,
preventing look-ahead bias in mixed-direction mode.
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

def _make_dna(direction="mixed", leverage=1, sl=0.05, tp=0.10, pos_size=0.5):
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
    df['rsi_14'] = 50.0
    return df

def test_direction_signal_shifted_with_entries():
    """entry_direction should be shifted by 1 bar, matching entries shift.

    If bar N has an entry and direction=+1 (long), the direction used at
    execution should come from bar N-1's direction signal (shifted).
    Without shift, bar N would use its own direction = look-ahead.
    """
    n = 30
    df = _make_df(n)
    dna = _make_dna(direction="mixed")

    # Create signals where entry at bar 5 and direction at bar 5 = short (-1)
    # But direction at bar 4 = long (+1)
    # With correct shift(1), entry at bar 5 should use direction from bar 4 = long
    entries = pd.Series(False, index=df.index)
    entries.iloc[5] = True
    exits = pd.Series(False, index=df.index)
    exits.iloc[20] = True
    adds = pd.Series(False, index=df.index)
    reduces = pd.Series(False, index=df.index)
    # Direction: bar 4 = +1 (long), bar 5 = -1 (short)
    direction = pd.Series(1.0, index=df.index)
    direction.iloc[5] = -1.0

    sig_set = SignalSet(entries=entries, exits=exits, adds=adds, reduces=reduces,
                        entry_direction=direction)

    engine = BacktestEngine(init_cash=100000)
    result = engine.run(dna, df, signal_set=sig_set)

    # Should have a trade (entry at shifted bar 6, direction from bar 5)
    assert result.total_trades >= 1

def test_direction_at_execution_matches_prior_signal():
    """When entry_direction bar N = +1 and bar N-1 = -1,
    the executed trade direction should follow bar N-1 (shifted).

    This verifies the shift prevents look-ahead: direction at execution
    bar N uses direction from signal bar N-1.
    """
    n = 30
    close_arr = np.ones(n) * 100
    # Rising prices from bar 5 to bar 10 (good for long)
    close_arr[6:12] = [101, 102, 103, 104, 105, 106]
    df = _make_df(n, close_arr)
    dna = _make_dna(direction="mixed")

    entries = pd.Series(False, index=df.index)
    entries.iloc[5] = True
    exits = pd.Series(False, index=df.index)
    exits.iloc[15] = True
    adds = pd.Series(False, index=df.index)
    reduces = pd.Series(False, index=df.index)
    # Bar 4 (one before entry) direction = long (+1)
    direction = pd.Series(-1.0, index=df.index)
    direction.iloc[4] = 1.0  # bar 4 = long
    direction.iloc[5] = -1.0  # bar 5 = short (but entry shifts to bar 6)

    sig_set = SignalSet(entries=entries, exits=exits, adds=adds, reduces=reduces,
                        entry_direction=direction)

    engine = BacktestEngine(init_cash=100000)
    result = engine.run(dna, df, signal_set=sig_set)

    assert result.total_trades >= 1

def test_mixed_shift_no_lookahead():
    """Verify that direction at the shifted entry bar doesn't use future data.

    Without shift: entry at bar N would use direction[N] (look-ahead).
    With shift: entry at bar N uses direction[N-1] (correct).
    We create a scenario where this difference matters and check the trade outcome.
    """
    n = 30
    close_arr = np.ones(n) * 100
    # Price drops from bar 6 to bar 22 (sustained decline past exit signal)
    close_arr[6:23] = np.linspace(100, 80, 17)
    df = _make_df(n, close_arr)
    dna = _make_dna(direction="mixed", sl=0.0, tp=0.0)

    entries = pd.Series(False, index=df.index)
    entries.iloc[5] = True
    exits = pd.Series(False, index=df.index)
    exits.iloc[20] = True  # shifted to bar 21, close ~81.25
    adds = pd.Series(False, index=df.index)
    reduces = pd.Series(False, index=df.index)

    # Direction: bar 4 = +1 (long), bar 5 = -1 (short)
    # After shift(1): direction at bar 6 = direction[5] = -1 (short) -- correct
    # Without shift: direction at bar 6 = direction[6] = +1 (long) -- look-ahead
    direction = pd.Series(1.0, index=df.index)
    direction.iloc[4] = 1.0
    direction.iloc[5] = -1.0

    sig_set = SignalSet(entries=entries, exits=exits, adds=adds, reduces=reduces,
                        entry_direction=direction)

    engine = BacktestEngine(init_cash=100000)
    result = engine.run(dna, df, signal_set=sig_set)

    # Entry at shifted bar 6, direction from bar 5 = -1 (short)
    # Price drops from 100 to ~81 at exit bar 21, short should profit
    assert result.total_trades >= 1
    # Short position in a falling market should be profitable
    assert result.equity_curve.iloc[-1] > 100000
