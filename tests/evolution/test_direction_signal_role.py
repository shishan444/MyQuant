"""Tests for SignalRole.DIRECTION data model."""
import json

import pytest

pytestmark = [pytest.mark.unit]

from core.strategy.dna import SignalRole, SignalGene, StrategyDNA, RiskGenes, LogicGenes


class TestDirectionRole:
    """Test DIRECTION role enum value."""

    def test_direction_role_exists(self):
        assert SignalRole.DIRECTION.value == "direction"

    def test_direction_role_is_member(self):
        assert SignalRole("direction") is SignalRole.DIRECTION


class TestDirectionGeneCreation:
    """Test SignalGene with DIRECTION role."""

    def test_direction_gene_creation(self):
        gene = SignalGene(
            indicator="EMA",
            params={"period": 50},
            role=SignalRole.DIRECTION,
            condition={"type": "price_above"},
        )
        assert gene.role == SignalRole.DIRECTION
        assert gene.indicator == "EMA"
        assert gene.condition == {"type": "price_above"}

    def test_direction_gene_with_field_name(self):
        gene = SignalGene(
            indicator="MACD",
            params={"fast": 12, "slow": 26, "signal": 9},
            role=SignalRole.DIRECTION,
            field_name="histogram",
            condition={"type": "gt", "threshold": 0},
        )
        assert gene.field_name == "histogram"
        assert gene.role == SignalRole.DIRECTION


class TestDirectionGeneSerialization:
    """Test DIRECTION gene to_dict / from_dict round-trip."""

    def test_direction_gene_to_dict(self):
        gene = SignalGene(
            indicator="SMA",
            params={"period": 20},
            role=SignalRole.DIRECTION,
            condition={"type": "price_below"},
        )
        d = gene.to_dict()
        assert d["role"] == "direction"
        assert d["indicator"] == "SMA"
        assert d["condition"] == {"type": "price_below"}

    def test_direction_gene_from_dict(self):
        data = {
            "indicator": "EMA",
            "params": {"period": 50},
            "role": "direction",
            "field": None,
            "condition": {"type": "price_above"},
        }
        gene = SignalGene.from_dict(data)
        assert gene.role == SignalRole.DIRECTION
        assert gene.indicator == "EMA"

    def test_direction_gene_round_trip(self):
        gene = SignalGene(
            indicator="RSI",
            params={"period": 14},
            role=SignalRole.DIRECTION,
            condition={"type": "gt", "threshold": 50},
        )
        d = gene.to_dict()
        restored = SignalGene.from_dict(d)
        assert restored.role == gene.role
        assert restored.indicator == gene.indicator
        assert restored.params == gene.params
        assert restored.condition == gene.condition


class TestDirectionGeneInDNA:
    """Test DIRECTION gene within StrategyDNA serialization."""

    def test_dna_with_direction_gene_serialization(self):
        dna = StrategyDNA(
            signal_genes=[
                SignalGene("RSI", {"period": 14}, SignalRole.ENTRY_TRIGGER,
                           None, {"type": "lt", "threshold": 30}),
                SignalGene("EMA", {"period": 50}, SignalRole.DIRECTION,
                           None, {"type": "price_above"}),
            ],
            logic_genes=LogicGenes(),
            risk_genes=RiskGenes(direction="mixed"),
        )
        d = dna.to_dict()

        # Verify DIRECTION gene is in serialized output
        direction_genes = [g for g in d["signal_genes"] if g["role"] == "direction"]
        assert len(direction_genes) == 1
        assert direction_genes[0]["indicator"] == "EMA"

    def test_dna_with_direction_gene_deserialization(self):
        data = {
            "strategy_id": "test-123",
            "signal_genes": [
                {"indicator": "RSI", "params": {"period": 14},
                 "role": "entry_trigger", "field": None,
                 "condition": {"type": "lt", "threshold": 30}},
                {"indicator": "EMA", "params": {"period": 50},
                 "role": "direction", "field": None,
                 "condition": {"type": "price_above"}},
            ],
            "logic_genes": {"entry_logic": "AND", "exit_logic": "OR",
                            "add_logic": "AND", "reduce_logic": "AND"},
            "execution_genes": {"timeframe": "4h", "symbol": "BTCUSDT"},
            "risk_genes": {"stop_loss": 0.05, "take_profit": None,
                           "position_size": 0.3, "leverage": 1,
                           "direction": "mixed"},
        }
        dna = StrategyDNA.from_dict(data)

        direction_genes = [g for g in dna.signal_genes if g.role == SignalRole.DIRECTION]
        assert len(direction_genes) == 1
        assert direction_genes[0].indicator == "EMA"
        assert dna.risk_genes.direction == "mixed"

    def test_dna_json_round_trip_with_direction(self):
        dna = StrategyDNA(
            signal_genes=[
                SignalGene("EMA", {"period": 100}, SignalRole.DIRECTION,
                           None, {"type": "price_below"}),
            ],
            risk_genes=RiskGenes(direction="mixed"),
        )
        json_str = dna.to_json()
        restored = StrategyDNA.from_json(json_str)

        direction_genes = [g for g in restored.signal_genes if g.role == SignalRole.DIRECTION]
        assert len(direction_genes) == 1
        assert restored.risk_genes.direction == "mixed"


class TestAPISchemaAcceptsDirection:
    """Test that API-facing models accept direction role."""

    def test_api_signal_role_string(self):
        # API uses string for SignalRole, so "direction" should be valid
        api_role = "direction"
        core_role = SignalRole(api_role)
        assert core_role == SignalRole.DIRECTION
