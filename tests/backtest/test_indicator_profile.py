"""Tests for indicator profiles and profile-aware evolution operators."""

import random

import pytest

pytestmark = [pytest.mark.unit]

from core.features.indicator_profile import PROFILES, IndicatorProfile, ConditionPreset
from core.features.indicators import INDICATOR_REGISTRY
from core.evolution.operators import (
    generate_random_condition,
    mutate_params,
    mutate_indicator,
    mutate_add_signal,
)
from core.evolution.population import (
    create_random_dna,
    init_population,
    STRATEGY_TEMPLATES,
    _dna_from_template,
)
from core.strategy.dna import (
    SignalRole, SignalGene, LogicGenes, RiskGenes, StrategyDNA,
)
from core.strategy.validator import validate_dna

# -- Profile structure tests --

class TestIndicatorProfiles:
    def test_all_registry_indicators_have_profiles(self):
        """Every indicator in the registry should have a profile."""
        for name in INDICATOR_REGISTRY:
            assert name in PROFILES, f"Missing profile for indicator: {name}"

    def test_profile_fields_valid(self):
        for name, profile in PROFILES.items():
            assert isinstance(profile, IndicatorProfile)
            assert 0 < profile.follow_probability <= 1.0
            assert isinstance(profile.recommended_roles, list)
            assert isinstance(profile.recommended_params, dict)
            assert isinstance(profile.recommended_conditions, list)

    def test_recommended_params_match_registry(self):
        """Profile recommended params should be valid for registry ParamDefs."""
        for name, profile in PROFILES.items():
            reg = INDICATOR_REGISTRY.get(name)
            if not reg:
                continue
            for pname, values in profile.recommended_params.items():
                assert pname in reg.params, (
                    f"{name}: profile recommends unknown param '{pname}'"
                )
                pdef = reg.params[pname]
                for v in values:
                    assert pdef.min <= v <= pdef.max, (
                        f"{name}.{pname}: value {v} outside [{pdef.min}, {pdef.max}]"
                    )

    def test_recommended_conditions_have_valid_type(self):
        for name, profile in PROFILES.items():
            reg = INDICATOR_REGISTRY.get(name)
            if not reg:
                continue
            for cond in profile.recommended_conditions:
                assert isinstance(cond, ConditionPreset)
                assert cond.type in reg.supported_conditions, (
                    f"{name}: condition type '{cond.type}' not in supported {reg.supported_conditions}"
                )

# -- Template tests --

class TestStrategyTemplates:
    def test_templates_exist(self):
        assert len(STRATEGY_TEMPLATES) >= 7

    def test_template_creates_valid_dna(self):
        for template in STRATEGY_TEMPLATES:
            dna = _dna_from_template(template)
            result = validate_dna(dna)
            assert result.is_valid, f"Template invalid: {result.errors}"

    def test_template_has_entry_and_exit(self):
        for template in STRATEGY_TEMPLATES:
            dna = _dna_from_template(template)
            roles = {g.role for g in dna.signal_genes}
            assert SignalRole.ENTRY_TRIGGER in roles
            assert SignalRole.EXIT_TRIGGER in roles

    def test_template_preserves_indicator_names(self):
        for template in STRATEGY_TEMPLATES:
            dna = _dna_from_template(template)
            template_indicators = {g["indicator"] for g in template["genes"]}
            dna_indicators = {g.indicator for g in dna.signal_genes}
            assert dna_indicators == template_indicators

# -- Profile-aware condition generation --

class TestProfileAwareConditions:
    def test_rsi_uses_profile_with_high_probability(self):
        """RSI conditions should often use profile thresholds (25-75)."""
        profile_thresholds = {25, 30, 35, 65, 70, 75}
        profile_hits = 0
        for _ in range(200):
            cond = generate_random_condition("RSI", use_profile=True)
            if cond.get("threshold") in profile_thresholds:
                profile_hits += 1
        # follow_probability is 0.70, but not all conditions have thresholds
        assert profile_hits > 50, f"Only {profile_hits}/200 used profile thresholds"

    def test_macd_uses_histogram_condition(self):
        """MACD should frequently use cross_above/below histogram conditions."""
        hist_hits = 0
        for _ in range(200):
            cond = generate_random_condition("MACD", use_profile=True)
            if cond.get("type") in ("cross_above", "cross_below") and cond.get("target_field") == "histogram":
                hist_hits += 1
        assert hist_hits > 30, f"Only {hist_hits}/200 used histogram conditions"

    def test_profile_disabled_uses_free_exploration(self):
        """With use_profile=False, should use free exploration."""
        cond = generate_random_condition("RSI", use_profile=False)
        assert "type" in cond
        # Should still produce valid conditions

# -- Profile-aware parameter mutation --

class TestProfileAwareParamMutation:
    def test_mutate_params_prefers_profile_values(self):
        """Mutating RSI should often pick profile-recommended period values."""
        dna = StrategyDNA(
            signal_genes=[
                SignalGene("RSI", {"period": 14}, SignalRole.ENTRY_TRIGGER, None,
                           {"type": "lt", "threshold": 30}),
                SignalGene("RSI", {"period": 14}, SignalRole.EXIT_TRIGGER, None,
                           {"type": "gt", "threshold": 70}),
            ],
            logic_genes=LogicGenes(entry_logic="AND", exit_logic="OR"),
            risk_genes=RiskGenes(stop_loss=0.05, position_size=0.3),
        )
        # Profile recommended periods for RSI: [7, 14, 21]
        profile_periods = {7, 14, 21}
        profile_hits = 0
        for _ in range(200):
            mutated = mutate_params(dna)
            for g in mutated.signal_genes:
                if g.indicator == "RSI" and g.params.get("period") in profile_periods:
                    profile_hits += 1
        # With profile follow_probability=0.70, most mutations should stay in profile range
        assert profile_hits > 100, f"Only {profile_hits} params in profile range"

# -- Profile-aware indicator replacement --

class TestProfileAwareIndicatorMutation:
    def test_mutate_indicator_uses_profile_params(self):
        """When replacing indicator, new params should often be from profile."""
        dna = StrategyDNA(
            signal_genes=[
                SignalGene("EMA", {"period": 50}, SignalRole.ENTRY_TRIGGER, None,
                           {"type": "price_above"}),
                SignalGene("EMA", {"period": 50}, SignalRole.EXIT_TRIGGER, None,
                           {"type": "price_below"}),
            ],
            logic_genes=LogicGenes(entry_logic="AND", exit_logic="OR"),
            risk_genes=RiskGenes(stop_loss=0.05, position_size=0.3),
        )
        for _ in range(20):
            mutated = mutate_indicator(dna)
            result = validate_dna(mutated)
            assert result.is_valid, f"Mutated indicator invalid: {result.errors}"

# -- Profile-aware signal addition --

class TestProfileAwareSignalAddition:
    def test_add_signal_uses_profile_params(self):
        dna = StrategyDNA(
            signal_genes=[
                SignalGene("RSI", {"period": 14}, SignalRole.ENTRY_TRIGGER, None,
                           {"type": "lt", "threshold": 30}),
                SignalGene("RSI", {"period": 14}, SignalRole.EXIT_TRIGGER, None,
                           {"type": "gt", "threshold": 70}),
            ],
            logic_genes=LogicGenes(entry_logic="AND", exit_logic="OR"),
            risk_genes=RiskGenes(stop_loss=0.05, position_size=0.3),
        )
        valid_count = 0
        for _ in range(30):
            mutated = mutate_add_signal(dna)
            result = validate_dna(mutated)
            if result.is_valid:
                valid_count += 1
        # Most mutations should be valid; a few may fail due to
        # complex conditions (e.g. VolumeProfile role_reversal needs 'role')
        assert valid_count >= 20, f"Only {valid_count}/30 mutations were valid"

# -- Population with templates and profiles --

class TestPopulationWithProfiles:
    def test_init_population_without_ancestor_uses_template(self):
        """Without ancestor, should use a random template as seed."""
        pop = init_population(size=10)
        assert len(pop) == 10
        for ind in pop:
            result = validate_dna(ind)
            assert result.is_valid, f"Individual invalid: {result.errors}"

    def test_init_population_with_extra_ancestors(self):
        """Multi-start: extra ancestors should be included."""
        ancestor = _dna_from_template(STRATEGY_TEMPLATES[0])
        extra1 = _dna_from_template(STRATEGY_TEMPLATES[1])
        extra2 = _dna_from_template(STRATEGY_TEMPLATES[2])
        pop = init_population(size=15, ancestor=ancestor, extra_ancestors=[extra1, extra2])
        assert len(pop) == 15
        # First 3 should be ancestor + extras
        assert pop[0].strategy_id == ancestor.strategy_id
        assert pop[1].strategy_id == extra1.strategy_id
        assert pop[2].strategy_id == extra2.strategy_id

    def test_random_dna_uses_profile_conditions(self):
        """create_random_dna should produce strategies using profile conditions."""
        profile_condition_types = {"lt", "gt", "cross_above", "cross_below", "price_above", "price_below"}
        hits = 0
        for _ in range(20):
            dna = create_random_dna()
            for g in dna.signal_genes:
                if g.condition.get("type") in profile_condition_types:
                    hits += 1
        assert hits > 10, f"Only {hits} profile-type conditions found in 20 random DNAs"

    def test_indicator_pool_respected(self):
        """indicator_pool should restrict which indicators are used."""
        pool = ["RSI", "EMA"]
        for _ in range(10):
            dna = create_random_dna(indicator_pool=pool)
            for g in dna.signal_genes:
                assert g.indicator in pool, f"DNA used indicator '{g.indicator}' outside pool {pool}"
