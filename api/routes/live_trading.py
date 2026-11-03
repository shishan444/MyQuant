"""实盘交易（demo mode）REST API（第8步）—— /api/live-trading/*。

任务 CRUD（创建/查询/列表/更新/删除）。Runner 循环的启动/停止（线程生命周期）
属集成层，后续接入 app.state（参考 trading.py 的 TradingRunner 模式）。
"""
from __future__ import annotations

import logging
import uuid
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request

from api.deps import get_db_path
from api.schemas import (
    LiveTradingTaskCreate,
    LiveTradingTaskListResponse,
    LiveTradingTaskResponse,
    LiveTradingTaskUpdate,
)
from core.persistence.repository import live_trading as repo

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/live-trading", tags=["live-trading"])


@router.post("/tasks", response_model=LiveTradingTaskResponse, status_code=201)
def create_task(req: LiveTradingTaskCreate, db_path=Depends(get_db_path)):
    """创建实盘任务（存 DNA + 方向 + 风控配置）。"""
    # dna_json 预检（形制对照 trading.py：异常文本形态不可控，detail
    # 固定文案、原文经日志保留）
    try:
        from core.strategy.dna import StrategyDNA
        dna = StrategyDNA.from_json(req.dna_json)
    except Exception as e:
        logger.warning("invalid dna_json on live task create: %r", e, exc_info=e)
        raise HTTPException(status_code=400, detail="Invalid dna_json format")

    # 手工 DNA 值域校验（review-042 CARD-12）+插件源能力边界
    # （recognize-plugin-source-dna T4，第一问·方向二）：live 链未
    # 接入插件信号组装，插件源在场即拒（含混合形态）；错误码分立
    # （补丁①）：PLUGIN_SOURCES_UNSUPPORTED=「执行环境不支持」而非
    # 「策略无效」——结构校验已过（上方），此处是环境能力边界。
    # signal-sources-structure-gate 卡 G 起两段拒绝表达收敛为
    # strategies/_common helper（跨目录函数级 import 无环；判据单源
    # 不动，消息逐字节不变）。
    from api.routes.strategies._common import _reject_invalid_or_plugin_dna
    _reject_invalid_or_plugin_dna(dna, library_dna=False, channel="live")

    # live 交易链兼容性前移（装载即拒的入口化——CARD-12 早拒锚）：trail
    # /分批等 live 链不支持的模式原本在 Runner 装载点才拒（任务已落库
    # 才失败），创建点前移到落库前——与 LiveTradingExecutor.__post_init__
    # 同一判据（validate_trading_compatibility 单源）
    from core.strategy.validator import validate_trading_compatibility
    _reasons = validate_trading_compatibility(dna, "live")
    if _reasons:
        raise HTTPException(
            status_code=422,
            detail={"code": "DNA_TRADING_INCOMPATIBLE",
                    "message": "live 链不支持该策略（装载即拒前移）",
                    "errors": _reasons})

    task_id = f"lt_{uuid.uuid4().hex[:12]}"
    repo.save_live_trading_task(
        db_path, task_id, req.symbol, req.timeframe, req.dna_json,
        req.direction, req.config_json,
    )
    return repo.get_live_trading_task(db_path, task_id)


@router.post("/tasks/{task_id}/start", response_model=LiveTradingTaskResponse)
def start_task(task_id: str, request: Request, db_path=Depends(get_db_path)):
    """D5 契约基线：pending→running；runner 装配挂 app.state（live_runner
    ——paper_scheduler 先例形制；内核未建时占位 None+状态先行迁移可观察）。"""
    try:
        out = repo.transition_live_task(db_path, task_id, "start")
    except repo.IllegalTransition as e:
        raise HTTPException(status_code=409, detail=str(e))
    if not out:
        raise HTTPException(404, detail="live trading task not found")
    # runner 装配挂 app.state（内核待 D5 全链设计卡——占位在册）
    request.app.state.live_runner = getattr(
        request.app.state, "live_runner", None)
    return out


@router.post("/tasks/{task_id}/stop", response_model=LiveTradingTaskResponse)
def stop_task(task_id: str, db_path=Depends(get_db_path)):
    """D5：running→stopped（stop_reason 落因；graceful=当前 bar 完退出
    ——runner 侧语义，状态迁移先行可观察）；停止已停 409。"""
    try:
        out = repo.transition_live_task(db_path, task_id, "stop")
    except repo.IllegalTransition as e:
        raise HTTPException(status_code=409, detail=str(e))
    if not out:
        raise HTTPException(404, detail="live trading task not found")
    return out


@router.get("/tasks/{task_id}/decisions")
def list_decisions(
    task_id: str, limit: int = 50, offset: int = 0,
    db_path=Depends(get_db_path),
):
    """D4 决策快照分页（append-only 只读——B6r 回放消费；零决策 200
    空集如实〔D5 前/启动初期〕；404 平文本沿域内先例）。"""
    if not repo.get_live_trading_task(db_path, task_id):
        raise HTTPException(404, detail="live trading task not found")
    rows, total = repo.list_live_decisions(
        db_path, task_id, limit=limit + 1, offset=offset)
    has_more = len(rows) > limit
    rows = rows[:limit]
    return {"items": rows, "total": total, "offset": offset,
            "limit": limit, "has_more": has_more}


@router.get("/tasks", response_model=LiveTradingTaskListResponse)
def list_tasks(status: Optional[str] = None, db_path=Depends(get_db_path)):
    tasks = repo.list_live_trading_tasks(db_path, status)
    return {"tasks": tasks, "total": len(tasks)}


@router.get("/tasks/{task_id}", response_model=LiveTradingTaskResponse)
def get_task(task_id: str, db_path=Depends(get_db_path)):
    t = repo.get_live_trading_task(db_path, task_id)
    if not t:
        raise HTTPException(404, detail="live trading task not found")
    return t


@router.patch("/tasks/{task_id}", response_model=LiveTradingTaskResponse)
def update_task(task_id: str, req: LiveTradingTaskUpdate, db_path=Depends(get_db_path)):
    if not repo.get_live_trading_task(db_path, task_id):
        raise HTTPException(404, detail="live trading task not found")
    updates = {k: v for k, v in req.model_dump().items() if v is not None}
    if updates:
        repo.update_live_trading_task(db_path, task_id, **updates)
    return repo.get_live_trading_task(db_path, task_id)


@router.delete("/tasks/{task_id}")
def remove_task(task_id: str, db_path=Depends(get_db_path)):
    if not repo.delete_live_trading_task(db_path, task_id):
        raise HTTPException(404, detail="live trading task not found")
    return {"deleted": True, "task_id": task_id}
