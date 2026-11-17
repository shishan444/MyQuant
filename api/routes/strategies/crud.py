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
from pydantic import BaseModel
from fastapi.responses import JSONResponse

from core.persistence.orm.engine import engine_for
from core.persistence.repository.strategy import (
    batch_delete_by_source_task,
    count_strategies,
    delete_strategy,
    find_by_gene_signature,
    get_strategy,
    list_strategies,
    list_transition_events,
    save_strategy,
    update_strategy,
)
from api.deps import get_db_path
from api.schemas import (
    DNAModel,
    StrategyCreate,
    StrategyListResponse,
    StrategyMetrics,
    StrategyResponse,
    StrategyUpdate,
)
from core.strategy.lifecycle import transit
from core.strategy.lifecycle.machine import LifecycleError, SUCCESSORS

logger = logging.getLogger(__name__)


def _lifecycle_error_to_http(exc: LifecycleError) -> HTTPException:
    """LifecycleError -> HTTPException（detail 三段形制，沿 research/
    common.research_error_to_http——strategies 子包不 import 兄弟模块，
    转换契约在本域单点维护）。"""
    return HTTPException(
        status_code=exc.status,
        detail={"code": exc.code, "message": exc.message, "detail": exc.detail},
    )


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
    # 形制校验（422 先于持久化）；不走 DNAModel field_validator——
    # ResearchError 冒泡应用层全局兜底出站（api/app.py 全局 handler）
    dna = payload.dna.to_dna()
    from core.analysis.event_policy import validate_policy
    # 回写归一形（security 观察项采纳）：显式 none 归一 None 落库=
    # 与缺省同存储同签名（与假设侧 D4 幂等签名语义对齐）；非法形
    # 在赋值前 422（validate 先抛，赋值不发生）
    dna.entry_trigger_policy = validate_policy(dna.entry_trigger_policy)

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

    # B8 同签名预检分流（research-execution-config-alignment 卡 B）：
    # save_strategy 同签名去重对非 draft 新行是「保留更优，否则静默
    # 返回」——POST 面表现为落库 id 不存在转 500（无结构化语义、无
    # 既有 id 可循）。预检在 save 前显式分流：同签名既有行（非 draft
    # 优先）且提交不更优（本端点不收 best_score，恒不更优）→ 200 +
    # duplicated 附既有 id（同签名已存在是幂等事实非冲突——与
    # translate 幂等分支同库同语义，消费方直接续用既有 id）；更优则
    # 透传 save_strategy（覆写路径，行为与现状逐位一致）；未命中正常
    # insert。真错误（解析失败/校验不过）已在上文既有通道先拒，不落
    # 入分流路径（错误/幂等两通道分离）。
    _sig_hits = find_by_gene_signature(db_path, dna.gene_signature) \
        if dna.gene_signature else []
    _hit = next((h for h in _sig_hits if h.get("status") != "draft"),
                _sig_hits[0] if _sig_hits else None)
    if _hit is not None:
        _existing_score = _hit.get("best_score")
        _new_score = None  # StrategyCreate 无 best_score 字段（恒不更优）
        _new_is_better = (
            _new_score is not None
            and (_existing_score is None or _new_score > _existing_score))
        if not _new_is_better:
            _draft_hit = _hit.get("status") == "draft"
            return JSONResponse(status_code=200, content={
                "strategy_id": _hit["strategy_id"],
                "duplicated": True,
                "gene_signature": dna.gene_signature,
                "note": ("同签名草稿策略已存在，保留既有（draft 无绩效"
                         "语义，不走保留更优对比）" if _draft_hit else
                         "同签名策略已存在且绩效不低于提交，保留既有"),
            })

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
    source_task_id: Optional[str] = None,
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

    qualified 过滤参数 deprecated（build-strategy-lifecycle 5.3 死列
    处置）：strategy.qualified 声明后从未写入实测全 NULL——布尔过滤
    恒空集=现状行为保留（API 兼容不摘参数）；资格语义已由六态+凭证
    接管（同名异列注意：backtest_result.qualified 是真数据活列）。
    """
    try:
        rows = list_strategies(
            db_path,
            symbol=symbol,
            source=source,
            source_task_id=source_task_id,
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
        status=status, source_task_id=source_task_id,
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
    # 机制保证而非约定；草稿 DNA 无修改通道（假设转化通道已退役），
    # 需不同 DNA 形制时新建策略
    if existing.get("status") == "draft" and payload.dna is not None:
        raise HTTPException(status_code=409, detail={
            "code": "RESEARCH_DRAFT_IMMUTABLE",
            "message": "草稿策略的 DNA 不可直接修改",
            "detail": {
                "strategy_id": strategy_id,
                "note": "草稿 DNA 无修改通道（假设转化通道已退役）——"
                        "需不同 DNA 形制时新建策略；name/tags/notes "
                        "等元数据仍可更新"},
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
        dna = payload.dna.to_dna()
        from core.analysis.event_policy import validate_policy
        dna.entry_trigger_policy = validate_policy(
            dna.entry_trigger_policy)   # 回写归一形（同 create）

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
    """按 ID 删除策略（build-strategy-lifecycle 3.3 两分支）：
    draft→物理删除（现状保持）；非 draft→经 transit(manual_archive)
    归档后 204（行保留+事件在场）——REST 同端点同状态码语义从物理
    删除变归档（strategy-lifecycle spec delta ADDED 承载，消费者影响
    经 S12 披露）。操作者载荷取本地单用户部署形态（工程无用户体系）。"""
    existing = get_strategy(db_path, strategy_id)
    if existing is None:
        raise HTTPException(status_code=404, detail="Strategy not found")
    if existing.get("status") == "draft":
        delete_strategy(db_path, strategy_id)
        return
    try:
        transit(
            db_path, strategy_id, target="archived",
            trigger="manual_archive",
            evidence={"operator": "local-user",
                      "note": "非 draft 删除请求转归档（DELETE 端点）"})
    except LifecycleError as exc:
        raise _lifecycle_error_to_http(exc) from exc


@router.get("/{strategy_id}/transitions")
def list_strategy_transitions(
    strategy_id: str, db_path: Path = Depends(get_db_path),
):
    """策略状态迁移事件史（be-strategy-transitions D7）：created_at 升序
    审计时序；evidence_json 解析嵌入（NULL→None）；零事件 200 空集。"""
    if get_strategy(db_path, strategy_id) is None:
        raise HTTPException(404, detail="Strategy not found")
    events = list_transition_events(db_path, strategy_id)
    for e in events:
        raw = e.pop("evidence_json", None)
        try:
            # t3r9 A1 修正：坏 JSON→None 不炸行（spec Scenario 三基准
            # 一致；原 500 形态系实施漏看 spec——通道外手改面生产不可达，
            # 降级语义以 spec 为准）
            e["evidence"] = json.loads(raw) if raw else None
        except (json.JSONDecodeError, TypeError):
            e["evidence"] = None
    return {"items": events, "total": len(events)}


@router.post("/{strategy_id}/revalidate")
def revalidate_strategy_endpoint(
    strategy_id: str,
    db_path: Path = Depends(get_db_path),
) -> StrategyResponse:
    """归档重启重验（build-strategy-lifecycle 3.4，沿 stop/clone 动作
    端点形制）：archived→draft+旧验证统计作废重置（五列：verify_count
    置 0，verify_avg_score/verify_best_score/verify_star/last_verified_at
    置 NULL——models.py verify 组列）+validation_records 不删不改（审计
    事实）+迁移事件 evidence 记作废明细（重置前快照——重置步骤中断时
    旧值可自事件追溯）+水位不归零（事件 append-only）。非
    archived→409 STRATEGY_ILLEGAL_TRANSITION。迁移+重置两步同事务
    （CARD-03）：重置失败则迁移一并回滚，不留「已 draft 但旧统计
    未作废」中间态。"""
    existing = get_strategy(db_path, strategy_id)
    if existing is None:
        raise HTTPException(status_code=404, detail="Strategy not found")
    if existing.get("status") != "archived":
        raise _lifecycle_error_to_http(LifecycleError(
            code="STRATEGY_ILLEGAL_TRANSITION",
            message="仅 archived 策略可重启重验",
            status=409,
            detail={"strategy_id": strategy_id,
                    "from_status": existing.get("status"),
                    "to_status": "draft",
                    "allowed_successors": sorted(SUCCESSORS["archived"])}))
    invalidated = {
        "verify_count": existing.get("verify_count"),
        "verify_avg_score": existing.get("verify_avg_score"),
        "verify_best_score": existing.get("verify_best_score"),
        "verify_star": existing.get("verify_star"),
        "last_verified_at": existing.get("last_verified_at"),
    }
    engine = engine_for(db_path)
    try:
        with engine.begin() as conn:
            transit(db_path, strategy_id, target="draft",
                    trigger="revalidate",
                    evidence={"invalidated_stats": invalidated}, conn=conn)
            update_strategy(
                db_path, strategy_id=strategy_id, conn=conn,
                verify_count=0, verify_avg_score=None,
                verify_best_score=None, verify_star=None,
                last_verified_at=None)
    except LifecycleError as exc:
        raise _lifecycle_error_to_http(exc) from exc
    row = get_strategy(db_path, strategy_id)
    return _strategy_row_to_response(row)


# ── 批量管理（research-tooling-data-quality 卡 D：按任务圈定的常设
#    管理通道——一次性全量清理由 change-2 任务 2.9 执行，本端点与其
#    正交并存；source_task_id 必填=无圈定的全库批量删除不提供）──


class BatchDeleteRequest(BaseModel):
    """批量删除请求体（confirm 契约=删除不可无声执行）。"""
    source_task_id: str
    generation_gt: Optional[int] = None
    best_score_lt: Optional[float] = None
    confirm: bool = False


@router.post("/batch")
def batch_delete_strategies(
    payload: BatchDeleteRequest,
    db_path: Path = Depends(get_db_path),
) -> Dict[str, Any]:
    """按进化任务批量物理删除其产物策略（不可逆——与单策略 DELETE
    的归档语义不同，端点分离承载语义差异）。

    确认契约：confirm≠true（含缺省）→ 422 结构化拒绝，detail 附
    matched_count 预览数+criteria 回显+score_summary（best_score
    分布摘要 {count,min,p25,median,p75,max,null_count}——count 为
    非 NULL 参与分布数、null_count 单独披露；1 行退化五数全同值、
    ≥2 行 inclusive 插值、空集五数全 None）；confirm=true → 单事务
    批量 DELETE（先查后删同事务），返回 {deleted_count, criteria,
    matched_before}；幂等：重复调用 matched 0→deleted_count=0。
    generation_gt/best_score_lt 可选收紧（预览与执行同判据）。

    挂盘预检（预览与执行共用、先于 confirm 分支；执行分支在 DELETE
    前同事务完成——防预览到执行间的状态变化）：删除域含受保护策略
    （status ∈ {paper, candidate, live}——纸盘在验/达标候选/实盘在役）
    或被非终态纸盘任务（pending/running）引用 → 422
    BATCH_DELETE_PAPER_REFERENCED（detail 附 protected_count/
    pt_active_count 与处置提示——先撤盘/归档后再清理）。
    """
    criteria: Dict[str, Any] = {"source_task_id": payload.source_task_id}
    if payload.generation_gt is not None:
        criteria["generation_gt"] = payload.generation_gt
    if payload.best_score_lt is not None:
        criteria["best_score_lt"] = payload.best_score_lt

    try:
        result = batch_delete_by_source_task(
            db_path,
            source_task_id=payload.source_task_id,
            generation_gt=payload.generation_gt,
            best_score_lt=payload.best_score_lt,
            confirm=payload.confirm,
        )
    except LifecycleError as exc:
        raise _lifecycle_error_to_http(exc) from exc

    if not payload.confirm:
        raise HTTPException(
            status_code=422,
            detail={"code": "BATCH_DELETE_CONFIRM_REQUIRED",
                    "message": "批量删除不可无声执行——确认请显式携带 "
                               "confirm=true",
                    "detail": {"matched_count": result.matched_count,
                               "criteria": criteria,
                               "score_summary": result.score_summary,
                               "hint": "删除不可逆，确认请显式携带 "
                                       "confirm=true（预览数见 "
                                       "matched_count，分数分布见 "
                                       "score_summary）"}})

    # 审计留痕（不可逆物理删除：何时按什么条件删了多少——事后审计
    # 五要素 source_task_id/criteria 全键/deleted_count/matched_before）
    logger.warning(
        "批量删除执行：source_task_id=%s, criteria=%s, "
        "deleted_count=%s, matched_before=%d",
        payload.source_task_id, criteria, result.deleted_count,
        result.matched_count)

    return {"deleted_count": result.deleted_count, "criteria": criteria,
            "matched_before": result.matched_count}
