"""Tests for Iteration 3: Signal confidence for paper trading.

Validates: BarSignals confidence extraction, evaluate() position sizing,
MTF confidence computation, backward compatibility, and boundary values.
"""

import math

import numpy as np
import pandas as pd
import pytest

pytestmark = [pytest.mark.unit]

from core.trading.types import AccountState, BarSignals, Decision, JudgmentConfig
from core.trading.judgment import evaluate


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_state(**overrides) -> AccountState:
    defaults = dict(
        balance=10000,
        has_position=False,
        position_side="flat",
        position_entry=0,
        position_quantity=0,
        position_margin=0,
        unrealized_pnl=0,
        position_bars_held=0,
        target_position_pct=0.3,
        actual_position_pct=0,
        equity=10000,
    )
    defaults.update(overrides)
    return AccountState(**defaults)


# ---------------------------------------------------------------------------
# 1. BarSignals confidence
# ---------------------------------------------------------------------------

class TestBarSignalsConfidence:
    """Validate confidence field in BarSignals."""

    def test_default_confidence_is_1(self):
        signals = BarSignals()
        assert signals.confidence == 1.0

    def test_confidence_can_be_set(self):
        signals = BarSignals(confidence=0.5)
        assert signals.confidence == 0.5

    def test_from_signal_set_without_confidence(self):
        """Without confidence in SignalSet, default to 1.0."""
        from core.strategy.executor import SignalSet

        n = 10
        ss = SignalSet(
            entries=pd.Series([False] * n),
            exits=pd.Series([False] * n),
            adds=pd.Series([False] * n),
            reduces=pd.Series([False] * n),
            entry_direction=pd.Series([1.0] * n),
        )
        bar = BarSignals.from_signal_set(ss, 0)
        assert bar.confidence == 1.0

    def test_from_signal_set_with_confidence(self):
        """Extract confidence from SignalSet."""
        from core.strategy.executor import SignalSet

        n = 10
        conf = pd.Series([0.3, 0.5, 0.7, 0.9, 1.0, 0.1, 0.4, 0.6, 0.8, 0.2])
        ss = SignalSet(
            entries=pd.Series([True] + [False] * 9),
            exits=pd.Series([False] * n),
            adds=pd.Series([False] * n),
            reduces=pd.Series([False] * n),
            entry_direction=pd.Series([1.0] * n),
            confidence=conf,
        )
        bar = BarSignals.from_signal_set(ss, 0)
        assert bar.confidence == pytest.approx(0.3, abs=0.01)

    def test_from_signal_set_nan_confidence_falls_back(self):
        """NaN confidence should fall back to 1.0."""
        from core.strategy.executor import SignalSet

        n = 5
        conf = pd.Series([np.nan, 0.5, 0.8, np.nan, 1.0])
        ss = SignalSet(
            entries=pd.Series([True] + [False] * 4),
            exits=pd.Series([False] * n),
            adds=pd.Series([False] * n),
            reduces=pd.Series([False] * n),
            entry_direction=pd.Series([1.0] * n),
            confidence=conf,
        )
        bar = BarSignals.from_signal_set(ss, 0)
        assert bar.confidence == 1.0  # NaN falls back


# ---------------------------------------------------------------------------
# 2. JudgmentConfig
# ---------------------------------------------------------------------------

class TestJudgmentConfigConfidence:
    """Validate confidence_sizing_enabled in JudgmentConfig."""

    def test_default_disabled(self):
        config = JudgmentConfig()
        assert config.confidence_sizing_enabled is False

    def test_can_enable(self):
        config = JudgmentConfig(confidence_sizing_enabled=True)
        assert config.confidence_sizing_enabled is True


# ---------------------------------------------------------------------------
# 3. evaluate() position sizing with confidence
# ---------------------------------------------------------------------------

class TestConfidencePositionSizing:
    """Validate confidence-weighted position sizing in evaluate()."""

    def test_confidence_disabled_no_effect(self):
        """When confidence_sizing_enabled=False, confidence is ignored."""
        signals = BarSignals(entry=True, direction=1.0, confidence=0.3)
        state = _make_state(has_position=False, allowed_direction="long")
        config = JudgmentConfig(confidence_sizing_enabled=False)
        decision = evaluate(signals, state, config)
        assert decision.action == "open"
        # Without confidence sizing, entry_pct = target * initial_entry_pct = 0.3 * 0.33
        assert decision.entry_size_pct > 0

    def test_confidence_enabled_scales_entry(self):
        """When enabled, entry_size_pct is scaled by confidence."""
        signals = BarSignals(entry=True, direction=1.0, confidence=0.5)
        state = _make_state(has_position=False, allowed_direction="long")
        config = JudgmentConfig(confidence_sizing_enabled=True)
        decision = evaluate(signals, state, config)

        signals_no_conf = BarSignals(entry=True, direction=1.0, confidence=1.0)
        decision_no_conf = evaluate(signals_no_conf, state, config)

        assert decision.action == "open"
        assert decision.entry_size_pct == pytest.approx(
            decision_no_conf.entry_size_pct * 0.5, abs=0.001
        )

    def test_confidence_zero_clamps_to_minimum(self):
        """confidence=0.0 should clamp to minimum 0.1."""
        signals = BarSignals(entry=True, direction=1.0, confidence=0.0)
        state = _make_state(has_position=False, allowed_direction="long")
        config = JudgmentConfig(confidence_sizing_enabled=True)
        decision = evaluate(signals, state, config)
        assert decision.action == "open"
        assert decision.entry_size_pct > 0  # should not be zero

    def test_confidence_1_no_change(self):
        """confidence=1.0 should not change sizing."""
        signals = BarSignals(entry=True, direction=1.0, confidence=1.0)
        state = _make_state(has_position=False, allowed_direction="long")
        config_enabled = JudgmentConfig(confidence_sizing_enabled=True)
        config_disabled = JudgmentConfig(confidence_sizing_enabled=False)
        d1 = evaluate(signals, state, config_enabled)
        d2 = evaluate(signals, state, config_disabled)
        assert d1.entry_size_pct == pytest.approx(d2.entry_size_pct, abs=0.001)

    def test_confidence_does_not_affect_exit(self):
        """Confidence should not affect close/reduce decisions."""
        signals = BarSignals(exit=True, confidence=0.1)
        state = _make_state(
            has_position=True, position_side="long",
            position_entry=100, position_quantity=1,
            position_bars_held=10,
        )
        config = JudgmentConfig(confidence_sizing_enabled=True)
        decision = evaluate(signals, state, config)
        assert decision.action == "close"


# ---------------------------------------------------------------------------
# 4. MTF confidence computation
# ---------------------------------------------------------------------------

class TestMTFConfidence:
    """Validate confidence computation in apply_decision_gate."""

    def _make_synthesis(self, confluence=0.8, momentum=0.5, direction=1.0):
        from core.strategy.mtf_engine import MTFSynthesis
        n = 10
        return MTFSynthesis(
            direction_score=pd.Series([direction] * n, dtype=float),
            confluence_score=pd.Series([confluence] * n, dtype=float),
            momentum_score=pd.Series([momentum] * n, dtype=float),
            strength_multiplier=1.0,
        )

    def test_confidence_computed_when_gating(self):
        """apply_decision_gate should compute confidence series."""
        from core.strategy.mtf_engine import apply_decision_gate
        from core.strategy.executor import SignalSet
        from core.strategy.dna import StrategyDNA, SignalGene, SignalRole, LogicGenes, RiskGenes, ExecutionGenes

        n = 10
        ss = SignalSet(
            entries=pd.Series([True] * n),
            exits=pd.Series([False] * n),
            adds=pd.Series([False] * n),
            reduces=pd.Series([False] * n),
            entry_direction=pd.Series([1.0] * n),
        )
        synthesis = self._make_synthesis()
        dna = StrategyDNA(
            signal_genes=[SignalGene("RSI", {"period": 14}, SignalRole.ENTRY_TRIGGER, None, {"type": "lt", "threshold": 30})],
            logic_genes=LogicGenes(entry_logic="AND", exit_logic="OR"),
            risk_genes=RiskGenes(direction="long"),
            execution_genes=ExecutionGenes(),
            mtf_mode="direction+confluence",
        )
        result = apply_decision_gate(ss, synthesis, dna)
        assert result.confidence is not None
        assert len(result.confidence) == n

    def test_confidence_range_01_to_1(self):
        """Confidence should be clipped to [0.1, 1.0]."""
        from core.strategy.mtf_engine import apply_decision_gate
        from core.strategy.executor import SignalSet
        from core.strategy.dna import StrategyDNA, SignalGene, SignalRole, LogicGenes, RiskGenes, ExecutionGenes

        n = 10
        ss = SignalSet(
            entries=pd.Series([True] * n),
            exits=pd.Series([False] * n),
            adds=pd.Series([False] * n),
            reduces=pd.Series([False] * n),
            entry_direction=pd.Series([1.0] * n),
        )
        # Very low scores → should clamp to 0.1
        synthesis = self._make_synthesis(confluence=0.0, momentum=0.0, direction=0.0)
        dna = StrategyDNA(
            signal_genes=[SignalGene("RSI", {"period": 14}, SignalRole.ENTRY_TRIGGER, None, {"type": "lt", "threshold": 30})],
            logic_genes=LogicGenes(entry_logic="AND", exit_logic="OR"),
            risk_genes=RiskGenes(direction="long"),
            execution_genes=ExecutionGenes(),
            mtf_mode="direction+confluence",
        )
        result = apply_decision_gate(ss, synthesis, dna)
        conf = result.confidence.dropna()
        assert (conf >= 0.1).all()
        assert (conf <= 1.0).all()

    def test_no_gating_still_computes_confidence(self):
        """mtf_mode=None should still compute confidence for diagnostics."""
        from core.strategy.mtf_engine import apply_decision_gate
        from core.strategy.executor import SignalSet
        from core.strategy.dna import StrategyDNA, SignalGene, SignalRole, LogicGenes, RiskGenes, ExecutionGenes

        n = 10
        ss = SignalSet(
            entries=pd.Series([True] * n),
            exits=pd.Series([False] * n),
            adds=pd.Series([False] * n),
            reduces=pd.Series([False] * n),
            entry_direction=pd.Series([1.0] * n),
        )
        synthesis = self._make_synthesis()
        dna = StrategyDNA(
            signal_genes=[SignalGene("RSI", {"period": 14}, SignalRole.ENTRY_TRIGGER, None, {"type": "lt", "threshold": 30})],
            logic_genes=LogicGenes(entry_logic="AND", exit_logic="OR"),
            risk_genes=RiskGenes(direction="long"),
            execution_genes=ExecutionGenes(),
            mtf_mode=None,
        )
        result = apply_decision_gate(ss, synthesis, dna)
        assert result.confidence is not None


# ---------------------------------------------------------------------------
# 5. Backward compatibility
# ---------------------------------------------------------------------------

class TestBackwardCompatibility:
    """Ensure existing behavior is preserved when confidence is disabled."""

    def test_hold_without_confidence(self):
        signals = BarSignals()
        state = _make_state()
        config = JudgmentConfig(confidence_sizing_enabled=True)
        decision = evaluate(signals, state, config)
        assert decision.action == "hold"

    def test_entry_without_confidence_field(self):
        """BarSignals without explicit confidence (default 1.0)."""
        signals = BarSignals(entry=True, direction=1.0)
        state = _make_state(has_position=False, allowed_direction="long")
        config = JudgmentConfig(confidence_sizing_enabled=True)
        decision = evaluate(signals, state, config)
        assert decision.action == "open"
        assert decision.entry_size_pct > 0

    def test_signal_set_without_confidence(self):
        """SignalSet without confidence field should produce default 1.0."""
        from core.strategy.executor import SignalSet

        n = 5
        ss = SignalSet(
            entries=pd.Series([True] + [False] * 4),
            exits=pd.Series([False] * n),
            adds=pd.Series([False] * n),
            reduces=pd.Series([False] * n),
            entry_direction=pd.Series([1.0] * n),
            # No confidence field
        )
        bar = BarSignals.from_signal_set(ss, 0)
        assert bar.confidence == 1.0
