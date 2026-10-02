"""研究日志与功能需求单端点（add-ai-research-mcp 3.4）。"""
from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends

from api.deps import get_db_path
from api.routes.research.common import decode_cursor, encode_cursor
from api.schemas import FeatureRequestCreate, FeatureRequestResponse
from core.persistence.repository import research as rr

router = APIRouter()


@router.get("/log")
def research_log(
    hypothesis_id: Optional[str] = None,
    entry_type: Optional[str] = None,
    page: int = 1,
    page_size: int = 50,
    cursor: Optional[str] = None,
    db_path: Path = Depends(get_db_path),
) -> Dict[str, Any]:
    """研究日志查询（假设 → 分析（含区间）→ 证据 → 结论全链路）。

    entry_type 过滤：按日志类型检索（如 screen=全局扫描留痕，
    供研究方自查历史扫描区间与候选指纹——add-screen-patterns G6）。
    缺省最新在前（desc）；cursor 续页（上一响应 next_cursor 原样
    回传，keyset 深翻页无漂移，提供时 page 被忽略）；total 为过滤
    口径真总数（非当页数）。
    """
    if page < 1:
        page = 1
    if page_size < 1 or page_size > 200:
        page_size = 50
    cur = decode_cursor(cursor)
    items = rr.list_research_log(
        db_path, hypothesis_id=hypothesis_id, entry_type=entry_type,
        offset=0 if cur is not None else (page - 1) * page_size,
        limit=page_size, cursor=cur, order="desc")
    out = []
    for e in items:
        e = dict(e)
        if e.get("data_ranges"):
            try:
                e["data_ranges"] = json.loads(e["data_ranges"])
            except (json.JSONDecodeError, TypeError):
                pass
        out.append(e)
    return {"items": out,
            "total": rr.count_research_log(
                db_path, hypothesis_id=hypothesis_id, entry_type=entry_type),
            "page": page, "page_size": page_size,
            "next_cursor": encode_cursor(out[-1]["created_at"], out[-1]["log_id"])
            if out else None}


@router.post("/feature-requests", response_model=FeatureRequestResponse)
def create_feature_request(
    payload: FeatureRequestCreate,
    db_path: Path = Depends(get_db_path),
) -> FeatureRequestResponse:
    """慢通道功能需求单：只保证持久化与可查，不自动实现。"""
    rid = f"fr_{uuid.uuid4().hex[:12]}"
    from core.persistence.timestamps import utc_now_iso

    now = utc_now_iso()
    rr.save_feature_request(
        db_path, request_id=rid, title=payload.title,
        payload=json.dumps(payload.payload, ensure_ascii=False),
        status="open", created_at=now)
    return FeatureRequestResponse(
        request_id=rid, title=payload.title, status="open", created_at=now)


@router.get("/feature-requests")
def list_feature_requests(
    status: Optional[str] = None,
    page: int = 1,
    page_size: int = 50,
    cursor: Optional[str] = None,
    db_path: Path = Depends(get_db_path),
) -> Dict[str, Any]:
    """慢通道需求单列表。分页契约与 /log 同款：缺省最新在前（desc）；
    cursor 续页（上一响应 next_cursor 回传，提供时 page 被忽略）；
    total 为过滤口径真总数。"""
    # 分页 clamp 与 /log、/hypotheses 同款（此前形制缺口：负 OFFSET
    # 回首页、LIMIT 0 假空页、超上限不收）
    if page < 1:
        page = 1
    if page_size < 1 or page_size > 200:
        page_size = 50
    cur = decode_cursor(cursor)
    rows = rr.list_feature_requests(
        db_path, status=status,
        offset=0 if cur is not None else (page - 1) * page_size,
        limit=page_size, cursor=cur, order="desc")
    for r in rows:
        try:
            r["payload"] = json.loads(r["payload"])
        except (json.JSONDecodeError, TypeError):
            pass
    return {"items": rows,
            "total": rr.count_feature_requests(db_path, status=status),
            "page": page, "page_size": page_size,
            "next_cursor": encode_cursor(
                rows[-1]["created_at"], rows[-1]["request_id"])
            if rows else None}
