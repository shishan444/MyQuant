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
from core.analysis.hypothesis_service import HYPOTHESIS_STATUSES
from core.persistence.repository import research as rr

logger = logging.getLogger(__name__)
router = APIRouter()

# 巨 page 钳制上限：offset=(page-1)*page_size 不得超过 SQLite 64 位
# 整数上界 2^63-1，超界时 SQL 参数绑定 OverflowError 裸穿 500。钳后
# 极端积 (_MAX_PAGE-1)*200≈2×10^17，较 2^63-1≈9.2×10^18 留 46 倍
# 余量（page_size 钳制上限若上调，此余量随之重算）
_MAX_PAGE = 10 ** 15
# q 截断上限：超长串进 LIKE 徒增绑定开销，截断到 200 字符（沿仓库
# 错误串截断惯例），不产 422；截断后按前缀子串匹配——超长精确查询
# 语义放弃（截断放宽命中面：前 200 字符命中的行不一定含全串）
_MAX_Q_LEN = 200


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
    # 唯一校验面：status 值域外 422（内联校验沿 records_list 先例）；
    # 值域=HYPOTHESIS_STATUSES（hypothesis_service 状态机权威单点引用，
    # 非手抄——与当前数据分布无关：supported 存量零仍属合法值，空态
    # 文案归前端）
    if status is not None and status not in HYPOTHESIS_STATUSES:
        from api.routes.research.common import research_error_to_http
        from core.analysis.errors import ResearchError
        raise research_error_to_http(ResearchError(
            "RESEARCH_PARAM_INVALID",
            f"status 非法: {status!r}（值域 {HYPOTHESIS_STATUSES}）",
            status=422,
            detail={"field": "status", "value": status,
                    "legal_range": list(HYPOTHESIS_STATUSES)}))
    # 钳制沿 /hypotheses、/log 同款（q/page/page_size 钳制语义不产 422）
    if page < 1:
        page = 1
    # 巨 page 钳到上限（同「page<1 钳到 1」边界族，不回退缺省）：
    # 防 offset 溢出 500，理由见 _MAX_PAGE 注释
    if page > _MAX_PAGE:
        page = _MAX_PAGE
    # 巨 q 截断（超限取前 _MAX_Q_LEN 字符，不产 422）
    if q is not None and len(q) > _MAX_Q_LEN:
        q = q[:_MAX_Q_LEN]
    if page_size < 1 or page_size > 200:
        page_size = 50
    cur = decode_cursor(cursor)
    if cur is not None and cur[0] == "None":
        # NULL 位续页还原：末页行无日志时编码侧 str(None) 产
        # "None|id"，decode 回字符串后须映射回 None 才进 _apply_keyset
        # 的 NULL 段分支（时间串与 "None" 字典序比较无意义）
        cur = (None, cur[1])
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
