"""假设登记/查询/迁移端点（add-ai-research-mcp 3.1）。"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException

from api.deps import get_data_dir, get_db_path
from api.schemas import (
    HypothesisCreate,
    HypothesisListResponse,
    HypothesisResponse,
    HypothesisTransitionRequest,
)
from core.analysis import hypothesis_service as hs
from core.analysis.hypothesis_service import ResearchError
from core.persistence.repository import research as rr

logger = logging.getLogger(__name__)
router = APIRouter()


def research_error_to_http(exc: ResearchError) -> HTTPException:
    """ResearchError -> HTTPException（detail 含 code/message/detail 三段）。"""
    return HTTPException(
        status_code=exc.status,
        detail={"code": exc.code, "message": exc.message, "detail": exc.detail},
    )


@router.post("/hypotheses", response_model=HypothesisResponse)
def register_hypothesis(
    payload: HypothesisCreate,
    db_path: Path = Depends(get_db_path),
    data_dir: Path = Depends(get_data_dir),
) -> HypothesisResponse:
    """登记假设：结构化 WHEN/THEN 条件 + 人话陈述 + 数据区间。"""
    try:
        # symbol/timeframe 在数据目录可达（422 RESEARCH_SYMBOL_NOT_FOUND）
        from core.data.mtf_loader import find_parquet
        for i, r in enumerate(payload.data_ranges):
            if find_parquet(data_dir, r.symbol, r.timeframe) is None:
                raise ResearchError(
                    "RESEARCH_SYMBOL_NOT_FOUND",
                    f"data_ranges[{i}] 无数据: {r.symbol} {r.timeframe}",
                    detail={"field": f"data_ranges[{i}]",
                            "symbol": r.symbol, "timeframe": r.timeframe})

        out = hs.register_hypothesis(
            db_path,
            conditions=payload.conditions.model_dump(),
            statement=payload.statement,
            data_ranges=[r.model_dump() for r in payload.data_ranges],
            provenance=(payload.provenance.model_dump()
                        if payload.provenance else None),
        )
        return HypothesisResponse(**out)
    except ResearchError as exc:
        raise research_error_to_http(exc) from exc


@router.get("/hypotheses", response_model=HypothesisListResponse)
def list_hypotheses(
    status: Optional[str] = None,
    page: int = 1,
    page_size: int = 50,
    db_path: Path = Depends(get_db_path),
) -> HypothesisListResponse:
    if page < 1:
        page = 1
    if page_size < 1 or page_size > 200:
        page_size = 50
    items = rr.list_hypotheses(
        db_path, status=status,
        offset=(page - 1) * page_size, limit=page_size)
    return HypothesisListResponse(
        items=[
            {**h, "conditions": json.loads(h["conditions"]),
             "data_ranges": json.loads(h["data_ranges"])}
            for h in items
        ],
        total=len(items), page=page, page_size=page_size)


@router.get("/hypotheses/{hypothesis_id}")
def get_hypothesis(
    hypothesis_id: str,
    db_path: Path = Depends(get_db_path),
) -> Dict[str, Any]:
    hyp = rr.get_hypothesis(db_path, hypothesis_id)
    if hyp is None:
        raise HTTPException(status_code=404, detail={
            "code": "RESEARCH_HYPOTHESIS_NOT_FOUND",
            "message": f"假设不存在: {hypothesis_id}", "detail": None})
    return {**hyp,
            "conditions": json.loads(hyp["conditions"]),
            "data_ranges": json.loads(hyp["data_ranges"])}


@router.post("/hypotheses/{hypothesis_id}/transition")
def transition_hypothesis(
    hypothesis_id: str,
    payload: HypothesisTransitionRequest,
    db_path: Path = Depends(get_db_path),
) -> Dict[str, Any]:
    """状态迁移：active -> supported|refuted|superseded（必引证据）。"""
    try:
        return hs.transition_hypothesis(
            db_path, hypothesis_id,
            target_status=payload.target_status,
            evidence_ref=payload.evidence_ref)
    except ResearchError as exc:
        raise research_error_to_http(exc) from exc
