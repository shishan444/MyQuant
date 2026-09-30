"""Phase 4: MTF layer role (trend/execution) to eliminate phantom signals."""

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
from core.strategy.executor import (
    SignalSet,
    dna_to_signal_set,
    resample_signals,
    evaluate_layer,
)
from core.strategy.validator import validate_dna

def _make_layer(tf="4h", role=None):
    return TimeframeLayer(
        timeframe=tf,
        signal_genes=[
            SignalGene('RSI', {'period': 14}, SignalRole.ENTRY_TRIGGER, 'RSI_14',
                        {'type': 'lt', 'threshold': 30}),
            SignalGene('RSI', {'period': 14}, SignalRole.EXIT_TRIGGER, 'RSI_14',
                        {'type': 'gt', 'threshold': 70}),
        ],
        logic_genes=LogicGenes(entry_logic='AND', exit_logic='AND'),
        role=role,
    )

def _make_df(n=100, seed=42):
    np.random.seed(seed)
    dates = pd.date_range('2024-01-01', periods=n, freq='4h', tz='UTC')
    close = 100 + np.cumsum(np.random.randn(n) * 0.5)
    df = pd.DataFrame({
        'open': close * 0.999, 'high': close * 1.005,
        'low': close * 0.995, 'close': close, 'volume': 1000.0,
    }, index=dates)
    df.index.name = 'timestamp'
    df['rsi_14'] = np.clip(50 + np.random.randn(n) * 20, 0, 100)
    return df

def test_layer_role_default_none():
    layer = TimeframeLayer(timeframe="4h")
    assert layer.role is None

def test_layer_role_trend():
    layer = TimeframeLayer(timeframe="1d", role="trend")
    assert layer.role == "trend"

def test_layer_role_execution():
    layer = TimeframeLayer(timeframe="4h", role="execution")
    assert layer.role == "execution"

def test_role_serialization():
    """Role should survive to_dict/from_dict roundtrip."""
    layer = _make_layer(role="trend")
    d = layer.to_dict()
    assert d["role"] == "trend"
    restored = TimeframeLayer.from_dict(d)
    assert restored.role == "trend"

def test_role_none_serialization():
    """None role should survive roundtrip."""
    layer = _make_layer(role=None)
    d = layer.to_dict()
    assert d["role"] is None
    restored = TimeframeLayer.from_dict(d)
    assert restored.role is None

def test_trend_signal_ffilled():
    """Trend layer signals: asof_completed 状态投影（0.2.14 收口）。

    Day3 的 1d bar（00:00 开盘）收盘于 Day4 00:00——其 True 信号从
    Day4 首 4h bar（index 24）起可见；Day3 内的 4h bar（18-23）处于
    该 1d bar 形成期，不可见（旧开盘即见 ffill 为前视窗口）。"""
    high_tf_index = pd.date_range('2024-01-01', periods=10, freq='1D', tz='UTC')
    signal = pd.Series(False, index=high_tf_index)
    # Day 3 and onward: True (trend established)
    signal.iloc[3:] = True

    target_index = pd.date_range('2024-01-01', periods=60, freq='4h', tz='UTC')

    result = resample_signals(signal, target_index)

    assert result.iloc[18] == False  # Day3 首 4h bar——1d bar 形成期不可见
    assert result.iloc[23] == False  # Day3 末 4h bar——同上
    assert result.iloc[24] == True   # Day4 首 4h bar——Day3 1d 收盘可见
    assert result.iloc[30] == True   # Day5（状态延续）

def test_execution_signal_not_ffilled():
    """Execution layer signals should NOT be forward-filled (pulse signals)."""
    high_tf_index = pd.date_range('2024-01-01', periods=10, freq='1D', tz='UTC')
    signal = pd.Series(False, index=high_tf_index)
    signal.iloc[3] = True

    target_index = pd.date_range('2024-01-01', periods=40, freq='4h', tz='UTC')

    from core.data.mtf_alignment import pulse_completed
    result = pulse_completed(signal, target_index, pd.Timedelta(days=1))

    # 脉冲 +tf 落位（0.2.14）：Day3 00:00 的脉冲收盘于 Day4 00:00——
    # 落在 Day4 首 4h bar（index 24）；单 bar 脉冲非 ffill
    true_bars = result[result].index
    assert len(true_bars) == 1
    assert true_bars[0] == target_index[24]

def test_no_role_defaults_execution():
    """Layers without role should be treated as execution (no ffill)."""
    df = _make_df(100)
    layer = _make_layer(role=None)  # No role -> defaults to execution

    # Evaluate the layer directly
    sig = evaluate_layer(layer, df)
    assert isinstance(sig, SignalSet)
    assert len(sig.entries) == len(df)

def test_phantom_signals_eliminated():
    """With trend+execution roles, phantom signals from ffill should be eliminated."""
    n = 60
    dates_4h = pd.date_range('2024-01-01', periods=n, freq='4h', tz='UTC')
    dates_1d = pd.date_range('2024-01-01', periods=n // 6 + 1, freq='1D', tz='UTC')

    # 4h data
    df_4h = pd.DataFrame({
        'open': 100.0, 'high': 101.0, 'low': 99.0,
        'close': 100.0, 'volume': 1000.0,
    }, index=dates_4h)
    df_4h.index.name = 'timestamp'
    df_4h['rsi_14'] = 50.0
    df_4h.loc[df_4h.index[10], 'rsi_14'] = 20  # Entry trigger at bar 10

    # 1d data
    df_1d = pd.DataFrame({
        'open': 100.0, 'high': 101.0, 'low': 99.0,
        'close': 100.0, 'volume': 1000.0,
    }, index=dates_1d)
    df_1d.index.name = 'timestamp'
    df_1d['rsi_14'] = 50.0
    # Trend: always bullish on 1d
    df_1d['rsi_14'] = 60.0  # Not oversold, so no entry signal

    # Create MTF DNA with trend role on 1d
    dna = StrategyDNA(
        signal_genes=[],
        logic_genes=LogicGenes(entry_logic='AND', exit_logic='AND'),
        execution_genes=ExecutionGenes(timeframe='4h', symbol='BTCUSDT'),
        risk_genes=RiskGenes(stop_loss=0.05, take_profit=0.10, position_size=0.5, leverage=1, direction='long'),
        layers=[
            _make_layer(tf='1d', role='trend'),
            _make_layer(tf='4h', role='execution'),
        ],
        cross_layer_logic='AND',
    )

    dfs_by_tf = {'4h': df_4h, '1d': df_1d}
    sig_set = dna_to_signal_set(dna, df_4h, dfs_by_timeframe=dfs_by_tf)

    # Execution signal should be pulse (only at bar 10, not ffilled)
    assert isinstance(sig_set.entries, pd.Series)

def test_backward_compat_no_role():
    """MTF DNA without roles should behave like before (all ffilled)."""
    df_4h = _make_df(60)
    df_1d = _make_df(15)

    dna = StrategyDNA(
        signal_genes=[],
        logic_genes=LogicGenes(entry_logic='AND', exit_logic='AND'),
        execution_genes=ExecutionGenes(timeframe='4h', symbol='BTCUSDT'),
        risk_genes=RiskGenes(stop_loss=0.05, take_profit=0.10, position_size=0.5, leverage=1, direction='long'),
        layers=[
            _make_layer(tf='1d', role=None),  # No role -> backward compat
            _make_layer(tf='4h', role=None),
        ],
        cross_layer_logic='AND',
    )

    dfs_by_tf = {'4h': df_4h, '1d': df_1d}
    sig_set = dna_to_signal_set(dna, df_4h, dfs_by_timeframe=dfs_by_tf)

    # Should produce valid signals
    assert len(sig_set.entries) == len(df_4h)
    assert isinstance(sig_set, SignalSet)

def test_validator_accepts_valid_roles():
    """Validator should accept trend and execution roles."""
    dna = StrategyDNA(
        signal_genes=[],
        logic_genes=LogicGenes(entry_logic='AND', exit_logic='AND'),
        execution_genes=ExecutionGenes(timeframe='4h', symbol='BTCUSDT'),
        risk_genes=RiskGenes(stop_loss=0.05, take_profit=0.10, position_size=0.5, leverage=1, direction='long'),
        layers=[
            _make_layer(tf='1d', role='trend'),
            _make_layer(tf='4h', role='execution'),
        ],
    )
    result = validate_dna(dna)
    assert result.is_valid, f"Errors: {result.errors}"
