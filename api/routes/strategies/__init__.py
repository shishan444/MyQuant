"""Strategies 路由包（B5.1 自单文件 strategies.py 拆分；retire-legacy
任务 11 后收缩为 CRUD 单模块——backtest/compare/batch 三通道随 Q1=B
退役，策略库页核算走 sandbox 统一流）。

对外接口不变：`from api.routes.strategies import router`（app.py 引用零改动）。
"""
from __future__ import annotations

from fastapi import APIRouter

from . import crud

router = APIRouter()
router.include_router(crud.router, prefix="/api/strategies", tags=["strategies"])

__all__ = ["router"]
