"""模拟盘任务 CRUD 与控制（/api/trading/*）。

职能：paper trading 任务的创建/查询/启停/重启/删除与成交、净值快照
查询。服务对象：web 交易页与 core.trading.runner（TradingRunner 经
任务表驱动执行）。
职责边界：任务生命周期入口，撮合与执行循环在 TradingRunner。
"""
from __future__ import annotations

import logging
import uuid
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from core.persistence.repository.paper_trading import (
    save_paper_trading_task,
    get_paper_trading_task,
    update_paper_trading_task,
    list_paper_trading_tasks,
    count_paper_trading_tasks,
    list_paper_trades,
    count_paper_trades,
    delete_paper_trading_task,
    list_equity_snapshots,
    compute_trading_metrics,
)
from api.deps import get_db_path

logger = logging.getLogger(__name__)
from api.schemas import (
    PaperTradingTaskCreate,
    PaperTradingTaskResponse,
    PaperTradingTaskListResponse,
    PaperTradeListResponse,
    PaperTradeResponse,
    EquitySnapshotResponse,
    EquitySnapshotListResponse,
    TradingMetricsResponse,
)

router = APIRouter(prefix="/api/trading", tags=["trading"])


def _task_to_response(row: dict) -> PaperTradingTaskResponse:
    return PaperTradingTaskResponse(
        task_id=row["task_id"],
        status=row["status"],
        strategy_name=row.get("strategy_name"),
        symbol=row["symbol"],
        timeframe=row["timeframe"],
        initial_cash=row["initial_cash"],
        fee=row["fee"],
        leverage=row["leverage"],
        direction=row["direction"],
        score_template=row.get("score_template", "explorer"),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        started_at=row.get("started_at"),
        stopped_at=row.get("stopped_at"),
        stop_reason=row.get("stop_reason"),
        position_side=row.get("position_side"),
        position_entry=row.get("position_entry"),
        position_quantity=row.get("position_quantity"),
        position_margin=row.get("position_margin"),
        position_funding=row.get("position_funding"),
        balance=row.get("balance"),
        unrealized_pnl=row.get("unrealized_pnl", 0.0),
        total_trades=row.get("total_trades", 0),
        total_pnl=row.get("total_pnl", 0.0),
        win_count=row.get("win_count", 0),
        loss_count=row.get("loss_count", 0),
        last_bar_time=row.get("last_bar_time"),
        last_bar_close=row.get("last_bar_close"),
        execution_model=row.get("execution_model", "v1"),
        confidence_sizing_enabled=bool(row.get("confidence_sizing_enabled", 0)),
        prediction_dna_json=row.get("prediction_dna_json"),
    )


@router.post("/tasks", response_model=PaperTradingTaskResponse, status_code=201)
def create_task(
    body: PaperTradingTaskCreate,
    db_path: Path = Depends(get_db_path),
) -> PaperTradingTaskResponse:
    # dna_json 预检（解析失败=业务拒绝 400）
    try:
        from core.strategy.dna import StrategyDNA
        dna = StrategyDNA.from_json(body.dna_json)
    except Exception as e:
        # B6.1 status 正确性：dna_json 解析失败=业务拒绝（422 保留给 Pydantic
        # 校验明细数组——G16.0 契约分层）；异常文本形态不可控，detail 固定
        # 文案、原文经日志保留
        logger.warning("invalid dna_json on task create: %r", e, exc_info=e)
        raise HTTPException(status_code=400, detail="Invalid dna_json format")

    # 手工 DNA 值域校验（review-042 CARD-12）：与解析预检分立——解析过
    # 而值域坏（NaN/越域/bool 收窄）此前静默落库，引擎 sl=ep×(1−1.0)=0
    # 恒不触发。422+结构化 detail（dict 形制与 Pydantic 自动 422 的明细
    # 数组可判别，非 G16.0 所指解析失败 400）
    from core.strategy.validator import validate_dna
    _vr = validate_dna(dna)
    if not _vr.is_valid:
        raise HTTPException(
            status_code=422,
            detail={"code": "DNA_VALIDATION_FAILED",
                    "message": "DNA 值域/结构校验未通过",
                    "errors": _vr.errors})

    task_id = uuid.uuid4().hex[:12]
    save_paper_trading_task(
        db_path,
        task_id=task_id,
        dna_json=body.dna_json,
        symbol=body.symbol,
        timeframe=body.timeframe,
        initial_cash=body.initial_cash,
        fee=body.fee,
        leverage=body.leverage,
        direction=body.direction,
        score_template=body.score_template,
        strategy_name=body.strategy_name,
        confidence_sizing_enabled=body.confidence_sizing_enabled,
        prediction_dna_json=body.prediction_dna_json,
    )
    row = get_paper_trading_task(db_path, task_id)
    response = _task_to_response(row)

    # 达标预警：带 strategy_id 创建时回查 qualified 状态
    warnings = []
    if body.strategy_id:
        from core.persistence.repository.strategy import get_strategy
        strat = get_strategy(db_path, body.strategy_id)
        if strat is not None:
            qualified_val = strat.get("qualified")
            if qualified_val is not None and not bool(qualified_val):
                warnings.append(
                    "Strategy has not passed qualification checks (qualified=False). "
                    "Performance may not meet minimum requirements."
                )

    response.warnings = warnings if warnings else None
    return response


@router.get("/tasks", response_model=PaperTradingTaskListResponse)
def list_tasks(
    status: Optional[str] = None,
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    db_path: Path = Depends(get_db_path),
) -> PaperTradingTaskListResponse:
    tasks = list_paper_trading_tasks(db_path, status=status, limit=limit, offset=offset)
    total = count_paper_trading_tasks(db_path, status=status)
    return PaperTradingTaskListResponse(
        tasks=[_task_to_response(t) for t in tasks],
        total=total,
    )


@router.get("/tasks/{task_id}", response_model=PaperTradingTaskResponse)
def get_task(
    task_id: str,
    db_path: Path = Depends(get_db_path),
) -> PaperTradingTaskResponse:
    row = get_paper_trading_task(db_path, task_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Task not found")
    return _task_to_response(row)


@router.post("/tasks/{task_id}/stop", response_model=PaperTradingTaskResponse)
def stop_task(
    task_id: str,
    db_path: Path = Depends(get_db_path),
) -> PaperTradingTaskResponse:
    row = get_paper_trading_task(db_path, task_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Task not found")
    if row["status"] not in ("running", "pending"):
        raise HTTPException(status_code=400, detail=f"Cannot stop task in status '{row['status']}'")

    # 控制器直达信号（即时停止执行循环）
    from core.trading.runner import get_trading_controllers
    controller = get_trading_controllers().get(task_id)
    if controller:
        controller.request_stop()

    # DB 状态同步落 stopped（双保险：runner 缺席也保证终态）
    update_paper_trading_task(db_path, task_id, status="stopped", stop_reason="user_stop")
    row = get_paper_trading_task(db_path, task_id)
    return _task_to_response(row)


@router.post("/tasks/{task_id}/restart", response_model=PaperTradingTaskResponse)
def restart_task(
    task_id: str,
    db_path: Path = Depends(get_db_path),
) -> PaperTradingTaskResponse:
    """按既有任务同策略/同参数克隆创建新任务。"""
    row = get_paper_trading_task(db_path, task_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Task not found")

    new_task_id = uuid.uuid4().hex[:12]
    save_paper_trading_task(
        db_path,
        task_id=new_task_id,
        strategy_name=row.get("strategy_name"),
        symbol=row["symbol"],
        timeframe=row["timeframe"],
        initial_cash=row["initial_cash"],
        fee=row["fee"],
        leverage=row["leverage"],
        direction=row["direction"],
        dna_json=row["dna_json"],
        score_template=row.get("score_template", "explorer"),
        confidence_sizing_enabled=bool(row.get("confidence_sizing_enabled", 0)),
        prediction_dna_json=row.get("prediction_dna_json"),
    )
    new_row = get_paper_trading_task(db_path, new_task_id)
    return _task_to_response(new_row)


@router.get("/tasks/{task_id}/trades", response_model=PaperTradeListResponse)
def get_trades(
    task_id: str,
    limit: int = Query(100, ge=1, le=500),
    db_path: Path = Depends(get_db_path),
) -> PaperTradeListResponse:
    row = get_paper_trading_task(db_path, task_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Task not found")
    trades = list_paper_trades(db_path, task_id, limit=limit)
    total = count_paper_trades(db_path, task_id)
    return PaperTradeListResponse(
        trades=[PaperTradeResponse(**t) for t in trades],
        total=total,
    )


@router.get("/tasks/{task_id}/equity", response_model=EquitySnapshotListResponse)
def get_equity(
    task_id: str,
    db_path: Path = Depends(get_db_path),
) -> EquitySnapshotListResponse:
    row = get_paper_trading_task(db_path, task_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Task not found")
    snapshots = list_equity_snapshots(db_path, task_id)
    return EquitySnapshotListResponse(
        snapshots=[EquitySnapshotResponse(**s) for s in snapshots],
        total=len(snapshots),
    )


@router.get("/tasks/{task_id}/metrics", response_model=TradingMetricsResponse)
def get_metrics(
    task_id: str,
    db_path: Path = Depends(get_db_path),
) -> TradingMetricsResponse:
    metrics = compute_trading_metrics(db_path, task_id)
    if metrics is None:
        raise HTTPException(status_code=404, detail="Task not found")
    return TradingMetricsResponse(**metrics)


@router.delete("/tasks/{task_id}")
def delete_task(
    task_id: str,
    db_path: Path = Depends(get_db_path),
) -> dict:
    row = get_paper_trading_task(db_path, task_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Task not found")
    if row["status"] in ("running", "pending"):
        raise HTTPException(status_code=400, detail="Cannot delete active task. Stop it first.")
    ok = delete_paper_trading_task(db_path, task_id)
    return {"deleted": ok}


@router.get("/runner-status")
def runner_status(request: Request) -> dict:
    runner = getattr(request.app.state, "trading_runner", None)
    if runner:
        return runner.get_status()
    return {"is_alive": False, "active_task_id": None}
