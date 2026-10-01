"""模式挖掘模板（add-ai-research-mcp 2.5）：discovery 引擎层包装。

语义：任意历史时点 t 的 similar/predict 查询——"当时的市场状态最像
历史哪些时刻、那些时刻之后发生了什么"。引擎层（SimilarCaseEngine）
本就支持任意特征向量；路由层的"末根 bar 硬编码"不在引擎层，本模板
直接调引擎层暴露历史时点能力（proposal 既定路径）。

防前视（模板层责任）：fit 只用 ``as_of`` 之前的数据——时点 t 的
邻域与后续收益样本只能来自 t 之前，fit 见到 t 之后的数据即前视。

已知限制（design Risks 登记在案，M1 后置修复）：FeatureEncoder
按前缀自动挑列（~16 维）且 NaN fillna(0)——跨查询特征一致性依赖
同一 fit；区间头部时点的特征有偏（数据无 warmup）。
"""
from __future__ import annotations

from typing import Any, Dict

import numpy as np
import pandas as pd

from core.analysis.result import AnalysisResult, build_result
from core.analysis.warmup_loader import to_utc


def similar_at_time(
    df: pd.DataFrame,
    *,
    as_of: str,
    n_neighbors: int = 50,
    horizon: int = 12,
    data_ranges: list[dict[str, str]],
    hypothesis_id: str | None = None,
) -> AnalysisResult:
    """任意时点 similar/predict（discovery 引擎层包装）。

    Args:
        as_of: 查询时点（ISO/UTC）；该时点的行作为特征向量来源。
        n_neighbors: 邻域数（引擎默认 50）。
        horizon: 引擎前向窗（bar）。

    Raises:
        ValueError: as_of 不在数据内 / 历史不足以 fit。
    """
    from core.discovery.knn_engine import SimilarCaseEngine

    # to_utc 统一（多 tz_convert 一步——aware 输入显式转 UTC，
    # 与 df.index 比较语义等价：pandas 按绝对时刻比较）
    as_of_ts = to_utc(pd.Timestamp(as_of))
    row_mask = df.index <= as_of_ts
    if not row_mask.any():
        raise ValueError(f"as_of 时点无数据（早于数据起点）: {as_of}")
    if as_of_ts > df.index[-1]:
        # as_of 落在数据末之后 = 查询未来时点（无意义且易掩盖传参错误），拒绝
        raise ValueError(f"as_of 时点无数据（晚于数据末尾 {df.index[-1]}）: {as_of}")

    t_pos = int(np.where(row_mask)[0][-1])

    # 防前视：fit 只用 t 之前（不含 as_of 行本身之后的任何 bar）
    hist = df.iloc[: t_pos + 1]
    if len(hist) < n_neighbors + horizon:
        raise ValueError(
            f"历史不足（{len(hist)} bar < n_neighbors+horizon={n_neighbors + horizon}）")

    engine = SimilarCaseEngine(n_neighbors=n_neighbors, horizon=horizon).fit(hist)

    # as_of 时点特征向量：与引擎 fit 同一 encoder（transform 用 fit 选定列）
    t_row = df.iloc[[t_pos]]
    feats = engine.encoder.transform(t_row).reshape(-1)

    cases = engine.find_similar(feats, n_neighbors=n_neighbors)
    prediction = engine.predict(feats)

    # 邻域防前视断言：所有邻域时刻严格早于 as_of
    neighbor_times = [c.timestamp for c in cases]
    payload = {
        "as_of": str(df.index[t_pos]),
        "n_neighbors": len(cases),
        "feature_columns": list(engine.encoder.feature_columns),
        "neighbors": [
            {"timestamp": c.timestamp, "distance": float(c.distance),
             "future_return_pct": float(c.future_return_pct)}
            for c in cases[:20]  # 裁剪：MCP 返回不铺满
        ],
        "prediction": {
            "direction": prediction.predicted_direction,
            "avg_return": float(prediction.avg_return),
            "median_return": float(prediction.median_return),
            "positive_pct": float(prediction.positive_pct),
            "price_range_low": float(prediction.price_range_low),
            "price_range_high": float(prediction.price_range_high),
            "engine_confidence": float(prediction.confidence),
        },
        "neighbor_time_max": max(neighbor_times) if neighbor_times else None,
    }

    confidence = {
        "method": "knn_neighbor_returns",
        "interval": [float(prediction.price_range_low), float(prediction.price_range_high)],
        "for": "neighbor_return_q25_q75",
        "n": len(cases),
        "horizon": horizon,
    }

    return build_result(
        "pattern_mining",
        sample_size=len(hist),
        confidence=confidence,
        data_ranges=data_ranges,
        payload=payload,
        hypothesis_id=hypothesis_id,
    )
