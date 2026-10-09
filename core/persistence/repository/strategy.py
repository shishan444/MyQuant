"""strategy repository（B3b.1 迁移，B3b.0 冻结映射）。

行为等价锚点：save 的 gene_signature 去重语义（同签名存在时高分者
保留，UPDATE 原行整体替换含 strategy_id；ORM 版经单事务原子化）；
list/count 过滤形制（tags LIKE、qualified 布尔）；list 排序列白名单
校验（ValueError 与旧轨一致）。
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional

from sqlalchemy import delete, func, insert, or_, select, update

from core.persistence.orm.models import Strategy
from core.persistence.orm.engine import engine_for as _engine_for
from core.persistence.timestamps import utc_now_iso as _now

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
    """
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
):
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
        status=status,
    ).order_by(order_col).limit(limit).offset(offset)
    engine = _engine_for(db_path)
    with engine.connect() as conn:
        rows = conn.execute(stmt).mappings().all()
    return [dict(r) for r in rows]


def update_strategy(
    db_path: Path,
    *,
    strategy_id: str,
    **fields: Any,
) -> None:
    """更新既有策略的一个或多个字段（白名单不变）。"""
    values = {k: v for k, v in fields.items() if k in _ALLOWED_UPDATE_FIELDS}
    if not values:
        return
    values["updated_at"] = _now()
    engine = _engine_for(db_path)
    with engine.begin() as conn:
        conn.execute(
            update(Strategy)
            .where(Strategy.strategy_id == strategy_id)
            .values(**values)
        )


def delete_strategy(db_path: Path, strategy_id: str) -> None:
    """按 ID 删策略（不存在时无错误）。"""
    engine = _engine_for(db_path)
    with engine.begin() as conn:
        conn.execute(delete(Strategy).where(Strategy.strategy_id == strategy_id))


def update_strategy_verify_stats(
    db_path: Path,
    strategy_id: str,
    *,
    new_score: float,
    verify_star: int,
    verified_at: str,
) -> None:
    """verify 滚动统计（COALESCE/MAX 增量语义原样保真，单事务）。"""
    from sqlalchemy import case, func

    engine = _engine_for(db_path)
    with engine.begin() as conn:
        conn.execute(
            update(Strategy)
            .where(Strategy.strategy_id == strategy_id)
            .values(
                verify_count=func.coalesce(Strategy.verify_count, 0) + 1,
                verify_avg_score=func.round_(
                    case(
                        (Strategy.verify_avg_score.is_(None), new_score),
                        else_=(Strategy.verify_avg_score * func.coalesce(Strategy.verify_count, 0) + new_score)
                        / (func.coalesce(Strategy.verify_count, 0) + 1),
                    ),
                    4,
                ),
                verify_best_score=func.round_(
                    case(
                        (Strategy.verify_best_score.is_(None), new_score),
                        else_=func.max(Strategy.verify_best_score, new_score),
                    ),
                    4,
                ),
                last_verified_at=verified_at,
                verify_star=verify_star,
                updated_at=verified_at,
            )
        )
