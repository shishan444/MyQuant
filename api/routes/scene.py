"""Scene verification API routes."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from api.deps import get_data_dir
from core.validation.scene.scene_engine import (
    run_scene_verification,
    SCENE_META,
)

router = APIRouter(prefix="/api", tags=["scene"])


# ---------------------------------------------------------------------------
# Request / Response models
# ---------------------------------------------------------------------------

class SceneRequest(BaseModel):
    symbol: str = Field(..., description="Trading pair, e.g. BTCUSDT")
    timeframe: str = Field("4h", description="K-line interval")
    scene_type: str = Field(..., description="Scene detector type")
    params: Dict[str, Any] = Field(default_factory=dict, description="Detector parameters")
    horizons: List[int] = Field(default=[6, 12, 24, 48], description="Forward-looking bar counts")
    data_start: Optional[str] = Field(None, description="Start date YYYY-MM-DD")
    data_end: Optional[str] = Field(None, description="End date YYYY-MM-DD")


class HorizonSummary(BaseModel):
    # win_rate_ci: Wilson 95% 置信区间（百分比域，与 win_rate 同口径）
    horizon: int
    total_triggers: int
    win_rate: float
    win_rate_ci: Optional[List[float]] = None
    avg_return_pct: float
    median_return_pct: float
    avg_max_gain_pct: float
    avg_max_loss_pct: float
    avg_bars_to_peak: float
    distribution: List[Dict[str, Any]] = []
    percentiles: Dict[str, float] = {}


class TriggerDetail(BaseModel):
    id: int
    timestamp: str
    trigger_price: float
    indicator_snapshot: Dict[str, float] = {}
    forward_stats: Dict[str, Dict[str, Any]] = {}
    pattern_subtype: str = ""
    pattern_metadata: Dict[str, Any] = {}


class SceneResponse(BaseModel):
    scene_type: str
    scene_label: str = ""
    scene_description: str = ""
    total_triggers: int
    statistics_by_horizon: List[HorizonSummary] = []
    trigger_details: List[TriggerDetail] = []
    warnings: List[str] = []


class SceneTypesResponse(BaseModel):
    """List of available scene types with metadata."""
    types: List[Dict[str, str]]


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.get("/scene/types", response_model=SceneTypesResponse)
def list_scene_types() -> SceneTypesResponse:
    """Return available scene types with labels and descriptions."""
    types = [
        {
            "id": key,
            "label": meta["label"],
            "description": meta["description"],
            "group": meta.get("group", ""),
            "parent": meta.get("parent", ""),
        }
        for key, meta in SCENE_META.items()
    ]
    return SceneTypesResponse(types=types)


@router.post("/validate/scene", response_model=SceneResponse)
def run_scene(
    payload: SceneRequest,
    data_dir: Path = Depends(get_data_dir),
) -> SceneResponse:
    """Run scene verification against historical data."""
    result = run_scene_verification(
        symbol=payload.symbol,
        timeframe=payload.timeframe,
        scene_type=payload.scene_type,
        params=payload.params,
        horizons=payload.horizons,
        data_start=payload.data_start,
        data_end=payload.data_end,
        data_dir=str(data_dir),
    )

    meta = SCENE_META.get(payload.scene_type, {})
    if not meta:
        meta = SCENE_META.get(result.scene_type, {})

    return SceneResponse(
        scene_type=result.scene_type,
        scene_label=meta.get("label", payload.scene_type),
        scene_description=meta.get("description", ""),
        total_triggers=result.total_triggers,
        statistics_by_horizon=[
            HorizonSummary(
                horizon=s.horizon,
                total_triggers=s.total_triggers,
                win_rate=s.win_rate,
                win_rate_ci=s.win_rate_ci,
                avg_return_pct=s.avg_return_pct,
                median_return_pct=s.median_return_pct,
                avg_max_gain_pct=s.avg_max_gain_pct,
                avg_max_loss_pct=s.avg_max_loss_pct,
                avg_bars_to_peak=s.avg_bars_to_peak,
                distribution=s.distribution,
                percentiles=s.percentiles,
            )
            for s in result.statistics_by_horizon
        ],
        trigger_details=[
            TriggerDetail(
                id=t["id"],
                timestamp=t["timestamp"],
                trigger_price=t["trigger_price"],
                indicator_snapshot=t.get("indicator_snapshot", {}),
                forward_stats=t.get("forward_stats", {}),
                pattern_subtype=t.get("pattern_subtype", ""),
                pattern_metadata=t.get("pattern_metadata", {}),
            )
            for t in result.trigger_details
        ],
        warnings=result.warnings,
    )
