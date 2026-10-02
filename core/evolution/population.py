"""Population initialization and random DNA generation."""
from __future__ import annotations

import random
import uuid
from typing import List, Optional

from core.strategy.dna import (
    SignalRole, SignalGene, LogicGenes, RiskGenes, ExecutionGenes,
    StrategyDNA, TimeframeLayer, derive_role,
)
from core.strategy.validator import validate_dna
from core.features.indicators import INDICATOR_REGISTRY
from core.features.indicator_profile import PROFILES
from core.evolution.operators import mutate_params, mutate_indicator, mutate_logic, mutate_risk, generate_random_condition


# ---------------------------------------------------------------------------
# Classic strategy templates (7 patterns from trading research)
# ---------------------------------------------------------------------------

STRATEGY_TEMPLATES = [
    {   # trend_ema: EMA trend following with MACD confirmation
        "name": "trend_ema",
        "genes": [
            {"indicator": "MACD", "params": {"fast": 12, "slow": 26, "signal": 9},
             "role": "entry_trigger", "field": "histogram",
             "condition": {"type": "cross_above", "threshold": 0}},
            {"indicator": "EMA", "params": {"period": 50},
             "role": "entry_guard", "field": None,
             "condition": {"type": "price_above"}},
            {"indicator": "MACD", "params": {"fast": 12, "slow": 26, "signal": 9},
             "role": "exit_trigger", "field": "histogram",
             "condition": {"type": "cross_below", "threshold": 0}},
            {"indicator": "ATR", "params": {"period": 14},
             "role": "exit_guard", "field": None,
             "condition": {"type": "gt"}},
        ],
        "logic": {"entry_logic": "AND", "exit_logic": "OR"},
    },
    {   # momentum: RSI + MACD momentum trading
        "name": "momentum",
        "genes": [
            {"indicator": "RSI", "params": {"period": 14},
             "role": "entry_trigger", "field": None,
             "condition": {"type": "lt", "threshold": 30}},
            {"indicator": "MACD", "params": {"fast": 12, "slow": 26, "signal": 9},
             "role": "entry_guard", "field": "histogram",
             "condition": {"type": "cross_above", "threshold": 0}},
            {"indicator": "RSI", "params": {"period": 14},
             "role": "exit_trigger", "field": None,
             "condition": {"type": "gt", "threshold": 70}},
            {"indicator": "BB", "params": {"period": 20, "std": 2.0},
             "role": "exit_guard", "field": "upper",
             "condition": {"type": "price_above"}},
        ],
        "logic": {"entry_logic": "AND", "exit_logic": "OR"},
    },
    {   # mean_reversion: BB lower/upper band + RSI
        "name": "mean_reversion",
        "genes": [
            {"indicator": "BB", "params": {"period": 20, "std": 2.0},
             "role": "entry_trigger", "field": "lower",
             "condition": {"type": "price_below"}},
            {"indicator": "RSI", "params": {"period": 14},
             "role": "entry_guard", "field": None,
             "condition": {"type": "lt", "threshold": 35}},
            {"indicator": "BB", "params": {"period": 20, "std": 2.0},
             "role": "exit_trigger", "field": "upper",
             "condition": {"type": "price_above"}},
        ],
        "logic": {"entry_logic": "AND", "exit_logic": "OR"},
    },
    {   # trend_breakout: low ADX (consolidation) + MACD breakout
        "name": "trend_breakout",
        "genes": [
            {"indicator": "ADX", "params": {"period": 14},
             "role": "entry_guard", "field": None,
             "condition": {"type": "lt", "threshold": 25}},
            {"indicator": "MACD", "params": {"fast": 12, "slow": 26, "signal": 9},
             "role": "entry_trigger", "field": "histogram",
             "condition": {"type": "cross_above", "threshold": 0}},
            {"indicator": "MACD", "params": {"fast": 12, "slow": 26, "signal": 9},
             "role": "exit_trigger", "field": "histogram",
             "condition": {"type": "cross_below", "threshold": 0}},
            {"indicator": "ATR", "params": {"period": 14},
             "role": "exit_guard", "field": None,
             "condition": {"type": "gt", "threshold": 0}},
        ],
        "logic": {"entry_logic": "AND", "exit_logic": "OR"},
    },
    {   # dual_ma_cross: EMA(10) crosses above/below EMA(20)
        "name": "dual_ma_cross",
        "genes": [
            {"indicator": "EMA", "params": {"period": 10},
             "role": "entry_trigger", "field": None,
             "condition": {"type": "cross_above_series", "target_indicator": "EMA", "target_params": {"period": 20}}},
            {"indicator": "EMA", "params": {"period": 20},
             "role": "entry_guard", "field": None,
             "condition": {"type": "price_above"}},
            {"indicator": "EMA", "params": {"period": 10},
             "role": "exit_trigger", "field": None,
             "condition": {"type": "cross_below_series", "target_indicator": "EMA", "target_params": {"period": 20}}},
            {"indicator": "EMA", "params": {"period": 20},
             "role": "exit_guard", "field": None,
             "condition": {"type": "price_below"}},
        ],
        "logic": {"entry_logic": "AND", "exit_logic": "OR"},
    },
    {   # multi_tf_trend: EMA trend + ADX strength confirmation
        "name": "multi_tf_trend",
        "genes": [
            {"indicator": "EMA", "params": {"period": 50},
             "role": "entry_trigger", "field": None,
             "condition": {"type": "price_above"}},
            {"indicator": "ADX", "params": {"period": 14},
             "role": "entry_guard", "field": None,
             "condition": {"type": "gt", "threshold": 25}},
            {"indicator": "EMA", "params": {"period": 50},
             "role": "exit_trigger", "field": None,
             "condition": {"type": "price_below"}},
            {"indicator": "ATR", "params": {"period": 14},
             "role": "exit_guard", "field": None,
             "condition": {"type": "gt", "threshold": 0}},
        ],
        "logic": {"entry_logic": "AND", "exit_logic": "OR"},
    },
    {   # volatility: low ADX (consolidation) + MACD breakout
        "name": "volatility",
        "genes": [
            {"indicator": "ADX", "params": {"period": 14},
             "role": "entry_guard", "field": None,
             "condition": {"type": "lt", "threshold": 25}},
            {"indicator": "MACD", "params": {"fast": 12, "slow": 26, "signal": 9},
             "role": "entry_trigger", "field": "histogram",
             "condition": {"type": "cross_above", "threshold": 0}},
            {"indicator": "MACD", "params": {"fast": 12, "slow": 26, "signal": 9},
             "role": "exit_trigger", "field": "histogram",
             "condition": {"type": "cross_below", "threshold": 0}},
            {"indicator": "ATR", "params": {"period": 14},
             "role": "exit_guard", "field": None,
             "condition": {"type": "gt", "threshold": 0}},
        ],
        "logic": {"entry_logic": "AND", "exit_logic": "OR"},
    },
]


# Trend indicators: use price_above (True = close > indicator = bullish)
_DIRECTION_TREND_INDICATORS = ["EMA", "SMA", "WMA", "DEMA", "TEMA", "VWMA"]

# Band indicators: use price_above on specific field
_DIRECTION_BAND_INDICATORS = ["BB", "Keltner", "Donchian"]

# Momentum indicators: use gt with directional threshold (True = bullish)
# Each config: (indicator_name, field_name, threshold, threshold_range)
_DIRECTION_MOMENTUM_CONFIGS = [
    ("RSI", None, 50, (30, 70)),
    ("MACD", "histogram", 0, (-5, 5)),
    ("CCI", None, 0, (-200, 200)),
    ("ROC", None, 0, (-10, 10)),
    ("CMO", None, 0, (-50, 50)),
    ("TRIX", None, 0, (-1, 1)),
    ("CMF", None, 0, (-0.5, 0.5)),
    ("MFI", None, 50, (30, 70)),
    ("Aroon", "aroon_osc", 0, (-50, 50)),
    ("MultifactorOsc", None, 0, (-1, 1)),
]


def _create_direction_gene() -> SignalGene:
    """Create a DIRECTION gene with trend or momentum indicator.

    Two categories with "True = bullish" semantics:
    - Trend indicators: price_above condition (close > indicator = bullish)
    - Momentum indicators: gt condition (indicator > threshold = bullish)
    """
    kind = random.choices(
        ["trend", "band", "momentum"],
        weights=[5, 3, 4],
    )[0]

    if kind == "trend":
        return _create_trend_direction_gene()
    elif kind == "band":
        return _create_band_direction_gene()
    else:
        return _create_momentum_direction_gene()


def _create_trend_direction_gene() -> SignalGene:
    """Create a DIRECTION gene using a trend indicator with price_above."""
    indicator_name = random.choice(_DIRECTION_TREND_INDICATORS)
    reg = INDICATOR_REGISTRY.get(indicator_name)
    profile = PROFILES.get(indicator_name)

    params = _build_params(reg, profile)
    return SignalGene(
        indicator=indicator_name,
        params=params,
        role=SignalRole.DIRECTION,
        field_name=None,
        condition={"type": "price_above"},
    )


def _create_band_direction_gene() -> SignalGene:
    """Create a DIRECTION gene using a band indicator with price_above."""
    indicator_name = random.choice(_DIRECTION_BAND_INDICATORS)
    reg = INDICATOR_REGISTRY.get(indicator_name)
    profile = PROFILES.get(indicator_name)

    params = _build_params(reg, profile)

    field_name = None
    if reg and len(reg.output_fields) > 1:
        field_name = random.choice(reg.output_fields)

    return SignalGene(
        indicator=indicator_name,
        params=params,
        role=SignalRole.DIRECTION,
        field_name=field_name,
        condition={"type": "price_above"},
    )


def _create_momentum_direction_gene() -> SignalGene:
    """Create a DIRECTION gene using a momentum indicator with gt threshold."""
    indicator_name, field_name, threshold, (lo, hi) = random.choice(
        _DIRECTION_MOMENTUM_CONFIGS
    )
    reg = INDICATOR_REGISTRY.get(indicator_name)
    profile = PROFILES.get(indicator_name)

    params = _build_params(reg, profile)
    # Allow threshold to vary around the default
    jittered_threshold = round(random.uniform(lo, hi), 2)

    return SignalGene(
        indicator=indicator_name,
        params=params,
        role=SignalRole.DIRECTION,
        field_name=field_name,
        condition={"type": "gt", "threshold": jittered_threshold},
    )


def _build_params(reg, profile) -> dict:
    """Build indicator params using profile guidance when available."""
    if profile and profile.recommended_params and random.random() < profile.follow_probability:
        params = {}
        for pname, pdef in reg.params.items():
            if pname in profile.recommended_params and profile.recommended_params[pname]:
                raw = random.choice(profile.recommended_params[pname])
                params[pname] = int(raw) if pdef.type == "int" else float(raw)
            else:
                params[pname] = int(pdef.default) if pdef.type == "int" else float(pdef.default)
    else:
        params = {}
        for pname, pdef in (reg.params.items() if reg else []):
            val = random.uniform(pdef.min, pdef.max)
            params[pname] = int(round(val / pdef.step) * pdef.step) if pdef.type == "int" \
                else round(round(val / pdef.step) * pdef.step, 2)
    return params


def _dna_from_template(
    template: dict,
    timeframe: str = "4h",
    symbol: str = "BTCUSDT",
    leverage: int = 1,
    direction: str = "long",
    timeframe_pool: list | None = None,
) -> StrategyDNA:
    """Create a StrategyDNA from a classic strategy template."""
    actual_direction = direction
    signals = []
    for g in template["genes"]:
        signals.append(SignalGene(
            indicator=g["indicator"],
            params=dict(g["params"]),
            role=SignalRole(g["role"]),
            field_name=g.get("field"),
            condition=dict(g["condition"]),
        ))

    # Auto-add a DIRECTION gene for mixed strategies
    if actual_direction == "mixed":
        signals.append(_create_direction_gene())

    logic = template["logic"]

    # Build MTF layers if timeframe_pool has multiple timeframes
    mtf_layers = None
    if timeframe_pool and len(timeframe_pool) > 1:
        other_tfs = [tf for tf in timeframe_pool if tf != timeframe][:2]
        if other_tfs:
            mtf_layers = [TimeframeLayer(
                timeframe=timeframe,
                signal_genes=list(signals),
                logic_genes=LogicGenes(**logic),
            )]
            for tf in other_tfs:
                mtf_layers.append(create_random_mtf_layer(tf, symbol))

    dna = StrategyDNA(
        signal_genes=signals,
        logic_genes=LogicGenes(**logic),
        execution_genes=ExecutionGenes(timeframe=timeframe, symbol=symbol),
        risk_genes=RiskGenes(
            stop_loss=0.05, take_profit=None, position_size=0.3,
            leverage=leverage, direction=actual_direction,
        ),
        layers=mtf_layers,
    )

    return dna


def _make_signal_profiled(role: SignalRole, pool: list) -> SignalGene:
    """Create a signal gene using indicator profile when available."""
    if not pool:
        pool[:] = [name for name in INDICATOR_REGISTRY.keys()
                    if not INDICATOR_REGISTRY[name].guard_only]
    indicator_name = random.choice(pool)
    reg = INDICATOR_REGISTRY[indicator_name]
    profile = PROFILES.get(indicator_name)

    if profile and random.random() < profile.follow_probability:
        # Follow recommended usage
        params = {}
        for pname, candidates in profile.recommended_params.items():
            if candidates:
                raw = random.choice(candidates)
                pdef = reg.params.get(pname)
                params[pname] = int(raw) if pdef and pdef.type == "int" else float(raw)
            elif pname in reg.params:
                pdef = reg.params[pname]
                params[pname] = int(pdef.default) if pdef.type == "int" else float(pdef.default)

        # If profile has no params but registry does, use defaults
        if not profile.recommended_params and reg.params:
            for pname, pdef in reg.params.items():
                params[pname] = int(pdef.default) if pdef.type == "int" else float(pdef.default)

        cond_preset = random.choice(profile.recommended_conditions)
        condition = {"type": cond_preset.type}
        if cond_preset.thresholds:
            condition["threshold"] = random.choice(cond_preset.thresholds)

        field_name = cond_preset.target_field
        if field_name is None and len(reg.output_fields) > 1:
            field_name = random.choice(reg.output_fields)
    else:
        # Free exploration (original logic)
        params = {}
        for pname, pdef in reg.params.items():
            if pdef.candidates:
                params[pname] = int(random.choice(pdef.candidates)) if pdef.type == "int" \
                    else float(random.choice(pdef.candidates))
            else:
                val = random.uniform(pdef.min, pdef.max)
                params[pname] = int(round(val / pdef.step) * pdef.step) if pdef.type == "int" \
                    else round(round(val / pdef.step) * pdef.step, 2)

        condition = generate_random_condition(indicator_name, reg)
        field_name = None
        if len(reg.output_fields) > 1:
            field_name = random.choice(reg.output_fields)

    return SignalGene(
        indicator=indicator_name,
        params=params,
        role=role,
        field_name=field_name,
        condition=condition,
    )


def create_random_dna(
    timeframe: str = "4h",
    symbol: str = "BTCUSDT",
    timeframe_pool: Optional[List[str]] = None,
    leverage: int = 1,
    direction: str = "long",
    indicator_pool: Optional[List[str]] = None,
    profiled: bool = True,
) -> StrategyDNA:
    """Generate a completely random but valid StrategyDNA.

    Args:
        profiled: If True, use indicator profiles for guided generation.
                  If False, generate completely random signals (free exploration).
    """
    # "mixed" is preserved; DIRECTION gene handles long/short decisions
    actual_direction = direction

    available_indicators = list(indicator_pool) if indicator_pool else list(INDICATOR_REGISTRY.keys())
    # Filter out indicators that cannot be computed during evolution:
    # - compute_mode="lazy" indicators are skipped by compute_all_indicators(skip_lazy=True)
    # - VWAP has no compute implementation in _compute_indicator
    _NO_COMPUTE = {"VWAP", "FractalEntropy", "MultifactorOsc", "VolumeProfile"}
    available_indicators = [n for n in available_indicators if n not in _NO_COMPUTE]
    trigger_indicators = [
        name for name in available_indicators
        if name in INDICATOR_REGISTRY and not INDICATOR_REGISTRY[name].guard_only
    ]
    all_indicators = [name for name in available_indicators if name in INDICATOR_REGISTRY]

    def _make_signal(role: SignalRole, pool: list) -> SignalGene:
        if profiled:
            return _make_signal_profiled(role, pool)
        # Free exploration: 100% random, no profile guidance
        if not pool:
            pool[:] = [name for name in INDICATOR_REGISTRY.keys()
                        if not (role.value.endswith("guard") and INDICATOR_REGISTRY.get(name, type('', (), {})()).guard_only)]
        indicator_name = random.choice(pool)
        reg = INDICATOR_REGISTRY[indicator_name]
        params = _random_params(indicator_name)
        condition = _random_condition(indicator_name)
        field_name = None
        if len(reg.output_fields) > 1:
            field_name = random.choice(reg.output_fields)
        return SignalGene(
            indicator=indicator_name,
            params=params,
            role=role,
            field_name=field_name,
            condition=condition,
        )

    # Build signal genes: at least 1 entry trigger + 1 exit trigger
    signals = []
    signals.append(_make_signal(SignalRole.ENTRY_TRIGGER, trigger_indicators))

    if random.random() < 0.5:
        signals.append(_make_signal(SignalRole.ENTRY_GUARD, all_indicators))

    signals.append(_make_signal(SignalRole.EXIT_TRIGGER, trigger_indicators))

    if random.random() < 0.4:
        signals.append(_make_signal(SignalRole.EXIT_GUARD, all_indicators))

    # Auto-add a DIRECTION gene for mixed strategies
    if actual_direction == "mixed":
        signals.append(_create_direction_gene())

    entry_logic = random.choice(["AND", "OR"])
    exit_logic = random.choice(["AND", "OR"])

    stop_loss = round(random.uniform(0.01, 0.15), 4)
    position_size = round(random.uniform(0.10, 0.60), 2)
    take_profit = None

    dna = StrategyDNA(
        signal_genes=signals,
        logic_genes=LogicGenes(entry_logic=entry_logic, exit_logic=exit_logic),
        execution_genes=ExecutionGenes(timeframe=timeframe, symbol=symbol),
        risk_genes=RiskGenes(stop_loss=stop_loss, take_profit=take_profit,
                             position_size=position_size, leverage=leverage,
                             direction=actual_direction),
    )

    # Validate and retry if needed
    result = validate_dna(dna)
    if not result.is_valid:
        fallback_signals = [
            SignalGene("RSI", {"period": 14}, SignalRole.ENTRY_TRIGGER, None,
                       {"type": "lt", "threshold": 30}),
            SignalGene("RSI", {"period": 14}, SignalRole.EXIT_TRIGGER, None,
                       {"type": "gt", "threshold": 70}),
        ]
        if actual_direction == "mixed":
            fallback_signals.append(SignalGene(
                "EMA", {"period": 50}, SignalRole.DIRECTION, None,
                {"type": "price_above"},
            ))
        dna = StrategyDNA(
            signal_genes=fallback_signals,
            logic_genes=LogicGenes(entry_logic="AND", exit_logic="OR"),
            risk_genes=RiskGenes(stop_loss=0.05, position_size=0.3,
                                 leverage=leverage, direction=actual_direction),
            execution_genes=ExecutionGenes(timeframe=timeframe, symbol=symbol),
        )

    # Generate MTF layers: include execution TF layer + all other TFs
    if timeframe_pool and len(timeframe_pool) > 1:
        other_tfs = [tf for tf in timeframe_pool if tf != timeframe][:2]
        if other_tfs:
            layers = [TimeframeLayer(
                timeframe=timeframe,
                signal_genes=list(dna.signal_genes),
                logic_genes=LogicGenes(
                    entry_logic=dna.logic_genes.entry_logic,
                    exit_logic=dna.logic_genes.exit_logic,
                ),
            )]
            for tf in other_tfs:
                layers.append(create_random_mtf_layer(tf, symbol))
            dna.layers = layers
            dna._layers_explicit = True
            dna.cross_layer_logic = random.choice(["AND", "OR"])

    return dna


def create_random_mtf_layer(
    timeframe: str,
    symbol: str = "BTCUSDT",
) -> TimeframeLayer:
    """Generate a single random TimeframeLayer for MTF strategies."""
    trigger_indicators = [
        name for name, defn in INDICATOR_REGISTRY.items()
        if not defn.guard_only
    ]
    all_indicators = list(INDICATOR_REGISTRY.keys())

    signals = []
    ind = random.choice(trigger_indicators)
    signals.append(SignalGene(
        indicator=ind,
        params=_random_params(ind),
        role=SignalRole.ENTRY_TRIGGER,
        condition=_random_condition(ind),
    ))

    if random.random() < 0.5:
        ind = random.choice(all_indicators)
        signals.append(SignalGene(
            indicator=ind,
            params=_random_params(ind),
            role=SignalRole.ENTRY_GUARD,
            condition=_random_condition(ind),
        ))

    ind = random.choice(trigger_indicators)
    signals.append(SignalGene(
        indicator=ind,
        params=_random_params(ind),
        role=SignalRole.EXIT_TRIGGER,
        condition=_random_condition(ind),
    ))

    return TimeframeLayer(
        timeframe=timeframe,
        signal_genes=signals,
        logic_genes=LogicGenes(
            entry_logic=random.choice(["AND", "OR"]),
            exit_logic=random.choice(["AND", "OR"]),
        ),
        role=derive_role(timeframe) if random.random() < 0.6 else "execution",
    )


def _random_params(indicator_name: str) -> dict:
    """Generate random params for a given indicator."""
    reg = INDICATOR_REGISTRY.get(indicator_name)
    if not reg:
        return {}
    params = {}
    for pname, pdef in reg.params.items():
        val = random.uniform(pdef.min, pdef.max)
        params[pname] = int(round(val / pdef.step) * pdef.step) if pdef.type == "int" \
            else round(round(val / pdef.step) * pdef.step, 2)
    return params


def _random_condition(indicator_name: str) -> dict:
    """Generate a random condition for a given indicator."""
    return generate_random_condition(indicator_name)


def init_population(
    size: int = 15,
    ancestor: Optional[StrategyDNA] = None,
    extra_ancestors: Optional[List[StrategyDNA]] = None,
    timeframe: str = "4h",
    symbol: str = "BTCUSDT",
    leverage: int = 1,
    direction: str = "long",
    timeframe_pool: Optional[List[str]] = None,
    indicator_pool: Optional[List[str]] = None,
    exclude_signatures: Optional[set] = None,
) -> List[StrategyDNA]:
    """Initialize population by mutating an ancestor.

    Args:
        size: Population size.
        ancestor: Initial strategy (first individual).
        extra_ancestors: Additional ancestors for multi-start (e.g. top 3).
        timeframe: K-line timeframe.
        symbol: Trading pair.
        leverage: Task-level leverage constraint.
        direction: Task-level direction constraint.
        timeframe_pool: Available timeframes for MTF layer generation.
        indicator_pool: Available indicators to restrict mutations.
        exclude_signatures: Set of gene signature hashes to avoid (for new populations).
    """
    population = []

    # First individual is the ancestor itself
    if ancestor is not None:
        population.append(ancestor)
    elif STRATEGY_TEMPLATES:
        # Use a random classic template as seed
        template = random.choice(STRATEGY_TEMPLATES)
        population.append(_dna_from_template(template, timeframe, symbol, leverage, direction, timeframe_pool))
    else:
        population.append(create_random_dna(timeframe, symbol,
                                            leverage=leverage, direction=direction,
                                            timeframe_pool=timeframe_pool,
                                            indicator_pool=indicator_pool))

    # Add extra ancestors (for multi-start continuous evolution)
    if extra_ancestors:
        for a in extra_ancestors:
            if a not in population:
                population.append(a)

    # Generate remaining individuals by 40/40/20 ratio
    remaining = size - len(population)
    n_template = max(1, int(remaining * 0.4))   # 40% from template mutations
    n_profiled = max(1, int(remaining * 0.4))   # 40% profile-aware random
    n_random = remaining - n_template - n_profiled  # 20% free exploration

    mutation_funcs = [mutate_params, mutate_indicator, mutate_logic, mutate_risk]

    # Template mutations: pick a strategy template, mutate the template seed
    for _ in range(n_template):
        if len(population) < size:
            template = random.choice(STRATEGY_TEMPLATES)
            seed = _dna_from_template(template, timeframe, symbol, leverage, direction, timeframe_pool)
            mut_func = random.choice(mutation_funcs)
            try:
                child = mut_func(seed)
                result = validate_dna(child)
                if result.is_valid:
                    population.append(child)
                else:
                    population.append(seed)
            except Exception:
                population.append(seed)

    # Profile-aware random DNA
    for _ in range(n_profiled):
        if len(population) < size:
            population.append(create_random_dna(
                timeframe, symbol,
                leverage=leverage, direction=direction,
                timeframe_pool=timeframe_pool,
                indicator_pool=indicator_pool,
            ))

    # Free exploration: random DNA without profile guidance
    for _ in range(n_random):
        if len(population) < size:
            population.append(create_random_dna(
                timeframe, symbol,
                leverage=leverage, direction=direction,
                timeframe_pool=timeframe_pool,
                indicator_pool=indicator_pool,
                profiled=False,
            ))

    # Dedup: filter out individuals whose signatures are in exclude_signatures
    if exclude_signatures:
        deduped = []
        seen = set()
        for ind in population:
            sig = ind.gene_signature
            if sig not in exclude_signatures and sig not in seen:
                deduped.append(ind)
                seen.add(sig)
        # Backfill with random individuals if dedup removed too many
        attempts = 0
        while len(deduped) < size and attempts < size * 3:
            candidate = create_random_dna(
                timeframe, symbol,
                leverage=leverage, direction=direction,
                timeframe_pool=timeframe_pool,
                indicator_pool=indicator_pool,
            )
            sig = candidate.gene_signature
            if sig not in exclude_signatures and sig not in seen:
                deduped.append(candidate)
                seen.add(sig)
            attempts += 1
        population = deduped

    return population
