"""Tests verifying evolution infrastructure effectiveness.

Validates that:
- All strategy templates produce valid signals (no dead genes)
- Profile recommended parameters align with _DEFAULT_PARAMS precomputed set
- Evolution selectable indicators all have precomputed columns
"""

import pytest

from core.features.indicators import _DEFAULT_PARAMS, compute_all_indicators
from core.features.indicator_profile import PROFILES
from core.features.registry import INDICATOR_REGISTRY, resolve_indicator_column
from core.evolution.population import (
    STRATEGY_TEMPLATES, _dna_from_template, create_random_dna,
)
from core.strategy.dna import SignalRole
from core.strategy.validator import validate_dna

pytestmark = [pytest.mark.unit]


# ===========================================================================
# S1: Strategy template effectiveness
# ===========================================================================

class TestTemplateEffectiveness:
    """Every template gene must resolve to a real precomputed column."""

    @pytest.fixture(params=STRATEGY_TEMPLATES)
    def template(self, request):
        return request.param

    def test_template_creates_valid_dna(self, template):
        dna = _dna_from_template(template)
        result = validate_dna(dna)
        assert result.is_valid, f"Template '{template['name']}' invalid: {result.errors}"

    def test_template_has_entry_and_exit(self, template):
        dna = _dna_from_template(template)
        roles = {g.role for g in dna.signal_genes}
        assert SignalRole.ENTRY_TRIGGER in roles, f"Template '{template['name']}' missing ENTRY_TRIGGER"
        assert SignalRole.EXIT_TRIGGER in roles, f"Template '{template['name']}' missing EXIT_TRIGGER"

    def test_template_genes_resolve_to_columns(self, template):
        """Every gene in every template must resolve to a column name that
        would exist in a precomputed DataFrame."""
        for gene_dict in template["genes"]:
            indicator = gene_dict["indicator"]
            params = gene_dict.get("params", {})
            field = gene_dict.get("field")

            reg = INDICATOR_REGISTRY.get(indicator)
            assert reg is not None, f"Template '{template['name']}' uses unknown indicator '{indicator}'"

            col = resolve_indicator_column(indicator, params, field or "", reg.naming)
            assert col, f"Template '{template['name']}' gene {indicator} resolved to empty column name"

            # Verify the params are in _DEFAULT_PARAMS
            default_set = _DEFAULT_PARAMS.get(indicator, [])
            assert any(
                all(p.get(k) == v for k, v in params.items()) for p in default_set
            ), (
                f"Template '{template['name']}' gene {indicator}({params}) "
                f"not in _DEFAULT_PARAMS: {[p for p in default_set]}"
            )

    def test_all_templates_have_unique_names(self):
        names = [t["name"] for t in STRATEGY_TEMPLATES]
        assert len(names) == len(set(names)), f"Duplicate template names: {names}"

    def test_no_template_uses_bb_percent_or_bandwidth(self):
        """BB percent/bandwidth are never computed and must not appear in templates."""
        for template in STRATEGY_TEMPLATES:
            for gene in template["genes"]:
                if gene["indicator"] == "BB":
                    field = gene.get("field")
                    assert field in (None, "upper", "middle", "lower"), (
                        f"Template '{template['name']}' uses BB field '{field}' "
                        f"which is never precomputed"
                    )

    def test_no_template_uses_cross_above_without_threshold(self):
        """cross_above/cross_below without threshold always return False."""
        for template in STRATEGY_TEMPLATES:
            for gene in template["genes"]:
                cond_type = gene.get("condition", {}).get("type")
                if cond_type in ("cross_above", "cross_below"):
                    threshold = gene["condition"].get("threshold")
                    assert threshold is not None, (
                        f"Template '{template['name']}' gene {gene['indicator']} "
                        f"uses {cond_type} without threshold (always returns False)"
                    )


# ===========================================================================
# S2: Profile parameter alignment
# ===========================================================================

class TestProfileParameterAlignment:
    """Profile recommended params must 100% exist in _DEFAULT_PARAMS."""

    def test_all_profile_params_in_default_params(self):
        # Indicators excluded from evolution have no precomputed columns
        _excluded = {"VWAP", "FractalEntropy", "MultifactorOsc", "VolumeProfile"}
        mismatches = []
        for name, profile in PROFILES.items():
            if name in _excluded:
                continue
            default_set = _DEFAULT_PARAMS.get(name, [])
            if not default_set and not profile.recommended_params:
                continue  # No-param indicator, OK
            for pname, values in profile.recommended_params.items():
                for v in values:
                    found = any(p.get(pname) == v for p in default_set)
                    if not found:
                        mismatches.append(f"{name}.{pname}={v}")
        assert not mismatches, (
            f"Profile params not in _DEFAULT_PARAMS ({len(mismatches)}):\n" +
            "\n".join(mismatches)
        )

    def test_profile_conditions_use_supported_types(self):
        for name, profile in PROFILES.items():
            reg = INDICATOR_REGISTRY.get(name)
            if not reg:
                continue
            for cond in profile.recommended_conditions:
                assert cond.type in reg.supported_conditions, (
                    f"{name}: condition '{cond.type}' not supported"
                )


# ===========================================================================
# S3: Invalid indicator filtering
# ===========================================================================

_NO_COMPUTE = {"VWAP", "FractalEntropy", "MultifactorOsc", "VolumeProfile"}


class TestInvalidIndicatorFiltering:
    """Indicators without compute implementations must not appear in evolution."""

    def test_no_compute_indicators_excluded_from_random_dna(self):
        """create_random_dna should never produce genes with no-compute indicators."""
        for _ in range(50):
            dna = create_random_dna()
            for gene in dna.signal_genes:
                assert gene.indicator not in _NO_COMPUTE, (
                    f"create_random_dna produced gene with '{gene.indicator}' "
                    f"which has no compute implementation"
                )

    def test_guard_only_not_used_as_trigger(self):
        """guard_only indicators must not appear as triggers."""
        guard_only_indicators = {
            name for name, reg in INDICATOR_REGISTRY.items()
            if reg.guard_only
        }
        for _ in range(50):
            dna = create_random_dna()
            for gene in dna.signal_genes:
                if gene.role in (SignalRole.ENTRY_TRIGGER, SignalRole.EXIT_TRIGGER,
                                 SignalRole.ADD_TRIGGER, SignalRole.REDUCE_TRIGGER):
                    assert gene.indicator not in guard_only_indicators, (
                        f"guard_only indicator '{gene.indicator}' used as trigger role {gene.role}"
                    )
