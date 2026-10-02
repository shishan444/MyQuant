"""进化任务生命周期入口（/api/evolution/*）。

职能：进化任务的创建/查询/暂停/停止/恢复、代际历史与发现策略查询，
以及 runner 健康状态。服务对象：web 进化页与 EvolutionRunner
（任务表即两者的交接面）。
职责边界：任务表读写与校验，进化执行在 runner 线程。
"""
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
    """返回 EvolutionRunner 健康状态。"""
    runner = getattr(request.app.state, "evolution_runner", None)
    if runner is None:
        return {"is_alive": False, "error": "runner not initialized"}
    return runner.get_status()


def _parse_json_list(raw: Optional[str]) -> Optional[List[str]]:
    """解析 DB 文本列的 JSON list（空/非法/类型不符回 None）。"""
    if not raw:
        return None
    try:
        val = json.loads(raw)
        return val if isinstance(val, list) else None
    except Exception:
        return None


def _parse_json_dict(raw: Optional[str]) -> Optional[Dict[str, Any]]:
    """解析 DB 文本列的 JSON dict（空/非法/类型不符回 None）。"""
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
    """DB 行 dict 转 EvolutionTaskResponse（附策略计数）。"""
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

    # requirements 自 JSON 列解析（非法回 None）
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
    """跨任务列出全部进化发现的策略（source=evolution）。"""
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
    """创建进化任务（runner 轮询 pending 表接手执行）。"""
    task_id = str(uuid.uuid4())

    # --- 由 timeframe_pool 自动推导执行周期 ---
    # 池按时长降序排，执行周期取最短者
    tf_pool = payload.timeframe_pool
    if tf_pool and len(tf_pool) > 1:
        tf_pool = sort_timeframes(tf_pool)
        # timeframe 覆写为池内最短
        payload.timeframe = tf_pool[-1]

    # 初始 DNA：用传入值或生成缺省
    if payload.initial_dna:
        dna = payload.initial_dna.to_dna()
    else:
        from core.strategy.dna import StrategyDNA, SignalGene, SignalRole, LogicGenes, ExecutionGenes, RiskGenes, TimeframeLayer
        base_signals = [
            SignalGene(indicator="EMA", params={"period": 20}, role=SignalRole.ENTRY_TRIGGER, condition={"type": "price_above"}),
            SignalGene(indicator="EMA", params={"period": 20}, role=SignalRole.EXIT_TRIGGER, condition={"type": "price_below"}),
        ]
        base_logic = LogicGenes(entry_logic="AND", exit_logic="AND")

        # MTF 任务在构造时直接建多层 DNA
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

    # 任务级杠杆/方向约束强制覆写到种子 DNA
    dna.risk_genes.leverage = payload.leverage
    dna.risk_genes.direction = payload.direction

    # 数据可用性校验（find_parquet 内含周期白名单与 core 别名表，杜绝旁路拼接）
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

    # 读取 parquet 时间范围（仅取行数与首尾时间戳，不全量装载——
    # 1m 数据集数十万行，read_parquet 全读仅为 len/min/max 属白读）
    try:
        import pyarrow.parquet as pq
        pf = pq.ParquetFile(parquet_path)
        data_row_count = pf.metadata.num_rows
        if data_row_count > 0:
            # 候选序覆盖原 pandas 三分支：有名 DatetimeIndex 落 timestamp 列
            # （工程 index name=timestamp，实际数据集 schema 已核）、
            # date 列存储、无名 index 的 __index_level_0__
            names = pf.schema_arrow.names
            ts_col = next(
                (c for c in ("timestamp", "date", "__index_level_0__")
                 if c in names), None,
            )
            if ts_col is not None:
                col = pf.read(columns=[ts_col]).column(0).to_pandas()
                data_time_start = str(col.min())
                data_time_end = str(col.max())
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

    # 扩展列更新（杠杆/方向约束与数据探测信息）
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
    """分页列出进化任务（可按 status 过滤）。"""
    offset = (page - 1) * page_size
    total = count_all_tasks(db_path, status=status)
    rows = list_all_tasks(db_path, status=status, limit=page_size, offset=offset)

    # 本页任务的策略计数批量查询
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
    """取进化任务详情。"""
    row = get_task(db_path, task_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Task not found")
    return _task_row_to_response(row)


@router.get("/tasks/{task_id}/history")
def get_task_history(
    task_id: str,
    db_path: Path = Depends(get_db_path),
) -> EvolutionHistoryResponse:
    """取任务代际历史。"""
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
    """暂停进化任务。"""
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
    """停止进化任务。"""
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
    """恢复已暂停的进化任务。"""
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
    """返回本任务实际生效的策略集（冠军与代际 top-N 快照策略，已收录冠军去重）。"""
    row = get_task(db_path, task_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Task not found")

    strategies = []

    # 冠军策略
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

    # 快照策略集
    from core.persistence.repository import evolution as _repo
    snapshots = _repo.list_top_snapshots(db_path, task_id, limit=10)

    for snap in snapshots:
        try:
            snap_dna = json.loads(snap["best_dna"])
            sid = snap_dna.get("strategy_id", "")
            # 已作为冠军收录的跳过
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
    """取任务自动入库的策略（strategy 表按 source_task_id 过滤）。"""
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
    """周期按时长降序排序。"""
    def _key(tf: str) -> int:
        return _TF_SECONDS.get(tf, 0)
    return sorted(tfs, key=_key, reverse=True)
