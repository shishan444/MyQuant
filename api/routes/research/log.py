"""研究日志与功能需求单端点（add-ai-research-mcp 3.4）。"""
from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from api.deps import get_db_path
from api.routes.research.common import decode_cursor, encode_cursor
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
