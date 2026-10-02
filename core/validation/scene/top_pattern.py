"""Top pattern detector: pivot-based chart pattern recognition (double top, head & shoulders, triple top)."""
from __future__ import annotations

from typing import Any, Dict, List

import numpy as np
import pandas as pd

from .base import SceneDetector, TriggerPoint, _param_number
from .pivot import detect_pivots
from .pattern_match import match_patterns


class TopPatternDetector(SceneDetector):

    @property
    def name(self) -> str:
        return "top_pattern"

    @property
    def default_params(self) -> Dict[str, Any]:
        return {
            "lookback": 5,
            "confirmation_bars": 5,
            "min_prominence_pct": 0.5,
            "min_spacing": 10,
            "pivot_bars_left": 6,
            "pivot_bars_right": 6,
        }

    def detect(self, df: pd.DataFrame, params: Dict[str, Any]) -> List[TriggerPoint]:
        p = {**self.default_params, **params}
        min_spacing = _param_number(p, "min_spacing", cast=int, min_value=1)

        # Step 1: Detect pivot points
        pivots = detect_pivots(
            df,
            bars_left=_param_number(p, "pivot_bars_left", cast=int, min_value=1),
            bars_right=_param_number(p, "pivot_bars_right", cast=int, min_value=1),
        )

        # Step 2: Match chart patterns on pivot sequence
        atr_series = df["atr_14"] if "atr_14" in df.columns else None
        patterns = match_patterns(
            pivots,
            df=df,
            atr_series=atr_series,
        )

        # Step 3: Build TriggerPoint for each detected pattern
        triggers: List[TriggerPoint] = []
        last_bar = -min_spacing - 1

        for pat in patterns:
            completion_bar = pat.completion_bar

            # Enforce minimum spacing between triggers
            if completion_bar - last_bar < min_spacing:
                continue

            # Use the close price at the completion bar as trigger_price
            trigger_price = float(df["close"].iloc[completion_bar])

            # Extract indicator snapshot at completion bar
            trigger_row = df.iloc[completion_bar]
            snapshot = _extract_snapshot(trigger_row)

            # Build pattern metadata
            metadata = {
                "pattern_type": pat.pattern_type,
                "key_points": pat.key_points,
            }

            triggers.append(TriggerPoint(
                id=len(triggers) + 1,
                timestamp=str(df.index[completion_bar]),
                trigger_price=trigger_price,
                bar_index=completion_bar,
                indicator_snapshot=snapshot,
                pattern_subtype=pat.pattern_type,
                pattern_metadata=metadata,
            ))
            last_bar = completion_bar

        return triggers


def _extract_snapshot(row: pd.Series) -> Dict[str, float]:
    """Extract key indicator values from a DataFrame row."""
    snapshot: Dict[str, float] = {}
    priority_cols = ["rsi_14", "macd_12_26_9", "bb_width_20_2.0", "atr_14",
                     "ema_20", "ema_50", "sma_20", "sma_50", "volume"]
    for col in priority_cols:
        if col in row.index:
            try:
                val = float(row[col])
                if not np.isnan(val):
                    snapshot[col] = round(val, 4)
            except (ValueError, TypeError):
                pass
    return snapshot
