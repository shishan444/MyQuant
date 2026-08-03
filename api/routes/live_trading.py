"""实盘交易（demo mode）REST API（第8步）—— /api/live-trading/*。

任务 CRUD（创建/查询/列表/更新/删除）。Runner 循环的启动/停止（线程生命周期）
属集成层，后续接入 app.state（参考 trading.py 的 TradingRunner 模式）。
"""
from __future__ import annotations

import uuid
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException

from api.deps import get_db_path
from api.schemas import (
    LiveTradingTaskCreate,
    LiveTradingTaskListResponse,
    LiveTradingTaskResponse,
    LiveTradingTaskUpdate,
)
from core.live_trading.persistence import (
    delete_live_trading_task,
    get_live_trading_task,
    init_live_trading_tables,
    list_live_trading_tasks,
    save_live_trading_task,
    update_live_trading_task,
)

router = APIRouter(prefix="/api/live-trading", tags=["live-trading"])


@router.post("/tasks", response_model=LiveTradingTaskResponse, status_code=201)
def create_task(req: LiveTradingTaskCreate, db_path=Depends(get_db_path)):
    """创建实盘任务（存 DNA + 方向 + 风控配置）。"""
    init_live_trading_tables(db_path)
    task_id = f"lt_{uuid.uuid4().hex[:12]}"
    save_live_trading_task(
        db_path, task_id, req.symbol, req.timeframe, req.dna_json,
        req.direction, req.config_json,
    )
    return get_live_trading_task(db_path, task_id)


@router.get("/tasks", response_model=LiveTradingTaskListResponse)
def list_tasks(status: Optional[str] = None, db_path=Depends(get_db_path)):
    tasks = list_live_trading_tasks(db_path, status)
    return {"tasks": tasks, "total": len(tasks)}


@router.get("/tasks/{task_id}", response_model=LiveTradingTaskResponse)
def get_task(task_id: str, db_path=Depends(get_db_path)):
    t = get_live_trading_task(db_path, task_id)
    if not t:
        raise HTTPException(404, detail="live trading task not found")
    return t


@router.patch("/tasks/{task_id}", response_model=LiveTradingTaskResponse)
def update_task(task_id: str, req: LiveTradingTaskUpdate, db_path=Depends(get_db_path)):
    if not get_live_trading_task(db_path, task_id):
        raise HTTPException(404, detail="live trading task not found")
    updates = {k: v for k, v in req.model_dump().items() if v is not None}
    if updates:
        update_live_trading_task(db_path, task_id, **updates)
    return get_live_trading_task(db_path, task_id)


@router.delete("/tasks/{task_id}")
def remove_task(task_id: str, db_path=Depends(get_db_path)):
    if not delete_live_trading_task(db_path, task_id):
        raise HTTPException(404, detail="live trading task not found")
    return {"deleted": True, "task_id": task_id}
