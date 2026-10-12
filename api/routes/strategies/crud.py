"""策略库管理（/api/strategies CRUD）。

职能：策略的创建/查询/更新/删除与列表分页。服务对象：web 策略页
（策略库面板）。职责边界：策略表读写，回测/验证在兄弟模块。
"""
from __future__ import annotations

import json
import logging
import uuid

from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from core.persistence.repository.backtest_result import (
    get_backtest_result,
    list_backtest_results,
    save_backtest_result,
)
from core.persistence.repository.strategy import (
    count_strategies,
    delete_strategy,
    get_strategy,
    list_strategies,
    save_strategy,
    update_strategy,
)
from core.persistence.repository.verify_session import (
    get_verify_session,
    list_verify_sessions,
    save_verify_session,
    update_verify_session,
)
from api.deps import get_db_path
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

logger = logging.getLogger(__name__)


router = APIRouter()




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
    """DB 行 dict 转 StrategyResponse。"""
    dna = None
    if row.get("dna_json"):
        try:
            dna_dict = json.loads(row["dna_json"])
            dna = DNAModel.model_validate(dna_dict)
        except Exception:
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
    """创建策略。"""
    strategy_id = str(uuid.uuid4())
    # CARD-01（event-triggers 批次三）：entry_trigger_policy 登记点早拒绝
    # ——策略通道此前全程无校验（裸奔入口），to_dna() 后经 validate_policy
    # 形制校验（422 先于持久化）；不走 DNAModel field_validator——api 层
    # 无全局 handler 会反落 500，走路由既有 ResearchError→HTTPException
    # 形制（research_error_to_http 共享底座）
    from core.analysis.hypothesis_service import ResearchError
    try:
        dna = payload.dna.to_dna()
        from core.analysis.event_policy import validate_policy
        # 回写归一形（security 观察项采纳）：显式 none 归一 None 落库=
        # 与缺省同存储同签名（与假设侧 D4 幂等签名语义对齐）；非法形
        # 在赋值前 422（validate 先抛，赋值不发生）
        dna.entry_trigger_policy = validate_policy(dna.entry_trigger_policy)
    except ResearchError as exc:
        from api.routes.research.common import research_error_to_http
        raise research_error_to_http(exc) from exc

    # 手工 DNA 值域校验（review-042 CARD-12）：validate_dna 此前在 api/
    # 零调用——NaN/越域/bool 收窄（True→1.0）经 Pydantic 全过落库，引擎
    # sl=ep×(1−1.0)=0 恒不触发（看似设了止损实为无止损）。errors 非空
    # 422 结构化拒绝（沿本入口 validate_policy 的 422 先例；detail=dict
    # 形制与 Pydantic 自动 422 的明细数组可判别）
    from core.strategy.validator import validate_dna
    _vr = validate_dna(dna)
    if not _vr.is_valid:
        raise HTTPException(
            status_code=422,
            detail={"code": "DNA_VALIDATION_FAILED",
                    "message": "DNA 值域/结构校验未通过",
                    "errors": _vr.errors})

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
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    db_path: Path = Depends(get_db_path),
) -> StrategyListResponse:
    """策略列表（过滤/排序/分页）。status 缺省视图排除 draft；
    显式 status=draft 可查草稿。
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
    """按 ID 取策略详情。"""
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
    """更新策略。"""
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
        # CARD-01：update 通道同款早拒绝（与 create 同形制）
        from core.analysis.hypothesis_service import ResearchError
        try:
            dna = payload.dna.to_dna()
            from core.analysis.event_policy import validate_policy
            dna.entry_trigger_policy = validate_policy(
                dna.entry_trigger_policy)   # 回写归一形（同 create）
        except ResearchError as exc:
            from api.routes.research.common import research_error_to_http
            raise research_error_to_http(exc) from exc

        # 手工 DNA 值域校验（review-042 CARD-15）：与 POST 段同款——
        # PUT 此前仅 validate_policy，先建合法策略过 POST 再 PUT 换坏
        # DNA 可绕过（stop_loss=1.0 经 PUT 200 落库=引擎假止损）
        from core.strategy.validator import validate_dna
        _vr = validate_dna(dna)
        if not _vr.is_valid:
            raise HTTPException(
                status_code=422,
                detail={"code": "DNA_VALIDATION_FAILED",
                        "message": "DNA 值域/结构校验未通过",
                        "errors": _vr.errors})
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
    """按 ID 删除策略。"""
    existing = get_strategy(db_path, strategy_id)
    if existing is None:
        raise HTTPException(status_code=404, detail="Strategy not found")
    delete_strategy(db_path, strategy_id)
