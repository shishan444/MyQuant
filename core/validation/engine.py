"""WHEN/THEN hypothesis validation engine."""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional
import numpy as np
import pandas as pd
from typing import TYPE_CHECKING

from core.features.indicators import compute_all_indicators

if TYPE_CHECKING:
    from core.analysis.condition_kernel import MtfEvalContext
from core.data.storage import load_parquet


@dataclass
class TriggerRecord:
    """A single trigger event."""
    id: int
    time: str
    trigger_price: float
    change_pct: float
    matched: bool
    indicator_values: Dict[str, float] = field(default_factory=dict)


@dataclass
class DistributionBucket:
    """A histogram bucket."""
    range_start: float
    range_end: float
    match_count: int
    mismatch_count: int
    total_count: int


@dataclass
class ValidationResult:
    """Complete validation output."""
    match_rate: float
    total_count: int
    match_count: int
    mismatch_count: int
    triggers: List[TriggerRecord] = field(default_factory=list)
    distribution: List[DistributionBucket] = field(default_factory=list)
    percentiles: Dict[str, float] = field(default_factory=dict)
    concentration: Dict[str, List[float]] = field(default_factory=dict)
    signal_frequency: Dict[str, float] = field(default_factory=dict)
    extremes: List[Dict[str, Any]] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)


def validate_hypothesis(
    pair: str,
    timeframe: str,
    start: str,
    end: str,
    when_conditions: List[Dict[str, Any]],
    then_conditions: List[Dict[str, Any]],
    indicator_params: Optional[Dict[str, Any]] = None,
    then_window: int = 8,
    data_dir: Optional[str] = None,
    base_timeframe: Optional[str] = None,
    mtf: Optional["MtfEvalContext"] = None,
) -> ValidationResult:
    """Validate a WHEN->THEN hypothesis against historical data.

    Args:
        pair: Trading pair like "BTCUSDT"
        timeframe: K-line interval like "4h"
        start: Start date "2024-01-01"
        end: End date "2024-12-31"
        when_conditions: List of WHEN condition dicts
        then_conditions: List of THEN condition dicts
        indicator_params: Optional indicator parameter overrides
        then_window: Number of bars to check for THEN fulfillment
        data_dir: Optional path to market data directory
        base_timeframe: Optional base timeframe for MTF validation

    Returns:
        ValidationResult with all statistics
    """
    from pathlib import Path
    from core.analysis.hypothesis_service import ResearchError
    from core.data.mtf_loader import safe_symbol, find_parquet
    from core.validation.mtf import load_mtf_data, merge_to_base, get_timeframe_minutes

    if data_dir is None:
        data_dir = str(Path(__file__).resolve().parent.parent.parent / "data" / "market")

    # Determine effective base timeframe
    effective_base = base_timeframe or timeframe

    # Load data（B4.1 共享路径发现：safe_symbol 归一 + 别名兜底；
    # 未知 timeframe 结构化拒绝而非静默空结果）
    try:
        parquet_path = find_parquet(Path(data_dir), safe_symbol(pair), timeframe)
    except ValueError as exc:
        raise ResearchError(
            "RESEARCH_PARAM_INVALID",
            f"timeframe 未知: {timeframe!r}",
            detail={"param": "timeframe", "value": timeframe,
                    "legal_range": "TF_SECONDS 已知周期"},
        ) from exc

    if parquet_path is None:
        return ValidationResult(match_rate=0, total_count=0, match_count=0, mismatch_count=0)

    df = load_parquet(parquet_path)
    if df is None or len(df) == 0:
        return ValidationResult(match_rate=0, total_count=0, match_count=0, mismatch_count=0)

    # Filter by date range
    if start:
        df = df[df.index >= start]
    if end:
        df = df[df.index <= end]

    if len(df) == 0:
        return ValidationResult(match_rate=0, total_count=0, match_count=0, mismatch_count=0)

    # Compute indicators for base timeframe
    enhanced_df = compute_all_indicators(df)

    # MTF: collect referenced timeframes and merge higher-TF indicators
    all_conditions = when_conditions + then_conditions
    referenced_tfs = set()
    for cond in all_conditions:
        cond_tf = cond.get("timeframe", "")
        if cond_tf and cond_tf != effective_base:
            referenced_tfs.add(cond_tf)

    # Collect warnings during evaluation
    warnings: List[str] = []

    if referenced_tfs:
        try:
            mtf_data = load_mtf_data(pair, list(referenced_tfs), start, end, data_dir)
            if mtf_data:
                # Compute indicators on each higher-TF DataFrame
                mtf_with_indicators = {}
                for tf, tf_df in mtf_data.items():
                    mtf_with_indicators[tf] = compute_all_indicators(tf_df)

                base_tf_minutes = get_timeframe_minutes(timeframe)
                tf_minutes_map = {tf: get_timeframe_minutes(tf) for tf in mtf_with_indicators}
                enhanced_df = merge_to_base(
                    enhanced_df, mtf_with_indicators,
                    base_tf_minutes, tf_minutes_map,
                )
        except Exception as exc:
            warnings.append(f"MTF data loading failed, falling back to single timeframe: {exc}")

    # Evaluate WHEN conditions（mtf 上下文透传——Lab 链高周期条件掩码投影）
    when_mask = _evaluate_conditions(enhanced_df, when_conditions, warnings, mtf=mtf)

    # Find trigger indices
    trigger_indices = enhanced_df.index[when_mask].tolist()

    if not trigger_indices:
        return ValidationResult(match_rate=0, total_count=0, match_count=0, mismatch_count=0, warnings=warnings)

    # Determine max THEN window across all THEN conditions
    max_then_window = then_window
    for cond in then_conditions:
        cond_window = cond.get("window")
        if cond_window is not None:
            max_then_window = max(max_then_window, cond_window)

    # Evaluate THEN conditions for each trigger
    triggers = []
    changes = []
    for i, trigger_idx in enumerate(trigger_indices):
        trigger_loc = enhanced_df.index.get_loc(trigger_idx)
        trigger_price = enhanced_df.loc[trigger_idx, "close"]

        # Look ahead for THEN window (use max window across conditions)
        end_loc = min(trigger_loc + max_then_window + 1, len(enhanced_df))
        window_df = enhanced_df.iloc[trigger_loc + 1:end_loc]

        if len(window_df) == 0:
            continue

        # Check THEN conditions (each condition uses its own window)
        then_matched = _check_then_conditions(window_df, then_conditions, trigger_price, trigger_loc, max_then_window)

        # Calculate change
        final_price = window_df.iloc[-1]["close"]
        change_pct = ((final_price - trigger_price) / trigger_price) * 100

        # Record indicator values at trigger time
        trigger_row = enhanced_df.loc[trigger_idx]
        ind_values = {}
        for col in enhanced_df.columns:
            if col not in ["open", "high", "low", "close", "volume"]:
                try:
                    val = float(trigger_row[col])
                    if not np.isnan(val):
                        ind_values[col] = round(val, 4)
                except (ValueError, TypeError):
                    pass

        triggers.append(TriggerRecord(
            id=i + 1,
            time=str(trigger_idx),
            trigger_price=trigger_price,
            change_pct=round(change_pct, 2),
            matched=then_matched,
            indicator_values=ind_values,
        ))
        changes.append(change_pct)

    if not triggers:
        return ValidationResult(match_rate=0, total_count=0, match_count=0, mismatch_count=0, warnings=warnings)

    match_count = sum(1 for t in triggers if t.matched)
    total_count = len(triggers)
    match_rate = round(match_count / total_count * 100, 1) if total_count > 0 else 0

    # Build distribution
    distribution = _build_distribution(changes, [t.matched for t in triggers])

    # Compute percentiles
    percentiles = {}
    if changes:
        arr = np.array(changes)
        percentiles = {
            "p10": round(float(np.percentile(arr, 10)), 2),
            "p25": round(float(np.percentile(arr, 25)), 2),
            "p50": round(float(np.percentile(arr, 50)), 2),
            "p75": round(float(np.percentile(arr, 75)), 2),
            "p90": round(float(np.percentile(arr, 90)), 2),
            "min": round(float(np.min(arr)), 2),
            "max": round(float(np.max(arr)), 2),
        }

    # Concentration
    concentration = {}
    if changes:
        arr = np.array(changes)
        concentration = {
            "p50_range": [round(float(np.percentile(arr, 25)), 2), round(float(np.percentile(arr, 75)), 2)],
            "p90_range": [round(float(np.percentile(arr, 5)), 2), round(float(np.percentile(arr, 95)), 2)],
        }

    # Signal frequency
    signal_frequency = {}
    if total_count > 0 and len(df) > 0:
        days_covered = (df.index[-1] - df.index[0]).days
        if days_covered > 0:
            months = days_covered / 30.44
            signal_frequency = {
                "per_month": round(total_count / months, 1),
                "per_week": round(total_count / (days_covered / 7), 1),
                "total_months": round(months, 0),
            }

    # Extremes
    extremes = []
    if changes:
        sorted_triggers = sorted(triggers, key=lambda t: t.change_pct)
        worst = sorted_triggers[0]
        extremes.append({
            "change_pct": worst.change_pct,
            "time": worst.time,
            "is_match": worst.matched,
        })

    return ValidationResult(
        match_rate=match_rate,
        total_count=total_count,
        match_count=match_count,
        mismatch_count=total_count - match_count,
        triggers=triggers,
        distribution=distribution,
        percentiles=percentiles,
        concentration=concentration,
        signal_frequency=signal_frequency,
        extremes=extremes,
        warnings=warnings,
    )


def _evaluate_conditions(
    df: pd.DataFrame, conditions: List[Dict],
    warnings: List[str] | None = None,
    mtf: "MtfEvalContext | None" = None,
) -> pd.Series:
    """逐条件 logic 组合求值：engine（WHEN/THEN）与 rule_engine（进出场配对）共用实现。

    每个条件自身的 ``logic`` 字段决定它与累积结果的组合方式（AND/OR），
    首条件无累积对象其 logic 忽略，IF 与 AND 在布尔级等价。
    """
    if not conditions:
        return pd.Series(False, index=df.index)

    result: Optional[pd.Series] = None
    from core.analysis.condition_kernel import _mask_for

    for i, cond in enumerate(conditions):
        mask = _mask_for(df, cond, mtf=mtf)   # 0.2.14 三分派（mtf 透传）
        # 逐条件语义：每个条件自身的 logic 与累积结果组合；
        # 首条件无累积对象，其 logic 忽略；IF 与 AND 布尔级等价
        logic = cond.get("logic", "AND") if i > 0 else None

        if result is None:
            result = mask
        elif logic == "OR":
            result = result | mask
        else:
            result = result & mask

    if result is not None:
        return result.fillna(False)

    return pd.Series(False, index=df.index)


def _evaluate_single_condition(df: pd.DataFrame, cond: Dict, warnings: List[str] | None = None) -> pd.Series:
    """Evaluate a single condition."""
    subject = cond.get("subject", "")
    action = cond.get("action", "")
    target = cond.get("target", "")

    # Get subject series
    subject_series = _resolve_subject(df, subject)
    if subject_series is None:
        if warnings is not None:
            warnings.append(f"Subject '{subject}' not found in data columns")
        return pd.Series(False, index=df.index)

    # Get target value/series using new resolver
    target_val = _resolve_target(df, target)

    if target_val is None:
        if warnings is not None:
            warnings.append(f"Target '{target}' could not be resolved to a numeric value or data column")
        return pd.Series(False, index=df.index)

    # Apply action
    action_map = {
        "touch": lambda s, t: _touch_condition(s, t),
        "cross_above": lambda s, t: _cross_above(s, t),
        "cross_below": lambda s, t: _cross_below(s, t),
        "breakout": lambda s, t: _cross_above(s, t),
        "breakdown": lambda s, t: _cross_below(s, t),
        "gt": lambda s, t: s > t,
        "lt": lambda s, t: s < t,
        "ge": lambda s, t: s >= t,
        "le": lambda s, t: s <= t,
        "spike": lambda s, t: _spike_condition(s, df, t),
        "shrink": lambda s, t: _shrink_condition(s, df, t),
        "divergence_top": lambda s, t: _resolve_pattern(df, "divergence_top", s.name if hasattr(s, "name") else subject),
        "divergence_bottom": lambda s, t: _resolve_pattern(df, "divergence_bottom", s.name if hasattr(s, "name") else subject),
        "consecutive_up": lambda s, t: _resolve_pattern(df, "consecutive_up", s.name if hasattr(s, "name") else subject, t),
        "consecutive_down": lambda s, t: _resolve_pattern(df, "consecutive_down", s.name if hasattr(s, "name") else subject, t),
        # Phase 2: dynamic context actions
        "cross_above_series": lambda s, t: _cross_above_series_condition(df, cond, s),
        "cross_below_series": lambda s, t: _cross_below_series_condition(df, cond, s),
        "lookback_any": lambda s, t: _lookback_condition(df, cond, s, "any"),
        # Phase 4: support/resistance actions
        "touch_bounce": lambda s, t: _resolve_snr_pattern(df, "touch_bounce", s.name if hasattr(s, "name") else subject, cond),
        "role_reversal": lambda s, t: _resolve_snr_pattern(df, "role_reversal", s.name if hasattr(s, "name") else subject, cond),
    }

    fn = action_map.get(action)
    if fn is None:
        if warnings is not None:
            warnings.append(f"Action '{action}' is not a recognized action type")
        return pd.Series(False, index=df.index)

    return fn(subject_series, target_val)


def _resolve_subject(df: pd.DataFrame, subject: str) -> Optional[pd.Series]:
    """Resolve a subject name to a DataFrame column."""
    subject_map = {
        "close": "close", "price": "close",
        "open": "open", "high": "high", "low": "low",
        "volume": "volume",
        "kdj": "stoch_k",
    }

    if subject in subject_map:
        col = subject_map[subject]
        return df[col] if col in df.columns else None

    # 0.2.13：模糊子串兜底退役（结构化引用+校验前置后失联即工程错误）；
    # 注册表前缀名（Lab 老通道公开形制）做确定性归一——前缀精确匹配
    # output_fields 后经 resolve 还原默认参数列（精确翻译，非模糊猜测）；
    # 其余未命中返回 None（消费方全 False 安全降级契约不变）
    if subject in df.columns:
        return df[subject]
    from core.features.registry import INDICATOR_REGISTRY
    for name, defn in INDICATOR_REGISTRY.items():
        if subject in defn.output_fields:
            from core.analysis.registry_meta import default_params_for
            from core.features.registry import resolve_indicator_column
            col = resolve_indicator_column(
                name, default_params_for(name), defn.output_fields[0])
            if col in df.columns:
                return df[col]
            return None
    return None


def _resolve_target(df: pd.DataFrame, target):
    """Resolve a target to a numeric value or a DataFrame column series.

    Resolution order:
    1. If target is already numeric, return as float.
    2. Try to parse target string as float.
    3. Try cross-timeframe format "tf:indicator" (e.g. "4h:ema_20").
    4. Try exact column name match in DataFrame.
    5. Try fuzzy column name match (same logic as _resolve_subject).
    """
    if isinstance(target, (int, float)):
        return float(target)

    if not isinstance(target, str) or not target:
        return None

    # Try numeric parse
    try:
        return float(target)
    except (ValueError, TypeError):
        pass

    # Cross-timeframe reference: "4h:ema_20" -> look for "ema_20_4h" column
    if ":" in target:
        parts = target.split(":", 1)
        if len(parts) == 2:
            tf_suffix, indicator = parts[0], parts[1]
            # Try direct suffixed column name
            suffixed = f"{indicator}_{tf_suffix}"
            if suffixed in df.columns:
                return df[suffixed]
            # Fuzzy match on suffixed name
            suffixed_normalized = suffixed.lower().replace("_", "")
            matches = [c for c in df.columns if suffixed_normalized in c.lower().replace("_", "")]
            if matches:
                return df[matches[0]]

    # Exact column match
    if target in df.columns:
        return df[target]

    # Fuzzy column match: strip underscores + lowercase, then substring match
    target_normalized = target.lower().replace("_", "")
    matches = [c for c in df.columns if target_normalized in c.lower().replace("_", "")]
    if matches:
        return df[matches[0]]

    return None


def _resolve_pattern(
    df: pd.DataFrame,
    pattern: str,
    subject_col: str,
    target=None,
) -> pd.Series:
    """Resolve pattern-type actions using patterns module."""
    from core.validation.patterns import (
        detect_divergence_top,
        detect_divergence_bottom,
        detect_consecutive_up,
        detect_consecutive_down,
    )

    # Resolve the actual column name
    if subject_col not in df.columns:
        # Try fuzzy match
        matches = [c for c in df.columns if subject_col.lower().replace("_", "") in c.lower().replace("_", "")]
        if not matches:
            return pd.Series(False, index=df.index)
        subject_col = matches[0]

    if pattern == "divergence_top":
        return detect_divergence_top(df, subject_col)
    elif pattern == "divergence_bottom":
        return detect_divergence_bottom(df, subject_col)
    elif pattern == "consecutive_up":
        count = 3
        if target is not None:
            try:
                count = int(float(target))
            except (ValueError, TypeError):
                pass
        return detect_consecutive_up(df, subject_col, count)
    elif pattern == "consecutive_down":
        count = 3
        if target is not None:
            try:
                count = int(float(target))
            except (ValueError, TypeError):
                pass
        return detect_consecutive_down(df, subject_col, count)

    return pd.Series(False, index=df.index)


def _cross_above_series_condition(
    df: pd.DataFrame, cond: Dict, subject_series: pd.Series,
) -> pd.Series:
    """Cross above another indicator series."""
    target_series = _resolve_subject(df, cond.get("target", ""))
    if target_series is None:
        return pd.Series(False, index=df.index)
    return (subject_series.shift(1) < target_series.shift(1)) & (subject_series >= target_series)


def _cross_below_series_condition(
    df: pd.DataFrame, cond: Dict, subject_series: pd.Series,
) -> pd.Series:
    """Cross below another indicator series."""
    target_series = _resolve_subject(df, cond.get("target", ""))
    if target_series is None:
        return pd.Series(False, index=df.index)
    return (subject_series.shift(1) > target_series.shift(1)) & (subject_series <= target_series)


def _lookback_condition(
    df: pd.DataFrame, cond: Dict, subject_series: pd.Series, mode: str,
) -> pd.Series:
    """Lookback window: check if inner condition is met within window bars."""
    window = cond.get("window", 5)
    # F-2：坏值/零/负窗口结构化拒绝——裸穿 rolling(window=0) 直接 500
    from core.analysis.hypothesis_service import ResearchError

    try:
        window = int(window)
    except (TypeError, ValueError, OverflowError):
        # OverflowError：window=inf 经 int() 转换裸穿（与 _param_number 同款收口）
        raise ResearchError(
            "RESEARCH_PARAM_INVALID",
            f"condition.window 须为整数: {window!r}",
            detail={"param": "condition.window", "value": str(window),
                    "legal_range": ">=1 整数"},
        ) from None
    if window < 1:
        raise ResearchError(
            "RESEARCH_PARAM_INVALID",
            f"condition.window 须为 >=1: {window!r}",
            detail={"param": "condition.window", "value": window,
                    "legal_range": ">=1 整数"},
        )

    inner_action = cond.get("inner_action", "price_above")
    target_val = cond.get("target", 0)

    if inner_action in ("gt", "lt", "ge", "le"):
        threshold = target_val
        if isinstance(threshold, str):
            try:
                threshold = float(threshold)
            except (ValueError, TypeError):
                threshold = 0
        if inner_action == "gt":
            inner_signal = subject_series > threshold
        elif inner_action == "lt":
            inner_signal = subject_series < threshold
        elif inner_action == "ge":
            inner_signal = subject_series >= threshold
        else:
            inner_signal = subject_series <= threshold
    else:
        # Default: price_above
        inner_signal = df["close"] > subject_series

    if mode == "any":
        return inner_signal.rolling(window=window, min_periods=1).apply(any, raw=False).fillna(False).astype(bool)
    else:
        return inner_signal.rolling(window=window, min_periods=1).apply(all, raw=False).fillna(False).astype(bool)


def _cond_number(cond: Dict, key: str, default, cast=float):
    """动作数值键取值（F-2：坏值结构化拒绝而非裸 float()/int() 穿 500）。

    弱类型入口（run_analysis 的 condition 为任意 dict）下 SNR 动作的
    proximity_pct 等键可为不可解析值——ResearchError 经 Lab/分析路由
    既有的 research_error_to_http 兜底转 422。cast 保留原类型语义
    （lookback 的 int 对 "10.5" 依旧拒绝，不隐式截断）。
    """
    from core.analysis.hypothesis_service import ResearchError

    raw = cond.get(key, default)
    try:
        return cast(raw)
    except (TypeError, ValueError):
        raise ResearchError(
            "RESEARCH_PARAM_INVALID",
            f"condition.{key} 须为数值: {raw!r}",
            detail={"param": f"condition.{key}", "value": raw,
                    "legal_range": "数值"}) from None


def _resolve_snr_pattern(
    df: pd.DataFrame,
    pattern: str,
    subject_col: str,
    cond: Dict,
) -> pd.Series:
    """Resolve support/resistance pattern actions using patterns module."""
    from core.validation.patterns import detect_touch_bounce, detect_role_reversal

    # Resolve the actual column name
    if subject_col not in df.columns:
        matches = [c for c in df.columns if subject_col.lower().replace("_", "") in c.lower().replace("_", "")]
        if not matches:
            return pd.Series(False, index=df.index)
        subject_col = matches[0]

    if pattern == "touch_bounce":
        direction = cond.get("direction", "support")
        proximity_pct = _cond_number(cond, "proximity_pct", 0.01)
        bounce_pct = _cond_number(cond, "bounce_pct", 0.005)
        return detect_touch_bounce(df, subject_col, direction, proximity_pct, bounce_pct)
    elif pattern == "role_reversal":
        role = cond.get("role", "resistance")
        lookback = _cond_number(cond, "lookback", 10, cast=int)
        return detect_role_reversal(df, subject_col, role, lookback)

    return pd.Series(False, index=df.index)


def _check_then_conditions(
    window_df: pd.DataFrame,
    conditions: List[Dict],
    trigger_price: float,
    trigger_loc: int = 0,
    max_window: int = 8,
) -> bool:
    """Check if THEN conditions are met in the forward window.

    Each condition can have its own ``window`` field; if present, the
    window_df is trimmed to that condition's window before evaluation.
    """
    for cond in conditions:
        action = cond.get("action", "")
        target_val = cond.get("target", 0)

        # Per-condition window: trim the window_df if condition specifies its own window
        cond_window = cond.get("window")
        if cond_window is not None and cond_window < max_window:
            cond_df = window_df.iloc[:cond_window]
        else:
            cond_df = window_df

        if len(cond_df) == 0:
            continue

        try:
            threshold = float(target_val)
        except (ValueError, TypeError):
            threshold = 0

        if action in ("drop", "lt", "le"):
            min_price = cond_df["close"].min()
            change = ((min_price - trigger_price) / trigger_price) * 100
            if action == "drop" and change <= threshold:
                return True
            if action == "lt" and change < threshold:
                return True
            if action == "le" and change <= threshold:
                return True
        elif action in ("rise", "gt", "ge"):
            max_price = cond_df["close"].max()
            change = ((max_price - trigger_price) / trigger_price) * 100
            if action == "rise" and change >= threshold:
                return True
            if action == "gt" and change > threshold:
                return True
            if action == "ge" and change >= threshold:
                return True

    # Default: check if any price moved by target threshold
    if conditions:
        cond = conditions[0]
        action = cond.get("action", "drop")
        try:
            threshold = float(cond.get("target", 0))
        except (ValueError, TypeError):
            threshold = 0

        if action in ("drop",):
            change = ((window_df["close"].min() - trigger_price) / trigger_price) * 100
            return change <= threshold
        elif action in ("rise",):
            change = ((window_df["close"].max() - trigger_price) / trigger_price) * 100
            return change >= threshold

    return False


def _touch_condition(series: pd.Series, target) -> pd.Series:
    """Check if series touches target (within 0.5% tolerance)."""
    tolerance = abs(target) * 0.005 if isinstance(target, (int, float)) else 0
    return abs(series - target) <= max(tolerance, series.std() * 0.1)


def _cross_above(series: pd.Series, target) -> pd.Series:
    return (series.shift(1) < target) & (series >= target)


def _cross_below(series: pd.Series, target) -> pd.Series:
    return (series.shift(1) > target) & (series <= target)


def _spike_condition(series: pd.Series, df: pd.DataFrame, target) -> pd.Series:
    """Volume spike: current > N * rolling average."""
    try:
        multiplier = float(target) if isinstance(target, (int, float, str)) else 2.0
    except (ValueError, TypeError):
        multiplier = 2.0
    rolling_avg = series.rolling(window=20, min_periods=1).mean()
    return series >= rolling_avg * multiplier


def _shrink_condition(series: pd.Series, df: pd.DataFrame, target) -> pd.Series:
    """Volume shrink: current < N * rolling average."""
    try:
        multiplier = float(target) if isinstance(target, (int, float, str)) else 0.5
    except (ValueError, TypeError):
        multiplier = 0.5
    rolling_avg = series.rolling(window=20, min_periods=1).mean()
    return series <= rolling_avg * multiplier


def _build_distribution(changes: List[float], matches: List[bool]) -> List[DistributionBucket]:
    """Build histogram buckets from change percentages."""
    if not changes:
        return []

    arr = np.array(changes)
    min_val = float(np.min(arr))
    max_val = float(np.max(arr))

    # Create buckets
    num_buckets = min(10, max(5, len(changes) // 5))
    bucket_width = (max_val - min_val) / num_buckets if max_val != min_val else 2

    buckets = []
    for i in range(num_buckets):
        start = min_val + i * bucket_width
        end = start + bucket_width

        mask = (arr >= start) & (arr < end) if i < num_buckets - 1 else (arr >= start) & (arr <= end)
        indices = np.where(mask)[0]

        match_count = sum(1 for idx in indices if matches[idx])
        total = len(indices)

        buckets.append(DistributionBucket(
            range_start=round(start, 1),
            range_end=round(end, 1),
            match_count=match_count,
            mismatch_count=total - match_count,
            total_count=total,
        ))

    return buckets
