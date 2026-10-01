"""research 路由公共工具层（零兄弟依赖）。

服务对象：research 子包各路由（analysis/cases/indicator_query/
plugins/snapshot/validation/hypotheses），范围外 api/routes/validate
经 hypotheses 的 re-export 间接消费。价值：ResearchError ->
HTTPException 转换契约单点维护，路由层 except 形制统一。
职责边界：只收无路由状态的纯工具；不 import 任何兄弟模块——
本模块是子包内共享底座，向上依赖会形成循环。
"""
from __future__ import annotations

from fastapi import HTTPException

from core.analysis.hypothesis_service import ResearchError


def research_error_to_http(exc: ResearchError) -> HTTPException:
    """ResearchError -> HTTPException（detail 含 code/message/detail 三段）。"""
    return HTTPException(
        status_code=exc.status,
        detail={"code": exc.code, "message": exc.message, "detail": exc.detail},
    )


def require_known_timeframe(timeframe: str, field: str) -> None:
    """timeframe 值域前置校验：非法即 ResearchError TIMEFRAME_UNKNOWN（422）。

    输入语义：timeframe 为待校验周期串；field 为请求字段路径（错误
    detail 披露定位用）。边界条件：合法=TF_SECONDS 键集成员（与读取
    路径 validate_known_interval 同口径）。错误语义：非法即拒，
    legal_range 附合法清单，形制照 hypothesis_service 既有先例——
    若不前置，装载层 validate_known_interval 抛裸 ValueError，路由层
    只捕 ResearchError，非法周期会裸穿 500。
    """
    from core.data.timeframes import TF_SECONDS

    if timeframe not in TF_SECONDS:
        raise ResearchError(
            "TIMEFRAME_UNKNOWN",
            f"未知周期: {timeframe!r}",
            detail={"field": field, "timeframe": timeframe,
                    "legal_range": sorted(TF_SECONDS)})
