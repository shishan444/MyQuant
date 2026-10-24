"""MTF (Multi-TimeFrame) resonance engine.

Implements the core algorithms for multi-dimensional score-gating
that replaces the legacy boolean AND/OR cross-layer logic.

Three-layer evaluation pipeline:
  1. Layer evaluation: extract context (direction, price levels, momentum)
  2. Cross-layer synthesis: direction score, confluence score, momentum score
  3. Decision gate: filter signals based on synthesis scores and mtf_mode

Key concepts:
  - s% (proximity percentage): ATR/close * proximity_mult
  - Price zones: [P*(1-s%), P*(1+s%)] around each price level
  - Confluence: overlap of price zones across layers
  - Direction: derived from structure layers (highest TF wins conflicts)
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional, Tuple

import numpy as np
import pandas as pd

from core.strategy.dna import StrategyDNA, SignalRole, SignalGene, derive_role
from core.strategy.executor import (
    SignalSet, evaluate_layer, _get_indicator_column, resample_signals,
    evaluate_condition, combine_signals,
)
from core.features.registry import INDICATOR_REGISTRY


# ---------------------------------------------------------------------------
# L2: Core algorithms
# ---------------------------------------------------------------------------

def compute_s_pct(atr: float, close: float, proximity_mult: float) -> float:
    """Compute proximity percentage s% = (ATR / close) * proximity_mult."""
    if close <= 0:
        return 0.0
    return (atr / close) * proximity_mult


def build_price_zone(price: float, s_pct: float) -> Tuple[float, float]:
    """Build price zone [P*(1-s%), P*(1+s%)] around a price level."""
    return (price * (1 - s_pct), price * (1 + s_pct))


def merge_intervals(intervals: List[Tuple[float, float]]) -> List[Tuple[float, float]]:
    """Merge overlapping intervals into sorted union."""
    if not intervals:
        return []
    sorted_ivs = sorted(intervals, key=lambda x: x[0])
    merged = [sorted_ivs[0]]
    for start, end in sorted_ivs[1:]:
        if start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return merged


def intersect_intervals(
    a: List[Tuple[float, float]],
    b: List[Tuple[float, float]],
) -> List[Tuple[float, float]]:
    """Compute intersection of two sorted interval sets."""
    result = []
    i, j = 0, 0
    while i < len(a) and j < len(b):
        lo = max(a[i][0], b[j][0])
        hi = min(a[i][1], b[j][1])
        if lo < hi:
            result.append((lo, hi))
        if a[i][1] < b[j][1]:
            i += 1
        else:
            j += 1
    return result


def compute_confluence_score(
    layer_zones: List[List[Tuple[float, float]]],
    current_price: float,
    max_zone_width: float,
) -> float:
    """Compute confluence score from overlapping price zones across layers.

    Score = (total_overlap_width / max_zone_width) * price_proximity_factor.
    price_proximity_factor penalizes when current price is far from overlap center.
    Returns 0.0 if fewer than 2 non-empty zone sets or price is outside overlap.
    """
    # Filter out empty zone sets
    non_empty = [z for z in layer_zones if z]
    if len(non_empty) < 2:
        return 0.0

    # Compute progressive intersection
    overlap = non_empty[0]
    for zones in non_empty[1:]:
        overlap = intersect_intervals(overlap, zones)
        if not overlap:
            return 0.0

    # Total overlap width
    total_width = sum(hi - lo for lo, hi in overlap)
    if total_width <= 0:
        return 0.0

    # Check if current price is within or near the overlap
    price_in_overlap = any(lo <= current_price <= hi for lo, hi in overlap)
    if not price_in_overlap:
        # Price outside overlap -> penalize heavily
        min_dist = min(abs(current_price - lo) for lo, hi in overlap)
        if min_dist > max_zone_width:
            return 0.0
        # Linear decay based on distance
        proximity_factor = max(0.0, 1.0 - min_dist / max_zone_width)
    else:
        proximity_factor = 1.0

    raw_score = total_width / max_zone_width
    # Cap at 1.0
    score = min(1.0, raw_score) * proximity_factor
    return round(score, 6)


def compute_proximity_score(
    price_levels: List[pd.Series],
    current_price: pd.Series,
    s_pct: float,
) -> pd.Series:
    """Compute proximity score for single-layer fallback (Type B gap B).

    Vectorized: builds (N, n_levels) price matrix and computes distances
    via numpy broadcasting.
    """
    if not price_levels or s_pct <= 0:
        return pd.Series(0.0, index=current_price.index)

    n = len(current_price)
    price_vals = current_price.values

    # Build (n, n_levels) matrix of level values
    level_arrays = [lv.values[:n] for lv in price_levels]
    level_matrix = np.column_stack(level_arrays)

    # Broadcasting: (n, 1) vs (n, n_levels)
    price_broadcast = price_vals[:, np.newaxis]
    safe_price = np.where(price_broadcast > 0, price_broadcast, 1.0)
    rel_dists = np.abs(price_broadcast - level_matrix) / safe_price

    # NaN levels -> infinite distance
    rel_dists = np.where(np.isnan(level_matrix), np.inf, rel_dists)

    # Min distance per bar
    min_rel_dist = np.nanmin(rel_dists, axis=1)

    # Score: 1 - min_dist/s_pct if within range, else 0
    scores = np.where(
        (min_rel_dist <= s_pct) & (price_vals > 0),
        np.maximum(0.0, 1.0 - min_rel_dist / s_pct),
        0.0,
    )

    return pd.Series(scores, index=current_price.index)


def resolve_direction_conflict(
    layers_with_direction: List[Tuple[str, pd.Series]],
) -> pd.Series:
    """Resolve direction conflicts among structure layers.

    Highest timeframe wins. '3d' > '1d' > '4h' etc.
    """
    if not layers_with_direction:
        return pd.Series(0.0)

    def _tf_hours(tf_str: str) -> float:
        """Convert timeframe string to hours for comparison."""
        tf = tf_str.strip().lower()
        if tf.endswith("d"):
            return float(tf[:-1]) * 24
        if tf.endswith("h"):
            return float(tf[:-1])
        if tf.endswith("m"):
            return float(tf[:-1]) / 60
        return 0.0

    # Sort by timeframe descending (highest TF first)
    sorted_layers = sorted(layers_with_direction, key=lambda x: _tf_hours(x[0]), reverse=True)
    return sorted_layers[0][1]


# ---------------------------------------------------------------------------
# L3: Layer evaluator + context extraction
# ---------------------------------------------------------------------------

@dataclass
class LayerResult:
    """Result of evaluating a single layer with context extraction."""
    signal_set: SignalSet
    direction: Optional[pd.Series] = None
    price_levels: List[pd.Series] = field(default_factory=list)
    momentum: Optional[pd.Series] = None
    strength: Optional[pd.Series] = None
    volatility: Optional[pd.Series] = None


def _get_all_columns(
    df: pd.DataFrame,
    indicator_name: str,
    params: dict,
) -> List[Tuple[str, pd.Series]]:
    """Extract all output column Series for an indicator from a DataFrame.

    Uses INDICATOR_REGISTRY to determine expected output fields.
    """
    reg = INDICATOR_REGISTRY.get(indicator_name)
    if reg is None:
        return []

    results = []
    for out_field in reg.output_fields:
        # Build a gene-like object for _get_indicator_column
        gene = _SimpleGene(indicator_name, params, out_field)
        try:
            col = _get_indicator_column(df, gene)
            results.append((out_field, col))
        except (ValueError, KeyError):
            continue
    return results


class _SimpleGene:
    """Minimal gene-like object for _get_indicator_column reuse."""
    __slots__ = ("indicator", "params", "field_name")

    def __init__(self, indicator: str, params: dict, field_name: str | None = None):
        self.indicator = indicator
        self.params = params
        self.field_name = field_name


# ---------------------------------------------------------------------------
# Declarative context mapping: category -> context types
# ---------------------------------------------------------------------------

_CONTEXT_SCHEMA: dict[str, set[str]] = {
    "trend": {"direction", "price_levels"},
    "volatility": {"price_levels"},
    "momentum": {"momentum"},
    "volume": {"momentum"},
    "trend_strength": {"momentum"},
    "derivatives": {"momentum"},
    "ml": {"momentum"},
}


def extract_context(
    df: pd.DataFrame,
    gene: SignalGene,
    indicator_category: str,
) -> dict:
    """Extract context information from a signal gene evaluation.

    Uses _CONTEXT_SCHEMA for declarative category-to-context mapping.
    Momentum is normalized per-series to [-1, +1] via rolling min-max.

    Returns dict with:
      - direction: pd.Series (+1/-1) for trend indicators with price conditions
      - price_levels: list of pd.Series for indicators with price-like outputs
      - momentum: pd.Series (normalized) for momentum-category indicators
    """
    ctx: dict = {}
    schema = _CONTEXT_SCHEMA.get(indicator_category, set())
    if not schema:
        return ctx

    close = df["close"]

    # Get indicator column
    try:
        indicator_col = _get_indicator_column(df, gene)
    except (ValueError, KeyError):
        return ctx

    cond_type = gene.condition.get("type", "")

    # Direction extraction for trend indicators with price conditions
    if "direction" in schema and cond_type in ("price_above", "price_below"):
        if cond_type == "price_above":
            ctx["direction"] = pd.Series(
                np.where(close > indicator_col, 1.0, -1.0),
                index=df.index,
            )
        elif cond_type == "price_below":
            ctx["direction"] = pd.Series(
                np.where(close < indicator_col, -1.0, 1.0),
                index=df.index,
            )

    # Price level extraction for indicators with price-like outputs
    if "price_levels" in schema:
        all_cols = _get_all_columns(df, gene.indicator, gene.params)
        price_cols = []
        for name, series in all_cols:
            if name in ("bandwidth", "percent"):
                continue
            price_cols.append(series)
        if price_cols:
            ctx["price_levels"] = price_cols

    # Momentum extraction with per-series normalization to [-1, +1]
    if "momentum" in schema:
        lookback = 100
        rolling_min = indicator_col.rolling(lookback, min_periods=1).min()
        rolling_max = indicator_col.rolling(lookback, min_periods=1).max()
        range_val = (rolling_max - rolling_min).replace(0, 1)
        normalized = (2 * (indicator_col - rolling_min) / range_val - 1).clip(-1, 1)
        ctx["momentum"] = normalized

    return ctx


def resample_values(
    series: pd.Series,
    target_index: pd.DatetimeIndex,
) -> pd.Series:
    """数值上下文投影（0.2.14 asof_completed 收口——第四投影形态）。

    direction/price_levels/momentum 上下文同受纪律约束：高周期 bar T
    的数值在 T+tf（收盘）后可见（现行开盘即见为前视窗口）。薄委托
    mtf_alignment.project_completed（四链同一权威实现）；不做 bool
    转换，首段 NaN 语义与既有形制一致。
    """
    from core.data.mtf_alignment import infer_tf_delta, project_completed

    if series.empty or len(target_index) == 0:
        return pd.Series(np.nan, index=target_index)

    return project_completed(
        series, target_index, infer_tf_delta(series.index))


def evaluate_layer_with_context(
    layer,
    df: pd.DataFrame,
    exec_df_index: pd.DatetimeIndex | None = None,
    dfs_by_timeframe: dict | None = None,
) -> LayerResult:
    """Evaluate a TimeframeLayer and extract context data.

    For structure/zone layers, also extracts direction and price levels.
    For execution layers, returns only the basic signal set.

    dfs_by_timeframe 透传（fix-mtf-execution-consistency R1）：层内带
    基因级周期标注的基因经 evaluate_layer 合流单点需要装载集；ctx
    提取（extract_context）保持层轴不扩（R1-M1 负向回归锚锁定）。
    """
    # Get base signal evaluation
    sig = evaluate_layer(layer, df, dfs_by_timeframe)

    role = layer.role
    if role is None:
        role = "execution"

    direction = None
    price_levels = []
    momentum = None
    strength = None
    volatility = None

    # Extract context from each gene
    for gene in layer.signal_genes:
        reg = INDICATOR_REGISTRY.get(gene.indicator)
        category = reg.category if reg else "unknown"

        ctx = extract_context(df, gene, category)

        if "direction" in ctx:
            raw_dir = ctx["direction"]
            if exec_df_index is not None and role in ("structure", "zone"):
                direction = resample_values(raw_dir, exec_df_index)
            else:
                direction = raw_dir
        if "price_levels" in ctx:
            # Resample price levels to execution timeframe if needed
            if exec_df_index is not None and role in ("structure", "zone"):
                for pl in ctx["price_levels"]:
                    resampled = resample_values(pl, exec_df_index)
                    price_levels.append(resampled)
            else:
                price_levels.extend(ctx["price_levels"])
        if "momentum" in ctx:
            if exec_df_index is not None and role in ("structure", "zone"):
                momentum = resample_values(ctx["momentum"], exec_df_index)
            else:
                momentum = ctx["momentum"]

    return LayerResult(
        signal_set=sig,
        direction=direction,
        price_levels=price_levels,
        momentum=momentum,
        strength=strength,
        volatility=volatility,
    )


# ---------------------------------------------------------------------------
# L4: Cross-layer synthesis + decision gate
# ---------------------------------------------------------------------------

@dataclass
class MTFSynthesis:
    """Cross-layer synthesis scores for MTF decision gating."""
    direction_score: pd.Series      # +1 (bullish) / -1 (bearish) / 0 (neutral)
    confluence_score: pd.Series     # 0.0 ~ 1.0
    momentum_score: pd.Series       # 0.0 ~ 1.0
    strength_multiplier: pd.Series  # 0.0 ~ 1.0+


def synthesize_cross_layer(
    layer_results: List[Tuple[str, LayerResult]],
    exec_index: pd.DatetimeIndex,
    exec_close: pd.Series,
    exec_atr: pd.Series,
    proximity_mult: float,
    dna: StrategyDNA,
) -> MTFSynthesis:
    """Synthesize cross-layer scores from all layer evaluations.

    Steps:
    1. Collect direction from structure layers (resolve conflicts)
    2. Compute confluence score from overlapping price zones
    3. Aggregate momentum from zone/structure layers
    4. Compute strength multiplier from zone layer volatility
    """
    n = len(exec_index)

    # Default scores
    direction_score = pd.Series(0.0, index=exec_index)
    confluence_score = pd.Series(0.0, index=exec_index)
    momentum_score = pd.Series(0.0, index=exec_index)
    strength_multiplier = pd.Series(1.0, index=exec_index)

    # Collect context by type
    structure_directions = []
    all_price_zones = []  # [(tf, [zones_per_bar])]
    zone_momentums = []

    for tf, lr in layer_results:
        role = None
        # Determine role from timeframe
        for layer in (dna.layers or []):
            if layer.timeframe == tf:
                role = layer.role
                break
        if role is None:
            role = derive_role(tf)

        if lr.direction is not None:
            structure_directions.append((tf, lr.direction))

    # 1. Direction: resolve from structure layers or DNA default
    if structure_directions:
        direction_score = resolve_direction_conflict(structure_directions)
    else:
        # No structure layers -> inherit from DNA direction
        if dna.risk_genes.direction == "long":
            direction_score = pd.Series(1.0, index=exec_index)
        elif dna.risk_genes.direction == "short":
            direction_score = pd.Series(-1.0, index=exec_index)
        else:
            direction_score = pd.Series(0.0, index=exec_index)

    # 2. Confluence: compute per-bar from price level zones
    # Collect non-execution layers' price levels
    non_exec_price_levels = []
    for tf, lr in layer_results:
        role = None
        for layer in (dna.layers or []):
            if layer.timeframe == tf:
                role = layer.role
                break
        if role is None:
            role = derive_role(tf)
        if role in ("structure", "zone") and lr.price_levels:
            non_exec_price_levels.append((tf, lr.price_levels))

    if len(non_exec_price_levels) >= 2:
        # Per-bar confluence computation with pre-extracted numpy arrays
        scores = np.zeros(n)
        exec_atr_vals = exec_atr.values
        exec_close_vals = exec_close.values
        # Pre-extract level numpy arrays to avoid per-bar .iloc[] overhead
        level_arrays_by_tf = []
        for tf, levels in non_exec_price_levels:
            level_arrays_by_tf.append([lv.values for lv in levels])

        for bar_idx in range(n):
            s_pct = compute_s_pct(exec_atr_vals[bar_idx], exec_close_vals[bar_idx], proximity_mult)
            layer_zones = []
            for level_vals_list in level_arrays_by_tf:
                zones = []
                for lv in level_vals_list:
                    if bar_idx < len(lv) and not np.isnan(lv[bar_idx]):
                        zone = build_price_zone(lv[bar_idx], s_pct)
                        zones.append(zone)
                if zones:
                    layer_zones.append(zones)
            scores[bar_idx] = compute_confluence_score(
                layer_zones, exec_close_vals[bar_idx],
                max_zone_width=exec_close_vals[bar_idx] * s_pct * 2 if s_pct > 0 else 1.0,
            )
        confluence_score = pd.Series(scores, index=exec_index)
    elif len(non_exec_price_levels) == 1:
        # Single non-exec layer: proximity score fallback
        tf, levels = non_exec_price_levels[0]
        s_pct_avg = (exec_atr / exec_close * proximity_mult).mean()
        if s_pct_avg > 0 and levels:
            confluence_score = compute_proximity_score(levels, exec_close, s_pct_avg)

    # 3. Momentum: average across zone/structure layers
    momentum_values = []
    non_exec_momenta = []  # momentum from structure/zone layers only
    for tf, lr in layer_results:
        role = None
        for layer in (dna.layers or []):
            if layer.timeframe == tf:
                role = layer.role
                break
        if role is None:
            role = derive_role(tf)
        if lr.momentum is not None:
            momentum_values.append(lr.momentum)
            if role in ("structure", "zone"):
                non_exec_momenta.append(lr.momentum)
    if momentum_values:
        # Normalize momentum to 0-1 range (simple approach)
        combined = pd.concat(momentum_values, axis=1).mean(axis=1)
        # Simple sigmoid-like normalization
        abs_max = combined.abs().max()
        if abs_max > 0:
            momentum_score = (combined / abs_max * 0.5 + 0.5).clip(0, 1)
        else:
            momentum_score = pd.Series(0.5, index=exec_index)
    else:
        momentum_score = pd.Series(0.5, index=exec_index)

    # 2b. Momentum confluence fallback (C1 fix):
    # When price confluence is 0 because structure/zone layers lack price_levels,
    # use momentum directional agreement as confluence score.
    if confluence_score.eq(0.0).all() and len(non_exec_momenta) >= 2:
        mom_matrix = np.column_stack([
            m.values[:n] for m in non_exec_momenta
        ])
        valid_mask = ~np.isnan(mom_matrix)
        valid_counts = valid_mask.sum(axis=1).astype(float)

        pos_counts = np.where(valid_mask, mom_matrix > 0, False).sum(axis=1).astype(float)
        neg_counts = np.where(valid_mask, mom_matrix < 0, False).sum(axis=1).astype(float)
        majority = np.maximum(pos_counts, neg_counts)

        agreement = np.where(valid_counts >= 2, majority / valid_counts, 0.0)

        # Scale: full agreement=1.0, bare majority=0.3
        momentum_confs = np.where(
            (agreement > 0.5) & (valid_counts >= 2),
            0.3 + 0.7 * (agreement - 0.5) / 0.5,
            0.0,
        )
        confluence_score = pd.Series(momentum_confs, index=exec_index)
    elif confluence_score.eq(0.0).all() and len(non_exec_momenta) == 1:
        # Single non-exec layer with momentum only:
        # Use momentum strength as confluence proxy (vectorized).
        mom_series = non_exec_momenta[0].values[:n]
        mom_abs_max = np.nanmax(np.abs(mom_series))
        if mom_abs_max > 0:
            normalized = np.abs(mom_series) / mom_abs_max
            direction = dna.risk_genes.direction
            if direction == "long":
                direction_mask = mom_series > 0
                scale = 0.5
            elif direction == "short":
                direction_mask = mom_series < 0
                scale = 0.5
            else:
                direction_mask = mom_series != 0
                scale = 0.3
            not_nan = ~np.isnan(mom_series)
            single_mom_confs = np.where(
                direction_mask & not_nan,
                normalized * scale,
                0.0,
            )
            confluence_score = pd.Series(single_mom_confs, index=exec_index)

    return MTFSynthesis(
        direction_score=direction_score,
        confluence_score=confluence_score,
        momentum_score=momentum_score,
        strength_multiplier=strength_multiplier,
    )


def _compute_confidence(synthesis: MTFSynthesis) -> pd.Series:
    """Compute blended confidence from MTF synthesis scores.

    Formula:
        confidence = confluence * 0.5 + momentum * 0.3 + |direction| * 0.2
        Clipped to [0.1, 1.0].

    Args:
        synthesis: MTFSynthesis with per-bar scores.

    Returns:
        pd.Series of confidence values clipped to [0.1, 1.0].
    """
    import numpy as np

    confidence = (
        synthesis.confluence_score * 0.5
        + synthesis.momentum_score * 0.3
        + synthesis.direction_score.abs() * 0.2
    )
    return confidence.clip(0.1, 1.0)


def apply_decision_gate(
    exec_signal_set: SignalSet,
    synthesis: MTFSynthesis,
    dna: StrategyDNA,
) -> SignalSet:
    """Apply MTF decision gate to filter execution-layer signals.

    Entry: requires timing_signal AND direction_match AND confluence >= threshold
    Exit: not filtered (risk management should not be blocked)
    Add: requires confluence >= threshold * 0.8
    Reduce: not filtered

    Controlled by mtf_mode:
      None: no gating (backward compatible)
      "direction": only direction filter
      "confluence": only confluence filter
      "direction+confluence": both filters
    """
    mtf_mode = dna.mtf_mode
    threshold = dna.confluence_threshold

    entries = exec_signal_set.entries.copy()
    exits = exec_signal_set.exits.copy()
    adds = exec_signal_set.adds.copy()
    reduces = exec_signal_set.reduces.copy()

    if mtf_mode is None:
        # No gating
        return SignalSet(
            entries=entries,
            exits=exits,
            adds=adds,
            reduces=reduces,
            degraded_layers=exec_signal_set.degraded_layers,
            entry_direction=exec_signal_set.entry_direction,
            mtf_diagnostics={
                "direction_score": synthesis.direction_score,
                "confluence_score": synthesis.confluence_score,
                "momentum_score": synthesis.momentum_score,
                "strength_multiplier": synthesis.strength_multiplier,
                "mtf_mode": None,
            },
            confidence=_compute_confidence(synthesis),
        )

    # Direction gate
    direction_pass = pd.Series(True, index=entries.index)
    if mtf_mode in ("direction", "direction+confluence"):
        if dna.risk_genes.direction == "long":
            direction_pass = synthesis.direction_score > 0
        elif dna.risk_genes.direction == "short":
            direction_pass = synthesis.direction_score < 0
        else:
            # mixed: direction must be non-zero (not neutral)
            direction_pass = synthesis.direction_score != 0

    # Confluence gate
    confluence_pass = pd.Series(True, index=entries.index)
    if mtf_mode in ("confluence", "direction+confluence"):
        confluence_pass = synthesis.confluence_score >= threshold

    # Apply gates to entries
    entries = entries & direction_pass & confluence_pass

    # Add signals require relaxed confluence (80% of threshold)
    add_confluence_pass = pd.Series(True, index=adds.index)
    if mtf_mode in ("confluence", "direction+confluence"):
        add_confluence_pass = synthesis.confluence_score >= threshold * 0.8
    adds = adds & add_confluence_pass

    # Exits and reduces are not filtered

    return SignalSet(
        entries=entries,
        exits=exits,
        adds=adds,
        reduces=reduces,
        degraded_layers=exec_signal_set.degraded_layers,
        entry_direction=exec_signal_set.entry_direction,
        mtf_diagnostics={
            "direction_score": synthesis.direction_score,
            "confluence_score": synthesis.confluence_score,
            "momentum_score": synthesis.momentum_score,
            "strength_multiplier": synthesis.strength_multiplier,
            "mtf_mode": mtf_mode,
        },
        confidence=_compute_confidence(synthesis),
    )


def run_mtf_engine(
    dna: StrategyDNA,
    dfs_by_timeframe: dict,
    enhanced_df: pd.DataFrame,
) -> SignalSet:
    """Main entry point for MTF engine.

    Replaces the legacy AND/OR cross-layer logic with the new
    multi-dimensional score-gating system.
    """
    exec_tf = dna.execution_genes.timeframe
    # Use the execution timeframe's DataFrame as the base
    exec_df = dfs_by_timeframe.get(exec_tf, enhanced_df)

    # Evaluate each layer with context extraction
    layer_results: List[Tuple[str, LayerResult]] = []
    degraded_count = 0

    for layer in (dna.layers or []):
        layer_df = dfs_by_timeframe.get(layer.timeframe)
        if layer_df is None:
            degraded_count += 1
            continue

        role = layer.role
        if role is None:
            role = derive_role(layer.timeframe)

        # For non-execution layers, pass exec index for resampling
        exec_index = exec_df.index if layer.timeframe != exec_tf else None
        lr = evaluate_layer_with_context(
            layer, layer_df, exec_df.index, dfs_by_timeframe)
        layer_results.append((layer.timeframe, lr))

    # Get execution layer signals
    exec_signal_set = _build_exec_signal_set(
        layer_results, dna, exec_df,
    )

    # Synthesize cross-layer scores
    exec_close = exec_df["close"]
    exec_atr = _get_exec_atr(exec_df)

    synthesis = synthesize_cross_layer(
        layer_results, exec_df.index, exec_close, exec_atr,
        dna.proximity_mult, dna,
    )

    # Apply decision gate
    result = apply_decision_gate(exec_signal_set, synthesis, dna)
    result.degraded_layers = degraded_count

    # Set entry_direction from synthesis
    result.entry_direction = synthesis.direction_score

    return result


def _build_exec_signal_set(
    layer_results: List[Tuple[str, LayerResult]],
    dna: StrategyDNA,
    exec_df: pd.DataFrame,
) -> SignalSet:
    """Build combined signal set from all layer results.

    Structure/zone layers provide state signals (ffilled).
    Execution layers provide pulse signals.
    """
    all_entries = []
    all_exits = []
    all_adds = []
    all_reduces = []

    for tf, lr in layer_results:
        role = None
        for layer in (dna.layers or []):
            if layer.timeframe == tf:
                role = layer.role
                break
        if role is None:
            role = derive_role(tf)

        # Resample signals to execution timeframe
        sig = lr.signal_set
        if tf != dna.execution_genes.timeframe:
            entries = resample_signals(sig.entries, exec_df.index)
            exits = resample_signals(sig.exits, exec_df.index)
            adds = resample_signals(sig.adds, exec_df.index)
            reduces = resample_signals(sig.reduces, exec_df.index)
        else:
            entries = sig.entries.reindex(exec_df.index, fill_value=False)
            exits = sig.exits.reindex(exec_df.index, fill_value=False)
            adds = sig.adds.reindex(exec_df.index, fill_value=False)
            reduces = sig.reduces.reindex(exec_df.index, fill_value=False)

        all_entries.append(entries)
        all_exits.append(exits)
        all_adds.append(adds)
        all_reduces.append(reduces)

    if not all_entries:
        return SignalSet(
            entries=pd.Series(False, index=exec_df.index),
            exits=pd.Series(False, index=exec_df.index),
            adds=pd.Series(False, index=exec_df.index),
            reduces=pd.Series(False, index=exec_df.index),
        )

    # Combine: AND across all layers for entries (strict)
    combined_entries = combine_signals(all_entries, "AND")
    combined_exits = combine_signals(all_exits, "OR")
    combined_adds = combine_signals(all_adds, "OR")
    combined_reduces = combine_signals(all_reduces, "OR")

    # Prevent simultaneous entry+exit
    both = combined_entries & combined_exits
    combined_entries = combined_entries & ~both

    return SignalSet(
        entries=combined_entries,
        exits=combined_exits,
        adds=combined_adds,
        reduces=combined_reduces,
    )


def _get_exec_atr(exec_df: pd.DataFrame) -> pd.Series:
    """Get ATR series from execution DataFrame, or compute from price."""
    # Try to find ATR column
    for col in exec_df.columns:
        if col.startswith("atr_"):
            return exec_df[col]
    # Fallback: compute simple ATR-like volatility
    if all(c in exec_df.columns for c in ("high", "low", "close")):
        tr = pd.concat([
            exec_df["high"] - exec_df["low"],
            (exec_df["high"] - exec_df["close"].shift(1)).abs(),
            (exec_df["low"] - exec_df["close"].shift(1)).abs(),
        ], axis=1).max(axis=1)
        return tr.rolling(14, min_periods=1).mean().fillna(exec_df["close"] * 0.02)
    # Final fallback
    return exec_df["close"] * 0.02
