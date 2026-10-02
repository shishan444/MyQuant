"""时点行情快照端点（add-market-snapshot 2.1）。

GET 只读先例（照 hypotheses.py list_hypotheses）：query 直收透传
服务层；ResearchError 统一 research_error_to_http 透传。返回结构即
服务层形制（顶层 symbol/as_of/window_days/notes/timeframes），路由层
零新增字段——响应体积 2KB 约束余量紧。
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends

from api.deps import get_data_dir, get_db_path
from api.routes.research.common import research_error_to_http
from core.analysis import market_snapshot as ms
from core.analysis.hypothesis_service import ResearchError

router = APIRouter()


@router.get("/market-snapshot")
def read_market_snapshot(
    symbol: str,
    timeframes: Optional[str] = None,
    as_of: Optional[str] = None,
    db_path: Path = Depends(get_db_path),
    data_dir: Path = Depends(get_data_dir),
) -> Dict[str, Any]:
    """时点行情快照：多周期五维行情卡（读数+90 日分位+三态判读）。

    timeframes 逗号分隔周期子集（如 "1d,4h"；缺省四档全量）；空集
    或非法周期由服务层结构化拒绝（422）。as_of 读数时点（缺省=最新
    一根 bar；闭端截至防前视）。
    """
    tfs: Optional[list[str]] = None
    if timeframes is not None:
        tfs = [t.strip() for t in timeframes.split(",") if t.strip()]
    try:
        return ms.get_market_snapshot(
            db_path, data_dir, symbol=symbol,
            timeframes=tfs, as_of=as_of)
    except ResearchError as exc:
        raise research_error_to_http(exc) from exc
