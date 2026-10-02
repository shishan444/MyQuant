"""Cross-timeframe detector: higher timeframe signal confirmed by lower timeframe entry."""
from __future__ import annotations

from typing import Any, Dict, List

import numpy as np
import pandas as pd

from .base import SceneDetector, TriggerPoint, _param_number
from .top_pattern import _extract_snapshot


class CrossTimeframeDetector(SceneDetector):

    @property
    def name(self) -> str:
        return "cross_timeframe"

    @property
    def default_params(self) -> Dict[str, Any]:
        return {
            "higher_tf": "1d",
            "signal_type": "ema_cross",
            "fast_period": 20,
            "slow_period": 50,
            "min_spacing": 5,
        }

    def detect(self, df: pd.DataFrame, params: Dict[str, Any]) -> List[TriggerPoint]:
        p = {**self.default_params, **params}
        higher_tf = str(p["higher_tf"])
        signal_type = str(p["signal_type"])
        fast_period = _param_number(p, "fast_period", cast=int, min_value=1)
        slow_period = _param_number(p, "slow_period", cast=int, min_value=1)
        min_spacing = _param_number(p, "min_spacing", cast=int, min_value=1)

        # Resolve higher-TF indicator columns (must be pre-merged by scene_engine)
        fast_col = f"{signal_type.split('_')[0]}_{fast_period}_{higher_tf}"
        slow_col = f"{signal_type.split('_')[0]}_{slow_period}_{higher_tf}"

        # Try alternate naming conventions
        if fast_col not in df.columns:
            fast_col = f"ema_{fast_period}_{higher_tf}"
        if slow_col not in df.columns:
            slow_col = f"ema_{slow_period}_{higher_tf}"

        if fast_col not in df.columns or slow_col not in df.columns:
            return []

        fast_vals = df[fast_col].values
        slow_vals = df[slow_col].values
        closes = df["close"].values

        # Resolve base-TF fast EMA for entry confirmation
        base_fast = f"ema_{fast_period}"
        if base_fast not in df.columns:
            base_fast = f"sma_{fast_period}"

        triggers: List[TriggerPoint] = []
        last_bar = -min_spacing - 1

        for i in range(1, len(df)):
            if np.isnan(fast_vals[i]) or np.isnan(slow_vals[i]):
                continue
            if np.isnan(fast_vals[i - 1]) or np.isnan(slow_vals[i - 1]):
                continue

            # Golden cross: fast crosses above slow on higher TF
            prev_diff = fast_vals[i - 1] - slow_vals[i - 1]
            curr_diff = fast_vals[i] - slow_vals[i]
            if not (prev_diff <= 0 and curr_diff > 0):
                continue

            # Entry confirmation: base TF close above base fast MA
            if base_fast in df.columns:
                base_fast_val = df[base_fast].values[i]
                if not np.isnan(base_fast_val) and closes[i] < base_fast_val:
                    continue

            if i - last_bar < min_spacing:
                continue

            trigger_row = df.iloc[i]
            snapshot = _extract_snapshot(trigger_row)
            snapshot[f"htf_fast"] = round(float(fast_vals[i]), 4)
            snapshot[f"htf_slow"] = round(float(slow_vals[i]), 4)

            triggers.append(TriggerPoint(
                id=len(triggers) + 1,
                timestamp=str(df.index[i]),
                trigger_price=float(closes[i]),
                bar_index=i,
                indicator_snapshot=snapshot,
            ))
            last_bar = i

        return triggers
