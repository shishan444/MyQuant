"""Evolution task management routes."""
from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from api.deps import get_db_path, get_data_dir
from api.schemas import (
    DNAModel,
    EvolutionHistoryRecord,
    EvolutionHistoryResponse,
    EvolutionTaskCreate,
    EvolutionTaskListResponse,
    EvolutionTaskResponse,
)
from core.persistence.db import (
    count_all_tasks,
    get_history,
    get_task,
    list_all_tasks,
    save_task,
    update_task,
)
from core.strategy.dna import StrategyDNA

router = APIRouter(prefix="/api/evolution", tags=["evolution"])


@router.get("/runner-status")
def runner_status(request: Request) -> dict:
    """Return EvolutionRunner health status."""
    runner = getattr(request.app.state, "evolution_runner", None)
    if runner is None:
        return {"is_alive": False, "error": "runner not initialized"}
    return runner.get_status()


def _parse_json_list(raw: Optional[str]) -> Optional[List[str]]:
    """Parse a JSON-encoded list from a DB text column."""
    if not raw:
        return None
    try:
        val = json.loads(raw)
        return val if isinstance(val, list) else None
    except Exception:
        return None


def _parse_json_dict(raw: Optional[str]) -> Optional[Dict[str, Any]]:
    """Parse a JSON-encoded dict from a DB text column."""
    if not raw:
        return None
    try:
        val = json.loads(raw)
        return val if isinstance(val, dict) else None
    except Exception:
        return None


def _task_row_to_response(
    row: Dict[str, Any],
    strategy_count: int = 0,
) -> EvolutionTaskResponse:
    """Convert a DB row dict to EvolutionTaskResponse."""
    initial_dna = None
    if row.get("initial_dna"):
        try:
            dna_dict = json.loads(row["initial_dna"])
            initial_dna = DNAModel.model_validate(dna_dict)
        except Exception:
            initial_dna = None

    champion_dna = None
    if row.get("champion_dna"):
        try:
            dna_dict = json.loads(row["champion_dna"])
            champion_dna = DNAModel.model_validate(dna_dict)
        except Exception:
            champion_dna = None

    # Parse requirements from JSON column
    requirements = None
    req_raw = row.get("requirements_json")
    if req_raw:
        try:
            from api.schemas import RequirementsConfigModel
            req_dict = json.loads(req_raw) if isinstance(req_raw, str) else req_raw
            requirements = RequirementsConfigModel(**req_dict)
        except Exception:
            requirements = None

    return EvolutionTaskResponse(
        task_id=row["task_id"],
        status=row["status"],
        target_score=row["target_score"],
        score_template=row["score_template"],
        symbol=row["symbol"],
        timeframe=row["timeframe"],
        initial_dna=initial_dna,
        champion_dna=champion_dna,
        population_size=row.get("population_size", 15),
        max_generations=row.get("max_generations", 200),
        elite_ratio=row.get("elite_ratio", 0.5),
        current_generation=row.get("current_generation", 0),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        stop_reason=row.get("stop_reason"),
        best_score=row.get("best_score"),
        best_fitness=row.get("best_fitness"),
        requirements=requirements,
        qualified_count=row.get("qualified_count", 0),
        leverage=row.get("leverage", 1),
        direction=row.get("direction", "long"),
        data_start=row.get("data_start"),
        data_end=row.get("data_end"),
        data_time_start=row.get("data_time_start"),
        data_time_end=row.get("data_time_end"),
        data_row_count=row.get("data_row_count", 0),
        indicator_pool=_parse_json_list(row.get("indicator_pool")),
        timeframe_pool=_parse_json_list(row.get("timeframe_pool")),
        mode=row.get("mode"),
        champion_metrics=_parse_json_dict(row.get("champion_metrics")),
        champion_dimension_scores=_parse_json_dict(row.get("champion_dimension_scores")),
        champion_satisfaction=_parse_json_dict(row.get("champion_satisfaction")),
        continuous=bool(row.get("continuous", 1)),
        strategy_threshold=row.get("strategy_threshold", 80.0),
        min_annual_return=row.get("min_annual_return", 0.10),
        max_drawdown_limit=row.get("max_drawdown_limit"),
        strategy_count=strategy_count,
        exploration_efficiency=round(
            strategy_count / max(row.get("current_generation", 0), 1), 4
        ),
        current_phase=row.get("current_phase"),
        progress_json=_parse_json_dict(row.get("progress_json")),
    )


def _strategies_to_dicts(
    strategies: List[Dict[str, Any]], min_score: Optional[float],
) -> List[Dict[str, Any]]:
    """策略行列表转 REST 响应 dict（min_score 过滤 + dna/metrics JSON 解析）。"""
    result: List[Dict[str, Any]] = []
    for s in strategies:
        score = s.get("best_score") or 0
        if min_score is not None and score < min_score:
            continue
        dna_dict = None
        if s.get("dna_json"):
            try:
                dna_dict = json.loads(s["dna_json"])
            except Exception:
                pass
        metrics = None
        if s.get("metrics_json"):
            try:
                metrics = json.loads(s["metrics_json"])
            except Exception:
                pass
        result.append({
            "strategy_id": s["strategy_id"],
            "name": s.get("name"),
            "dna": dna_dict,
            "source": "evolution",
            "source_task_id": s.get("source_task_id"),
            "score": score,
            "fitness": s.get("best_fitness"),
            "qualified": bool(s.get("qualified", 0)) if s.get("qualified") is not None else None,
            "generation": s.get("generation", 0),
            "created_at": s.get("created_at"),
            "symbol": s.get("symbol", ""),
            "timeframe": s.get("timeframe", ""),
            "metrics": metrics,
        })
    return result


@router.get("/strategies")
def list_all_discovered_strategies(
    min_score: Optional[float] = None,
    limit: int = Query(20, ge=1, le=500),
    db_path: Path = Depends(get_db_path),
) -> List[Dict[str, Any]]:
    """List all evolution-discovered strategies across all tasks."""
    from core.persistence.db_ext import list_strategies
    strategies = list_strategies(
        db_path, source="evolution", sort_by="best_score",
        sort_order="desc", limit=limit,
    )
    return _strategies_to_dicts(strategies, min_score)


@router.post("/tasks", status_code=201)
def create_task(
    payload: EvolutionTaskCreate,
    db_path: Path = Depends(get_db_path),
    data_dir: Path = Depends(get_data_dir),
) -> EvolutionTaskResponse:
    """Create a new evolution task."""
    task_id = str(uuid.uuid4())

    # --- Auto-derive execution timeframe from timeframe_pool ---
    # Sort pool by duration (longest first), execution TF = shortest
    tf_pool = payload.timeframe_pool
    if tf_pool and len(tf_pool) > 1:
        tf_pool = sort_timeframes(tf_pool)
        # Override timeframe with the shortest in pool
        payload.timeframe = tf_pool[-1]

    # Build initial DNA: use provided or generate a default
    if payload.initial_dna:
        dna = payload.initial_dna.to_dna()
    else:
        from core.strategy.dna import StrategyDNA, SignalGene, SignalRole, LogicGenes, ExecutionGenes, RiskGenes, TimeframeLayer
        base_signals = [
            SignalGene(indicator="EMA", params={"period": 20}, role=SignalRole.ENTRY_TRIGGER, condition={"type": "price_above"}),
            SignalGene(indicator="EMA", params={"period": 20}, role=SignalRole.EXIT_TRIGGER, condition={"type": "price_below"}),
        ]
        base_logic = LogicGenes(entry_logic="AND", exit_logic="AND")

        # For MTF tasks, build multi-layer DNA directly in constructor
        mtf_layers = None
        if tf_pool and len(tf_pool) > 1:
            exec_tf = payload.timeframe
            other_tfs = [tf for tf in tf_pool if tf != exec_tf]
            mtf_layers = [
                TimeframeLayer(timeframe=exec_tf, signal_genes=list(base_signals), logic_genes=LogicGenes(entry_logic="AND", exit_logic="AND")),
            ]
            for tf in other_tfs:
                mtf_layers.append(TimeframeLayer(
                    timeframe=tf,
                    signal_genes=[SignalGene(indicator="SMA", params={"period": 30}, role=SignalRole.ENTRY_TRIGGER, condition={"type": "price_above"})],
                    logic_genes=LogicGenes(entry_logic="AND", exit_logic="AND"),
                ))

        dna = StrategyDNA(
            signal_genes=base_signals,
            logic_genes=base_logic,
            execution_genes=ExecutionGenes(timeframe=payload.timeframe, symbol=payload.symbol),
            risk_genes=RiskGenes(stop_loss=0.03, take_profit=0.06, position_size=1.0,
                                 leverage=payload.leverage, direction=payload.direction),
            layers=mtf_layers,
        )

    # Force override leverage/direction on seed DNA with task-level constraints
    dna.risk_genes.leverage = payload.leverage
    dna.risk_genes.direction = payload.direction

    # Validate data availability（find_parquet 内含周期白名单与 core 别名表，杜绝旁路拼接）
    from core.data.mtf_loader import find_parquet, safe_symbol

    try:
        parquet_path = find_parquet(data_dir, safe_symbol(payload.symbol), payload.timeframe)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    data_time_start = None
    data_time_end = None
    data_row_count = 0

    if parquet_path is None:
        raise HTTPException(
            status_code=400,
            detail=f"无 {payload.symbol}/{payload.timeframe} 的K线数据"
        )

    # Read parquet time range
    try:
        import pandas as pd
        df = pd.read_parquet(parquet_path)
        data_row_count = len(df)
        if data_row_count > 0:
            if isinstance(df.index, pd.DatetimeIndex):
                data_time_start = str(df.index.min())
                data_time_end = str(df.index.max())
            elif "timestamp" in df.columns or "date" in df.columns:
                ts_col = "timestamp" if "timestamp" in df.columns else "date"
                data_time_start = str(df[ts_col].min())
                data_time_end = str(df[ts_col].max())
    except Exception:
        pass

    save_task(
        db_path,
        task_id=task_id,
        target_score=payload.target_score,
        template=payload.score_template,
        symbol=payload.symbol,
        timeframe=payload.timeframe,
        initial_dna=dna,
    )

    # Update extended columns (including leverage/direction constraints and data info)
    requirements_json = None
    if payload.requirements:
        requirements_json = json.dumps(payload.requirements.model_dump())

    from core.persistence.repository import evolution as _repo
    _repo.update_task_config(
        db_path, task_id,
        population_size=payload.population_size,
        max_generations=payload.max_generations,
        elite_ratio=payload.elite_ratio,
        leverage=payload.leverage, direction=payload.direction,
        data_start=payload.data_start, data_end=payload.data_end,
        data_time_start=data_time_start, data_time_end=data_time_end,
        data_row_count=data_row_count,
        indicator_pool=json.dumps(payload.indicator_pool) if payload.indicator_pool else None,
        timeframe_pool=json.dumps(payload.timeframe_pool) if payload.timeframe_pool else None,
        mode=payload.mode,
        continuous=1 if payload.continuous else 0,
        strategy_threshold=payload.strategy_threshold,
        min_annual_return=payload.min_annual_return,
        max_drawdown_limit=payload.max_drawdown_limit,
        requirements_json=requirements_json,
    )

    row = get_task(db_path, task_id)
    if row is None:
        raise HTTPException(status_code=500, detail="Failed to create task")
    return _task_row_to_response(row)


@router.get("/tasks")
def list_tasks(
    status: Optional[str] = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=500),
    db_path: Path = Depends(get_db_path),
) -> EvolutionTaskListResponse:
    """List evolution tasks with optional status filter and pagination."""
    offset = (page - 1) * page_size
    total = count_all_tasks(db_path, status=status)
    rows = list_all_tasks(db_path, status=status, limit=page_size, offset=offset)

    # Batch query strategy counts for all tasks on this page
    from core.persistence.db_ext import count_strategies_by_tasks
    task_ids = [r["task_id"] for r in rows]
    strategy_counts = count_strategies_by_tasks(db_path, task_ids) if task_ids else {}

    items = [
        _task_row_to_response(r, strategy_count=strategy_counts.get(r["task_id"], 0))
        for r in rows
    ]
    return EvolutionTaskListResponse(items=items, total=total, page=page, page_size=page_size)


@router.get("/tasks/{task_id}")
def get_task_endpoint(
    task_id: str,
    db_path: Path = Depends(get_db_path),
) -> EvolutionTaskResponse:
    """Get evolution task details."""
    row = get_task(db_path, task_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Task not found")
    return _task_row_to_response(row)


@router.get("/tasks/{task_id}/history")
def get_task_history(
    task_id: str,
    db_path: Path = Depends(get_db_path),
) -> EvolutionHistoryResponse:
    """Get generation history for an evolution task."""
    row = get_task(db_path, task_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Task not found")

    history = get_history(db_path, task_id)
    generations = [
        EvolutionHistoryRecord(
            generation=h["generation"],
            best_score=h["best_score"],
            avg_score=h["avg_score"],
            best_fitness=h.get("best_fitness"),
            avg_fitness=h.get("avg_fitness"),
            top3_summary=h.get("top3_summary"),
            created_at=h["created_at"],
        )
        for h in history
    ]

    return EvolutionHistoryResponse(
        task_id=task_id,
        generations=generations,
    )


@router.post("/tasks/{task_id}/pause")
def pause_task(
    task_id: str,
    db_path: Path = Depends(get_db_path),
) -> EvolutionTaskResponse:
    """Pause an evolution task."""
    # B6.2 路由薄化：判定与前态守护在 service 用例（batch1 D3 语义不变）
    from core.services import evolution_service
    try:
        result = evolution_service.pause_task(db_path, task_id)
    except evolution_service.TaskNotFound:
        raise HTTPException(status_code=404, detail="Task not found")
    except evolution_service.IllegalTransition as e:
        raise HTTPException(status_code=409, detail=str(e))
    return _task_row_to_response(result.task_row)


@router.post("/tasks/{task_id}/stop")
def stop_task(
    task_id: str,
    db_path: Path = Depends(get_db_path),
) -> EvolutionTaskResponse:
    """Stop an evolution task."""
    from core.services import evolution_service
    try:
        result = evolution_service.stop_task(db_path, task_id)
    except evolution_service.TaskNotFound:
        raise HTTPException(status_code=404, detail="Task not found")
    except evolution_service.IllegalTransition as e:
        raise HTTPException(status_code=409, detail=str(e))
    return _task_row_to_response(result.task_row)


@router.post("/tasks/{task_id}/resume")
def resume_task(
    task_id: str,
    db_path: Path = Depends(get_db_path),
) -> EvolutionTaskResponse:
    """Resume a paused evolution task."""
    from core.services import evolution_service
    try:
        result = evolution_service.resume_task(db_path, task_id)
    except evolution_service.TaskNotFound:
        raise HTTPException(status_code=404, detail="Task not found")
    except evolution_service.IllegalTransition as e:
        raise HTTPException(status_code=400, detail=str(e))
    return _task_row_to_response(result.task_row)


@router.get("/tasks/{task_id}/strategies")
def get_task_strategies(
    task_id: str,
    db_path: Path = Depends(get_db_path),
) -> dict:
    """Return effective strategies discovered by this evolution task.

    Returns champion and any snapshot strategies with score > 0.
    """
    row = get_task(db_path, task_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Task not found")

    strategies = []

    # Champion strategy
    if row.get("champion_dna"):
        try:
            champion = json.loads(row["champion_dna"])
            strategies.append({
                "strategy_id": champion.get("strategy_id", ""),
                "dna": champion,
                "source": "champion",
                "score": row.get("champion_score", 0),
            })
        except Exception:
            pass

    # Snapshot-based strategies
    from core.persistence.repository import evolution as _repo
    snapshots = _repo.list_top_snapshots(db_path, task_id, limit=10)

    for snap in snapshots:
        try:
            snap_dna = json.loads(snap["best_dna"])
            sid = snap_dna.get("strategy_id", "")
            # Skip if already included as champion
            if any(s["strategy_id"] == sid for s in strategies):
                continue
            strategies.append({
                "strategy_id": sid,
                "dna": snap_dna,
                "source": "snapshot",
                "generation": snap["generation"],
                "score": snap["best_score"],
            })
        except Exception:
            continue

    return {"task_id": task_id, "strategies": strategies}


@router.get("/tasks/{task_id}/discovered-strategies")
def get_discovered_strategies(
    task_id: str,
    min_score: Optional[float] = None,
    limit: int = Query(50, ge=1, le=500),
    db_path: Path = Depends(get_db_path),
) -> List[Dict[str, Any]]:
    """Get auto-extracted strategies for a task from the strategy table."""
    from core.persistence.db_ext import list_strategies
    strategies = list_strategies(
        db_path,
        source="evolution",
        source_task_id=task_id,
        sort_by="best_score",
        sort_order="desc",
        limit=limit,
    )
    return _strategies_to_dicts(strategies, min_score)


# ── Timeframe utilities ──

from core.data.timeframes import TF_SECONDS as _TF_SECONDS  # B4.1 归一


def sort_timeframes(tfs: List[str]) -> List[str]:
    """Sort timeframes by duration, longest first."""
    def _key(tf: str) -> int:
        return _TF_SECONDS.get(tf, 0)
    return sorted(tfs, key=_key, reverse=True)
