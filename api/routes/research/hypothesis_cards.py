"""主任务聚合端点（add-hypothesis-cards B1）。

03 验证记录页区 1「AI 正在研究什么」的数据源：research_hypotheses×
validation_records×research_log 三表只读联查返回主任务卡片行——
卡片全部字段为三源表派生视图，权威在各源表，本端点零持久化零副作用
（GET 幂等；沿 GET /api/workbench/pending-events 只读现算聚合先例）。

adhoc 归属语义：hypothesis_id='adhoc' 的验证记录在假设表零父行，
主表联查自然排除（结构事实非过滤特判）；其可见路径=既有
GET /validation-records?hypothesis_id=adhoc（零新增行为不变）。

路径不用 /hypotheses/cards——既有 GET /hypotheses/{hypothesis_id}
路径参数路由会吞掉 cards 段，独立路径避开。
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends

from api.deps import get_db_path
from api.routes.research.common import decode_cursor, encode_cursor
from core.persistence.repository import research as rr

logger = logging.getLogger(__name__)
router = APIRouter()

# 状态机值域（hypothesis_service _TERMINAL 同源——与当前数据分布无关：
# supported 存量零仍属合法值，空态文案归前端）
_STATUSES = ("active", "supported", "refuted", "superseded", "abandoned")


@router.get("/hypothesis-cards")
def hypothesis_cards(
    status: Optional[str] = None,
    q: Optional[str] = None,
    page: int = 1,
    page_size: int = 50,
    cursor: Optional[str] = None,
    db_path: Path = Depends(get_db_path),
) -> Dict[str, Any]:
    """主任务卡片查询：status 等值+q 子串过滤，recent_activity_at
    降序（次序键 hypothesis_id），cursor keyset 续页（提供时 page 被
    忽略），total 为过滤口径真总数。"""
    # 唯一校验面：status 值域外 422（内联校验沿 records_list 先例）
    if status is not None and status not in _STATUSES:
        from api.routes.research.common import research_error_to_http
        from core.analysis.errors import ResearchError
        raise research_error_to_http(ResearchError(
            "RESEARCH_PARAM_INVALID",
            f"status 非法: {status!r}（值域 {_STATUSES}）",
            status=422,
            detail={"field": "status", "value": status,
                    "legal_range": list(_STATUSES)}))
    # 钳制沿 /hypotheses、/log 同款（q/page/page_size 钳制语义不产 422）
    if page < 1:
        page = 1
    if page_size < 1 or page_size > 200:
        page_size = 50
    cur = decode_cursor(cursor)
    items = rr.list_hypothesis_cards(
        db_path, status=status, q=q,
        offset=0 if cur is not None else (page - 1) * page_size,
        limit=page_size, cursor=cur, order="desc")
    return {"items": items,
            "total": rr.count_hypothesis_cards(db_path, status=status, q=q),
            "page": page, "page_size": page_size,
            "next_cursor": encode_cursor(
                str(items[-1]["recent_activity_at"]),
                items[-1]["hypothesis_id"])
            if items else None}
