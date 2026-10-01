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
