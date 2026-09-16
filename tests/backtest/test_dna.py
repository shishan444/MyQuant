"""Tests for Strategy DNA data structure."""

import json
import pytest

pytestmark = [pytest.mark.unit]
from MyQuant.core.strategy.dna import (
    ConditionType,
    SignalRole,
    SignalGene,
    LogicGenes,
    ExecutionGenes,
    RiskGenes,
    StrategyDNA,
    TimeframeLayer,
)

class TestConditionType:
    """ConditionType enum tests."""

    def test_all_condition_types_defined(self):
        assert len(ConditionType) == 15

    def test_condition_values(self):
        assert ConditionType.LT.value == "lt"
        assert ConditionType.GT.value == "gt"
        assert ConditionType.LE.value == "le"
        assert ConditionType.GE.value == "ge"
        assert ConditionType.CROSS_ABOVE.value == "cross_above"
        assert ConditionType.CROSS_BELOW.value == "cross_below"
        assert ConditionType.PRICE_ABOVE.value == "price_above"
        assert ConditionType.PRICE_BELOW.value == "price_below"
        # Phase 2: dynamic conditions
        assert ConditionType.CROSS_ABOVE_SERIES.value == "cross_above_series"
        assert ConditionType.CROSS_BELOW_SERIES.value == "cross_below_series"
        assert ConditionType.LOOKBACK_ANY.value == "lookback_any"
        assert ConditionType.LOOKBACK_ALL.value == "lookback_all"
        # Phase 4: support/resistance conditions
        assert ConditionType.TOUCH_BOUNCE.value == "touch_bounce"
        assert ConditionType.ROLE_REVERSAL.value == "role_reversal"
        assert ConditionType.WICK_TOUCH.value == "wick_touch"

class TestSignalRole:
    """SignalRole enum tests."""

    def test_all_roles_defined(self):
        assert len(SignalRole) == 9

    def test_role_values(self):
        assert SignalRole.ENTRY_TRIGGER.value == "entry_trigger"
        assert SignalRole.ENTRY_GUARD.value == "entry_guard"
        assert SignalRole.EXIT_TRIGGER.value == "exit_trigger"
        assert SignalRole.EXIT_GUARD.value == "exit_guard"
        assert SignalRole.ADD_TRIGGER.value == "add_trigger"
        assert SignalRole.ADD_GUARD.value == "add_guard"
        assert SignalRole.REDUCE_TRIGGER.value == "reduce_trigger"
        assert SignalRole.REDUCE_GUARD.value == "reduce_guard"

class TestSignalGene:
    """SignalGene dataclass tests."""

    def test_create_basic_signal(self):
        gene = SignalGene(
            indicator="RSI",
            params={"period": 14},
            role=SignalRole.ENTRY_TRIGGER,
            field_name=None,
            condition={"type": "lt", "threshold": 30},
        )
        assert gene.indicator == "RSI"
        assert gene.params == {"period": 14}
        assert gene.role == SignalRole.ENTRY_TRIGGER
        assert gene.field_name is None
        assert gene.condition == {"type": "lt", "threshold": 30}

    def test_create_multi_output_signal(self):
        gene = SignalGene(
            indicator="MACD",
            params={"fast": 12, "slow": 26, "signal": 9},
            role=SignalRole.ENTRY_TRIGGER,
            field_name="histogram",
            condition={"type": "cross_above", "threshold": 0},
        )
        assert gene.field_name == "histogram"

    def test_to_dict(self):
        gene = SignalGene(
            indicator="EMA",
            params={"period": 100},
            role=SignalRole.ENTRY_GUARD,
            field_name=None,
            condition={"type": "price_above"},
        )
        d = gene.to_dict()
        assert d["indicator"] == "EMA"
        assert d["role"] == "entry_guard"
        assert d["field"] is None
        assert d["condition"] == {"type": "price_above"}

    def test_from_dict(self):
        d = {
            "indicator": "RSI",
            "params": {"period": 14},
            "role": "entry_trigger",
            "field": None,
            "condition": {"type": "lt", "threshold": 30},
        }
        gene = SignalGene.from_dict(d)
        assert gene.indicator == "RSI"
        assert gene.role == SignalRole.ENTRY_TRIGGER

class TestTimeframeLayer:
    """TimeframeLayer dataclass tests."""

    def test_create_layer(self):
        layer = TimeframeLayer(
            timeframe="4h",
            signal_genes=[
                SignalGene("RSI", {"period": 14}, SignalRole.ENTRY_TRIGGER, None,
                           {"type": "lt", "threshold": 30}),
            ],
            logic_genes=LogicGenes(entry_logic="AND", exit_logic="OR"),
        )
        assert layer.timeframe == "4h"
        assert len(layer.signal_genes) == 1

    def test_layer_serialization_roundtrip(self):
        layer = TimeframeLayer(
            timeframe="1d",
            signal_genes=[
                SignalGene("RSI", {"period": 14}, SignalRole.ENTRY_TRIGGER, None,
                           {"type": "lt", "threshold": 30}),
                SignalGene("EMA", {"period": 50}, SignalRole.EXIT_TRIGGER, None,
                           {"type": "price_below"}),
            ],
            logic_genes=LogicGenes(entry_logic="OR", exit_logic="AND"),
        )
        d = layer.to_dict()
        restored = TimeframeLayer.from_dict(d)
        assert restored.timeframe == "1d"
        assert len(restored.signal_genes) == 2
        assert restored.logic_genes.entry_logic == "OR"

class TestStrategyDNA:
    """StrategyDNA full structure tests."""

    @pytest.fixture
    def sample_dna(self):
        return StrategyDNA(
            strategy_id="test-001",
            generation=0,
            parent_ids=[],
            mutation_ops=[],
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

    def test_dna_creation(self, sample_dna):
        assert sample_dna.strategy_id == "test-001"
        assert sample_dna.generation == 0
        assert len(sample_dna.signal_genes) == 3

    def test_to_dict_roundtrip(self, sample_dna):
        d = sample_dna.to_dict()
        restored = StrategyDNA.from_dict(d)
        assert restored.strategy_id == sample_dna.strategy_id
        assert len(restored.signal_genes) == len(sample_dna.signal_genes)
        assert restored.logic_genes.entry_logic == "AND"
        assert restored.risk_genes.stop_loss == 0.05

    def test_to_json_roundtrip(self, sample_dna):
        json_str = sample_dna.to_json()
        restored = StrategyDNA.from_json(json_str)
        assert restored.strategy_id == sample_dna.strategy_id
        assert restored.generation == sample_dna.generation

    def test_json_is_valid_structure(self, sample_dna):
        json_str = sample_dna.to_json()
        parsed = json.loads(json_str)
        assert "strategy_id" in parsed
        assert "signal_genes" in parsed
        assert "logic_genes" in parsed
        assert "execution_genes" in parsed
        assert "risk_genes" in parsed
        assert "cross_layer_logic" in parsed

    def test_default_values(self):
        dna = StrategyDNA(
            signal_genes=[],
            logic_genes=LogicGenes(entry_logic="AND", exit_logic="OR"),
            execution_genes=ExecutionGenes(timeframe="4h", symbol="BTCUSDT"),
            risk_genes=RiskGenes(stop_loss=0.05, take_profit=None, position_size=0.3),
        )
        assert dna.generation == 0
        assert dna.parent_ids == []
        assert dna.mutation_ops == []
        assert dna.strategy_id
        assert dna.risk_genes.leverage == 1
        assert dna.risk_genes.direction == "long"

    def test_auto_wrap_creates_single_layer(self, sample_dna):
        """Legacy DNA deserialized via from_dict should auto-create a single layer."""
        # Auto-wrap happens in from_dict, not in __init__
        data = sample_dna.to_dict()
        dna = StrategyDNA.from_dict(data)
        assert dna.layers is not None
        assert len(dna.layers) == 1
        assert dna.layers[0].timeframe == "4h"
        assert len(dna.layers[0].signal_genes) == 3

    def test_is_mtf_single_layer(self, sample_dna):
        """Single layer DNA should not be considered MTF."""
        assert not sample_dna.is_mtf

    def test_is_mtf_multi_layer(self):
        """DNA with multiple layers should be considered MTF."""
        dna = StrategyDNA(
            signal_genes=[
                SignalGene("RSI", {"period": 14}, SignalRole.ENTRY_TRIGGER, None,
                           {"type": "lt", "threshold": 30}),
            ],
            logic_genes=LogicGenes(entry_logic="AND", exit_logic="OR"),
            execution_genes=ExecutionGenes(timeframe="4h", symbol="BTCUSDT"),
            risk_genes=RiskGenes(stop_loss=0.05, position_size=0.3),
            layers=[
                TimeframeLayer(timeframe="4h", signal_genes=[
                    SignalGene("RSI", {"period": 14}, SignalRole.ENTRY_TRIGGER, None,
                               {"type": "lt", "threshold": 30}),
                ]),
                TimeframeLayer(timeframe="1d", signal_genes=[
                    SignalGene("EMA", {"period": 50}, SignalRole.ENTRY_TRIGGER, None,
                               {"type": "price_above"}),
                ]),
            ],
        )
        assert dna.is_mtf
        assert dna.timeframes == ["4h", "1d"]

    def test_mtf_serialization_roundtrip(self):
        """MTF DNA should survive dict roundtrip."""
        dna = StrategyDNA(
            signal_genes=[
                SignalGene("RSI", {"period": 14}, SignalRole.ENTRY_TRIGGER, None,
                           {"type": "lt", "threshold": 30}),
                SignalGene("RSI", {"period": 14}, SignalRole.EXIT_TRIGGER, None,
                           {"type": "gt", "threshold": 70}),
            ],
            logic_genes=LogicGenes(entry_logic="AND", exit_logic="OR"),
            execution_genes=ExecutionGenes(timeframe="4h", symbol="BTCUSDT"),
            risk_genes=RiskGenes(stop_loss=0.05, position_size=0.3),
            layers=[
                TimeframeLayer(timeframe="4h", signal_genes=[
                    SignalGene("RSI", {"period": 14}, SignalRole.ENTRY_TRIGGER, None,
                               {"type": "lt", "threshold": 30}),
                ]),
                TimeframeLayer(timeframe="1d", signal_genes=[
                    SignalGene("EMA", {"period": 50}, SignalRole.ENTRY_TRIGGER, None,
                               {"type": "price_above"}),
                ]),
            ],
            cross_layer_logic="OR",
        )
        d = dna.to_dict()
        assert "layers" in d
        assert len(d["layers"]) == 2
        assert d["cross_layer_logic"] == "OR"

        restored = StrategyDNA.from_dict(d)
        assert restored.is_mtf
        assert len(restored.layers) == 2
        assert restored.layers[0].timeframe == "4h"
        assert restored.layers[1].timeframe == "1d"
        assert restored.cross_layer_logic == "OR"

    def test_backward_compat_from_old_dict(self):
        """Old format (no layers key) should auto-wrap correctly."""
        old_data = {
            "strategy_id": "old-001",
            "signal_genes": [
                {"indicator": "RSI", "params": {"period": 14}, "role": "entry_trigger",
                 "field": None, "condition": {"type": "lt", "threshold": 30}},
            ],
            "logic_genes": {"entry_logic": "AND", "exit_logic": "OR"},
            "execution_genes": {"timeframe": "4h", "symbol": "BTCUSDT"},
            "risk_genes": {"stop_loss": 0.05, "take_profit": None, "position_size": 0.3},
        }
        dna = StrategyDNA.from_dict(old_data)
        assert not dna.is_mtf
        assert dna.layers is not None
        assert len(dna.layers) == 1
        assert dna.layers[0].timeframe == "4h"


class TestGeneSignature:
    """StrategyDNA.gene_signature property (migrated from diversity._gene_signature).

    The signature is an intrinsic genotype fingerprint used by diversity
    measurement, strategy-library dedup, and the evolution exclude-set.
    """

    @staticmethod
    def _gene(indicator="RSI", period=14, role=SignalRole.ENTRY_TRIGGER, cond=None):
        if cond is None:
            cond = {"type": "lt", "threshold": 30}
        return SignalGene(indicator, {"period": period}, role, None, cond)

    def _make_dna(self, signal_genes=None, leverage=2, direction="long", layers=None):
        return StrategyDNA(
            signal_genes=signal_genes or [self._gene()],
            risk_genes=RiskGenes(leverage=leverage, direction=direction),
            layers=layers,
        )

    def test_property_equals_legacy_function(self):
        """gene_signature property must equal the legacy diversity._gene_signature."""
        from MyQuant.core.evolution.diversity import _gene_signature
        dna = self._make_dna()
        assert dna.gene_signature == _gene_signature(dna)

    def test_property_equals_legacy_function_with_layers(self):
        """Equivalence holds for MTF (multi-layer) DNA too."""
        from MyQuant.core.evolution.diversity import _gene_signature
        layer = TimeframeLayer(
            timeframe="4h",
            signal_genes=[self._gene("EMA", 50)],
            logic_genes=LogicGenes(entry_logic="AND", exit_logic="OR"),
        )
        dna = self._make_dna(layers=[layer])
        assert dna.gene_signature == _gene_signature(dna)

    def test_different_params_different_signature(self):
        a = self._make_dna(signal_genes=[self._gene("RSI", 14)])
        b = self._make_dna(signal_genes=[self._gene("RSI", 21)])
        assert a.gene_signature != b.gene_signature

    def test_different_leverage_different_signature(self):
        a = self._make_dna(leverage=2)
        b = self._make_dna(leverage=5)
        assert a.gene_signature != b.gene_signature

    def test_different_direction_different_signature(self):
        a = self._make_dna(direction="long")
        b = self._make_dna(direction="short")
        assert a.gene_signature != b.gene_signature

    def test_stable_across_calls(self):
        """Same DNA must produce identical signature on repeated calls."""
        dna = self._make_dna()
        assert dna.gene_signature == dna.gene_signature
