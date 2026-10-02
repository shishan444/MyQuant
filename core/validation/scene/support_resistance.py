"""支撑/阻力检测器：价格触及计算出的支撑或阻力位。

服务对象：scene_engine 的 DETECTORS 注册表（"support_
resistance" 键）；_resolve_sr_column 另有 tests 直调锚
（test_scene_feature_net.py:573）。职责边界：邻近触发单条件
扫描（both 时双列任一侧邻近即触发），支撑/阻力列按 source +
direction 关键词匹配解析，指标快照复用 top_pattern._
extract_snapshot。
"""
from __future__ import annotations

from typing import Any, Dict, List

import numpy as np
import pandas as pd

from .base import SceneDetector, TriggerPoint, _param_number
from .top_pattern import _extract_snapshot


class SupportResistanceDetector(SceneDetector):

    @property
    def name(self) -> str:
        return "support_resistance"

    @property
    def default_params(self) -> Dict[str, Any]:
        return {
            "level_source": "bb",
            "direction": "support",
            "proximity_pct": 1.0,
            "min_spacing": 10,
        }

    def detect(self, df: pd.DataFrame, params: Dict[str, Any]) -> List[TriggerPoint]:
        p = {**self.default_params, **params}
        level_source = str(p["level_source"])
        direction = str(p["direction"])
        proximity = _param_number(p, "proximity_pct", min_value=0, exclusive=True)
        min_spacing = _param_number(p, "min_spacing", cast=int, min_value=1)

        # both 时分别解析 support/resistance 两列，单方向只解析自身列
        sides = ("support", "resistance") if direction == "both" else (direction,)
        side_levels = {}
        for side in sides:
            col = _resolve_sr_column(level_source, side, df.columns)
            if col is not None:
                side_levels[side] = df[col].values
        if not side_levels:
            return []

        highs = df["high"].values
        lows = df["low"].values
        closes = df["close"].values

        triggers: List[TriggerPoint] = []
        last_bar = -min_spacing - 1

        for i in range(1, len(df)):
            # 逐侧检查：support 配 lows（下探支撑）、resistance 配 highs
            # （上冲阻力），任一侧邻近即触发，level 值取触发侧
            touched_level = None
            proximity_dist = 0.0
            for side, level_vals in side_levels.items():
                # ==0 与 NaN 同拒：dist 计算除以 level（与 volume_breakout
                # 的 breakout_pct 守卫同款）
                if np.isnan(level_vals[i]) or level_vals[i] == 0:
                    continue
                price = lows[i] if side == "support" else highs[i]
                dist = abs(price - level_vals[i]) / level_vals[i] * 100
                if dist <= proximity:
                    touched_level = level_vals[i]
                    proximity_dist = dist
                    break

            if touched_level is None:
                continue
            if i - last_bar < min_spacing:
                continue

            trigger_row = df.iloc[i]
            snapshot = _extract_snapshot(trigger_row)
            snapshot["level_value"] = round(float(touched_level), 4)
            snapshot["proximity_pct"] = round(proximity_dist, 2)

            triggers.append(TriggerPoint(
                id=len(triggers) + 1,
                timestamp=str(df.index[i]),
                trigger_price=float(closes[i]),
                bar_index=i,
                indicator_snapshot=snapshot,
            ))
            last_bar = i

        return triggers


def _resolve_sr_column(
    source: str, direction: str, columns: pd.Index,
) -> str | None:
    """查找支撑/阻力位对应的指标列。"""
    direction_map = {
        "support": ["lower", "l", "support"],
        "resistance": ["upper", "u", "resistance"],
    }
    dir_keywords = direction_map.get(direction, ["lower", "upper"])

    source_map = {
        "bb": ["bb_", "bbu_", "bbl_"],
        "dc": ["dc_", "donchian_"],
        "keltner": ["keltner_", "kc_"],
    }
    src_prefixes = source_map.get(source, [source])

    for col in columns:
        has_src = any(col.startswith(p) or p in col for p in src_prefixes)
        has_dir = any(k in col.lower() for k in dir_keywords)
        if has_src and has_dir:
            return col

    return None
