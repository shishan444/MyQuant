"""Tests for indicator profiles and profile-aware evolution operators."""

import random

import pytest

pytestmark = [pytest.mark.unit]

from core.features.indicator_profile import PROFILES, IndicatorProfile, ConditionPreset
from core.features.indicators import INDICATOR_REGISTRY


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
