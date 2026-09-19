"""Research 路由包（add-ai-research-mcp 3.x）。

形制照 api/routes/strategies 子包先例：子 router 无前缀，聚合
include 时统一附加 prefix/tags。
"""
from __future__ import annotations

from fastapi import APIRouter

from . import analysis, hypotheses, log, registry, validation

router = APIRouter()
for _sub in (hypotheses, validation, analysis, registry, log):
    router.include_router(_sub.router, prefix="/api/research", tags=["research"])
