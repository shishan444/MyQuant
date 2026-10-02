"""Volume breakout detector: volume surge combined with price breaking a key level."""
from __future__ import annotations

from typing import Any, Dict, List

import numpy as np
import pandas as pd

from .base import SceneDetector, TriggerPoint, _param_number
from .top_pattern import _extract_snapshot


class VolumeBreakoutDetector(SceneDetector):

    @property
    def name(self) -> str:
        return "volume_breakout"

    @property
    def default_params(self) -> Dict[str, Any]:
        return {
            "volume_multiplier": 2.0,
            "volume_avg_period": 20,
            "level_type": "bb_upper",
            "min_spacing": 10,
        }

    def detect(self, df: pd.DataFrame, params: Dict[str, Any]) -> List[TriggerPoint]:
        p = {**self.default_params, **params}
        vol_mult = _param_number(p, "volume_multiplier", min_value=0, exclusive=True)
        vol_period = _param_number(p, "volume_avg_period", cast=int, min_value=1)
        level_type = str(p["level_type"])
        min_spacing = _param_number(p, "min_spacing", cast=int, min_value=1)

        volume = df["volume"].values
        closes = df["close"].values
        rolling_avg = pd.Series(volume).rolling(window=vol_period, min_periods=1).mean().values

        # Resolve level column
        level_col = _resolve_level_column(level_type, df.columns)
        if level_col is None:
            return []
        level_vals = df[level_col].values

        triggers: List[TriggerPoint] = []
        last_bar = -min_spacing - 1

        for i in range(vol_period, len(df)):
            # Volume condition
            if rolling_avg[i] == 0:
                continue
            rvol = volume[i] / rolling_avg[i]
            if rvol < vol_mult:
                continue

            # Price breakout condition（==0 与 NaN 同拒：breakout_pct 除以
            # level 会外泄 inf）
            if np.isnan(level_vals[i]) or level_vals[i] == 0:
                continue
            if closes[i] <= level_vals[i]:
                continue

            if i - last_bar < min_spacing:
                continue

            trigger_row = df.iloc[i]
            snapshot = _extract_snapshot(trigger_row)
            snapshot["rvol"] = round(rvol, 2)
            snapshot["level_value"] = round(float(level_vals[i]), 4)
            snapshot["breakout_pct"] = round(
                (closes[i] - level_vals[i]) / level_vals[i] * 100, 2
            )

            triggers.append(TriggerPoint(
                id=len(triggers) + 1,
                timestamp=str(df.index[i]),
                trigger_price=float(closes[i]),
                bar_index=i,
                indicator_snapshot=snapshot,
            ))
            last_bar = i

        return triggers


def _resolve_level_column(level_type: str, columns: pd.Index) -> str | None:
    """Find the indicator column matching the requested level type.

    候选（前缀, 后缀）与生成方 core.features.indicators 的列名规则对齐：
    bb/dc 的语义段在列首（bb_upper_20_2），keltner 的语义段在列尾
    （kc_20_10_2.0_upper）。兜底按候选完整语义前缀/后缀匹配，不再
    按首词宽匹配撞到同前缀的其他语义列；无命中返回 None，由调用方
    跳过该 level 要素。
    """
    mapping: Dict[str, List[tuple]] = {
        "bb_upper": [("bb_upper_20_2", ""), ("bbu_20_2", ""), ("bb_upper_", "")],
        "bb_lower": [("bb_lower_20_2", ""), ("bbl_20_2", ""), ("bb_lower_", "")],
        "dc_upper": [("dc_upper_20", ""), ("donchian_upper_", "")],
        "dc_lower": [("dc_lower_20", ""), ("donchian_lower_", "")],
        "keltner_upper": [("kc_upper", ""), ("kc_", "_upper")],
        "keltner_lower": [("kc_lower", ""), ("kc_", "_lower")],
    }

    candidates = mapping.get(level_type, [(level_type, "")])
    for prefix, suffix in candidates:
        for col in columns:
            if col.startswith(prefix) and col.endswith(suffix):
                return col
    return None
