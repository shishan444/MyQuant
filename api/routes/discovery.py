"""模式发现入口（/api/discovery/*：规则发现/相似案例/区间预测）。

职能：暴露 core.discovery 的三个引擎（决策树规则发现、KNN 相似
案例、KNN 区间预测）为 REST 端点。服务对象：暂无稳定前端消费方
（历史入口，OpenAPI generated 类型仍在）。
职责边界：传输与引擎装配，发现与预测算法在 core.discovery。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from pathlib import Path

from api.deps import get_db_path, get_data_dir

router = APIRouter(prefix="/api/discovery", tags=["discovery"])


class DiscoveryRequest(BaseModel):
    symbol: str = "BTCUSDT"
    timeframe: str = "4h"
    horizon: int = 12
    max_depth: int = 5
    data_start: Optional[str] = None
    data_end: Optional[str] = None


class SimilarRequest(BaseModel):
    symbol: str = "BTCUSDT"
    timeframe: str = "4h"
    horizon: int = 12
    n_neighbors: int = 50
    data_start: Optional[str] = None
    data_end: Optional[str] = None


class PredictRequest(BaseModel):
    symbol: str = "BTCUSDT"
    timeframe: str = "4h"
    horizon: int = 12
    n_neighbors: int = 50
    data_start: Optional[str] = None
    data_end: Optional[str] = None


def _load_enhanced_df(data_dir: Path, symbol: str, timeframe: str,
                      data_start: Optional[str], data_end: Optional[str]):
    """装载并增强行情数据（发现族三端点共用的数据入口）。"""
    from core.data.mtf_loader import load_and_prepare_df
    return load_and_prepare_df(data_dir, symbol, timeframe, data_start, data_end)


@router.post("/patterns")
def discover_patterns(
    req: DiscoveryRequest,
    data_dir: Path = Depends(get_data_dir),
) -> Dict[str, Any]:
    """决策树模式发现：从历史数据挖掘 top 交易规则。"""
    df = _load_enhanced_df(data_dir, req.symbol, req.timeframe,
                           req.data_start, req.data_end)
    if df is None:
        return {"error": "No data available", "rules": []}

    from core.discovery.tree_engine import PatternDiscoveryEngine
    engine = PatternDiscoveryEngine(
        max_depth=req.max_depth,
        horizon=req.horizon,
    )
    result = engine.discover(df)

    return {
        "rules": [
            {
                "conditions": r.conditions,
                "direction": r.direction,
                "confidence": r.confidence,
                "samples": r.samples,
                "lift": r.lift,
            }
            for r in result.rules
        ],
        "accuracy": result.accuracy,
        "cv_scores": result.cv_scores,
        "feature_importance": result.feature_importance,
        "tree_depth": result.tree_depth,
        "n_samples": result.n_samples,
    }


@router.post("/similar")
def find_similar(
    req: SimilarRequest,
    data_dir: Path = Depends(get_data_dir),
) -> Dict[str, Any]:
    """KNN 相似案例：最相似历史行情及其后续走势。"""
    df = _load_enhanced_df(data_dir, req.symbol, req.timeframe,
                           req.data_start, req.data_end)
    if df is None:
        return {"error": "No data available", "cases": []}

    from core.discovery.knn_engine import SimilarCaseEngine

    engine = SimilarCaseEngine(n_neighbors=req.n_neighbors, horizon=req.horizon)
    engine.fit(df)

    # 末行即当前状态（对齐前端图表末端）
    encoder = engine.encoder
    current_features = encoder.transform(df.iloc[[-1]])[0]

    cases = engine.find_similar(current_features, req.n_neighbors)

    return {
        "cases": [
            {
                "index": c.index,
                "timestamp": c.timestamp,
                "close_price": c.close_price,
                "future_return_pct": c.future_return_pct,
                "future_high_pct": c.future_high_pct,
                "future_low_pct": c.future_low_pct,
                "distance": round(c.distance, 4),
            }
            for c in cases
        ],
    }


@router.post("/predict")
def predict_range(
    req: PredictRequest,
    data_dir: Path = Depends(get_data_dir),
) -> Dict[str, Any]:
    """KNN 区间预测：方向/置信度与价格区间。"""
    df = _load_enhanced_df(data_dir, req.symbol, req.timeframe,
                           req.data_start, req.data_end)
    if df is None:
        return {"error": "No data available"}

    from core.discovery.knn_engine import SimilarCaseEngine

    engine = SimilarCaseEngine(n_neighbors=req.n_neighbors, horizon=req.horizon)
    engine.fit(df)

    # 末行即当前状态（对齐前端图表末端）
    current_features = engine.encoder.transform(df.iloc[[-1]])[0]
    prediction = engine.predict(current_features, req.n_neighbors)

    return {
        "predicted_direction": prediction.predicted_direction,
        "avg_return": prediction.avg_return,
        "median_return": prediction.median_return,
        "positive_pct": prediction.positive_pct,
        "price_range_low": prediction.price_range_low,
        "price_range_high": prediction.price_range_high,
        "confidence": prediction.confidence,
        "accuracy": prediction.accuracy,
        "n_cases": len(prediction.similar_cases),
    }
