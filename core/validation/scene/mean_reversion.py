"""均值回归检测器：价格偏离均线 N% 触发。

服务对象：scene_engine 的 DETECTORS 注册表（"mean_reversion"
键）；tests/evolution 特征锚直调。职责边界：单一偏离条件的
触发点扫描，MA 列缺失时按 ma_type 就地计算兜底，指标快照
复用 top_pattern._extract_snapshot。
"""
from __future__ import annotations

from typing import Any, Dict, List

import numpy as np
import pandas as pd

from .base import SceneDetector, TriggerPoint, _param_number
from .top_pattern import _extract_snapshot


class MeanReversionDetector(SceneDetector):

    @property
    def name(self) -> str:
        return "mean_reversion"

    @property
    def default_params(self) -> Dict[str, Any]:
        return {
            "ma_type": "ema",
            "ma_period": 50,
            "deviation_pct": 3.0,
            "direction": "below",
            "min_spacing": 10,
        }

    def detect(self, df: pd.DataFrame, params: Dict[str, Any]) -> List[TriggerPoint]:
        p = {**self.default_params, **params}
        ma_type = str(p["ma_type"]).lower()
        ma_period = _param_number(p, "ma_period", cast=int, min_value=1)
        threshold = _param_number(p, "deviation_pct", min_value=0, exclusive=True)
        direction = str(p["direction"]).lower()
        min_spacing = _param_number(p, "min_spacing", cast=int, min_value=1)

        # 解析 MA 列
        ma_col = f"{ma_type}_{ma_period}"
        if ma_col not in df.columns:
            # 兜底：就地计算
            if ma_type == "ema":
                df[ma_col] = df["close"].ewm(span=ma_period, adjust=False).mean()
            else:
                df[ma_col] = df["close"].rolling(window=ma_period, min_periods=1).mean()

        closes = df["close"].values
        ma_vals = df[ma_col].values

        triggers: List[TriggerPoint] = []
        last_bar = -min_spacing - 1

        for i in range(ma_period, len(df)):
            if np.isnan(ma_vals[i]) or ma_vals[i] == 0:
                continue

            deviation = (closes[i] - ma_vals[i]) / ma_vals[i] * 100

            triggered = False
            if direction == "below" and deviation <= -threshold:
                triggered = True
            elif direction == "above" and deviation >= threshold:
                triggered = True
            elif direction == "both" and abs(deviation) >= threshold:
                triggered = True

            if not triggered:
                continue
            if i - last_bar < min_spacing:
                continue

            trigger_row = df.iloc[i]
            snapshot = _extract_snapshot(trigger_row)
            snapshot["ma_value"] = round(float(ma_vals[i]), 4)
            snapshot["deviation_pct"] = round(deviation, 2)

            triggers.append(TriggerPoint(
                id=len(triggers) + 1,
                timestamp=str(df.index[i]),
                trigger_price=float(closes[i]),
                bar_index=i,
                indicator_snapshot=snapshot,
            ))
            last_bar = i

        return triggers
