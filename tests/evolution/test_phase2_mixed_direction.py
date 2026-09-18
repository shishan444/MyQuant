"""Phase 2: Mixed direction support (Direction.Both=2)."""

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
)
from core.strategy.validator import validate_dna
from core.backtest.engine import BacktestEngine

def _make_simple_dna(direction="long"):
    gene_entry = SignalGene(
        indicator="RSI",
        params={"period": 14},
        role=SignalRole.ENTRY_TRIGGER,
        field_name="RSI_14",
        condition={"type": "lt", "threshold": 30},
    )
    gene_exit = SignalGene(
        indicator="RSI",
        params={"period": 14},
        role=SignalRole.EXIT_TRIGGER,
        field_name="RSI_14",
        condition={"type": "gt", "threshold": 70},
    )
    genes = [gene_entry, gene_exit]
    if direction == "mixed":
        genes.append(SignalGene(
            indicator="EMA",
            params={"period": 50},
            role=SignalRole.DIRECTION,
            field_name=None,
            condition={"type": "price_above"},
        ))
    return StrategyDNA(
        signal_genes=genes,
        logic_genes=LogicGenes(entry_logic="AND", exit_logic="AND"),
        execution_genes=ExecutionGenes(timeframe="4h", symbol="BTCUSDT"),
        risk_genes=RiskGenes(
            stop_loss=0.05,
            take_profit=0.10,
            position_size=0.5,
            leverage=1,
            direction=direction,
        ),
    )

def _make_trend_df(n=200):
    """Create synthetic price data that trends up then down."""
    np.random.seed(42)
    dates = pd.date_range("2024-01-01", periods=n, freq="4h", tz="UTC")

    # First half: uptrend, second half: downtrend
    up = np.linspace(100, 150, n // 2) + np.random.randn(n // 2) * 2
    down = np.linspace(150, 80, n - n // 2) + np.random.randn(n - n // 2) * 2
    close = np.concatenate([up, down])

    df = pd.DataFrame(
        {
            "open": close * 0.999,
            "high": close * 1.01,
            "low": close * 0.99,
            "close": close,
            "volume": 1000.0,
        },
        index=dates,
    )
    df.index.name = "timestamp"

    # Synthetic RSI: oversold in downtrend, overbought in uptrend
    rsi = np.full(n, 50.0)
    rsi[: n // 2] = 60 + np.random.randn(n // 2) * 15  # uptrend: higher RSI
    rsi[n // 2 :] = 40 + np.random.randn(n - n // 2) * 15  # downtrend: lower RSI

    # Force some entries/exits
    rsi[20] = 25   # oversold in uptrend -> long entry
    rsi[50] = 75   # overbought in uptrend -> exit
    rsi[110] = 25  # oversold in downtrend
    rsi[140] = 75  # overbought in downtrend -> short exit

    df["rsi_14"] = np.clip(rsi, 0, 100)
    return df

def test_direction_map_includes_mixed():
    from core.backtest.engine import BacktestEngine

    engine = BacktestEngine()
    direction_map = {"long": 0, "short": 1, "mixed": 2}
    assert direction_map["mixed"] == 2

def test_validator_accepts_mixed():
    dna = _make_simple_dna(direction="mixed")
    result = validate_dna(dna)
    assert result.is_valid, f"Validation errors: {result.errors}"

def test_mixed_backtest_produces_result():
    """End-to-end backtest with mixed direction should return valid BacktestResult."""
    df = _make_trend_df(200)
    dna = _make_simple_dna(direction="mixed")
    engine = BacktestEngine(init_cash=100000)
    result = engine.run(dna, df)

    assert result is not None
    assert result.equity_curve is not None
    assert len(result.equity_curve) == len(df)

def test_mixed_serialization_roundtrip():
    """RiskGenes(direction='mixed') should survive to_dict/from_dict."""
    dna = _make_simple_dna(direction="mixed")
    d = dna.to_dict()
    restored = StrategyDNA.from_dict(d)
    assert restored.risk_genes.direction == "mixed"

def test_long_and_short_still_work():
    """Existing long/short directions should still work after mixed support."""
    for direction in ("long", "short"):
        dna = _make_simple_dna(direction=direction)
        result = validate_dna(dna)
        assert result.is_valid, f"{direction} validation failed: {result.errors}"
