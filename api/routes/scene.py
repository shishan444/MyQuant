"""场景验证入口（/api/scene/types 与 /api/validate/scene）。

职能：暴露 core.validation.scene 的场景探测器清单与历史数据回放
验证（触发统计/前瞻收益分布）。服务对象：web Lab 场景页。
职责边界：传输校验与 ResearchError→HTTP 映射，检测与统计在引擎层。
"""
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
# 请求/响应模型
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
    """可用场景类型清单（含元数据）。"""
    types: List[Dict[str, str]]


# ---------------------------------------------------------------------------
# 端点
# ---------------------------------------------------------------------------

@router.get("/scene/types", response_model=SceneTypesResponse)
def list_scene_types() -> SceneTypesResponse:
    """返回可用场景类型清单（标签/描述/分组，供前端选择器）。"""
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
    """对历史数据跑场景验证（触发统计与前瞻收益分布）。"""
    from core.analysis.hypothesis_service import ResearchError
    from api.routes.research.common import research_error_to_http

    try:
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
    except ResearchError as exc:
        # 坏参数/未知 timeframe 结构化拒绝（与 validate.py 两端点同款兜底），
        # 不裸穿 500
        raise research_error_to_http(exc) from exc

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
