"""顶部形态检测器：pivot 序列上的图表形态识别（双顶/头肩顶/三重顶）。

服务对象：scene_engine 的 DETECTORS 注册表（"top_pattern" 键）；
模块级 _extract_snapshot 是 scene/ 五检测器共享的指标快照
helper（cross_timeframe/mean_reversion/support_resistance/
volume_breakout/volume_spike 复用）。职责边界：pivot 检测与
形态匹配分别委托 pivot.py 与 pattern_match.py，本模块只做
参数编排与 TriggerPoint 构建。
"""
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

        # 第一步：检测 pivot 点
        pivots = detect_pivots(
            df,
            bars_left=_param_number(p, "pivot_bars_left", cast=int, min_value=1),
            bars_right=_param_number(p, "pivot_bars_right", cast=int, min_value=1),
        )

        # 第二步：在 pivot 序列上匹配图表形态
        atr_series = df["atr_14"] if "atr_14" in df.columns else None
        patterns = match_patterns(
            pivots,
            df=df,
            atr_series=atr_series,
        )

        # 第三步：为每个检出的形态构建 TriggerPoint
        triggers: List[TriggerPoint] = []
        last_bar = -min_spacing - 1

        for pat in patterns:
            completion_bar = pat.completion_bar

            # 强制触发点之间的最小间距
            if completion_bar - last_bar < min_spacing:
                continue

            # 以完成 bar 的 close 价作为 trigger_price
            trigger_price = float(df["close"].iloc[completion_bar])

            # 提取完成 bar 处的指标快照
            trigger_row = df.iloc[completion_bar]
            snapshot = _extract_snapshot(trigger_row)

            # 构建形态元数据
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
    """从 DataFrame 行提取关键指标值（优先列内非 NaN 值，round 4 位）。"""
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
