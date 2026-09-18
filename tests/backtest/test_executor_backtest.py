"""Tests for DNA validator, executor, and backtest engine."""

import pytest

pytestmark = [pytest.mark.unit]
import pandas as pd
import numpy as np

from core.strategy.dna import (
    ConditionType,
    SignalRole,
    SignalGene,
    LogicGenes,
    ExecutionGenes,
    RiskGenes,
    StrategyDNA,
)
from core.strategy.validator import validate_dna, ValidationResult
from core.strategy.executor import evaluate_condition, combine_signals, dna_to_signals
from core.backtest.engine import BacktestEngine, BacktestResult
from core.features.indicators import compute_all_indicators

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def valid_dna():
    """Standard RSI strategy DNA."""
    return StrategyDNA(
        strategy_id="test-valid",
        signal_genes=[
            SignalGene("RSI", {"period": 14}, SignalRole.ENTRY_TRIGGER, None,
                       {"type": "lt", "threshold": 30}),
            SignalGene("EMA", {"period": 100}, SignalRole.ENTRY_GUARD, None,
                       {"type": "price_above"}),
            SignalGene("RSI", {"period": 14}, SignalRole.EXIT_TRIGGER, None,
                       {"type": "gt", "threshold": 70}),
        ],
        logic_genes=LogicGenes(entry_logic="AND", exit_logic="OR"),
        execution_genes=ExecutionGenes(timeframe="4h", symbol="BTCUSDT"),
        risk_genes=RiskGenes(stop_loss=0.05, take_profit=None, position_size=0.3),
    )

@pytest.fixture
def sample_ohlcv():
    """500 bars of synthetic 4h OHLCV data."""
    np.random.seed(42)
    n = 500
    dates = pd.date_range("2023-01-01", periods=n, freq="4h")
    close = 30000 + np.cumsum(np.random.randn(n) * 200)
    df = pd.DataFrame({
        "open": close + np.random.randn(n) * 50,
        "high": close + abs(np.random.randn(n) * 100),
        "low": close - abs(np.random.randn(n) * 100),
        "close": close,
        "volume": np.random.randint(100, 10000, n).astype(float),
    }, index=dates)
    return compute_all_indicators(df)

# ===========================================================================
# Validator Tests
# ===========================================================================

class TestValidator:
    def test_valid_dna_passes(self, valid_dna):
        result = validate_dna(valid_dna)
        assert result.is_valid
        assert len(result.errors) == 0

    def test_no_entry_signal_fails(self):
        dna = StrategyDNA(
            signal_genes=[
                SignalGene("RSI", {"period": 14}, SignalRole.EXIT_TRIGGER, None,
                           {"type": "gt", "threshold": 70}),
            ],
            logic_genes=LogicGenes(entry_logic="AND", exit_logic="OR"),
            execution_genes=ExecutionGenes(timeframe="4h", symbol="BTCUSDT"),
            risk_genes=RiskGenes(stop_loss=0.05, take_profit=None, position_size=0.3),
        )
        result = validate_dna(dna)
        assert not result.is_valid
        assert any("entry" in e.lower() for e in result.errors)

    def test_no_exit_signal_fails(self):
        dna = StrategyDNA(
            signal_genes=[
                SignalGene("RSI", {"period": 14}, SignalRole.ENTRY_TRIGGER, None,
                           {"type": "lt", "threshold": 30}),
            ],
            logic_genes=LogicGenes(entry_logic="AND", exit_logic="OR"),
            execution_genes=ExecutionGenes(timeframe="4h", symbol="BTCUSDT"),
            risk_genes=RiskGenes(stop_loss=0.05, take_profit=None, position_size=0.3),
        )
        result = validate_dna(dna)
        assert not result.is_valid
        assert any("exit" in e.lower() for e in result.errors)

    def test_stop_loss_too_small_fails(self):
        dna = StrategyDNA(
            signal_genes=[
                SignalGene("RSI", {"period": 14}, SignalRole.ENTRY_TRIGGER, None,
                           {"type": "lt", "threshold": 30}),
                SignalGene("RSI", {"period": 14}, SignalRole.EXIT_TRIGGER, None,
                           {"type": "gt", "threshold": 70}),
            ],
            logic_genes=LogicGenes(entry_logic="AND", exit_logic="OR"),
            execution_genes=ExecutionGenes(timeframe="4h", symbol="BTCUSDT"),
            risk_genes=RiskGenes(stop_loss=0.002, take_profit=None, position_size=0.3),
        )
        result = validate_dna(dna)
        assert not result.is_valid

    def test_stop_loss_too_large_fails(self):
        dna = StrategyDNA(
            signal_genes=[
                SignalGene("RSI", {"period": 14}, SignalRole.ENTRY_TRIGGER, None,
                           {"type": "lt", "threshold": 30}),
                SignalGene("RSI", {"period": 14}, SignalRole.EXIT_TRIGGER, None,
                           {"type": "gt", "threshold": 70}),
            ],
            logic_genes=LogicGenes(entry_logic="AND", exit_logic="OR"),
            execution_genes=ExecutionGenes(timeframe="4h", symbol="BTCUSDT"),
            risk_genes=RiskGenes(stop_loss=0.5, take_profit=None, position_size=0.3),
        )
        result = validate_dna(dna)
        assert not result.is_valid

    def test_position_size_too_small_fails(self):
        dna = StrategyDNA(
            signal_genes=[
                SignalGene("RSI", {"period": 14}, SignalRole.ENTRY_TRIGGER, None,
                           {"type": "lt", "threshold": 30}),
                SignalGene("RSI", {"period": 14}, SignalRole.EXIT_TRIGGER, None,
                           {"type": "gt", "threshold": 70}),
            ],
            logic_genes=LogicGenes(entry_logic="AND", exit_logic="OR"),
            execution_genes=ExecutionGenes(timeframe="4h", symbol="BTCUSDT"),
            risk_genes=RiskGenes(stop_loss=0.05, take_profit=None, position_size=0.05),
        )
        result = validate_dna(dna)
        assert not result.is_valid

    def test_boundary_values_pass(self):
        """Min stop_loss=0.005, max position_size=1.0 should pass."""
        dna = StrategyDNA(
            signal_genes=[
                SignalGene("RSI", {"period": 14}, SignalRole.ENTRY_TRIGGER, None,
                           {"type": "lt", "threshold": 30}),
                SignalGene("RSI", {"period": 14}, SignalRole.EXIT_TRIGGER, None,
                           {"type": "gt", "threshold": 70}),
            ],
            logic_genes=LogicGenes(entry_logic="AND", exit_logic="OR"),
            execution_genes=ExecutionGenes(timeframe="4h", symbol="BTCUSDT"),
            risk_genes=RiskGenes(stop_loss=0.005, take_profit=None, position_size=1.0),
        )
        result = validate_dna(dna)
        assert result.is_valid

# ===========================================================================
# Executor Tests
# ===========================================================================

class TestEvaluateCondition:
    def test_lt_condition(self, sample_ohlcv):
        rsi = sample_ohlcv["rsi_14"]
        result = evaluate_condition(rsi, sample_ohlcv["close"], {"type": "lt", "threshold": 30})
        assert isinstance(result, pd.Series)
        assert result.dtype == bool
        # Some bars should be below 30, some above
        assert result.any()
        assert not result.all()

    def test_gt_condition(self, sample_ohlcv):
        rsi = sample_ohlcv["rsi_14"]
        result = evaluate_condition(rsi, sample_ohlcv["close"], {"type": "gt", "threshold": 70})
        assert isinstance(result, pd.Series)

    def test_price_above_condition(self, sample_ohlcv):
        ema = sample_ohlcv["ema_100"]
        result = evaluate_condition(ema, sample_ohlcv["close"], {"type": "price_above"})
        assert isinstance(result, pd.Series)
        assert result.dtype == bool

    def test_cross_above_condition(self, sample_ohlcv):
        rsi = sample_ohlcv["rsi_14"]
        result = evaluate_condition(rsi, sample_ohlcv["close"], {"type": "cross_above", "threshold": 50})
        assert isinstance(result, pd.Series)
        # First value should be False (no previous bar to compare)
        assert result.iloc[0] == False

class TestCombineSignals:
    def test_and_logic(self, sample_ohlcv):
        s1 = pd.Series([True, True, False, False], index=sample_ohlcv.index[:4])
        s2 = pd.Series([True, False, True, False], index=sample_ohlcv.index[:4])
        result = combine_signals([s1, s2], "AND")
        assert result.tolist() == [True, False, False, False]

    def test_or_logic(self, sample_ohlcv):
        s1 = pd.Series([True, True, False, False], index=sample_ohlcv.index[:4])
        s2 = pd.Series([True, False, True, False], index=sample_ohlcv.index[:4])
        result = combine_signals([s1, s2], "OR")
        assert result.tolist() == [True, True, True, False]

class TestDnaToSignals:
    def test_generates_entry_exit_signals(self, valid_dna, sample_ohlcv):
        entries, exits = dna_to_signals(valid_dna, sample_ohlcv)
        assert isinstance(entries, pd.Series)
        assert isinstance(exits, pd.Series)
        assert entries.dtype == bool
        assert exits.dtype == bool
        assert len(entries) == len(sample_ohlcv)
        assert len(exits) == len(sample_ohlcv)

# ===========================================================================
# Backtest Engine Tests
# ===========================================================================

class TestBacktestEngine:
    def test_run_returns_result(self, valid_dna, sample_ohlcv):
        engine = BacktestEngine()
        result = engine.run(valid_dna, sample_ohlcv)
        assert isinstance(result, BacktestResult)
        assert result.total_trades >= 0
        assert result.total_return is not None
        assert isinstance(result.equity_curve, pd.Series)

    def test_result_has_key_metrics(self, valid_dna, sample_ohlcv):
        engine = BacktestEngine()
        result = engine.run(valid_dna, sample_ohlcv)
        assert result.sharpe_ratio is not None
        assert result.max_drawdown is not None
        assert result.win_rate is not None

    def test_equity_curve_starts_at_init_cash(self, valid_dna, sample_ohlcv):
        engine = BacktestEngine()
        result = engine.run(valid_dna, sample_ohlcv)
        assert abs(result.equity_curve.iloc[0] - 100000) < 1

    def test_max_drawdown_non_positive(self, valid_dna, sample_ohlcv):
        engine = BacktestEngine()
        result = engine.run(valid_dna, sample_ohlcv)
        assert result.max_drawdown <= 0

    def test_win_rate_between_0_and_1(self, valid_dna, sample_ohlcv):
        engine = BacktestEngine()
        result = engine.run(valid_dna, sample_ohlcv)
        if result.total_trades > 0:
            assert 0 <= result.win_rate <= 1
