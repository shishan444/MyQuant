"""Strategy CRUD + backtest + compare routes."""
from __future__ import annotations

import json
import logging
import uuid
import asyncio
from datetime import datetime

import pandas as pd
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException

from core.persistence.db_ext import (
    count_strategies,
    delete_strategy,
    get_backtest_result,
    get_strategy,
    get_verify_session,
    list_backtest_results,
    list_strategies,
    list_verify_sessions,
    save_backtest_result,
    save_strategy,
    save_verify_session,
    update_strategy,
    update_verify_session,
)
from api.deps import get_data_dir, get_db_path
from api.schemas import (
    BacktestRequest,
    BacktestResponse,
    BatchBacktestRequest,
    BatchBacktestResultItem,
    BatchBacktestSummaryItem,
    CompareRequest,
    CompareResponse,
    CompareResultItem,
    DNAModel,
    StrategyCreate,
    StrategyListResponse,
    StrategyMetrics,
    StrategyResponse,
    StrategyUpdate,
    VerifyRequest,
    VerifyResponse,
    VerifyResultItem,
    VerifySummaryItem,
    VerifyPeriodSummary,
    VerifyHistoryItem,
    VerifyHistoryResponse,
    VerifySessionResponse,
    VerifySessionListResponse,
)
from core.backtest import engine as _bt_engine_mod
from core.scoring.scorer import score_strategy
from core.services import backtest_service
from core.strategy.dna import StrategyDNA

logger = logging.getLogger(__name__)


router = APIRouter()




def _dna_model_to_dna(dna_model: DNAModel) -> StrategyDNA:
    """Convert a Pydantic DNAModel to a core StrategyDNA."""
    data = dna_model.model_dump()
    return StrategyDNA.from_dict(data)


def _gene_signature_or_derive(row: Dict[str, Any]) -> Optional[str]:
    """行内 gene_signature 为空时从 dna_json 现算兜底（形制同 save_strategy 内建 fallback）。"""
    sig = row.get("gene_signature")
    if sig:
        return sig
    try:
        from core.strategy.dna import StrategyDNA
        return StrategyDNA.from_json(row.get("dna_json")).gene_signature
    except Exception:
        return None


def _strategy_row_to_response(row: Dict[str, Any]) -> StrategyResponse:
    """Convert a DB row dict to StrategyResponse."""
    dna = None
    if row.get("dna_json"):
        try:
            dna_dict = json.loads(row["dna_json"])
            dna = DNAModel.model_validate(dna_dict)
        except (json.JSONDecodeError, Exception):
            dna = None

    metrics = None
    if row.get("metrics_json"):
        try:
            metrics = StrategyMetrics(**json.loads(row["metrics_json"]))
        except Exception:
            pass

    return StrategyResponse(
        strategy_id=row["strategy_id"],
        name=row.get("name"),
        dna=dna,
        symbol=row["symbol"],
        timeframe=row["timeframe"],
        source=row.get("source", "manual"),
        source_task_id=row.get("source_task_id"),
        gene_signature=_gene_signature_or_derive(row),
        status=row.get("status") or "active",
        best_score=row.get("best_score"),
        best_fitness=row.get("best_fitness"),
        qualified=bool(row.get("qualified", 0)) if row.get("qualified") is not None else None,
        metrics=metrics,
        generation=row.get("generation", 0),
        parent_ids=row.get("parent_ids"),
        tags=row.get("tags"),
        notes=row.get("notes"),
        verify_count=row.get("verify_count", 0) or 0,
        verify_avg_score=row.get("verify_avg_score"),
        verify_best_score=row.get("verify_best_score"),
        last_verified_at=row.get("last_verified_at"),
        verify_star=row.get("verify_star"),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


@router.post("", status_code=201)
def create_strategy(
    payload: StrategyCreate,
    db_path: Path = Depends(get_db_path),
) -> StrategyResponse:
    """Create a new strategy."""
    strategy_id = str(uuid.uuid4())
    dna = _dna_model_to_dna(payload.dna)

    save_strategy(
        db_path,
        strategy_id=strategy_id,
        name=payload.name,
        dna_json=dna.to_json(),
        symbol=payload.symbol,
        timeframe=payload.timeframe,
        source=payload.source,
        source_task_id=payload.source_task_id,
        tags=payload.tags,
        notes=payload.notes,
    )

    row = get_strategy(db_path, strategy_id)
    if row is None:
        raise HTTPException(status_code=500, detail="Failed to create strategy")
    return _strategy_row_to_response(row)


@router.get("")
def list_strategies_endpoint(
    symbol: Optional[str] = None,
    source: Optional[str] = None,
    tags: Optional[str] = None,
    qualified: Optional[bool] = None,
    status: Optional[str] = None,
    sort_by: str = "created_at",
    sort_order: str = "desc",
    limit: int = 100,
    offset: int = 0,
    db_path: Path = Depends(get_db_path),
) -> StrategyListResponse:
    """List strategies with filtering, sorting, and pagination.

    status=None 默认视图排除 draft；显式 status=draft 可查草稿。
    """
    try:
        rows = list_strategies(
            db_path,
            symbol=symbol,
            source=source,
            tags=tags,
            qualified=qualified,
            status=status,
            sort_by=sort_by,
            sort_order=sort_order,
            limit=limit,
            offset=offset,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    total = count_strategies(
        db_path, symbol=symbol, source=source, tags=tags, qualified=qualified,
        status=status,
    )
    items = [_strategy_row_to_response(r) for r in rows]
    return StrategyListResponse(items=items, total=total)


@router.get("/{strategy_id}")
def get_strategy_endpoint(
    strategy_id: str,
    db_path: Path = Depends(get_db_path),
) -> StrategyResponse:
    """Get strategy details by ID."""
    row = get_strategy(db_path, strategy_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Strategy not found")
    return _strategy_row_to_response(row)


@router.put("/{strategy_id}")
def update_strategy_endpoint(
    strategy_id: str,
    payload: StrategyUpdate,
    db_path: Path = Depends(get_db_path),
) -> StrategyResponse:
    """Update an existing strategy."""
    existing = get_strategy(db_path, strategy_id)
    if existing is None:
        raise HTTPException(status_code=404, detail="Strategy not found")

    # draft 写锁（M4）：受保护字段（dna；status/parent_ids/source 不在
    # update schema 内天然锁死）对 PUT 不可写——"验证的就是出货的"由
    # 机制保证而非约定；改 DNA 只能修改假设条件后重新 translate
    if existing.get("status") == "draft" and payload.dna is not None:
        raise HTTPException(status_code=409, detail={
            "code": "RESEARCH_DRAFT_IMMUTABLE",
            "message": "草稿策略的 DNA 不可直接修改",
            "detail": {
                "strategy_id": strategy_id,
                "note": "修改假设条件后重新转化（translate）产出新草稿；"
                        "name/tags/notes 等元数据仍可更新"},
        })

    fields: Dict[str, Any] = {}
    if payload.name is not None:
        fields["name"] = payload.name
    if payload.tags is not None:
        fields["tags"] = payload.tags
    if payload.notes is not None:
        fields["notes"] = payload.notes
    if payload.best_score is not None:
        fields["best_score"] = payload.best_score
    if payload.dna is not None:
        dna = _dna_model_to_dna(payload.dna)
        fields["dna_json"] = dna.to_json()

    if fields:
        update_strategy(db_path, strategy_id=strategy_id, **fields)

    row = get_strategy(db_path, strategy_id)
    return _strategy_row_to_response(row)


@router.delete("/{strategy_id}", status_code=204)
def delete_strategy_endpoint(
    strategy_id: str,
    db_path: Path = Depends(get_db_path),
) -> None:
    """Delete a strategy by ID."""
    existing = get_strategy(db_path, strategy_id)
    if existing is None:
        raise HTTPException(status_code=404, detail="Strategy not found")
    delete_strategy(db_path, strategy_id)
