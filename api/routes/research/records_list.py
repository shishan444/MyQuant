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

# 值域（与 ORM 注释/thresholds 三段闸门一致——非法值 422 由值域校验层产；
# staged_validation 为 add-hypothesis-cards B3 修正补入——原漏致 DB 实测
# 55 条 staged 级记录按此筛选被 422 拒；composite/component 死值域保留；
# sandbox_backtest 为 add-sandbox-records-unified-flow B3 扩入——沙盒
# 留痕入统一流，verdict 恒空串不入 _VERDICTS）
_LEVELS = ("isolated_backtest", "random_window", "staged_validation",
           "composite", "component", "sandbox_backtest")
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
    # 假设原话引文（upgrade-validation-records-list B1.2——LEFT JOIN
    # 取 statement；无匹配行 null——adhoc/假设已删如实，前端显「—」）
    statement: Optional[str] = None


class ValidationRecordsResponse(BaseModel):
    records: List[ValidationRecordRow]
    total: int
    offset: int
    limit: int
    has_more: bool
    # verdict 分布（B1.2——三键恒在场 0=该 verdict 无记录；口径与
    # total 同基数；sandbox 第四键按 level=sandbox_backtest 计数——
    # add-sandbox-records-unified-flow C4，四键恒在场）
    verdict_counts: Dict[str, int]
    # 无收益值记录数（B1.2——sort=total_return_desc 时 worst 值缺失
    # 计数供落尾披露；其他 sort 值恒 0：语义键在场值退化，前端免判
    # sort 分支）
    unsortable_count: int


@router.get("/validation-records", response_model=ValidationRecordsResponse)
def list_validation_records_endpoint(
    request: Request,
    hypothesis_id: Optional[str] = Query(None),
    strategy_id: Optional[str] = Query(None),
    level: Optional[str] = Query(None),
    verdict: Optional[str] = Query(None),
    sort: str = Query("time_desc"),
    q: Optional[str] = Query(None, max_length=200),
    generalization: Optional[bool] = Query(None),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db_path=Depends(get_db_path),
    data_dir=Depends(get_data_dir),
) -> ValidationRecordsResponse:
    """跨策略验证记录列表（upgrade-validation-records-list B2 终态——
    sort/q/generalization 检索+六维过滤下推）。

    sort 封闭枚举四值（time_desc 缺省=现状零变化/time_asc/
    total_return_desc 按 worst 值参排 NULL 落尾/verdict_quality 品质
    序）；q 关键词搜索（statement LIKE、%/_ 通配符字面化）；level/
    verdict 非法值 422（沿内联校验先例）；generalization 独立 bool
    泛化过滤（⑦终裁 toggle——可与 level 组合取交集）。全六维过滤
    （hypothesis_id/strategy_id/q/generalization/level/verdict）SQL
    WHERE 下推——total=全部过滤维基数、页满恒 limit（过滤后结果集
    分页）；verdict_counts/unsortable_count 与 total 同基数。
    """
    from core.analysis.errors import ResearchError

    _SORTS = ("time_desc", "time_asc", "total_return_desc",
              "verdict_quality")
    if sort not in _SORTS:
        raise ResearchError(
            "RESEARCH_PARAM_INVALID",
            f"sort 非法: {sort!r}（值域 {_SORTS}）",
            status=422, detail={"field": "sort", "value": sort})
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

    import json as _json

    def _embed(raw: Optional[str]) -> Optional[Dict[str, Any]]:
        if raw is None:
            return None
        try:
            v = _json.loads(raw)
            return v if isinstance(v, dict) else {"value": v}
        except (ValueError, TypeError):
            return None  # 坏 JSON 如实 None（不捏造不抛——观察面）

    # B2 六维下推（upgrade-validation-records-list 4.1）：level/verdict
    # 经仓储 WHERE（形参 B1 预置）——全过滤维 SQL 下推，total/counts=
    # 六维基数，页满恒 limit（旧「行级过滤当页行数<limit 而 total 不变」
    # 行为退役）
    rows = rr.list_validation_records(
        db_path,
        hypothesis_id=hypothesis_id, strategy_id=strategy_id,
        offset=offset, limit=limit + 1,
        order="asc" if sort == "time_asc" else "desc",
        q=(q or None), generalization=generalization, sort=sort,
        level=level, verdict=verdict,
    )
    has_more = len(rows) > limit
    rows = rows[:limit]

    out_rows: List[ValidationRecordRow] = []
    for r in rows:
        out_rows.append(ValidationRecordRow(
            record_id=r["record_id"], hypothesis_id=r["hypothesis_id"],
            strategy_id=r["strategy_id"], level=r["level"],
            segmentation=r["segmentation"], verdict=r["verdict"],
            range_hash=r["range_hash"],
            pollution_cause=r["pollution_cause"],
            created_at=r["created_at"],
            params=_embed(r["params"]), result=_embed(r["result"]),
            statement=r.get("statement"),
        ))

    # 计数六维口径（B2.1——total=全部过滤维基数；verdict_counts/
    # unsortable_count 同基数）
    agg = rr.validation_records_aggregates(
        db_path, hypothesis_id=hypothesis_id, strategy_id=strategy_id,
        q=(q or None), generalization=generalization,
        level=level, verdict=verdict)
    return ValidationRecordsResponse(
        records=out_rows, total=agg["total"], offset=offset, limit=limit,
        has_more=has_more,
        verdict_counts={"pass": agg["pass"], "fail": agg["fail"],
                        "inconclusive": agg["inconclusive"],
                        "sandbox": agg["sandbox"]},
        unsortable_count=(agg["unsortable_count"]
                          if sort == "total_return_desc" else 0))
