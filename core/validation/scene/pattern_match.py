"""Chart pattern matching engine: extreme-first search with post-validation.

Core algorithm transplanted from BennyThadikaran/stock-pattern (368 stars).
Key difference from previous version: search starts from the most prominent
peaks (highest price) and works outward, rather than scanning pivots
sequentially. This ensures the "best" peak candidates are always tried first.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd

from .pivot import PivotPoint


@dataclass
class PatternMatch:
    """A detected chart pattern."""

    pattern_type: str                               # "double_top" | "head_shoulders_top" | "triple_top"
    completion_bar: int                             # bar index where pattern completes
    key_points: List[Dict[str, Any]] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _build_key_points(*pairs: tuple) -> List[Dict[str, Any]]:
    """Build key_points list from (label, PivotPoint) pairs."""
    return [
        {"label": label, "index": p.index, "price": p.price}
        for label, p in pairs
    ]


def _compute_avg_bar_length(bar_lengths: np.ndarray, start_idx: int, end_idx: int) -> float:
    """Compute median bar length (high - low) in the given index range.

    Args:
        bar_lengths: Pre-computed numpy array of (high - low) values.
    """
    start = max(0, start_idx)
    end = min(len(bar_lengths), end_idx + 1)
    if start >= end:
        return 0.0
    return float(np.median(bar_lengths[start:end]))


def _validate_pivot_anchor(point: PivotPoint, df: pd.DataFrame) -> bool:
    """Verify peak price equals bar High, trough price equals bar Low.

    Rejects pivots that are anchored to the wrong OHLC component.
    For example, a peak pivot whose price comes from Low rather than High
    indicates the pivot is incorrectly positioned.
    """
    if point.index < 0 or point.index >= len(df):
        return False
    row = df.iloc[point.index]
    if point.pivot_type == "peak":
        return abs(point.price - row["high"]) < 0.01
    return abs(point.price - row["low"]) < 0.01


def _check_level_intact(
    level_price: float,
    from_bar: int,
    df: pd.DataFrame,
    direction: str,
    avg_bar_length: float,
    check_bars: int = 0,
) -> bool:
    """Check whether a key price level has NOT been breached after from_bar.

    Args:
        level_price: The critical price level.
        from_bar: Bar index from which to check.
        df: DataFrame with high/low columns.
        direction: "below" means level must not be broken downward,
                   "above" means level must not be broken upward.
        avg_bar_length: Breach tolerance (must exceed 1x avgBarLength to count).
        check_bars: Number of bars to check. 0 means check all subsequent bars.

    Returns:
        True if the level is still intact (not meaningfully breached).
    """
    if from_bar + 1 >= len(df):
        return True
    end = len(df) if check_bars == 0 else min(from_bar + 1 + check_bars, len(df))
    subsequent = df.iloc[from_bar + 1:end]
    if direction == "below":
        lowest = subsequent["low"].min()
        return lowest > level_price - avg_bar_length
    # direction == "above"
    highest = subsequent["high"].max()
    return highest < level_price + avg_bar_length


def _get_atr(atr_series: pd.Series, bar_index: int) -> Optional[float]:
    """Safely get ATR value at a bar index."""
    if atr_series is not None and 0 <= bar_index < len(atr_series):
        val = atr_series.iloc[bar_index]
        if not np.isnan(val):
            return float(val)
    return None


# ---------------------------------------------------------------------------
# Pattern finders: extreme-first search strategy
# ---------------------------------------------------------------------------

# Max bar distance between first and last point of a pattern.
# Prevents combining peaks that are far apart (noise) and limits search space.
_MAX_PATTERN_SPAN = 200


def _find_double_tops(
    pivots: List[PivotPoint],
    df: pd.DataFrame,
    bar_lengths: np.ndarray,
    atr_series: Optional[pd.Series],
) -> List[PatternMatch]:
    """Find all double top patterns using extreme-first search.

    Algorithm (from stock-pattern):
    1. Sort peaks by price descending (most prominent first).
    2. For each peak A, find the highest peak C after A (within span limit).
    3. Find the lowest trough B between A and C.
    4. Apply geometric constraints + post-validation.
    5. On failure, skip to next A.
    """
    peaks = [p for p in pivots if p.pivot_type == "peak"]
    if len(peaks) < 2:
        return []

    peaks_by_price = sorted(peaks, key=lambda p: p.price, reverse=True)

    # Pre-sort peaks by index for fast range queries
    peaks_by_idx = sorted(peaks, key=lambda p: p.index)

    results: List[PatternMatch] = []
    used_indices: set = set()

    for a in peaks_by_price:
        if a.index in used_indices:
            continue

        # Find peaks after A, within _MAX_PATTERN_SPAN bars, sorted by price desc
        c_candidates = sorted(
            [p for p in peaks_by_idx
             if a.index < p.index <= a.index + _MAX_PATTERN_SPAN
             and p.index not in used_indices],
            key=lambda p: p.price,
            reverse=True,
        )
        if not c_candidates:
            continue

        found = False
        for c in c_candidates:
            # Find lowest trough between A and C
            b_candidates = [
                p for p in pivots
                if p.pivot_type == "trough"
                and a.index < p.index < c.index
            ]
            if not b_candidates:
                continue
            b = min(b_candidates, key=lambda p: p.price)

            # --- Geometric constraints ---
            abl = _compute_avg_bar_length(bar_lengths, a.index, c.index)
            if abl <= 0:
                continue

            # Two peaks close: price diff <= 0.5 * avgBarLength
            if abs(a.price - c.price) > abl * 0.5:
                continue

            # Valley below both peaks
            if b.price >= min(a.price, c.price):
                continue

            # ATR height constraint (8x upper bound for crypto volatility)
            atr_val = _get_atr(atr_series, c.index)
            height = c.price - b.price
            if atr_val and atr_val > 0:
                if height > atr_val * 8 or height < atr_val * 0.5:
                    continue

            # Volume confirmation (relaxed: reject only if 2nd peak volume
            # exceeds 1.5x of 1st peak — avoids noise in crypto volume)
            if a.volume > 0 and c.volume > 0:
                if c.volume >= a.volume * 1.5:
                    continue

            # --- Post-validation ---
            if not _validate_pivot_anchor(a, df):
                continue
            if not _validate_pivot_anchor(b, df):
                continue
            if not _validate_pivot_anchor(c, df):
                continue

            # C level intact: only check next 10 bars (short-term confirmation)
            if not _check_level_intact(c.price, c.index, df, "above", abl, check_bars=10):
                continue

            # Pattern confirmed
            used_indices.update([a.index, b.index, c.index])
            results.append(PatternMatch(
                pattern_type="double_top",
                completion_bar=c.index,
                key_points=_build_key_points(
                    ("peak_1", a), ("trough_1", b), ("peak_2", c),
                ),
            ))
            found = True
            break

    results.sort(key=lambda m: m.completion_bar)
    return results


def _find_head_shoulders_tops(
    pivots: List[PivotPoint],
    df: pd.DataFrame,
    bar_lengths: np.ndarray,
    atr_series: Optional[pd.Series],
) -> List[PatternMatch]:
    """Find all head-and-shoulders top patterns using extreme-first search.

    Algorithm (from stock-pattern):
    1. Sort peaks by price descending (head candidates).
    2. For each head C, search backward for left shoulder A (highest before C).
    3. Search forward for right shoulder E (highest after C).
    4. Find armpits B (lowest between A-C) and D (lowest between C-E).
    5. Apply geometric constraints + post-validation.
    """
    peaks = [p for p in pivots if p.pivot_type == "peak"]
    if len(peaks) < 3:
        return []

    peaks_by_price = sorted(peaks, key=lambda p: p.price, reverse=True)

    results: List[PatternMatch] = []
    used_indices: set = set()

    for head in peaks_by_price:
        if head.index in used_indices:
            continue

        # Left shoulder: highest peak BEFORE head (within span)
        left_candidates = [
            p for p in peaks
            if head.index - _MAX_PATTERN_SPAN <= p.index < head.index
            and p.index not in used_indices
        ]
        if not left_candidates:
            continue
        left_shoulder = max(left_candidates, key=lambda p: p.price)

        # Right shoulder: highest peak AFTER head (within span)
        right_candidates = [
            p for p in peaks
            if head.index < p.index <= head.index + _MAX_PATTERN_SPAN
            and p.index not in used_indices
        ]
        if not right_candidates:
            continue
        right_shoulder = max(right_candidates, key=lambda p: p.price)

        # Left armpit: lowest trough between left_shoulder and head
        armpit_left_cands = [
            p for p in pivots
            if p.pivot_type == "trough"
            and left_shoulder.index < p.index < head.index
        ]
        if not armpit_left_cands:
            continue
        armpit_left = min(armpit_left_cands, key=lambda p: p.price)

        # Right armpit: lowest trough between head and right_shoulder
        armpit_right_cands = [
            p for p in pivots
            if p.pivot_type == "trough"
            and head.index < p.index < right_shoulder.index
        ]
        if not armpit_right_cands:
            continue
        armpit_right = min(armpit_right_cands, key=lambda p: p.price)

        # --- Geometric constraints ---
        abl = _compute_avg_bar_length(bar_lengths, armpit_left.index, armpit_right.index)
        if abl <= 0:
            continue

        # Head must be highest
        if head.price <= left_shoulder.price or head.price <= right_shoulder.price:
            continue

        # Both armpits below both shoulders
        if max(armpit_left.price, armpit_right.price) >= min(left_shoulder.price, right_shoulder.price):
            continue

        # Shoulders approximately equal
        if abs(left_shoulder.price - right_shoulder.price) > abl * 0.5:
            continue

        # Neckline (armpits) approximately level
        if abs(armpit_left.price - armpit_right.price) >= abl:
            continue

        # Head-shoulder structure obvious: head protrudes >= 0.6 * avgBarLength
        avg_shoulder = (left_shoulder.price + right_shoulder.price) / 2.0
        if head.price - avg_shoulder < abl * 0.6:
            continue

        # ATR height constraint (8x upper bound for crypto volatility)
        atr_val = _get_atr(atr_series, right_shoulder.index)
        if atr_val and atr_val > 0:
            avg_neckline = (armpit_left.price + armpit_right.price) / 2.0
            h = head.price - avg_neckline
            if h > atr_val * 8 or h < atr_val * 0.5:
                continue

        # --- Post-validation ---
        all_points = [left_shoulder, armpit_left, head, armpit_right, right_shoulder]
        if not all(_validate_pivot_anchor(p, df) for p in all_points):
            continue

        # Neckline intact: only check next 10 bars (short-term confirmation)
        neckline_price = min(armpit_left.price, armpit_right.price)
        if not _check_level_intact(neckline_price, right_shoulder.index, df, "below", abl, check_bars=10):
            continue

        used_indices.update([p.index for p in all_points])
        results.append(PatternMatch(
            pattern_type="head_shoulders_top",
            completion_bar=right_shoulder.index,
            key_points=_build_key_points(
                ("left_shoulder", left_shoulder), ("neckline_1", armpit_left),
                ("head", head), ("neckline_2", armpit_right),
                ("right_shoulder", right_shoulder),
            ),
        ))

    results.sort(key=lambda m: m.completion_bar)
    return results


def _find_triple_tops(
    pivots: List[PivotPoint],
    df: pd.DataFrame,
    bar_lengths: np.ndarray,
    atr_series: Optional[pd.Series],
) -> List[PatternMatch]:
    """Find all triple top patterns using extreme-first search.

    Algorithm:
    1. Sort peaks by price descending.
    2. For each peak P1, find the next two peaks P2, P3 that are
       chronologically after P1 and close in price.
    3. Find troughs between each pair.
    4. Apply geometric constraints + post-validation.
    """
    peaks = [p for p in pivots if p.pivot_type == "peak"]
    if len(peaks) < 3:
        return []

    peaks_by_price = sorted(peaks, key=lambda p: p.price, reverse=True)

    results: List[PatternMatch] = []
    used_indices: set = set()

    for p1 in peaks_by_price:
        if p1.index in used_indices:
            continue

        # Find all peaks after P1 (within span), sorted by price descending
        later_peaks = sorted(
            [p for p in peaks
             if p1.index < p.index <= p1.index + _MAX_PATTERN_SPAN
             and p.index not in used_indices],
            key=lambda p: p.price,
            reverse=True,
        )
        if len(later_peaks) < 2:
            continue

        # Try pairs of later peaks as P2, P3
        found = False
        for p2 in later_peaks:
            if found:
                break
            for p3 in later_peaks:
                if p3.index <= p2.index:
                    continue

                # Trough between P1 and P2
                t1_cands = [
                    p for p in pivots
                    if p.pivot_type == "trough"
                    and p1.index < p.index < p2.index
                ]
                if not t1_cands:
                    continue
                t1 = min(t1_cands, key=lambda p: p.price)

                # Trough between P2 and P3
                t2_cands = [
                    p for p in pivots
                    if p.pivot_type == "trough"
                    and p2.index < p.index < p3.index
                ]
                if not t2_cands:
                    continue
                t2 = min(t2_cands, key=lambda p: p.price)

                # --- Geometric constraints ---
                abl = _compute_avg_bar_length(bar_lengths, p1.index, p3.index)
                if abl <= 0:
                    continue

                # All three peaks close in price
                threshold = abl * 0.5
                if abs(p1.price - p2.price) > threshold:
                    continue
                if abs(p2.price - p3.price) > threshold:
                    continue
                if abs(p1.price - p3.price) > threshold:
                    continue

                # Troughs below peaks
                avg_peak = (p1.price + p2.price + p3.price) / 3.0
                if t1.price >= avg_peak or t2.price >= avg_peak:
                    continue

                # ATR height constraint (8x upper bound for crypto volatility)
                atr_val = _get_atr(atr_series, p3.index)
                avg_trough = (t1.price + t2.price) / 2.0
                height = avg_peak - avg_trough
                if atr_val and atr_val > 0:
                    if height > atr_val * 8 or height < atr_val * 0.5:
                        continue

                # Volume: relaxed decreasing trend (p3 must not exceed 1.5x p1)
                if p1.volume > 0 and p3.volume > 0:
                    if p3.volume >= p1.volume * 1.5:
                        continue

                # --- Post-validation ---
                all_pts = [p1, t1, p2, t2, p3]
                if not all(_validate_pivot_anchor(pt, df) for pt in all_pts):
                    continue

                # P3 level intact: only check next 10 bars
                if not _check_level_intact(p3.price, p3.index, df, "above", abl, check_bars=10):
                    continue

                used_indices.update([pt.index for pt in all_pts])
                results.append(PatternMatch(
                    pattern_type="triple_top",
                    completion_bar=p3.index,
                    key_points=_build_key_points(
                        ("peak_1", p1), ("trough_1", t1),
                        ("peak_2", p2), ("trough_2", t2),
                        ("peak_3", p3),
                    ),
                ))
                found = True
                break

    results.sort(key=lambda m: m.completion_bar)
    return results


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def match_patterns(
    pivots: List[PivotPoint],
    df: Optional[pd.DataFrame] = None,
    atr_series: Optional[pd.Series] = None,
) -> List[PatternMatch]:
    """Scan a pivot sequence for chart patterns using extreme-first search.

    Each pattern type is searched independently with its own finder.
    Results are deduplicated by key point indices.

    Args:
        pivots: Chronologically ordered pivot points.
        df: DataFrame with high/low columns for avgBarLength and post-validation.
        atr_series: ATR values series for height constraints.

    Returns:
        List of PatternMatch instances, ordered by completion_bar.
    """
    if len(pivots) < 3:
        return []

    if df is None:
        return []

    # Pre-compute bar lengths as numpy array for fast median computation
    bar_lengths = (df["high"].values.astype(np.float64) - df["low"].values.astype(np.float64))

    all_results: List[PatternMatch] = []

    # Run finders: H&S first (most specific), then triple, then double
    all_results.extend(_find_head_shoulders_tops(pivots, df, bar_lengths, atr_series))
    all_results.extend(_find_triple_tops(pivots, df, bar_lengths, atr_series))
    all_results.extend(_find_double_tops(pivots, df, bar_lengths, atr_series))

    # Deduplicate by key point bar indices (same bars = same pattern)
    seen: set = set()
    deduped: List[PatternMatch] = []
    for m in sorted(all_results, key=lambda m: m.completion_bar):
        bars = tuple(kp["index"] for kp in m.key_points)
        if bars not in seen:
            seen.add(bars)
            deduped.append(m)

    return deduped
