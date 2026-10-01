"""研究日志与功能需求单端点（add-ai-research-mcp 3.4）。"""
from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends

from api.deps import get_db_path
from api.schemas import FeatureRequestCreate, FeatureRequestResponse
from core.persistence.repository import research as rr

router = APIRouter()


@router.get("/log")
def research_log(
    hypothesis_id: Optional[str] = None,
    entry_type: Optional[str] = None,
    page: int = 1,
    page_size: int = 50,
    db_path: Path = Depends(get_db_path),
) -> Dict[str, Any]:
    """研究日志查询（假设 → 分析（含区间）→ 证据 → 结论全链路）。

    entry_type 过滤：按日志类型检索（如 screen=全局扫描留痕，
    供研究方自查历史扫描区间与候选指纹——add-screen-patterns G6）。
    """
    if page < 1:
        page = 1
    if page_size < 1 or page_size > 200:
        page_size = 50
    items = rr.list_research_log(
        db_path, hypothesis_id=hypothesis_id, entry_type=entry_type,
        offset=(page - 1) * page_size, limit=page_size)
    out = []
    for e in items:
        e = dict(e)
        if e.get("data_ranges"):
            try:
                e["data_ranges"] = json.loads(e["data_ranges"])
            except (json.JSONDecodeError, TypeError):
                pass
        out.append(e)
    return {"items": out, "total": len(out), "page": page, "page_size": page_size}


@router.post("/feature-requests", response_model=FeatureRequestResponse)
def create_feature_request(
    payload: FeatureRequestCreate,
    db_path: Path = Depends(get_db_path),
) -> FeatureRequestResponse:
    """慢通道功能需求单：只保证持久化与可查，不自动实现。"""
    rid = f"fr_{uuid.uuid4().hex[:12]}"
    from core.persistence.timestamps import utc_now_iso
    from core.persistence.repository.dataset_meta import _engine_for
    from sqlalchemy import insert
    from core.persistence.orm.models import FeatureRequest

    with _engine_for(db_path).begin() as conn:
        conn.execute(insert(FeatureRequest).values(
            request_id=rid, title=payload.title,
            payload=json.dumps(payload.payload, ensure_ascii=False),
            status="open", created_at=utc_now_iso()))
    return FeatureRequestResponse(
        request_id=rid, title=payload.title, status="open",
        created_at=utc_now_iso())


@router.get("/feature-requests")
def list_feature_requests(
    status: Optional[str] = None,
    page: int = 1,
    page_size: int = 50,
    db_path: Path = Depends(get_db_path),
) -> Dict[str, Any]:
    # 分页 clamp 与 /log、/hypotheses 同款（此前形制缺口：负 OFFSET
    # 回首页、LIMIT 0 假空页、超上限不收）
    if page < 1:
        page = 1
    if page_size < 1 or page_size > 200:
        page_size = 50
    from sqlalchemy import select
    from core.persistence.orm.models import FeatureRequest
    from core.persistence.repository.dataset_meta import _engine_for

    stmt = select(FeatureRequest).order_by(
        FeatureRequest.created_at, FeatureRequest.request_id
    ).offset((page - 1) * page_size).limit(page_size)
    if status is not None:
        stmt = stmt.where(FeatureRequest.status == status)
    with _engine_for(db_path).connect() as conn:
        rows = [dict(r) for r in conn.execute(stmt).mappings()]
    for r in rows:
        r["payload"] = json.loads(r["payload"])
    return {"items": rows, "total": len(rows), "page": page, "page_size": page_size}
