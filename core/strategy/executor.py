"""DNA to vectorbt signal conversion - the critical bridge module."""

from __future__ import annotations

import logging
from dataclasses import dataclass

import numpy as np
import pandas as pd

from core.strategy.dna import StrategyDNA, SignalRole
from core.features.registry import INDICATOR_REGISTRY, resolve_indicator_column

logger = logging.getLogger(__name__)


@dataclass
class SignalSet:
    """Complete set of trading signals for a strategy."""

    entries: pd.Series     # open position signals
    exits: pd.Series       # close position signals
    adds: pd.Series        # add to position signals
    reduces: pd.Series     # reduce position signals
    degraded_layers: int = 0    # number of MTF layers skipped due to missing data
    entry_direction: pd.Series | None = None  # +1 long, -1 short (for mixed direction)
    mtf_diagnostics: dict | None = None  # MTF engine diagnostic scores
    confidence: pd.Series | None = None  # MTF confidence [0.1, 1.0] per bar


def evaluate_condition(
    indicator_series: pd.Series,
    close_series: pd.Series,
    condition: dict,
    df: pd.DataFrame | None = None,
) -> pd.Series:
    """Convert a structured condition dict to a boolean Series.

    Args:
        indicator_series: The indicator values.
        close_series: The close price series.
        condition: Dict with "type" and optional "threshold".
        df: Full enhanced DataFrame (needed for cross_above_series,
            lookback_any/all, touch_bounce, role_reversal, wick_touch).

    Returns:
        Boolean Series with same index as input.
    """
    cond_type = condition["type"]

    if cond_type == "eq":
        threshold = condition.get("threshold", 1)
        return indicator_series == threshold
    elif cond_type == "lt":
        return indicator_series < condition["threshold"]
    elif cond_type == "gt":
        return indicator_series > condition["threshold"]
    elif cond_type == "le":
        return indicator_series <= condition["threshold"]
    elif cond_type == "ge":
        return indicator_series >= condition["threshold"]
    elif cond_type == "cross_above":
        threshold = condition.get("threshold")
        if threshold is not None:
            return (indicator_series.shift(1) < threshold) & (indicator_series >= threshold)
        else:
            return pd.Series(False, index=indicator_series.index)
    elif cond_type == "cross_below":
        threshold = condition.get("threshold")
        if threshold is not None:
            return (indicator_series.shift(1) > threshold) & (indicator_series <= threshold)
        else:
            return pd.Series(False, index=indicator_series.index)
    elif cond_type == "price_above":
        return close_series > indicator_series
    elif cond_type == "price_below":
        return close_series < indicator_series
    # ── Phase 2: dynamic conditions ──
    elif cond_type == "cross_above_series":
        target_series = _resolve_target_indicator_series(condition, df)
        if target_series is None:
            return pd.Series(False, index=indicator_series.index)
        return (indicator_series.shift(1) < target_series.shift(1)) & (indicator_series >= target_series)
    elif cond_type == "cross_below_series":
        target_series = _resolve_target_indicator_series(condition, df)
        if target_series is None:
            return pd.Series(False, index=indicator_series.index)
        return (indicator_series.shift(1) > target_series.shift(1)) & (indicator_series <= target_series)
    elif cond_type == "lookback_any":
        window = condition.get("window", 5)
        inner = condition.get("inner", {})
        inner_signal = _evaluate_inner_condition(indicator_series, close_series, inner, df)
        return (inner_signal.astype(float).rolling(window=window, min_periods=1).sum() > 0).fillna(False).astype(bool)
    elif cond_type == "lookback_all":
        window = condition.get("window", 5)
        inner = condition.get("inner", {})
        inner_signal = _evaluate_inner_condition(indicator_series, close_series, inner, df)
        return (inner_signal.astype(float).rolling(window=window, min_periods=window).sum() == window).fillna(False).astype(bool)
    # ── Phase 4: support/resistance conditions ──
    elif cond_type == "touch_bounce":
        return _eval_touch_bounce(indicator_series, close_series, df, condition)
    elif cond_type == "role_reversal":
        return _eval_role_reversal(indicator_series, close_series, df, condition)
    elif cond_type == "wick_touch":
        return _eval_wick_touch(indicator_series, close_series, df, condition)
    else:
        return pd.Series(False, index=indicator_series.index)


def _resolve_target_indicator_series(condition: dict, df: pd.DataFrame | None) -> pd.Series | None:
    """Resolve target_indicator + target_params to a Series from df."""
    if df is None:
        return None
    target_name = condition.get("target_indicator", "")
    target_params = condition.get("target_params", {})
    # Build a minimal gene-like object to reuse _get_indicator_column
    gene = _SimpleGene(target_name, target_params)
    try:
        return _get_indicator_column(df, gene)
    except ValueError:
        return None


def _evaluate_inner_condition(
    indicator_series: pd.Series,
    close_series: pd.Series,
    inner: dict,
    df: pd.DataFrame | None,
) -> pd.Series:
    """Evaluate an inner condition for lookback window types."""
    inner_type = inner.get("type", "")
    if inner_type in ("lt", "gt", "le", "ge"):
        threshold = inner.get("threshold", 0)
        if inner_type == "lt":
            return indicator_series < threshold
        elif inner_type == "gt":
            return indicator_series > threshold
        elif inner_type == "le":
            return indicator_series <= threshold
        elif inner_type == "ge":
            return indicator_series >= threshold
    elif inner_type == "price_above":
        return close_series > indicator_series
    elif inner_type == "price_below":
        return close_series < indicator_series
    # For complex inner conditions, delegate to evaluate_condition
    return evaluate_condition(indicator_series, close_series, inner, df)


def _eval_touch_bounce(
    indicator_series: pd.Series,
    close_series: pd.Series,
    df: pd.DataFrame | None,
    condition: dict,
) -> pd.Series:
    """Touch-bounce: price touches indicator line then bounces.

    direction="support": low near line + close above line + next close higher
    direction="resistance": high near line + close below line + next close lower
    """
    if df is None:
        return pd.Series(False, index=indicator_series.index)

    direction = condition.get("direction", "support")
    proximity_pct = condition.get("proximity_pct", 0.01)

    low = df["low"]
    high = df["high"]
    line = indicator_series

    # Proximity check using only past/current data (no shift(-1))
    if direction == "support":
        proximity = (low - line).abs() <= line.abs() * proximity_pct
        above_line = close_series > line
        bounce_up = close_series > close_series.shift(1)  # current close > previous close
        result = proximity & above_line & bounce_up
    else:
        proximity = (high - line).abs() <= line.abs() * proximity_pct
        below_line = close_series < line
        bounce_down = close_series < close_series.shift(1)  # current close < previous close
        result = proximity & below_line & bounce_down

    return result.fillna(False)


def _eval_role_reversal(
    indicator_series: pd.Series,
    close_series: pd.Series,
    df: pd.DataFrame | None,
    condition: dict,
) -> pd.Series:
    """Role reversal: indicator line switches from support to resistance or vice versa.

    role="support": recently crossed above + currently above
    role="resistance": recently crossed below + currently below
    """
    if df is None:
        return pd.Series(False, index=indicator_series.index)

    role = condition.get("role", "resistance")
    lookback = condition.get("lookback", 10)

    line = indicator_series

    if role == "support":
        # Line was resistance (price was below), now price is above
        was_below = close_series.shift(lookback) < line.shift(lookback)
        now_above = close_series > line
        result = was_below & now_above
    else:
        # Line was support (price was above), now price is below
        was_above = close_series.shift(lookback) > line.shift(lookback)
        now_below = close_series < line
        result = was_above & now_below

    return result.fillna(False)


def _eval_wick_touch(
    indicator_series: pd.Series,
    close_series: pd.Series,
    df: pd.DataFrame | None,
    condition: dict,
) -> pd.Series:
    """Wick touch: wick touches indicator line but close is on the other side.

    direction="above": high near line + close below line
    direction="below": low near line + close above line
    """
    if df is None:
        return pd.Series(False, index=indicator_series.index)

    direction = condition.get("direction", "above")
    proximity_pct = condition.get("proximity_pct", 0.01)

    low = df["low"]
    high = df["high"]
    line = indicator_series

    if direction == "above":
        wick_near = (high - line).abs() <= line.abs() * proximity_pct
        close_below = close_series < line
        result = wick_near & close_below
    else:
        wick_near = (low - line).abs() <= line.abs() * proximity_pct
        close_above = close_series > line
        result = wick_near & close_above

    return result.fillna(False)


class _SimpleGene:
    """Minimal gene-like object for _get_indicator_column reuse."""
    __slots__ = ("indicator", "params", "field_name")

    def __init__(self, indicator: str, params: dict, field_name: str | None = None):
        self.indicator = indicator
        self.params = params
        self.field_name = field_name


def _empty_signal_set(df: pd.DataFrame) -> SignalSet:
    """Return an all-False SignalSet matching the DataFrame index."""
    return SignalSet(
        entries=pd.Series(False, index=df.index),
        exits=pd.Series(False, index=df.index),
        adds=pd.Series(False, index=df.index),
        reduces=pd.Series(False, index=df.index),
    )


def combine_signals(signal_list: list[pd.Series], logic: str) -> pd.Series:
    """Combine multiple boolean Series with AND or OR logic."""
    if not signal_list:
        return pd.Series(False, index=signal_list[0].index if signal_list else [])

    if logic == "AND":
        result = signal_list[0].copy()
        for s in signal_list[1:]:
            result = result & s
        return result
    elif logic == "OR":
        result = signal_list[0].copy()
        for s in signal_list[1:]:
            result = result | s
        return result
    else:
        return signal_list[0].copy()


_indicator_column_cache: dict = {}


def _get_indicator_column(df: pd.DataFrame, gene, _cache=None) -> pd.Series:
    """Resolve an indicator column from the enhanced DataFrame.

    Tries exact name match first, then prefix-based search.
    Results are cached for repeated lookups within the same generation.
    """
    cache = _cache if _cache is not None else _indicator_column_cache
    cache_key = (id(df), gene.indicator,
                 tuple(sorted(gene.params.items())),
                 gene.field_name)
    if cache_key in cache:
        return cache[cache_key]

    indicator = gene.indicator
    params = gene.params

    # Resolve column name via registry
    reg = INDICATOR_REGISTRY.get(indicator)
    if reg is not None:
        col = resolve_indicator_column(
            indicator, params, gene.field_name or "", reg.naming
        )
    else:
        # Fallback for unknown indicators
        matches = [c for c in df.columns if c.lower().startswith(indicator.lower())]
        if matches:
            col = matches[0]
        else:
            raise ValueError(f"Cannot find column for indicator {indicator}")

    if col in df.columns:
        result = df[col]
        cache[cache_key] = result
        return result

    # Fallback: prefix match
    matches = [c for c in df.columns if col in c]
    if matches:
        result = df[matches[0]]
        cache[cache_key] = result
        return result

    # On-demand compute: if the indicator is registered but the column was not
    # pre-computed (e.g. mutation produced non-default params), compute it now.
    if reg is not None:
        from core.features.indicators import _compute_indicator
        new_cols = _compute_indicator(df, indicator, params)
        if new_cols:
            # 一次整块写回（fix-executor-batch-health）：逐列 insert 在宽
            # enhanced_df（>100 列）上触发 pandas PerformanceWarning 且插入
            # 成本线性上升——攒齐 new_cols 整块赋值，列集合与值不变；
            # 极短数据下部分输出列可能为 None（原逐列写回同样容忍），
            # 以 None 列写入保持列集合不变
            df[list(new_cols.keys())] = pd.DataFrame(
                {name: (series if series is not None else None)
                 for name, series in new_cols.items()},
                index=df.index)
            # Retry lookup after writing computed columns
            if col in df.columns:
                result = df[col]
                cache[cache_key] = result
                return result
            # Column name mismatch — try matching by output
            for name in new_cols:
                if col in name or name in col:
                    result = df[name]
                    cache[cache_key] = result
                    return result

    raise ValueError(f"Column '{col}' not found in DataFrame. Available: {list(df.columns)}")


def clear_indicator_cache() -> None:
    """Clear the indicator column cache.

    Should be called at the start of each generation to avoid stale references.
    """
    _indicator_column_cache.clear()


def evaluate_layer(
    layer,
    df: pd.DataFrame,
) -> SignalSet:
    """Evaluate a single TimeframeLayer on a pre-computed DataFrame.

    Returns SignalSet with entries, exits, adds, reduces.
    """
    close = df["close"]
    entry_triggers, entry_guards = [], []
    exit_triggers, exit_guards = [], []
    add_triggers, add_guards = [], []
    reduce_triggers, reduce_guards = [], []
    direction_genes = []

    for gene in layer.signal_genes:
        try:
            indicator_col = _get_indicator_column(df, gene)
            signal = evaluate_condition(indicator_col, close, gene.condition, df=df)
            signal = signal.fillna(False)

            if gene.role == SignalRole.ENTRY_TRIGGER:
                entry_triggers.append(signal)
            elif gene.role == SignalRole.ENTRY_GUARD:
                entry_guards.append(signal)
            elif gene.role == SignalRole.EXIT_TRIGGER:
                exit_triggers.append(signal)
            elif gene.role == SignalRole.EXIT_GUARD:
                exit_guards.append(signal)
            elif gene.role == SignalRole.ADD_TRIGGER:
                add_triggers.append(signal)
            elif gene.role == SignalRole.ADD_GUARD:
                add_guards.append(signal)
            elif gene.role == SignalRole.REDUCE_TRIGGER:
                reduce_triggers.append(signal)
            elif gene.role == SignalRole.REDUCE_GUARD:
                reduce_guards.append(signal)
            elif gene.role == SignalRole.DIRECTION:
                gene_direction = pd.Series(
                    np.where(signal, 1.0, -1.0),
                    index=df.index,
                )
                direction_genes.append(gene_direction)
        except ValueError:
            continue

    all_entry = entry_triggers + entry_guards
    entries = combine_signals(all_entry, layer.logic_genes.entry_logic) if all_entry else pd.Series(False, index=df.index)

    all_exit = exit_triggers + exit_guards
    exits = combine_signals(all_exit, layer.logic_genes.exit_logic) if all_exit else pd.Series(False, index=df.index)

    logic = layer.logic_genes
    add_logic = getattr(logic, 'add_logic', 'AND') or 'AND'
    reduce_logic = getattr(logic, 'reduce_logic', 'AND') or 'AND'

    all_add = add_triggers + add_guards
    adds = combine_signals(all_add, add_logic) if all_add else pd.Series(False, index=df.index)

    all_reduce = reduce_triggers + reduce_guards
    reduces = combine_signals(all_reduce, reduce_logic) if all_reduce else pd.Series(False, index=df.index)

    # Prevent simultaneous entry+exit (favor exit)
    both = entries & exits
    entries = entries & ~both

    # Build entry_direction from DIRECTION genes if present
    entry_direction = direction_genes[0] if direction_genes else None

    return SignalSet(
        entries=entries,
        exits=exits,
        adds=adds,
        reduces=reduces,
        entry_direction=entry_direction,
    )


def resample_signals(
    high_tf_signals: pd.Series,
    target_index: pd.DatetimeIndex,
) -> pd.Series:
    """高周期信号投影到执行周期索引（0.2.14 asof_completed 收口）。

    高周期 bar T 的信号在 T+tf（收盘时刻）后才对基准可见——现行
    开盘即见 ffill 为前视窗口（bar T 开盘即见 T 收盘才知道的信号）。
    薄委托 mtf_alignment.project_completed_bool（四链同一权威实现）；
    tf 由信号索引差分中位数推断（infer_tf_delta——对缺口鲁棒，缺口
    上保守方向无前视）。收口 = 回测对齐实盘 ``_filter_forming_bar``
    的时间语义（实盘每时刻丢形成中 bar，shift(1) 与现行 ffill 在实盘
    时序下同值——实盘/模拟盘结果不变）。
    """
    from core.data.mtf_alignment import (
        infer_tf_delta, project_completed_bool,
    )

    if high_tf_signals.empty or len(target_index) == 0:
        return pd.Series(False, index=target_index)

    return project_completed_bool(
        high_tf_signals, target_index,
        infer_tf_delta(high_tf_signals.index))


def dna_to_signals(
    dna: StrategyDNA,
    enhanced_df: pd.DataFrame,
    dfs_by_timeframe: dict | None = None,
) -> tuple[pd.Series, pd.Series]:
    """Convert a StrategyDNA to entry/exit boolean Series.

    Returns (entries, exits) for backward compatibility.
    Use dna_to_signal_set() for full SignalSet with adds/reduces.
    """
    sig_set = dna_to_signal_set(dna, enhanced_df, dfs_by_timeframe)
    return sig_set.entries, sig_set.exits


def dna_to_signal_set(
    dna: StrategyDNA,
    enhanced_df: pd.DataFrame,
    dfs_by_timeframe: dict | None = None,
) -> SignalSet:
    """Convert a StrategyDNA to a full SignalSet with adds/reduces."""
    # Clear the module-level indicator column cache at entry. It is keyed by
    # id(df) and never auto-cleared, so without this, leftover Series from a
    # previous call/test leak across invocations — and Python id reuse can make
    # a freshly-built df hit a stale key, returning wrong indicator columns.
    clear_indicator_cache()
    # MTF mode with role-aware signal combination
    if dna.is_mtf and dfs_by_timeframe is not None:
        # New MTF engine path: activated when mtf_mode is set
        if dna.mtf_mode is not None:
            from core.strategy.mtf_engine import run_mtf_engine
            return run_mtf_engine(dna, dfs_by_timeframe, enhanced_df)
        trend_entries = []
        trend_exits = []
        exec_entries = []
        exec_exits = []
        exec_adds = []
        exec_reduces = []

        # Legacy: also track all signals for non-role-aware combination
        all_layer_entries = []
        all_layer_exits = []
        all_layer_adds = []
        all_layer_reduces = []

        # Track trend direction for mixed strategies
        trend_direction_series = []

        has_any_role = any(layer.role is not None for layer in dna.layers)

        degraded_count = 0
        for layer in dna.layers:
            layer_df = dfs_by_timeframe.get(layer.timeframe)
            if layer_df is None:
                logger.error(
                    "MTF layer timeframe %r data not found in dfs_by_timeframe, "
                    "skipping layer. Available: %s",
                    layer.timeframe, list(dfs_by_timeframe.keys()),
                )
                degraded_count += 1
                continue

            sig = evaluate_layer(layer, layer_df)
            layer_role = layer.role  # None means "execution" default
            exec_tf = dna.execution_genes.timeframe
            is_cross_tf = layer.timeframe != exec_tf

            if is_cross_tf:
                resampled_entries = resample_signals(sig.entries, enhanced_df.index)
                resampled_exits = resample_signals(sig.exits, enhanced_df.index)
                resampled_adds = resample_signals(sig.adds, enhanced_df.index)
                resampled_reduces = resample_signals(sig.reduces, enhanced_df.index)

                # 0.2.14：脉冲投影收口 pulse_completed（触发时间戳 +tf
                # 平移——落在收盘时刻所在基准 bar；_resample_pulse 退役，
                # 末 bar 硬编码 1 天窗口缺陷一并消除）
                from core.data.mtf_alignment import infer_tf_delta, pulse_completed
                _layer_delta = infer_tf_delta(layer_df.index)
                pulse_entries = pulse_completed(sig.entries, enhanced_df.index, _layer_delta)
                pulse_exits = pulse_completed(sig.exits, enhanced_df.index, _layer_delta)
                pulse_adds = pulse_completed(sig.adds, enhanced_df.index, _layer_delta)
                pulse_reduces = pulse_completed(sig.reduces, enhanced_df.index, _layer_delta)
            else:
                resampled_entries = sig.entries.reindex(enhanced_df.index, fill_value=False)
                resampled_exits = sig.exits.reindex(enhanced_df.index, fill_value=False)
                resampled_adds = sig.adds.reindex(enhanced_df.index, fill_value=False)
                resampled_reduces = sig.reduces.reindex(enhanced_df.index, fill_value=False)

                pulse_entries = resampled_entries
                pulse_exits = resampled_exits
                pulse_adds = resampled_adds
                pulse_reduces = resampled_reduces

            # Always add to legacy all_layer lists for backward compat
            all_layer_entries.append(resampled_entries)
            all_layer_exits.append(resampled_exits)
            all_layer_adds.append(resampled_adds)
            all_layer_reduces.append(resampled_reduces)

            if layer_role in ("trend", "structure", "zone"):
                # Trend/structure/zone layers: forward-filled state signals
                trend_entries.append(resampled_entries)
                trend_exits.append(resampled_exits)

                # Determine direction from trend layer's condition types
                entry_triggers = [g for g in layer.signal_genes
                                  if g.role == SignalRole.ENTRY_TRIGGER]
                direction = pd.Series(1.0, index=enhanced_df.index)
                for gene in entry_triggers:
                    cond_type = gene.condition.get("type", "")
                    if cond_type == "price_above":
                        # price_above=True -> close > indicator -> bullish -> +1
                        direction = pd.Series(
                            np.where(resampled_entries, 1.0, -1.0),
                            index=enhanced_df.index,
                        )
                        break
                    elif cond_type == "price_below":
                        # price_below=True -> close < indicator -> bearish -> -1
                        direction = pd.Series(
                            np.where(resampled_entries, -1.0, 1.0),
                            index=enhanced_df.index,
                        )
                        break
                trend_direction_series.append(direction)
            else:
                # Execution layers (default): pulse signals (no ffill)
                exec_entries.append(pulse_entries)
                exec_exits.append(pulse_exits)
                exec_adds.append(pulse_adds)
                exec_reduces.append(pulse_reduces)

        # Combine signals
        if has_any_role:
            # Role-aware combination: respect cross_layer_logic
            logic = dna.cross_layer_logic or "AND"
            if trend_entries and exec_entries:
                trend_gate = combine_signals(trend_entries, "AND")
                exec_trigger = combine_signals(exec_entries, "OR")
                if logic == "OR":
                    combined_entries = trend_gate | exec_trigger
                else:
                    combined_entries = trend_gate & exec_trigger
            elif exec_entries:
                combined_entries = combine_signals(exec_entries, "OR")
            elif trend_entries:
                combined_entries = combine_signals(trend_entries, "AND")
            else:
                combined_entries = pd.Series(False, index=enhanced_df.index)

            combined_exits = combine_signals(exec_exits, "OR") if exec_exits else pd.Series(False, index=enhanced_df.index)
            if trend_exits:
                trend_exit_signal = combine_signals(trend_exits, "OR")
                combined_exits = combined_exits | trend_exit_signal
            combined_adds = combine_signals(exec_adds, "OR") if exec_adds else pd.Series(False, index=enhanced_df.index)
            combined_reduces = combine_signals(exec_reduces, "OR") if exec_reduces else pd.Series(False, index=enhanced_df.index)
        else:
            # Legacy behavior: no roles defined, use cross_layer_logic on all
            if not all_layer_entries:
                return SignalSet(
                    entries=pd.Series(False, index=enhanced_df.index),
                    exits=pd.Series(False, index=enhanced_df.index),
                    adds=pd.Series(False, index=enhanced_df.index),
                    reduces=pd.Series(False, index=enhanced_df.index),
                    degraded_layers=degraded_count,
                )
            combined_entries = combine_signals(all_layer_entries, dna.cross_layer_logic)
            combined_exits = combine_signals(all_layer_exits, dna.cross_layer_logic)
            combined_adds = combine_signals(all_layer_adds, dna.cross_layer_logic)
            combined_reduces = combine_signals(all_layer_reduces, dna.cross_layer_logic)

        both = combined_entries & combined_exits
        combined_entries = combined_entries & ~both

        # Build entry_direction from trend layers (for mixed direction support)
        entry_direction = None
        if has_any_role and trend_direction_series:
            # Combine trend directions: use first trend layer's direction
            entry_direction = trend_direction_series[0]

        return SignalSet(
            entries=combined_entries,
            exits=combined_exits,
            adds=combined_adds,
            reduces=combined_reduces,
            degraded_layers=degraded_count,
            entry_direction=entry_direction,
        )

    # Single-timeframe mode
    if dna.is_mtf and dfs_by_timeframe is None:
        raise ValueError(
            f"MTF DNA requires dfs_by_timeframe but got None. "
            f"Needed timeframes: {dna.timeframes}"
        )

    close = enhanced_df["close"]

    entry_triggers, entry_guards = [], []
    exit_triggers, exit_guards = [], []
    add_triggers, add_guards = [], []
    reduce_triggers, reduce_guards = [], []
    direction_genes = []

    for gene in dna.signal_genes:
        try:
            indicator_col = _get_indicator_column(enhanced_df, gene)
            signal = evaluate_condition(indicator_col, close, gene.condition, df=enhanced_df)
            signal = signal.fillna(False)

            if gene.role == SignalRole.ENTRY_TRIGGER:
                entry_triggers.append(signal)
            elif gene.role == SignalRole.ENTRY_GUARD:
                entry_guards.append(signal)
            elif gene.role == SignalRole.EXIT_TRIGGER:
                exit_triggers.append(signal)
            elif gene.role == SignalRole.EXIT_GUARD:
                exit_guards.append(signal)
            elif gene.role == SignalRole.ADD_TRIGGER:
                add_triggers.append(signal)
            elif gene.role == SignalRole.ADD_GUARD:
                add_guards.append(signal)
            elif gene.role == SignalRole.REDUCE_TRIGGER:
                reduce_triggers.append(signal)
            elif gene.role == SignalRole.REDUCE_GUARD:
                reduce_guards.append(signal)
            elif gene.role == SignalRole.DIRECTION:
                # DIRECTION gene: True -> +1 (long), False -> -1 (short)
                gene_direction = pd.Series(
                    np.where(signal, 1.0, -1.0),
                    index=enhanced_df.index,
                )
                direction_genes.append(gene_direction)
        except ValueError:
            continue

    all_entry = entry_triggers + entry_guards
    entries = combine_signals(all_entry, dna.logic_genes.entry_logic) if all_entry else pd.Series(False, index=enhanced_df.index)

    all_exit = exit_triggers + exit_guards
    exits = combine_signals(all_exit, dna.logic_genes.exit_logic) if all_exit else pd.Series(False, index=enhanced_df.index)

    add_logic = getattr(dna.logic_genes, 'add_logic', 'AND') or 'AND'
    reduce_logic = getattr(dna.logic_genes, 'reduce_logic', 'AND') or 'AND'

    all_add = add_triggers + add_guards
    adds = combine_signals(all_add, add_logic) if all_add else pd.Series(False, index=enhanced_df.index)

    all_reduce = reduce_triggers + reduce_guards
    reduces = combine_signals(all_reduce, reduce_logic) if all_reduce else pd.Series(False, index=enhanced_df.index)

    both = entries & exits
    entries = entries & ~both

    # Generate entry_direction for mixed mode
    entry_direction = None
    dna_direction = getattr(dna.risk_genes, "direction", "long")
    if dna_direction == "mixed":
        if direction_genes:
            entry_direction = direction_genes[0]
        else:
            # No DIRECTION gene: neutral direction (signals will not fire)
            entry_direction = pd.Series(0.0, index=enhanced_df.index)
    else:
        # For single-direction DNA, set explicit direction
        entry_direction = pd.Series(
            1.0 if dna_direction == "long" else -1.0,
            index=enhanced_df.index,
        )

    return SignalSet(
        entries=entries,
        exits=exits,
        adds=adds,
        reduces=reduces,
        entry_direction=entry_direction,
    )


def _gene_signature(gene) -> tuple:
    """Create a hashable signature for a signal gene for deduplication."""
    return (
        gene.indicator,
        tuple(sorted(gene.params.items())),
        gene.role.value if hasattr(gene.role, "value") else str(gene.role),
        gene.field_name,
        tuple(sorted(gene.condition.items())) if gene.condition else (),
    )


def batch_signal_sets(
    individuals: list[StrategyDNA],
    enhanced_df: pd.DataFrame,
    dfs_by_timeframe: dict | None = None,
) -> list[SignalSet]:
    """Batch-compute signal sets for a population with gene-level deduplication.

    Instead of computing each individual's signals independently, this function:
    1. Collects all unique gene signatures across the population
    2. Evaluates each unique gene condition once
    3. Assembles per-individual SignalSets from cached results

    MTF individuals fall back to dna_to_signal_set() since their signal
    computation involves cross-layer synthesis.

    Args:
        individuals: List of StrategyDNA to evaluate.
        enhanced_df: Enhanced DataFrame with indicators.
        dfs_by_timeframe: Multi-timeframe data dict (optional).

    Returns:
        List[SignalSet] in the same order as individuals.
    """
    if not individuals:
        return []

    # Step 1: Evaluate all unique gene conditions
    gene_results: dict[tuple, pd.Series] = {}
    skipped_genes = 0  # fix-executor-batch-health：签名非法跳过计数（可观测）

    for dna in individuals:
        # MTF individuals use the full dna_to_signal_set path
        if dna.is_mtf and dfs_by_timeframe is not None:
            continue

        for gene in dna.signal_genes:
            # 签名构造/去重查询纳入保护：结构非法（条件含 dict 值等
            # 不可哈希形态）在 hash 时抛 TypeError——不入去重表 + 计数，
            # 不上炸整批（组装段 .get 默认全 False 天然回退）
            try:
                sig = _gene_signature(gene)
                if sig in gene_results:
                    continue
            except TypeError:
                skipped_genes += 1
                logger.warning(
                    "batch_signal_sets 跳过签名非法基因（不可哈希）: "
                    "indicator=%s condition=%r",
                    gene.indicator, gene.condition)
                continue

            try:
                indicator_col = _get_indicator_column(enhanced_df, gene)
                signal = evaluate_condition(
                    indicator_col, enhanced_df["close"], gene.condition,
                    df=enhanced_df,
                )
                gene_results[sig] = signal.fillna(False)
            except (ValueError, KeyError, TypeError):
                # TypeError 兜条件 target 为 None 等形态的求值失败
                # （numpy 数值 vs None 比较）——与既有白名单同走全 False
                # 降级，单基因失败不炸整批
                gene_results[sig] = pd.Series(False, index=enhanced_df.index)

    if skipped_genes:
        logger.warning(
            "batch_signal_sets 本批共跳过 %d 个签名非法基因", skipped_genes)

    # Step 2: Assemble per-individual SignalSets
    results: list[SignalSet] = []
    for dna in individuals:
        # MTF: use full path
        if dna.is_mtf and dfs_by_timeframe is not None:
            try:
                results.append(dna_to_signal_set(dna, enhanced_df, dfs_by_timeframe))
            except Exception:
                results.append(_empty_signal_set(enhanced_df))
            continue

        # Single-TF: assemble from cached gene results
        entry_triggers, entry_guards = [], []
        exit_triggers, exit_guards = [], []
        add_triggers, add_guards = [], []
        reduce_triggers, reduce_guards = [], []
        direction_genes = []

        _default_off = pd.Series(False, index=enhanced_df.index)
        for gene in dna.signal_genes:
            # 签名非法（不可哈希——Step 1 已跳过未入表）在组装层同样
            # 降级全 False，不炸整批
            try:
                sig = _gene_signature(gene)
                signal = gene_results.get(sig, _default_off)
            except TypeError:
                signal = _default_off

            if gene.role == SignalRole.ENTRY_TRIGGER:
                entry_triggers.append(signal)
            elif gene.role == SignalRole.ENTRY_GUARD:
                entry_guards.append(signal)
            elif gene.role == SignalRole.EXIT_TRIGGER:
                exit_triggers.append(signal)
            elif gene.role == SignalRole.EXIT_GUARD:
                exit_guards.append(signal)
            elif gene.role == SignalRole.ADD_TRIGGER:
                add_triggers.append(signal)
            elif gene.role == SignalRole.ADD_GUARD:
                add_guards.append(signal)
            elif gene.role == SignalRole.REDUCE_TRIGGER:
                reduce_triggers.append(signal)
            elif gene.role == SignalRole.REDUCE_GUARD:
                reduce_guards.append(signal)
            elif gene.role == SignalRole.DIRECTION:
                gene_direction = pd.Series(
                    np.where(signal, 1.0, -1.0),
                    index=enhanced_df.index,
                )
                direction_genes.append(gene_direction)

        all_entry = entry_triggers + entry_guards
        entries = combine_signals(all_entry, dna.logic_genes.entry_logic) if all_entry else pd.Series(False, index=enhanced_df.index)

        all_exit = exit_triggers + exit_guards
        exits = combine_signals(all_exit, dna.logic_genes.exit_logic) if all_exit else pd.Series(False, index=enhanced_df.index)

        add_logic = getattr(dna.logic_genes, "add_logic", "AND") or "AND"
        reduce_logic = getattr(dna.logic_genes, "reduce_logic", "AND") or "AND"

        all_add = add_triggers + add_guards
        adds = combine_signals(all_add, add_logic) if all_add else pd.Series(False, index=enhanced_df.index)

        all_reduce = reduce_triggers + reduce_guards
        reduces = combine_signals(all_reduce, reduce_logic) if all_reduce else pd.Series(False, index=enhanced_df.index)

        both = entries & exits
        entries = entries & ~both

        # Generate entry_direction for mixed mode
        entry_direction = None
        if getattr(dna.risk_genes, "direction", None) == "mixed":
            if direction_genes:
                entry_direction = direction_genes[0]
            else:
                # No DIRECTION gene: neutral direction
                entry_direction = pd.Series(0.0, index=enhanced_df.index)

        results.append(SignalSet(
            entries=entries,
            exits=exits,
            adds=adds,
            reduces=reduces,
            entry_direction=entry_direction,
        ))

    return results
