"""strategy repository（B3b.1 迁移，B3b.0 冻结映射）。

行为等价锚点：save 的 gene_signature 去重语义（同签名存在时高分者
保留，UPDATE 原行整体替换含 strategy_id；ORM 版经单事务原子化）；
list/count 过滤形制（tags LIKE、qualified 布尔）；list 排序列白名单
校验（ValueError 与旧轨一致）。
"""
from __future__ import annotations

import statistics

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

from sqlalchemy import Connection, and_, delete, func, insert, or_, select, update

from core.persistence.orm.models import PtTask, Strategy
from core.persistence.orm.engine import engine_for as _engine_for
from core.persistence.repository.paper import PT_STATUS_ACTIVE
from core.persistence.timestamps import utc_now_iso as _now
from core.strategy.lifecycle.machine import INITIAL_STATUSES, LifecycleError

_SORT_ALLOWED_COLUMNS = {
    "created_at", "updated_at", "best_score", "best_fitness",
    "name", "symbol", "timeframe", "source", "generation",
}
_SORT_ALLOWED_ORDERS = {"asc", "desc"}

_ALLOWED_UPDATE_FIELDS = frozenset({
    "name", "dna_json", "source", "source_task_id", "symbol", "timeframe",
    "best_score", "generation", "parent_ids", "tags", "notes", "metrics_json",
    "verify_count", "verify_avg_score", "verify_best_score", "last_verified_at", "verify_star",
})


def save_strategy(
    db_path: Path,
    *,
    strategy_id: str,
    dna_json: str,
    symbol: str,
    timeframe: str,
    name: Optional[str] = None,
    source: str = "manual",
    source_task_id: Optional[str] = None,
    best_score: Optional[float] = None,
    generation: int = 0,
    parent_ids: Optional[str] = None,
    tags: Optional[str] = None,
    notes: Optional[str] = None,
    metrics_json: Optional[str] = None,
    gene_signature: Optional[str] = None,
    status: str = "active",
) -> None:
    """新增策略记录，按 gene_signature 去重。

    同签名已存在时保留 best_score 更高者（单事务原子，语义同旧轨）。

    status="draft" 免去重（improve-mcp-agent-loop M4）：草稿无绩效，
    "保留更优"语义错位且会静默吞掉插入（translate 幽灵 id）——草稿
    一律纯 insert，同签名分流由调用方（translate）显式处理。

    初始态白名单（build-strategy-lifecycle 1.3）：只允许 {draft,
    active}——先于 DB INSERT 触发器的函数级结构化拒绝（422 形制，
    双层守卫的层②面；六态其余值只能经生命周期迁移服务进入）。
    """
    if status not in INITIAL_STATUSES:
        raise LifecycleError(
            code="STRATEGY_INITIAL_STATUS_INVALID",
            message="策略初始状态只允许 draft 或 active",
            status=422,
            detail={"strategy_id": strategy_id, "status": status,
                    "allowed": sorted(INITIAL_STATUSES),
                    "note": "其余状态只能经 core/strategy/lifecycle 迁移服务进入"},
        )
    if gene_signature is None:
        try:
            from core.strategy.dna import StrategyDNA
            dna = StrategyDNA.from_json(dna_json)
            gene_signature = dna.gene_signature
        except Exception:
            gene_signature = None

    now = _now()
    engine = _engine_for(db_path)
    with engine.begin() as conn:
        if gene_signature and status != "draft":
            existing = conn.execute(
                select(Strategy.strategy_id, Strategy.best_score)
                .where(Strategy.gene_signature == gene_signature)
                .limit(1)
            ).first()
            if existing:
                existing_id, existing_score = existing[0], existing[1]
                new_is_better = (
                    best_score is not None
                    and (existing_score is None or best_score > existing_score)
                )
                if new_is_better:
                    conn.execute(
                        update(Strategy)
                        .where(Strategy.strategy_id == existing_id)
                        .values(
                            strategy_id=strategy_id, name=name, dna_json=dna_json,
                            source=source, source_task_id=source_task_id,
                            symbol=symbol, timeframe=timeframe, best_score=best_score,
                            generation=generation, parent_ids=parent_ids, tags=tags,
                            notes=notes, metrics_json=metrics_json,
                            gene_signature=gene_signature, updated_at=now,
                        )
                    )
                return

        conn.execute(insert(Strategy).values(
            strategy_id=strategy_id, name=name, dna_json=dna_json, source=source,
            source_task_id=source_task_id, symbol=symbol, timeframe=timeframe,
            best_score=best_score, generation=generation, parent_ids=parent_ids,
            tags=tags, notes=notes, metrics_json=metrics_json,
            gene_signature=gene_signature, status=status,
            created_at=now, updated_at=now,
        ))


def get_strategy(db_path: Path, strategy_id: str) -> Optional[Dict[str, Any]]:
    """按 ID 取单条策略（不存在返回 None）。"""
    engine = _engine_for(db_path)
    with engine.connect() as conn:
        row = conn.execute(
            select(Strategy).where(Strategy.strategy_id == strategy_id)
        ).mappings().first()
    return dict(row) if row is not None else None


def find_by_gene_signature(
    db_path: Path, gene_signature: str,
) -> List[Dict[str, Any]]:
    """按基因签名查全部同签名策略（含 status；translate 同签名分流用）。"""
    engine = _engine_for(db_path)
    with engine.connect() as conn:
        rows = conn.execute(
            select(Strategy).where(Strategy.gene_signature == gene_signature)
        ).mappings().all()
    return [dict(r) for r in rows]


def count_strategies_by_tasks(
    db_path: Path,
    task_ids: List[str],
    min_score: float = 0,
) -> Dict[str, int]:
    """按 task_id 批量计策略数（GROUP BY + 缺失补 0，同旧轨）。"""
    if not task_ids:
        return {}
    engine = _engine_for(db_path)
    with engine.connect() as conn:
        rows = conn.execute(
            select(Strategy.source_task_id, func.count())
            .where(
                Strategy.source_task_id.in_(task_ids),
                Strategy.source == "evolution",
                Strategy.best_score >= min_score,
            )
            .group_by(Strategy.source_task_id)
        ).all()
    result: Dict[str, int] = {tid: 0 for tid in task_ids}
    for source_task_id, cnt in rows:
        result[source_task_id] = cnt
    return result


def _strategy_filters(
    stmt,
    *,
    symbol: Optional[str] = None,
    source: Optional[str] = None,
    source_task_id: Optional[str] = None,
    tags: Optional[str] = None,
    qualified: Optional[bool] = None,
    status: Optional[str] = None,
    min_score: Optional[float] = None,
):
    """过滤面组装（list/count 共用）。

    qualified 过滤 deprecated（build-strategy-lifecycle 5.3 死列处置）：
    strategy.qualified 声明后从未写入实测全 NULL——布尔过滤恒空集=
    现状行为保留（API 兼容不摘参数）；资格语义已由六态+凭证接管。
    """
    if symbol is not None:
        stmt = stmt.where(Strategy.symbol == symbol)
    if source is not None:
        stmt = stmt.where(Strategy.source == source)
    if source_task_id is not None:
        stmt = stmt.where(Strategy.source_task_id == source_task_id)
    if tags is not None:
        stmt = stmt.where(Strategy.tags.like(f"%{tags}%"))
    if qualified is not None:
        stmt = stmt.where(Strategy.qualified == (1 if qualified else 0))
    # 状态过滤（M4）：None=默认视图排除 draft；显式值=精确匹配（draft 可查）
    if status is None:
        stmt = stmt.where(or_(Strategy.status.is_(None),
                              Strategy.status != "draft"))
    else:
        stmt = stmt.where(Strategy.status == status)
    # 分数阈值（A-01 下推）：NULL 分行等同 0 分（与既有内存过滤语义一致）
    if min_score is not None:
        stmt = stmt.where(func.coalesce(Strategy.best_score, 0.0) >= min_score)
    return stmt


def count_strategies(
    db_path: Path,
    *,
    symbol: Optional[str] = None,
    source: Optional[str] = None,
    source_task_id: Optional[str] = None,
    tags: Optional[str] = None,
    qualified: Optional[bool] = None,
    status: Optional[str] = None,
) -> int:
    """计策略数（过滤形制同 list_strategies——status=None 默认排除 draft）。"""
    stmt = _strategy_filters(
        select(func.count()).select_from(Strategy),
        symbol=symbol, source=source, source_task_id=source_task_id,
        tags=tags, qualified=qualified, status=status,
    )
    engine = _engine_for(db_path)
    with engine.connect() as conn:
        return conn.execute(stmt).scalar_one()


def list_strategies(
    db_path: Path,
    *,
    symbol: Optional[str] = None,
    source: Optional[str] = None,
    source_task_id: Optional[str] = None,
    tags: Optional[str] = None,
    qualified: Optional[bool] = None,
    status: Optional[str] = None,
    sort_by: str = "created_at",
    sort_order: str = "desc",
    limit: int = 100,
    offset: int = 0,
    min_score: Optional[float] = None,
) -> List[Dict[str, Any]]:
    """列策略（过滤+白名单排序+分页）。

    status=None 默认视图排除 draft；显式 status（draft/active/archived）
    精确匹配（草稿可见性走显式查询）。
    """
    if sort_by not in _SORT_ALLOWED_COLUMNS:
        raise ValueError(f"Invalid sort_by: {sort_by!r}. Allowed: {sorted(_SORT_ALLOWED_COLUMNS)}")
    if sort_order.lower() not in _SORT_ALLOWED_ORDERS:
        raise ValueError(f"Invalid sort_order: {sort_order!r}. Allowed: asc, desc")

    col = getattr(Strategy, sort_by)
    order_col = col.desc() if sort_order.lower() == "desc" else col.asc()
    stmt = _strategy_filters(
        select(Strategy), symbol=symbol, source=source,
        source_task_id=source_task_id, tags=tags, qualified=qualified,
        status=status, min_score=min_score,
    ).order_by(order_col).limit(limit).offset(offset)
    engine = _engine_for(db_path)
    with engine.connect() as conn:
        rows = conn.execute(stmt).mappings().all()
    return [dict(r) for r in rows]


def update_strategy(
    db_path: Path,
    *,
    strategy_id: str,
    conn: Optional[Connection] = None,
    **fields: Any,
) -> None:
    """更新既有策略的一个或多个字段（白名单不变）。

    status 键显式拒绝（build-strategy-lifecycle 1.3）：此前为静默
    过滤（_ALLOWED_UPDATE_FIELDS 不含 status），现传入即 raise——
    状态变更唯一合法通道=生命周期迁移服务（写面收敛，行为变化点）。

    conn 可选透传（CARD-03，形制同 paper.create_task）：传入则更新走
    外层事务（如 revalidate 的迁移+统计重置两步同事务）；缺省自开
    单事务（既有直更行为不变）。
    """
    if "status" in fields:
        raise LifecycleError(
            code="STRATEGY_STATUS_WRITE_FORBIDDEN",
            message="status 变更只经生命周期迁移服务（transit）",
            status=422,
            detail={"strategy_id": strategy_id,
                    "note": "update 通道显式拒绝 status 键；"
                            "唯一写点=core/strategy/lifecycle/service.py CAS"},
        )
    values = {k: v for k, v in fields.items() if k in _ALLOWED_UPDATE_FIELDS}
    if not values:
        return
    values["updated_at"] = _now()
    stmt = (
        update(Strategy)
        .where(Strategy.strategy_id == strategy_id)
        .values(**values)
    )
    if conn is not None:
        conn.execute(stmt)
        return
    engine = _engine_for(db_path)
    with engine.begin() as own:
        own.execute(stmt)


def delete_strategy(db_path: Path, strategy_id: str) -> None:
    """按 ID 删策略（不存在时无错误）。"""
    engine = _engine_for(db_path)
    with engine.begin() as conn:
        conn.execute(delete(Strategy).where(Strategy.strategy_id == strategy_id))


# ── 批量删除（research-tooling-data-quality CARD-05 下沉）──

# 保护集（Team Lead 批准扩展至流通/在役三态）：paper=纸盘在验、
# candidate=达标候选、live=实盘在役——删除域命中即整批拒绝
# （本集合是 batch 删除域的设计决策子集，非生命周期派生——保留字面）
_BATCH_DELETE_PROTECTED_STATUSES = ("paper", "candidate", "live")
# 并集防御第二判据：非终态纸盘任务引用（状态迁移与任务状态可能不同步
# 的数据一致性兜底——任务终态回流迁移依赖 strategy 行在场）。
# 活跃集引用 paper repo 的 PT_STATUS_ACTIVE 派生视图（单一书写点，
# PT 状态机扩态自动跟随）


@dataclass
class BatchDeleteResult:
    """批量删除预检/执行结果（路由转 HTTP 的内部载体）。

    deleted_count=None 表示预览（confirm=false 只查未删）。
    """

    matched_count: int
    score_summary: Dict[str, Any]
    deleted_count: Optional[int]


def _score_summary(scores: List[Optional[float]]) -> Dict[str, Any]:
    """best_score 列的五数概括+计数+NULL 披露。

    count=非 NULL 参与分布数（null_count 单独披露，两值之和=总行数）；
    1 行退化五数全同值；≥2 行用 statistics.quantiles inclusive 插值
    （四分位主流定义）；空集五数全 None。
    """
    values = [s for s in scores if s is not None]
    null_count = len(scores) - len(values)
    n = len(values)
    if n == 0:
        return {"count": 0, "min": None, "p25": None, "median": None,
                "p75": None, "max": None, "null_count": null_count}
    if n == 1:
        v = values[0]
        return {"count": 1, "min": v, "p25": v, "median": v,
                "p75": v, "max": v, "null_count": null_count}
    p25, median, p75 = statistics.quantiles(values, n=4, method="inclusive")
    return {"count": n, "min": min(values), "p25": p25, "median": median,
            "p75": p75, "max": max(values), "null_count": null_count}


def batch_delete_by_source_task(
    db_path: Path,
    *,
    source_task_id: str,
    generation_gt: Optional[int] = None,
    best_score_lt: Optional[float] = None,
    confirm: bool = False,
) -> BatchDeleteResult:
    """按进化任务圈定批量物理删除产物策略（预览/执行同判据同事务）。

    单事务先查后删（TOCTOU 收窄：预览计数、取列与删除执行收进同一
    engine.begin，窗口=事务持锁期间）。挂盘预检（预览与执行共用，
    先于删除）：删除域命中保护集（paper/candidate/live）或被非终态
    纸盘任务（pending/running）引用 → LifecycleError 422 整批拒绝
    （fail-closed——在用资产显式对待，先撤盘/归档后再清理）。
    """
    conds = [Strategy.source_task_id == source_task_id]
    if generation_gt is not None:
        conds.append(Strategy.generation > generation_gt)
    if best_score_lt is not None:
        conds.append(Strategy.best_score < best_score_lt)
    where = and_(*conds)

    engine = _engine_for(db_path)
    with engine.begin() as conn:
        protected_count = conn.execute(
            select(func.count()).select_from(Strategy)
            .where(where, Strategy.status.in_(_BATCH_DELETE_PROTECTED_STATUSES))
        ).scalar_one()
        pt_active_count = conn.execute(
            select(func.count()).select_from(PtTask)
            .where(
                PtTask.strategy_id.in_(
                    select(Strategy.strategy_id).where(where)),
                PtTask.status.in_(PT_STATUS_ACTIVE),
            )
        ).scalar_one()
        if protected_count or pt_active_count:
            raise LifecycleError(
                code="BATCH_DELETE_PAPER_REFERENCED",
                message="删除域含受保护策略（在验/在役或被挂盘任务引用）",
                status=422,
                detail={
                    "protected_count": protected_count,
                    "pt_active_count": pt_active_count,
                    "protected_statuses": list(_BATCH_DELETE_PROTECTED_STATUSES),
                    "hint": "先撤盘/归档（paper/candidate/live 经生命周期"
                            "迁移退出）或结束挂盘任务后再清理",
                },
            )
        matched_count = conn.execute(
            select(func.count()).select_from(Strategy).where(where)
        ).scalar_one()
        scores = [row[0] for row in conn.execute(
            select(Strategy.best_score).where(where)).all()]
        if not confirm:
            return BatchDeleteResult(
                matched_count=matched_count,
                score_summary=_score_summary(scores),
                deleted_count=None,
            )
        deleted_count = conn.execute(
            delete(Strategy).where(where)).rowcount
        return BatchDeleteResult(
            matched_count=matched_count,
            score_summary=_score_summary(scores),
            deleted_count=deleted_count,
        )


def list_transition_events(
    db_path: Path, strategy_id: str,
) -> List[Dict[str, Any]]:
    """策略状态迁移事件史（be-strategy-transitions D7——审计段数据源：
    created_at 升序时序回放；append-only 只增不删同 lifecycle 语义）。"""
    from core.persistence.orm.models import StrategyTransitionEvent
    engine = _engine_for(db_path)
    with engine.connect() as conn:
        rows = conn.execute(
            select(StrategyTransitionEvent)
            .where(StrategyTransitionEvent.strategy_id == strategy_id)
            .order_by(StrategyTransitionEvent.created_at.asc())
        ).mappings().all()
    return [dict(r) for r in rows]
