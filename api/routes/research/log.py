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


class FeatureRequestPatch(BaseModel):
    """回销 PATCH 载荷（变更二 4.2）：status 必填；resolution 可选
    （{source 必填, note 可选}——at/by 由服务端补齐）。"""

    status: str
    resolution: Optional[Dict[str, Any]] = None


_FR_STATUS_LEGAL = ("open", "resolved", "wont_fix")


@router.patch("/feature-requests/{request_id}")
def patch_feature_request(
    request_id: str,
    payload: FeatureRequestPatch,
    db_path: Path = Depends(get_db_path),
) -> Dict[str, Any]:
    """慢通道需求单回销（变更二 4.2——工程侧动作，研究侧 MCP 面不设
    回销工具：通道隔离承载权限语义，本工程无认证体系下的唯一可执行
    权限边界）。

    部分更新三规则：
    ① 不带 resolution 键 = 该字段不更新（保留现值）；
    ② status→open 时 resolution 自动置 null（open 态无出处的一致性）；
    ③ status=resolved 时 resolution 必填（无出处不得标已实现）→ 422
       detail.param="resolution"；wont_fix 时可选（裁决依据=用户终局
       裁决，note 建议不强制）。
    幂等：请求所含字段与现值全等时 200 返回当前态。
    """
    from fastapi import HTTPException
    from core.persistence.timestamps import utc_now_iso

    if payload.status not in _FR_STATUS_LEGAL:
        raise HTTPException(status_code=422, detail={
            "code": "RESEARCH_PARAM_INVALID",
            "message": f"feature_requests.status 值域外: {payload.status}",
            "detail": {"param": "status",
                       "legal_range": list(_FR_STATUS_LEGAL)}})

    current = rr.get_feature_request(db_path, request_id)
    if current is None:
        raise HTTPException(status_code=404, detail={
            "code": "FEATURE_REQUEST_NOT_FOUND",
            "message": f"需求单不存在: {request_id}", "detail": None})

    has_resolution = ("resolution" in payload.model_fields_set
                      and payload.resolution is not None)
    # 规则③：resolved 语义=已实现并附出处
    if payload.status == "resolved" and not has_resolution:
        raise HTTPException(status_code=422, detail={
            "code": "RESEARCH_PARAM_INVALID",
            "message": "status=resolved 须附 resolution（已实现须可溯源"
                       "出处——source 为对应 change 名/出处标识）",
            "detail": {"param": "resolution",
                       "legal_form": "{source: 必填, note: 可选}"}})
    # 规则①②：不带键=不动现值；status→open 联动清空
    touch = has_resolution or payload.status == "open"
    resolution_json: Optional[str] = None
    if has_resolution:
        src = (payload.resolution or {}).get("source")
        if not src:
            raise HTTPException(status_code=422, detail={
                "code": "RESEARCH_PARAM_INVALID",
                "message": "resolution.source 必填（回销出处标识——"
                           "对应 change 名）",
                "detail": {"param": "resolution",
                           "legal_form": "{source: 必填, note: 可选}"}})
        merged = {"source": str(src),
                  "note": (payload.resolution or {}).get("note"),
                  "at": utc_now_iso(),
                  "by": "engineering"}
        resolution_json = json.dumps(merged, ensure_ascii=False)

    # 幂等短路：请求所含字段与现值全等 → 200 返回当前态（不重写
    # resolution.at——重复同 body 不产生新副作用）
    def _parse_resolution(raw: Optional[str]) -> Optional[Dict[str, Any]]:
        try:
            return json.loads(raw) if raw else None
        except (json.JSONDecodeError, TypeError):
            return None

    def _same_resolution(cur: Optional[str]) -> bool:
        cur_res = _parse_resolution(cur)
        if has_resolution:
            return (cur_res is not None
                    and cur_res.get("source") == (payload.resolution or {}).get("source")
                    and cur_res.get("note") == (payload.resolution or {}).get("note"))
        return not touch or current.get("resolution") is None

    if payload.status == current["status"] and _same_resolution(current.get("resolution")):
        row = current
    else:
        row = rr.update_feature_request(
            db_path, request_id=request_id, status=payload.status,
            resolution=resolution_json, touch_resolution=touch)

    return {"request_id": row["request_id"], "title": row["title"],
            "status": row["status"],
            "resolution": _parse_resolution(row.get("resolution")),
            "created_at": row["created_at"]}


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
        # 回销出处并列披露（变更二：容缺 null——存量行/损坏 JSON 不炸）
        try:
            r["resolution"] = json.loads(r["resolution"]) if r.get("resolution") else None
        except (json.JSONDecodeError, TypeError):
            r["resolution"] = None
    return {"items": rows,
            "total": rr.count_feature_requests(db_path, status=status),
            "page": page, "page_size": page_size,
            "next_cursor": encode_cursor(
                rows[-1]["created_at"], rows[-1]["request_id"])
            if rows else None}
