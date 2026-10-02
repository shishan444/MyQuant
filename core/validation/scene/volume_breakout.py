"""放量突破检测器：量能放大与价格突破关键位的组合触发。

服务对象：scene_engine 的 DETECTORS 注册表（"volume_breakout"
键）；_resolve_level_column 另有 tests 直调锚（test_scene_
feature_net.py:432/438 与 tests/backtest/test_indicator_
invariants.py:308）。职责边界：量能 + 突破双条件扫描，关键
位列解析对齐 core.features.indicators 的列名规则，指标快照
复用 top_pattern._extract_snapshot。
"""
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

        # 解析关键位列
        level_col = _resolve_level_column(level_type, df.columns)
        if level_col is None:
            return []
        level_vals = df[level_col].values

        triggers: List[TriggerPoint] = []
        last_bar = -min_spacing - 1

        for i in range(vol_period, len(df)):
            # 量能条件
            if rolling_avg[i] == 0:
                continue
            rvol = volume[i] / rolling_avg[i]
            if rvol < vol_mult:
                continue

            # 价格突破条件（==0 与 NaN 同拒：breakout_pct 除以
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
    """查找与请求 level 类型匹配的指标列。

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
