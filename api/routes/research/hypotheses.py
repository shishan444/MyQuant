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

# research_error_to_http 已迁 common（子包共享底座）；此处 import 兼容
# 范围外 api/routes/validate.py 的既有引用，新代码请直用 common
from api.routes.research.common import (
    decode_cursor,
    encode_cursor,
    require_known_timeframe,
    research_error_to_http,  # noqa: F401 —— 兼容 validate.py 既有引用
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
        from core.data.mtf_loader import find_parquet, safe_symbol
        for i, r in enumerate(payload.data_ranges):
            # P0 错误通道：非法周期前置 422——否则 find_parquet 内
            # validate_known_interval 抛裸 ValueError 落 500
            require_known_timeframe(r.timeframe, f"data_ranges[{i}].timeframe")
            # F-1：探测统一走 safe_symbol 清洗（与下方 timeframe 探测
            # 同函数一致——原始 symbol 直传在符号含分隔符时会变成
            # 存在性 oracle，纯收紧）
            if find_parquet(data_dir, safe_symbol(r.symbol), r.timeframe) is None:
                raise ResearchError(
                    "RESEARCH_SYMBOL_NOT_FOUND",
                    f"data_ranges[{i}] 无数据: {r.symbol} {r.timeframe}",
                    detail={"field": f"data_ranges[{i}]",
                            "symbol": r.symbol, "timeframe": r.timeframe})

        # 0.2.14：when 条件 timeframe 键去重逐周期可达校验（与
        # data_ranges 可达同族同层早失败）；N2：拒绝 detail 附
        # find_parquet 探测的实际可得周期清单——「周期非法」与
        # 「周期无数据」语义区分
        from core.data.timeframes import TF_SECONDS
        _cond = payload.conditions.model_dump()
        _required_tfs = []
        for c in _cond["when_conditions"]:
            tf_key = c.get("timeframe")
            if (tf_key and tf_key not in _required_tfs
                    and tf_key != payload.data_ranges[0].timeframe):
                _required_tfs.append(tf_key)
        if _required_tfs:
            safe = safe_symbol(payload.data_ranges[0].symbol)
            _available = [tf for tf in TF_SECONDS
                          if find_parquet(data_dir, safe, tf) is not None]
            for tf_key in _required_tfs:
                if tf_key in _available:
                    continue
                raise ResearchError(
                    "RESEARCH_SYMBOL_NOT_FOUND",
                    f"when 条件 timeframe={tf_key} 无数据: "
                    f"{payload.data_ranges[0].symbol} {tf_key}",
                    detail={"field": "when_conditions.timeframe",
                            "symbol": payload.data_ranges[0].symbol,
                            "timeframe": tf_key,
                            "available_timeframes": _available})

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
    cursor: Optional[str] = None,
    db_path: Path = Depends(get_db_path),
) -> HypothesisListResponse:
    """假设列表。分页契约与 /log 同款：缺省最新在前（desc）；cursor
    续页（上一响应 next_cursor 回传，提供时 page 被忽略）；total 为
    过滤口径真总数（非当页数）。"""
    if page < 1:
        page = 1
    if page_size < 1 or page_size > 200:
        page_size = 50
    cur = decode_cursor(cursor)
    items = rr.list_hypotheses(
        db_path, status=status,
        offset=0 if cur is not None else (page - 1) * page_size,
        limit=page_size, cursor=cur, order="desc")
    out = [
        {**h, "conditions": json.loads(h["conditions"]),
         "data_ranges": json.loads(h["data_ranges"])}
        for h in items
    ]
    return HypothesisListResponse(
        items=out,
        total=rr.count_hypotheses(db_path, status=status),
        page=page, page_size=page_size,
        next_cursor=encode_cursor(
            items[-1]["created_at"], items[-1]["hypothesis_id"])
        if items else None)


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
