"""Volume spike detector: bars where volume exceeds N times the rolling average."""
from __future__ import annotations

from typing import Any, Dict, List

import numpy as np
import pandas as pd

from .base import SceneDetector, TriggerPoint, _param_number
from .top_pattern import _extract_snapshot


class VolumeSpikeDetector(SceneDetector):

    @property
    def name(self) -> str:
        return "volume_spike"

    @property
    def default_params(self) -> Dict[str, Any]:
        return {
            "multiplier": 2.5,
            "avg_period": 20,
            "min_spacing": 5,
        }

    def detect(self, df: pd.DataFrame, params: Dict[str, Any]) -> List[TriggerPoint]:
        p = {**self.default_params, **params}
        multiplier = _param_number(p, "multiplier", min_value=0, exclusive=True)
        avg_period = _param_number(p, "avg_period", cast=int, min_value=1)
        min_spacing = _param_number(p, "min_spacing", cast=int, min_value=1)

        volume = df["volume"].values
        closes = df["close"].values
        rolling_avg = pd.Series(volume).rolling(window=avg_period, min_periods=1).mean().values

        triggers: List[TriggerPoint] = []
        last_bar = -min_spacing - 1

        for i in range(avg_period, len(df)):
            if rolling_avg[i] == 0:
                continue
            rvol = volume[i] / rolling_avg[i]
            if rvol < multiplier:
                continue
            if i - last_bar < min_spacing:
                continue

            trigger_row = df.iloc[i]
            snapshot = _extract_snapshot(trigger_row)
            snapshot["rvol"] = round(rvol, 2)

            triggers.append(TriggerPoint(
                id=len(triggers) + 1,
                timestamp=str(df.index[i]),
                trigger_price=float(closes[i]),
                bar_index=i,
                indicator_snapshot=snapshot,
            ))
            last_bar = i

        return triggers
