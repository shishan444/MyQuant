"""REST API routes for paper trading tasks."""
from __future__ import annotations

import logging
import uuid
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request

from core.persistence.db_ext import (
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
    # Validate dna_json format
    try:
        from core.strategy.dna import StrategyDNA
        StrategyDNA.from_json(body.dna_json)
    except Exception as e:
        # B6.1 status 正确性：dna_json 解析失败=业务拒绝（422 保留给 Pydantic
        # 校验明细数组——G16.0 契约分层）；异常文本形态不可控，detail 固定
        # 文案、原文经日志保留
        logger.warning("invalid dna_json on task create: %r", e, exc_info=e)
        raise HTTPException(status_code=400, detail="Invalid dna_json format")

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

    # Qualified warning check: if strategy_id provided, look up qualified status
    warnings = []
    if body.strategy_id:
        from core.persistence.db_ext import get_strategy
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
    limit: int = 50,
    offset: int = 0,
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

    # Signal via controller (immediate)
    from core.trading.runner import get_trading_controllers
    controller = get_trading_controllers().get(task_id)
    if controller:
        controller.request_stop()

    # Also set DB status (belt-and-suspenders)
    update_paper_trading_task(db_path, task_id, status="stopped", stop_reason="user_stop")
    row = get_paper_trading_task(db_path, task_id)
    return _task_to_response(row)


@router.post("/tasks/{task_id}/restart", response_model=PaperTradingTaskResponse)
def restart_task(
    task_id: str,
    db_path: Path = Depends(get_db_path),
) -> PaperTradingTaskResponse:
    """Create a new task using the same strategy/params as an existing task."""
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
    limit: int = 100,
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
