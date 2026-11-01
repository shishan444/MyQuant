"""GET /api/research/validation-records 列表端点（workbench-observation 组 A）。

01 页 B2-3「最近验证结论」数据源：四维可选过滤+分页+created_at 降序；
与既有按策略档案 GET /validation/{strategy_id}/report 路径区分（本端点
跨策略时间流视图，档案端点单策略全史）。行字段一次给全（前端概要/展开
为呈现形制，不设两段式取数）。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel

from api.deps import get_db_path, get_data_dir
from core.persistence.repository import research as rr

router = APIRouter(tags=["research"])

# 值域（与 ORM 注释/thresholds 三段闸门一致——非法值 422 由值域校验层产）
_LEVELS = ("isolated_backtest", "random_window", "composite", "component")
_VERDICTS = ("pass", "fail", "inconclusive")


class ValidationRecordRow(BaseModel):
    record_id: str
    hypothesis_id: str
    strategy_id: Optional[str] = None
    level: str
    segmentation: str
    verdict: str
    range_hash: str
    pollution_cause: Optional[str] = None
    created_at: str
    params: Optional[Dict[str, Any]] = None   # JSON 解析嵌入（坏 JSON→None 如实）
    result: Optional[Dict[str, Any]] = None   # NULL 列→None（三态不捏造）


class ValidationRecordsResponse(BaseModel):
    records: List[ValidationRecordRow]
    total: int
    offset: int
    limit: int
    has_more: bool


@router.get("/validation-records", response_model=ValidationRecordsResponse)
def list_validation_records_endpoint(
    request: Request,
    hypothesis_id: Optional[str] = Query(None),
    strategy_id: Optional[str] = Query(None),
    level: Optional[str] = Query(None),
    verdict: Optional[str] = Query(None),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db_path=Depends(get_db_path),
    data_dir=Depends(get_data_dir),
) -> ValidationRecordsResponse:
    """跨策略验证记录时间流（created_at 降序——「最近」消费语义）。

    四维可选过滤；level/verdict 非法值 422（沿 research_error_to_http
    结构化错误形制——本端点内联校验产同形 detail）；分页探测式
    has_more（多取一条判定）。
    """
    from api.routes.research.common import research_error_to_http
    from core.analysis.errors import ResearchError

    try:
        if level is not None and level not in _LEVELS:
            raise ResearchError(
                "RESEARCH_PARAM_INVALID",
                f"level 非法: {level!r}（值域 {_LEVELS}）",
                status=422, detail={"field": "level", "value": level})
        if verdict is not None and verdict not in _VERDICTS:
            raise ResearchError(
                "RESEARCH_PARAM_INVALID",
                f"verdict 非法: {verdict!r}（值域 {_VERDICTS}）",
                status=422, detail={"field": "verdict", "value": verdict})
    except ResearchError as exc:
        raise research_error_to_http(exc) from exc

    import json as _json

    def _embed(raw: Optional[str]) -> Optional[Dict[str, Any]]:
        if raw is None:
            return None
        try:
            v = _json.loads(raw)
            return v if isinstance(v, dict) else {"value": v}
        except (ValueError, TypeError):
            return None  # 坏 JSON 如实 None（不捏造不抛——观察面）

    # 仓储现函数降序不支持（order 缺省 asc）——取 count 后按 keyset 降序
    # 复用 list_validation_records：order 参数直传（仓储已支持 order 形参）
    rows = rr.list_validation_records(
        db_path,
        hypothesis_id=hypothesis_id, strategy_id=strategy_id,
        offset=offset, limit=limit + 1, order="desc",
    )
    has_more = len(rows) > limit
    rows = rows[:limit]

    # level/verdict 过滤在 SQL 仓储面无对应形参（仓储仅两维）——行级
    # 过滤在分页之后取段内命中（时间流语义：过滤维不改变页窗口）。
    # 注：count 口径按两维（与仓储一致）；level/verdict 行级过滤不进
    # total（total=两维过滤基数——页语义基准，如实披露）。
    out_rows: List[ValidationRecordRow] = []
    for r in rows:
        if level is not None and r["level"] != level:
            continue
        if verdict is not None and r["verdict"] != verdict:
            continue
        out_rows.append(ValidationRecordRow(
            record_id=r["record_id"], hypothesis_id=r["hypothesis_id"],
            strategy_id=r["strategy_id"], level=r["level"],
            segmentation=r["segmentation"], verdict=r["verdict"],
            range_hash=r["range_hash"],
            pollution_cause=r["pollution_cause"],
            created_at=r["created_at"],
            params=_embed(r["params"]), result=_embed(r["result"]),
        ))

    total = rr.count_validation_records(
        db_path, hypothesis_id=hypothesis_id, strategy_id=strategy_id)
    return ValidationRecordsResponse(
        records=out_rows, total=total, offset=offset, limit=limit,
        has_more=has_more)
