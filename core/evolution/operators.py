"""Evolution operators: 4 mutation types + crossover.

Mutation types:
1. Parameter mutation: adjust indicator parameters within valid ranges
2. Indicator replacement: swap with same-category indicator
3. Logic mutation: flip AND/OR, add/remove guard conditions
4. Risk mutation: adjust stop_loss, take_profit, position_size
"""
from __future__ import annotations

import copy
import itertools
import random
import uuid
from typing import Optional

from core.strategy.dna import (
    SignalRole, SignalGene, StrategyDNA, derive_role,
)
from core.strategy.validator import validate_dna
from core.features.indicators import INDICATOR_REGISTRY, get_interchangeable
from core.features.registry import IndicatorDef
from core.features.indicator_profile import PROFILES


def _new_id() -> str:
    return str(uuid.uuid4())


def _pick_signal_pool(data: dict) -> dict:
    """Pick a signal gene pool to mutate: base signal_genes or a random MTF layer.

    Returns a dict with a mutable reference to the gene list:
      {"genes": <list>}
    For base: data["signal_genes"]
    For layer: data["layers"][i]["signal_genes"]
    """
    layers = data.get("layers")
    if not layers:
        return {"genes": data["signal_genes"]}

    # 50% chance base, 50% split among layers
    n_targets = 1 + len(layers)  # base + each layer
    choice = random.randint(0, n_targets - 1)
    if choice == 0:
        return {"genes": data["signal_genes"]}
    else:
        layer_idx = choice - 1
        return {"genes": layers[layer_idx]["signal_genes"]}


def generate_random_condition(
    indicator_name: str,
    reg: IndicatorDef | None = None,
    df_columns: list | None = None,
    use_profile: bool = True,
) -> dict:
    """Generate a random condition dict for a given indicator.

    When use_profile is True and a profile exists, follows recommended
    conditions with the profile's follow_probability. Otherwise falls back
    to free exploration from supported_conditions.

    Args:
        indicator_name: Name of the indicator.
        reg: IndicatorDef from registry (fetched if None).
        df_columns: Optional list of available DataFrame columns (unused for now).
        use_profile: Whether to use indicator profile recommendations.
    """
    if reg is None:
        reg = INDICATOR_REGISTRY.get(indicator_name)
    if reg is None or not reg.supported_conditions:
        return {"type": "gt", "threshold": 0.0}

    # Pick condition type: profile-recommended (if use_profile) or free exploration.
    cond_type = None
    cond_preset = None
    if use_profile:
        profile = PROFILES.get(indicator_name)
        if profile and profile.recommended_conditions and random.random() < profile.follow_probability:
            cond_preset = random.choice(profile.recommended_conditions)
            cond_type = cond_preset.type
    if cond_type is None:
        cond_type = random.choice(reg.supported_conditions)

    condition: dict = {"type": cond_type}

    # Populate type-specific fields. Both the profile path and the free-exploration
    # path go through the SAME field population — previously the profile path
    # returned early with only type/threshold/target_field, omitting required
    # fields for complex types (lookback_any needs window/inner;
    # cross_above_series needs target_indicator), which produced invalid DNA.
    if cond_preset and cond_preset.thresholds and cond_type in ("eq", "lt", "gt", "le", "ge"):
        condition["threshold"] = random.choice(cond_preset.thresholds)
    elif cond_type == "eq":
        # Pattern indicators: check if value equals 1 (pattern present)
        condition["threshold"] = 1
    elif cond_type in ("lt", "gt", "le", "ge"):
        if indicator_name == "RSI":
            condition["threshold"] = random.choice([25, 30, 35, 40, 60, 65, 70, 75])
        else:
            condition["threshold"] = round(random.uniform(-1, 1), 2)
    elif cond_type in ("cross_above_series", "cross_below_series"):
        # Pick a target indicator from same category or trend category
        target = _pick_series_target(indicator_name, reg)
        condition["target_indicator"] = target
        target_reg = INDICATOR_REGISTRY.get(target)
        if target_reg and target_reg.params:
            condition["target_params"] = {
                k: int(v.default) if v.type == "int" else float(v.default)
                for k, v in target_reg.params.items()
            }
        else:
            condition["target_params"] = {}
    elif cond_type in ("lookback_any", "lookback_all"):
        window = random.choice([3, 5, 8, 10])
        # Generate a simple inner condition
        inner_types = ["price_above", "price_below", "gt", "lt"]
        inner_type = random.choice(inner_types)
        inner: dict = {"type": inner_type}
        if inner_type in ("lt", "gt"):
            inner["threshold"] = round(random.uniform(-1, 1), 2)
            if inner_type == "gt":
                inner["threshold"] = random.choice([0.0, 0.5, 1.0])
            else:
                inner["threshold"] = random.choice([0.0, -0.5, -1.0])
        condition["window"] = window
        condition["inner"] = inner
    elif cond_type == "touch_bounce":
        condition["direction"] = random.choice(["support", "resistance"])
        condition["proximity_pct"] = random.choice([0.005, 0.01, 0.02])
        condition["bounce_pct"] = random.choice([0.003, 0.005, 0.01])
    elif cond_type == "role_reversal":
        condition["role"] = random.choice(["support", "resistance"])
        condition["lookback"] = random.choice([5, 10, 15, 20])
    elif cond_type == "wick_touch":
        condition["direction"] = random.choice(["above", "below"])
        condition["proximity_pct"] = random.choice([0.005, 0.01, 0.02])

    # Profile preset's target_field overrides (e.g. BB upper/middle/lower).
    if cond_preset and cond_preset.target_field:
        condition["target_field"] = cond_preset.target_field

    return condition


def _pick_series_target(indicator_name: str, reg: IndicatorDef) -> str:
    """Pick a suitable target indicator for cross_above/below_series."""
    # Prefer same-category indicators
    same_cat = get_interchangeable(indicator_name)
    # Also add trend indicators as common targets
    trend_indicators = [name for name, d in INDICATOR_REGISTRY.items()
                        if d.category == "trend" and name != indicator_name]
    candidates = same_cat + trend_indicators
    # Remove guard_only
    candidates = [c for c in candidates if not INDICATOR_REGISTRY.get(c, IndicatorDef("", {}, [], [], guard_only=True)).guard_only]
    if not candidates:
        return "EMA"
    return random.choice(candidates)


# ---------------------------------------------------------------------------
# MTF (Multi-Timeframe) Mutation Operators
# ---------------------------------------------------------------------------

# Standard timeframe hierarchy for candidate selection
_STANDARD_TIMEFRAMES = ["15m", "30m", "1h", "4h", "1d", "3d"]


def mutate_add_layer(dna: StrategyDNA, candidate_timeframes: list | None = None) -> StrategyDNA:
    """Insert a new timeframe layer with random signals.

    Picks an adjacent timeframe not already present.
    """
    if candidate_timeframes is None:
        candidate_timeframes = _STANDARD_TIMEFRAMES

    # Hard limit: max 3 layers
    if dna.layers and len(dna.layers) >= 3:
        return dna

    existing = set(dna.timeframes)
    candidates = [tf for tf in candidate_timeframes if tf not in existing]
    if not candidates:
        return dna

    # Pick a timeframe adjacent to existing ones
    new_tf = random.choice(candidates)

    # Create random signals for new layer
    from core.evolution.population import create_random_dna
    seed_dna = create_random_dna(timeframe=new_tf, symbol=dna.execution_genes.symbol)

    new_layer_data = {
        "timeframe": new_tf,
        "signal_genes": [sg.to_dict() for sg in seed_dna.signal_genes],
        "logic_genes": seed_dna.logic_genes.to_dict(),
        "role": derive_role(new_tf),
    }

    data = dna.to_dict()
    data["strategy_id"] = _new_id()
    data["parent_ids"] = [dna.strategy_id]
    data["mutation_ops"] = list(dna.mutation_ops) + ["add_layer"]
    data["generation"] = dna.generation + 1

    if "layers" not in data or not data["layers"]:
        # Wrap existing signals into first layer
        data["layers"] = [{
            "timeframe": dna.execution_genes.timeframe,
            "signal_genes": data["signal_genes"],
            "logic_genes": data["logic_genes"],
        }]

    data["layers"].append(new_layer_data)
    return StrategyDNA.from_dict(data)


def mutate_remove_layer(dna: StrategyDNA) -> StrategyDNA:
    """Remove a non-execution timeframe layer (keep at least 1)."""
    if not dna.is_mtf:
        return dna

    data = dna.to_dict()
    layers = data.get("layers", [])
    if len(layers) <= 1:
        return dna

    exec_tf = data["execution_genes"]["timeframe"]
    removable = [i for i, l in enumerate(layers) if l["timeframe"] != exec_tf]
    if not removable:
        return dna

    idx = random.choice(removable)
    layers.pop(idx)

    data["strategy_id"] = _new_id()
    data["parent_ids"] = [dna.strategy_id]
    data["mutation_ops"] = list(dna.mutation_ops) + ["remove_layer"]
    data["generation"] = dna.generation + 1
    data["layers"] = layers
    return StrategyDNA.from_dict(data)


def mutate_layer_timeframe(dna: StrategyDNA, candidate_timeframes: list | None = None) -> StrategyDNA:
    """Change the timeframe of a random non-execution layer."""
    if candidate_timeframes is None:
        candidate_timeframes = _STANDARD_TIMEFRAMES

    if not dna.is_mtf:
        return dna

    data = dna.to_dict()
    layers = data.get("layers", [])
    exec_tf = data["execution_genes"]["timeframe"]
    changeable = [i for i, l in enumerate(layers) if l["timeframe"] != exec_tf]
    if not changeable:
        return dna

    idx = random.choice(changeable)
    current_tf = layers[idx]["timeframe"]
    # Exclude current TF, exec TF, and timeframes used by sibling layers
    sibling_tfs = {layers[j]["timeframe"] for j in range(len(layers)) if j != idx}
    alternatives = [tf for tf in candidate_timeframes
                    if tf != current_tf and tf != exec_tf and tf not in sibling_tfs]
    if not alternatives:
        return dna

    layers[idx]["timeframe"] = random.choice(alternatives)

    data["strategy_id"] = _new_id()
    data["parent_ids"] = [dna.strategy_id]
    data["mutation_ops"] = list(dna.mutation_ops) + ["layer_timeframe"]
    data["generation"] = dna.generation + 1
    data["layers"] = layers
    return StrategyDNA.from_dict(data)


def mutate_cross_logic(dna: StrategyDNA) -> StrategyDNA:
    """Flip the cross-layer logic (AND <-> OR)."""
    if not dna.is_mtf:
        return dna

    data = dna.to_dict()
    current = data.get("cross_layer_logic", "AND")
    data["cross_layer_logic"] = "OR" if current == "AND" else "AND"
    data["strategy_id"] = _new_id()
    data["parent_ids"] = [dna.strategy_id]
    data["mutation_ops"] = list(dna.mutation_ops) + ["cross_logic"]
    data["generation"] = dna.generation + 1
    return StrategyDNA.from_dict(data)


def mutate_mtf_mode(dna: StrategyDNA) -> StrategyDNA:
    """Cycle through MTF mode options."""
    if not dna.is_mtf:
        return dna

    modes = [None, "direction", "confluence", "direction+confluence"]
    current_idx = modes.index(dna.mtf_mode) if dna.mtf_mode in modes else 0
    new_idx = (current_idx + random.randint(1, len(modes) - 1)) % len(modes)

    data = dna.to_dict()
    data["mtf_mode"] = modes[new_idx]
    data["strategy_id"] = _new_id()
    data["parent_ids"] = [dna.strategy_id]
    data["mutation_ops"] = list(dna.mutation_ops) + ["mtf_mode"]
    data["generation"] = dna.generation + 1
    return StrategyDNA.from_dict(data)


def mutate_confluence_threshold(dna: StrategyDNA) -> StrategyDNA:
    """Mutate confluence_threshold within [0.1, 0.9] using polynomial mutation."""
    data = dna.to_dict()
    current = data.get("confluence_threshold", 0.3)
    new_val = _polynomial_mutation(current, 0.1, 0.9, eta=20.0)
    data["confluence_threshold"] = round(new_val, 3)
    data["strategy_id"] = _new_id()
    data["parent_ids"] = [dna.strategy_id]
    data["mutation_ops"] = list(dna.mutation_ops) + ["confluence_threshold"]
    data["generation"] = dna.generation + 1
    return StrategyDNA.from_dict(data)


def mutate_proximity_mult(dna: StrategyDNA) -> StrategyDNA:
    """Mutate proximity_mult within [0.5, 3.0] using polynomial mutation."""
    data = dna.to_dict()
    current = data.get("proximity_mult", 1.5)
    new_val = _polynomial_mutation(current, 0.5, 3.0, eta=20.0)
    data["proximity_mult"] = round(new_val, 3)
    data["strategy_id"] = _new_id()
    data["parent_ids"] = [dna.strategy_id]
    data["mutation_ops"] = list(dna.mutation_ops) + ["proximity_mult"]
    data["generation"] = dna.generation + 1
    return StrategyDNA.from_dict(data)


def _polynomial_mutation(
    value: float, lower: float, upper: float, eta: float = 20.0,
) -> float:
    """Polynomial bounded mutation (Deb & Goyal, 1996).

    Produces small perturbations near current value with high probability,
    and large jumps with low probability. eta controls the distribution shape:
    - High eta (e.g. 20): small mutations (exploitation)
    - Low eta (e.g. 5): large mutations (exploration)

    Args:
        value: Current parameter value.
        lower: Lower bound.
        upper: Upper bound.
        eta: Distribution index (higher = more concentrated).

    Returns:
        Mutated value within [lower, upper].
    """
    delta = random.random()
    if delta < 0.5:
        xy = 1.0 - (value - lower) / max(upper - lower, 1e-10)
        val = 2.0 * delta + (1.0 - 2.0 * delta) * (xy ** (eta + 1.0))
        deltaq = val ** (1.0 / (eta + 1.0)) - 1.0
    else:
        xy = 1.0 - (upper - value) / max(upper - lower, 1e-10)
        val = 2.0 * (1.0 - delta) + 2.0 * (delta - 0.5) * (xy ** (eta + 1.0))
        deltaq = 1.0 - val ** (1.0 / (eta + 1.0))
    return max(lower, min(upper, value + deltaq * (upper - lower)))


def mutate_params(dna: StrategyDNA) -> StrategyDNA:
    """Mutate a random parameter in a random signal gene (base or MTF layer)."""
    data = dna.to_dict()
    data["strategy_id"] = _new_id()
    data["parent_ids"] = [dna.strategy_id]
    data["mutation_ops"] = list(dna.mutation_ops) + ["param_mutation"]
    data["generation"] = dna.generation + 1

    # Pick which signal gene pool to mutate: base or a random layer
    gene_pool = _pick_signal_pool(data)

    if not gene_pool["genes"]:
        return StrategyDNA.from_dict(data)

    # Pick a random signal gene with params
    candidates = [i for i, sg in enumerate(gene_pool["genes"])
                  if sg.get("params")]
    if not candidates:
        return StrategyDNA.from_dict(data)

    idx = random.choice(candidates)
    gene = gene_pool["genes"][idx]
    indicator = gene["indicator"]
    params = dict(gene["params"])

    if indicator in INDICATOR_REGISTRY:
        reg = INDICATOR_REGISTRY[indicator]
        param_name = random.choice(list(reg.params.keys()))
        pdef = reg.params[param_name]

        # 1. Profile recommended params (highest priority)
        profile = PROFILES.get(indicator)
        if profile and param_name in profile.recommended_params:
            rec_vals = profile.recommended_params[param_name]
            if rec_vals and random.random() < profile.follow_probability:
                new_val = random.choice(rec_vals)
                params[param_name] = int(new_val) if pdef.type == "int" else round(float(new_val), 4)
                gene["params"] = params
                return StrategyDNA.from_dict(data)

        # 2. Registry candidates (fallback)
        if pdef.candidates and random.random() < 0.5:
            candidates_filtered = [c for c in pdef.candidates
                                   if pdef.min <= c <= pdef.max]
            if candidates_filtered:
                new_val = random.choice(candidates_filtered)
                params[param_name] = int(new_val) if pdef.type == "int" else round(float(new_val), 4)
                gene["params"] = params
                return StrategyDNA.from_dict(data)

        # 3. Polynomial bounded mutation (DE-style)
        # Better exploration/exploitation balance than uniform random delta
        current = params.get(param_name, pdef.default)
        new_val = _polynomial_mutation(
            current, pdef.min, pdef.max, eta=20.0,
        )
        new_val = pdef.clamp(round(new_val / pdef.step) * pdef.step)
        params[param_name] = int(new_val) if pdef.type == "int" else round(float(new_val), 4)
        gene["params"] = params

    return StrategyDNA.from_dict(data)


def mutate_indicator(dna: StrategyDNA) -> StrategyDNA:
    """Replace a random indicator with a same-category alternative (base or layer)."""
    data = dna.to_dict()
    data["strategy_id"] = _new_id()
    data["parent_ids"] = [dna.strategy_id]
    data["mutation_ops"] = list(dna.mutation_ops) + ["indicator_replacement"]
    data["generation"] = dna.generation + 1

    gene_pool = _pick_signal_pool(data)

    if not gene_pool["genes"]:
        return StrategyDNA.from_dict(data)

    idx = random.randint(0, len(gene_pool["genes"]) - 1)
    gene = gene_pool["genes"][idx]
    current = gene["indicator"]

    alternatives = get_interchangeable(current)
    if not alternatives:
        return StrategyDNA.from_dict(data)

    role = gene.get("role", "entry_trigger")
    is_trigger = role.endswith("_trigger")
    if is_trigger:
        alternatives = [a for a in alternatives
                        if not INDICATOR_REGISTRY[a].guard_only]

    if not alternatives:
        return StrategyDNA.from_dict(data)

    new_indicator = random.choice(alternatives)
    reg = INDICATOR_REGISTRY[new_indicator]

    # Use profile for params when available
    profile = PROFILES.get(new_indicator)
    if profile and profile.recommended_params and random.random() < profile.follow_probability:
        new_params = {}
        for pname, pdef in reg.params.items():
            if pname in profile.recommended_params and profile.recommended_params[pname]:
                raw = random.choice(profile.recommended_params[pname])
                new_params[pname] = int(raw) if pdef.type == "int" else float(raw)
            else:
                new_params[pname] = int(pdef.default) if pdef.type == "int" else float(pdef.default)
    else:
        new_params = {k: int(v.default) if v.type == "int" else float(v.default)
                      for k, v in reg.params.items()}

    condition = generate_random_condition(new_indicator, reg)

    gene["indicator"] = new_indicator
    gene["params"] = new_params
    gene["field"] = None if len(reg.output_fields) == 1 else random.choice(reg.output_fields)
    gene["condition"] = condition

    return StrategyDNA.from_dict(data)


def mutate_logic(dna: StrategyDNA) -> StrategyDNA:
    """Flip AND/OR logic in base layer or a random MTF layer."""
    data = dna.to_dict()
    data["strategy_id"] = _new_id()
    data["parent_ids"] = [dna.strategy_id]
    data["mutation_ops"] = list(dna.mutation_ops) + ["logic_mutation"]
    data["generation"] = dna.generation + 1

    action = random.choice(["flip_entry", "flip_exit", "flip_both"])

    # Pick which logic_genes to mutate: base or a random layer
    targets = [("base", data["logic_genes"])]
    for i, layer in enumerate(data.get("layers", [])):
        targets.append((f"layer_{i}", layer["logic_genes"]))

    _, target_logic = random.choice(targets)

    if action == "flip_entry" or action == "flip_both":
        current = target_logic["entry_logic"]
        target_logic["entry_logic"] = "OR" if current == "AND" else "AND"
    if action == "flip_exit" or action == "flip_both":
        current = target_logic["exit_logic"]
        target_logic["exit_logic"] = "OR" if current == "AND" else "AND"

    return StrategyDNA.from_dict(data)


def mutate_add_signal(dna: StrategyDNA) -> StrategyDNA:
    """Add a random guard or DIRECTION signal gene to the strategy (base or layer)."""
    data = dna.to_dict()
    data["strategy_id"] = _new_id()
    data["parent_ids"] = [dna.strategy_id]
    data["mutation_ops"] = list(dna.mutation_ops) + ["add_signal"]
    data["generation"] = dna.generation + 1

    gene_pool = _pick_signal_pool(data)

    if not gene_pool["genes"]:
        return StrategyDNA.from_dict(data)

    # Check existing roles
    has_entry_guard = any(sg.get("role") == "entry_guard" for sg in gene_pool["genes"])
    has_exit_guard = any(sg.get("role") == "exit_guard" for sg in gene_pool["genes"])
    has_direction = any(sg.get("role") == "direction" for sg in gene_pool["genes"])

    # For mixed strategies, chance to add DIRECTION gene if missing
    is_mixed = data.get("risk_genes", {}).get("direction") == "mixed"
    if is_mixed and not has_direction and random.random() < 0.2:
        from core.evolution.population import _create_direction_gene
        gene = _create_direction_gene()
        new_gene = {
            "indicator": gene.indicator,
            "params": gene.params,
            "role": "direction",
            "field": gene.field_name,
            "condition": gene.condition,
        }
        gene_pool["genes"].append(new_gene)
        return StrategyDNA.from_dict(data)

    # Decide which guard to add (prefer missing one)
    if not has_entry_guard and (has_exit_guard or random.random() < 0.5):
        role = "entry_guard"
    elif not has_exit_guard:
        role = "exit_guard"
    else:
        role = random.choice(["entry_guard", "exit_guard"])

    # Pick indicator (all indicators eligible for guards)
    all_indicators = [name for name in INDICATOR_REGISTRY.keys()
                      if not INDICATOR_REGISTRY[name].guard_only or role.endswith("_guard")]
    if not all_indicators:
        return StrategyDNA.from_dict(data)

    indicator_name = random.choice(all_indicators)
    reg = INDICATOR_REGISTRY[indicator_name]

    # Use profile for params when available
    profile = PROFILES.get(indicator_name)
    if profile and profile.recommended_params and random.random() < profile.follow_probability:
        new_params = {}
        for pname, pdef in reg.params.items():
            if pname in profile.recommended_params and profile.recommended_params[pname]:
                raw = random.choice(profile.recommended_params[pname])
                new_params[pname] = int(raw) if pdef.type == "int" else float(raw)
            else:
                new_params[pname] = int(pdef.default) if pdef.type == "int" else float(pdef.default)
    else:
        new_params = {}
        for pname, pdef in reg.params.items():
            val = random.uniform(pdef.min, pdef.max)
            new_params[pname] = int(round(val / pdef.step) * pdef.step) if pdef.type == "int" \
                else round(round(val / pdef.step) * pdef.step, 2)

    condition = generate_random_condition(indicator_name, reg)
    field_name = None if len(reg.output_fields) <= 1 else random.choice(reg.output_fields)

    new_gene = {
        "indicator": indicator_name,
        "params": new_params,
        "role": role,
        "field": field_name,
        "condition": condition,
    }

    gene_pool["genes"].append(new_gene)
    return StrategyDNA.from_dict(data)


def mutate_remove_signal(dna: StrategyDNA) -> StrategyDNA:
    """Remove a random guard signal gene from the strategy (base or layer)."""
    data = dna.to_dict()
    data["strategy_id"] = _new_id()
    data["parent_ids"] = [dna.strategy_id]
    data["mutation_ops"] = list(dna.mutation_ops) + ["remove_signal"]
    data["generation"] = dna.generation + 1

    gene_pool = _pick_signal_pool(data)

    # Only remove guards, never remove triggers
    guard_indices = [i for i, sg in enumerate(gene_pool["genes"])
                     if sg.get("role", "").endswith("_guard")]
    if not guard_indices:
        return StrategyDNA.from_dict(data)

    idx = random.choice(guard_indices)
    gene_pool["genes"].pop(idx)
    return StrategyDNA.from_dict(data)


def mutate_risk(dna: StrategyDNA) -> StrategyDNA:
    """Adjust risk parameters within valid ranges."""
    data = dna.to_dict()
    data["strategy_id"] = _new_id()
    data["parent_ids"] = [dna.strategy_id]
    data["mutation_ops"] = list(dna.mutation_ops) + ["risk_mutation"]
    data["generation"] = dna.generation + 1

    risk = data["risk_genes"]

    # Mutate stop_loss: range [0.005, 0.20], step 0.005
    if random.random() < 0.5:
        current = risk["stop_loss"]
        delta = random.choice([-1, 1]) * 0.005 * random.randint(1, 5)
        risk["stop_loss"] = round(max(0.005, min(0.20, current + delta)), 4)

    # Mutate position_size: range [0.10, 1.0], step 0.05
    if random.random() < 0.5:
        current = risk["position_size"]
        delta = random.choice([-1, 1]) * 0.05 * random.randint(1, 3)
        risk["position_size"] = round(max(0.10, min(1.0, current + delta)), 2)

    # Mutate take_profit
    if random.random() < 0.3:
        if risk["take_profit"] is None:
            risk["take_profit"] = round(risk["stop_loss"] * random.uniform(1.5, 3.0), 4)
        else:
            delta = random.choice([-1, 1]) * 0.01 * random.randint(1, 3)
            risk["take_profit"] = round(
                max(risk["stop_loss"] + 0.005, risk["take_profit"] + delta), 4
            )

    # direction 不参与变异：评估前由任务级 direction 强制覆盖
    # （engine/evaluate_population 三处），变异产物必然被抹除

    data["risk_genes"] = risk
    return StrategyDNA.from_dict(data)


def crossover(parent_a: StrategyDNA, parent_b: StrategyDNA) -> StrategyDNA:
    """Create offspring by combining genes from two parents.

    Preserves MTF layer structure: crosses corresponding layers' signal genes.
    Both parents must have the same layer structure (same timeframe count).
    """
    child_signals = []
    # Take some genes from each parent
    a_genes = parent_a.signal_genes
    b_genes = parent_b.signal_genes

    # Mix: take entry from one, exit from other
    entry_a = [g for g in a_genes if g.role in (SignalRole.ENTRY_TRIGGER, SignalRole.ENTRY_GUARD)]
    exit_b = [g for g in b_genes if g.role in (SignalRole.EXIT_TRIGGER, SignalRole.EXIT_GUARD)]

    child_signals.extend(entry_a if entry_a else [a_genes[0]] if a_genes else [])
    child_signals.extend(exit_b if exit_b else [b_genes[-1]] if b_genes else [])

    # Inherit DIRECTION gene: always preserve if either parent has one
    direction_a = [g for g in a_genes if g.role == SignalRole.DIRECTION]
    direction_b = [g for g in b_genes if g.role == SignalRole.DIRECTION]
    direction_candidates = direction_a or direction_b
    if direction_candidates:
        child_signals.append(random.choice(direction_candidates))

    if not child_signals:
        child_signals = list(a_genes)

    # Logic from random parent
    logic = random.choice([parent_a.logic_genes, parent_b.logic_genes])
    # Risk from random parent
    risk = random.choice([parent_a.risk_genes, parent_b.risk_genes])

    # --- Cross MTF layers (preserve structure) ---
    child_layers = None
    child_cross_logic = "AND"

    a_has_layers = parent_a.layers is not None and len(parent_a.layers) > 0
    b_has_layers = parent_b.layers is not None and len(parent_b.layers) > 0

    if a_has_layers and b_has_layers:
        # Both have layers: cross corresponding layers
        from core.strategy.dna import TimeframeLayer, LogicGenes
        child_layers = []
        for la, lb in itertools.zip_longest(parent_a.layers, parent_b.layers):
            # Handle case where one parent has more layers than the other
            if lb is None:
                # Parent a has extra layer, copy it
                child_layers.append(TimeframeLayer(
                    timeframe=la.timeframe,
                    signal_genes=list(la.signal_genes),
                    logic_genes=la.logic_genes,
                    role=la.role,
                ))
                continue
            if la is None:
                # Parent b has extra layer, copy it
                child_layers.append(TimeframeLayer(
                    timeframe=lb.timeframe,
                    signal_genes=list(lb.signal_genes),
                    logic_genes=lb.logic_genes,
                    role=lb.role,
                ))
                continue

            # Same timeframe position: take entry from one, exit from other
            layer_entry_a = [g for g in la.signal_genes
                             if g.role in (SignalRole.ENTRY_TRIGGER, SignalRole.ENTRY_GUARD)]
            layer_exit_b = [g for g in lb.signal_genes
                            if g.role in (SignalRole.EXIT_TRIGGER, SignalRole.EXIT_GUARD)]
            layer_signals = []
            layer_signals.extend(layer_entry_a if layer_entry_a else
                                 [la.signal_genes[0]] if la.signal_genes else [])
            layer_signals.extend(layer_exit_b if layer_exit_b else
                                 [lb.signal_genes[-1]] if lb.signal_genes else [])
            # Inherit DIRECTION gene: always preserve if either parent has one
            layer_dir_a = [g for g in la.signal_genes if g.role == SignalRole.DIRECTION]
            layer_dir_b = [g for g in lb.signal_genes if g.role == SignalRole.DIRECTION]
            layer_dir_candidates = layer_dir_a or layer_dir_b
            if layer_dir_candidates:
                layer_signals.append(random.choice(layer_dir_candidates))
            if not layer_signals:
                layer_signals = list(la.signal_genes)
            layer_logic = random.choice([la.logic_genes, lb.logic_genes])
            # Inherit role from random parent
            layer_role = random.choice([la.role, lb.role])
            child_layers.append(TimeframeLayer(
                timeframe=la.timeframe,
                signal_genes=layer_signals,
                logic_genes=layer_logic,
                role=layer_role,
            ))
        child_cross_logic = random.choice(
            [parent_a.cross_layer_logic, parent_b.cross_layer_logic]
        )
    elif a_has_layers:
        child_layers = list(parent_a.layers)
        child_cross_logic = parent_a.cross_layer_logic
    elif b_has_layers:
        child_layers = list(parent_b.layers)
        child_cross_logic = parent_b.cross_layer_logic

    child = StrategyDNA(
        signal_genes=child_signals,
        logic_genes=logic,
        risk_genes=risk,
        execution_genes=parent_a.execution_genes,
        parent_ids=[parent_a.strategy_id, parent_b.strategy_id],
        mutation_ops=["crossover"],
        generation=max(parent_a.generation, parent_b.generation) + 1,
        layers=child_layers,
        cross_layer_logic=child_cross_logic,
        mtf_mode=random.choice([parent_a.mtf_mode, parent_b.mtf_mode]),
        confluence_threshold=random.choice([parent_a.confluence_threshold, parent_b.confluence_threshold]),
        proximity_mult=random.choice([parent_a.proximity_mult, parent_b.proximity_mult]),
    )
    return child


def mutate_composition(dna: StrategyDNA) -> StrategyDNA:
    """组合基因变异（add-ai-research-mcp 6.3）。

    作用面：signal_sources 权重 / composition 阈值与逻辑。无组合基因
    （退化形态）时 no-op 返回原 dna——纯退化种群行为零改变。
    子代合法性：变异仅在合法值域内扰动（权重 clip 到 [0,2]、阈值
    [0,1]、logic op ∈ {AND, OR}、mode 不变）。
    """
    data = dna.to_dict()
    sources = data.get("signal_sources")
    comp = data.get("composition")
    if not sources and not comp:
        return dna

    choice = random.random()
    if comp and choice < 0.5:
        # 阈值扰动
        if comp.get("mode") == "logic":
            comp["op"] = "OR" if comp.get("op", "AND") == "AND" else "AND"
        else:
            t = float(comp.get("threshold", 0.5))
            comp["threshold"] = round(min(1.0, max(0.0, t + random.uniform(-0.1, 0.1))), 4)
    else:
        # 权重扰动（有 sources 时）
        for s in sources or []:
            if random.random() < 0.5:
                w = float(s.get("weight", 1.0))
                s["weight"] = round(min(2.0, max(0.0, w + random.uniform(-0.3, 0.3))), 4)

    if sources:
        data["signal_sources"] = sources
    if comp:
        data["composition"] = comp
    return StrategyDNA.from_dict(data)
