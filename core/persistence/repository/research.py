"""research repository（add-ai-research-mcp 1.2）：研究域八表读写。

形制锚：core/persistence/repository 既有域文件（db_path + engine.begin 函数级
事务、_engine_for/_now 共享 helper）。设计卡（幂等/并发/事务）权威在
core/analysis/DESIGN.md（随 7.3 建立），本文件只落存储语义：

- 双写原子性经组合函数保证（一个 engine.begin 覆盖双表写入，附.6）
- 幂等键查询：假设 (conditions_hash,ranges_hash) WHERE active；
  验证 (hypothesis_id,strategy_id,range_hash)
- 假设迁移是条件更新（WHERE status='active'），影响行数由调用方判读
  （0 → 重查：同目标幂等返回 / 异终态 409，卡2）
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from uuid import uuid4

from sqlalchemy import and_, func, insert, or_, select, update

from core.persistence.orm.models import (
    AnalysisTask,
    FeatureRequest,
    FormulaPlugin,
    ModelPlugin,
    ResearchHypothesis,
    ResearchLog,
    SignalRegistryEntry,
    ValidationRecord,
)
from core.persistence.repository.dataset_meta import _engine_for, _now


def _new_id() -> str:
    """研究域行 id：uuid4 hex（日志 id 生成内聚于此，调用方不关心）。"""
    return uuid4().hex


def _apply_keyset(
    stmt: Any,
    created_at_col: Any,
    pk_col: Any,
    cursor: Optional[Tuple[str, str]],
    order: str,
) -> Any:
    """研究域列表的 keyset 过滤与稳定排序（四表同构收口）。

    cursor=(created_at, 主键) 按字典序严格比较（>cursor / <cursor 的
    and_/or_ 展开形，不依赖 SQLite 行值支持）；order 缺省 asc——
    offset/limit 翻页消费方（iter_all_pages）零改动，HTTP 列表端点
    传 desc（最新在前）并经 cursor 续页（深翻页漂移免疫）。
    """
    if cursor is not None:
        c, k = cursor
        if order == "desc":
            stmt = stmt.where(or_(
                created_at_col < c,
                and_(created_at_col == c, pk_col < k)))
        else:
            stmt = stmt.where(or_(
                created_at_col > c,
                and_(created_at_col == c, pk_col > k)))
    if order == "desc":
        return stmt.order_by(created_at_col.desc(), pk_col.desc())
    return stmt.order_by(created_at_col, pk_col)


# ============================================================ 假设（双写组合）

def save_hypothesis_with_log(
    db_path: Path,
    *,
    hypothesis_id: str,
    conditions: str,
    conditions_hash: str,
    statement: str,
    data_ranges: str,
    ranges_hash: str,
    log_id: Optional[str] = None,
    screen_use: Optional[Dict[str, str]] = None,
) -> None:
    """原子写：research_hypotheses + research_log(register) [+ screen_use]。

    log_id 可注入（测试原子性用）；任一后续 insert 失败时假设行一并
    回滚。screen_use（add-screen-patterns）：{ref_id, data_ranges}——
    登记引用扫描候选时同事务追加谱系日志（区间经判定并集机制自动
    纳入隔离，isolation.py 零改动）。
    """
    engine = _engine_for(db_path)
    now = _now()
    with engine.begin() as conn:
        conn.execute(insert(ResearchHypothesis).values(
            hypothesis_id=hypothesis_id,
            conditions=conditions, conditions_hash=conditions_hash,
            statement=statement,
            data_ranges=data_ranges, ranges_hash=ranges_hash,
            status="active",
            created_at=now, updated_at=now,
        ))
        conn.execute(insert(ResearchLog).values(
            log_id=log_id or _new_id(),
            hypothesis_id=hypothesis_id,
            entry_type="register",
            ref_id=hypothesis_id,
            data_ranges=data_ranges,
            summary=statement,
            created_at=now,
        ))
        if screen_use is not None:
            conn.execute(insert(ResearchLog).values(
                log_id=_new_id(),
                hypothesis_id=hypothesis_id,
                entry_type="screen_use",
                ref_id=screen_use["ref_id"],
                data_ranges=screen_use["data_ranges"],
                summary="scan candidate referenced at registration",
                created_at=now,
            ))


def get_hypothesis(db_path: Path, hypothesis_id: str) -> Optional[Dict[str, Any]]:
    engine = _engine_for(db_path)
    with engine.connect() as conn:
        row = conn.execute(
            select(ResearchHypothesis).where(
                ResearchHypothesis.hypothesis_id == hypothesis_id)
        ).mappings().first()
    return dict(row) if row is not None else None


def find_active_hypothesis_by_identity(
    db_path: Path, conditions_hash: str, ranges_hash: str
) -> Optional[Dict[str, Any]]:
    """幂等键查询：同条件集+同区间集且 active 的既有假设（重复登记返回既有）。"""
    engine = _engine_for(db_path)
    with engine.connect() as conn:
        row = conn.execute(
            select(ResearchHypothesis).where(
                ResearchHypothesis.conditions_hash == conditions_hash,
                ResearchHypothesis.ranges_hash == ranges_hash,
                ResearchHypothesis.status == "active",
            ).order_by(ResearchHypothesis.created_at)
        ).mappings().first()
    return dict(row) if row is not None else None


def list_hypotheses(
    db_path: Path, *, status: Optional[str] = None,
    offset: int = 0, limit: int = 50,
    cursor: Optional[Tuple[str, str]] = None,
    order: str = "asc",
) -> List[Dict[str, Any]]:
    """假设列表（分页双轨：offset/limit 原样；cursor=(created_at,
    hypothesis_id) keyset 严格比较——HTTP 列表端点 desc+cursor 续页）。
    count 配套 count_hypotheses。"""
    engine = _engine_for(db_path)
    stmt = _apply_keyset(
        select(ResearchHypothesis),
        ResearchHypothesis.created_at, ResearchHypothesis.hypothesis_id,
        cursor, order,
    ).offset(offset).limit(limit)
    if status is not None:
        stmt = stmt.where(ResearchHypothesis.status == status)
    with engine.connect() as conn:
        return [dict(r) for r in conn.execute(stmt).mappings()]


def count_hypotheses(
    db_path: Path, *, status: Optional[str] = None,
) -> int:
    """假设真总数（过滤口径与 list_hypotheses 一致）。"""
    stmt = select(func.count()).select_from(ResearchHypothesis)
    if status is not None:
        stmt = stmt.where(ResearchHypothesis.status == status)
    engine = _engine_for(db_path)
    with engine.connect() as conn:
        return conn.execute(stmt).scalar_one()


def transition_hypothesis_with_promotion(
    db_path: Path,
    hypothesis_id: str,
    *,
    target_status: str,
    terminal_ref: str,
    superseded_by: Optional[str] = None,
    promote: bool = False,
    log_id: Optional[str] = None,
) -> Dict[str, Any]:
    """条件迁移 + 迁移日志 +（promote 时）谱系 draft 原子转正，同一事务。

    promote 独立证据条件（improve-mcp-agent-loop M4）：仅当存在挂
    (hypothesis_id, 该 draft) 的 verdict=pass 记录才转正——防"用别的
    策略的 pass 定证、转正未验证的 draft"的调包；无自身 pass 时假设
    照常定证、draft 保持 draft（日志记因）。draft 已被人工删除则跳过
    转正并落日志（显式清理不阻塞假设定案）。

    Returns:
        {"rows": 0|1, "promoted": bool, "promoted_strategy_id": Optional[str],
         "promotion_note": Optional[str]}
    """
    from core.persistence.orm.models import Strategy

    engine = _engine_for(db_path)
    now = _now()
    out: Dict[str, Any] = {"rows": 0, "promoted": False,
                           "promoted_strategy_id": None, "promotion_note": None}
    with engine.begin() as conn:
        result = conn.execute(
            update(ResearchHypothesis)
            .where(ResearchHypothesis.hypothesis_id == hypothesis_id,
                   ResearchHypothesis.status == "active")
            .values(
                status=target_status,
                terminal_ref=terminal_ref,
                superseded_by=superseded_by,
                updated_at=now,
            )
        )
        if result.rowcount == 0:
            return out
        out["rows"] = 1
        conn.execute(insert(ResearchLog).values(
            log_id=log_id or _new_id(),
            hypothesis_id=hypothesis_id,
            entry_type="transition",
            ref_id=terminal_ref,
            summary=f"active -> {target_status} (ref={terminal_ref})",
            created_at=now,
        ))

        if not promote:
            return out

        drafts = conn.execute(
            select(Strategy.strategy_id)
            .where(Strategy.parent_ids == hypothesis_id,
                   Strategy.status == "draft")
        ).scalars().all()
        if not drafts:
            out["promotion_note"] = "谱系无 draft（已删除或未转化），跳过转正"
            conn.execute(insert(ResearchLog).values(
                log_id=_new_id(), hypothesis_id=hypothesis_id,
                entry_type="promotion", ref_id=terminal_ref,
                summary="promotion skipped: no draft in lineage",
                created_at=now))
            return out

        for draft_id in drafts:
            has_own_pass = conn.execute(
                select(ValidationRecord.record_id)
                .where(ValidationRecord.hypothesis_id == hypothesis_id,
                       ValidationRecord.strategy_id == draft_id,
                       ValidationRecord.verdict == "pass")
                .limit(1)
            ).first()
            if has_own_pass is None:
                out["promotion_note"] = (
                    f"draft {draft_id} 无自身 pass 记录，保持 draft（证据绑定未满足）")
                conn.execute(insert(ResearchLog).values(
                    log_id=_new_id(), hypothesis_id=hypothesis_id,
                    entry_type="promotion", ref_id=draft_id,
                    summary="promotion skipped: draft has no own pass record",
                    created_at=now))
                continue
            conn.execute(
                update(Strategy)
                .where(Strategy.strategy_id == draft_id)
                .values(status="active", updated_at=now)
            )
            out["promoted"] = True
            out["promoted_strategy_id"] = draft_id
            conn.execute(insert(ResearchLog).values(
                log_id=_new_id(), hypothesis_id=hypothesis_id,
                entry_type="promotion", ref_id=draft_id,
                summary=f"draft promoted -> active (evidence-bound pass {has_own_pass[0]})",
                created_at=now))
        return out


# ============================================================ 研究日志

def append_research_log(
    db_path: Path,
    *,
    hypothesis_id: Optional[str],
    entry_type: str,
    ref_id: Optional[str] = None,
    data_ranges: Optional[str] = None,
    summary: Optional[str] = None,
) -> str:
    """追加一条研究日志（返回 log_id）。日志不可变（无 update 路径）。"""
    log_id = _new_id()
    engine = _engine_for(db_path)
    with engine.begin() as conn:
        conn.execute(insert(ResearchLog).values(
            log_id=log_id,
            hypothesis_id=hypothesis_id,
            entry_type=entry_type,
            ref_id=ref_id,
            data_ranges=data_ranges,
            summary=summary,
            created_at=_now(),
        ))
    return log_id


def list_research_log(
    db_path: Path, *,
    hypothesis_id: Optional[str] = None,
    entry_type: Optional[str] = None,
    offset: int = 0, limit: int = 50,
    cursor: Optional[Tuple[str, str]] = None,
    order: str = "asc",
) -> List[Dict[str, Any]]:
    """按 hypothesis_id / entry_type 过滤的日志列表（稳定排序）。

    entry_type 过滤供反向核查（全局 screen 留痕）与审计查询通道
    （add-screen-patterns G6）使用；两参数均缺省时行为与既往一致
    （iter_all_pages 的 fetch_page(offset,limit) 契约不动）。分页
    双轨：cursor=(created_at, log_id) keyset 严格比较，order 缺省
    asc，HTTP 列表端点传 desc+cursor 续页。count 配套
    count_research_log。
    """
    engine = _engine_for(db_path)
    stmt = _apply_keyset(
        select(ResearchLog),
        ResearchLog.created_at, ResearchLog.log_id,
        cursor, order,
    ).offset(offset).limit(limit)
    if hypothesis_id is not None:
        stmt = stmt.where(ResearchLog.hypothesis_id == hypothesis_id)
    if entry_type is not None:
        stmt = stmt.where(ResearchLog.entry_type == entry_type)
    with engine.connect() as conn:
        return [dict(r) for r in conn.execute(stmt).mappings()]


def count_research_log(
    db_path: Path, *,
    hypothesis_id: Optional[str] = None,
    entry_type: Optional[str] = None,
) -> int:
    """日志真总数（过滤口径与 list_research_log 一致）。"""
    stmt = select(func.count()).select_from(ResearchLog)
    if hypothesis_id is not None:
        stmt = stmt.where(ResearchLog.hypothesis_id == hypothesis_id)
    if entry_type is not None:
        stmt = stmt.where(ResearchLog.entry_type == entry_type)
    engine = _engine_for(db_path)
    with engine.connect() as conn:
        return conn.execute(stmt).scalar_one()


# ============================================================ 验证记录（双写组合）

def save_validation_with_log(
    db_path: Path,
    *,
    record_id: str,
    hypothesis_id: str,
    strategy_id: Optional[str],
    level: str,
    params: str,
    result: Optional[str],
    verdict: str,
    range_hash: str,
    components: Optional[str] = None,
    log_id: Optional[str] = None,
) -> None:
    """原子双写：validation_records + research_log(validate)。"""
    engine = _engine_for(db_path)
    now = _now()
    with engine.begin() as conn:
        conn.execute(insert(ValidationRecord).values(
            record_id=record_id,
            hypothesis_id=hypothesis_id,
            strategy_id=strategy_id,
            level=level,
            params=params,
            result=result,
            verdict=verdict,
            range_hash=range_hash,
            components=components,
            created_at=now,
        ))
        conn.execute(insert(ResearchLog).values(
            log_id=log_id or _new_id(),
            hypothesis_id=hypothesis_id,
            entry_type="validate",
            ref_id=record_id,
            summary=f"{level}: {verdict}",
            created_at=now,
        ))


def find_validation_by_identity(
    db_path: Path, hypothesis_id: str, strategy_id: Optional[str], range_hash: str
) -> Optional[Dict[str, Any]]:
    """幂等键查询：同 (hypothesis_id, strategy_id, range_hash) 的既有记录。"""
    engine = _engine_for(db_path)
    with engine.connect() as conn:
        row = conn.execute(
            select(ValidationRecord).where(
                ValidationRecord.hypothesis_id == hypothesis_id,
                ValidationRecord.strategy_id == strategy_id,
                ValidationRecord.range_hash == range_hash,
            ).order_by(ValidationRecord.created_at)
        ).mappings().first()
    return dict(row) if row is not None else None


def list_validation_records(
    db_path: Path, *,
    hypothesis_id: Optional[str] = None,
    strategy_id: Optional[str] = None,
    offset: int = 0, limit: int = 50,
    cursor: Optional[Tuple[str, str]] = None,
    order: str = "asc",
) -> List[Dict[str, Any]]:
    """验证记录列表（分页双轨：offset/limit 原样——iter_all_pages 的
    fetch_page 契约不动；cursor=(created_at, record_id) keyset 严格
    比较，order 缺省 asc）。count 配套 count_validation_records。"""
    engine = _engine_for(db_path)
    stmt = _apply_keyset(
        select(ValidationRecord),
        ValidationRecord.created_at, ValidationRecord.record_id,
        cursor, order,
    ).offset(offset).limit(limit)
    if hypothesis_id is not None:
        stmt = stmt.where(ValidationRecord.hypothesis_id == hypothesis_id)
    if strategy_id is not None:
        stmt = stmt.where(ValidationRecord.strategy_id == strategy_id)
    with engine.connect() as conn:
        return [dict(r) for r in conn.execute(stmt).mappings()]


def count_validation_records(
    db_path: Path, *,
    hypothesis_id: Optional[str] = None,
    strategy_id: Optional[str] = None,
) -> int:
    """验证记录真总数（过滤口径与 list_validation_records 一致）。"""
    stmt = select(func.count()).select_from(ValidationRecord)
    if hypothesis_id is not None:
        stmt = stmt.where(ValidationRecord.hypothesis_id == hypothesis_id)
    if strategy_id is not None:
        stmt = stmt.where(ValidationRecord.strategy_id == strategy_id)
    engine = _engine_for(db_path)
    with engine.connect() as conn:
        return conn.execute(stmt).scalar_one()


# ============================================================ 插件（双写组合）

def save_plugin_with_registry(
    db_path: Path,
    *,
    source: str,  # formula | model
    plugin_id: str,
    name: str,
    version: int,
    expression: Optional[str] = None,      # formula
    output_columns: Optional[str] = None,  # formula
    config: Optional[str] = None,          # model
    signal_column: Optional[str] = None,   # model
    status: str = "valid",
    reject_reason: Optional[str] = None,
    entry_id: Optional[str] = None,        # None = rejected 只留记录不注册
    column_name: Optional[str] = None,     # 注册列名（entry 非 None 时必填）
    log_id: Optional[str] = None,
) -> Optional[str]:
    """原子双写：插件表 + signal_registry（valid 时）+ research_log(plugin)。

    Returns:
        注册 entry_id（valid）；rejected 时返回 None（不写注册表）。
    """
    engine = _engine_for(db_path)
    now = _now()
    with engine.begin() as conn:
        if source == "formula":
            conn.execute(insert(FormulaPlugin).values(
                plugin_id=plugin_id, name=name, version=version,
                expression=expression, output_columns=output_columns,
                status=status, reject_reason=reject_reason,
                strategy_visible=1, created_at=now,
            ))
        else:
            conn.execute(insert(ModelPlugin).values(
                plugin_id=plugin_id, name=name, version=version,
                config=config, signal_column=signal_column,
                status=status, reject_reason=reject_reason,
                strategy_visible=1, created_at=now,
            ))
        registered: Optional[str] = None
        if status == "valid" and entry_id is not None:
            conn.execute(insert(SignalRegistryEntry).values(
                entry_id=entry_id, source=source, plugin_id=plugin_id,
                column_name=column_name, version=version,
                strategy_visible=1, created_at=now,
            ))
            registered = entry_id
        conn.execute(insert(ResearchLog).values(
            log_id=log_id or _new_id(),
            hypothesis_id=None,
            entry_type="plugin",
            ref_id=plugin_id,
            summary=f"{source} plugin {name} v{version}: {status}"
                    + (f" ({reject_reason})" if reject_reason else ""),
            created_at=now,
        ))
    return registered


def list_signal_registry_entries(
    db_path: Path, *, only_visible: bool = False,
) -> List[Dict[str, Any]]:
    """插件信号列清单（内置指标不在此表——运行时与 INDICATOR_REGISTRY 合并）。"""
    engine = _engine_for(db_path)
    stmt = select(SignalRegistryEntry).order_by(
        SignalRegistryEntry.column_name, SignalRegistryEntry.version
    )
    if only_visible:
        stmt = stmt.where(SignalRegistryEntry.strategy_visible == 1)
    with engine.connect() as conn:
        return [dict(r) for r in conn.execute(stmt).mappings()]


def list_plugins(
    db_path: Path, *, source: str, name: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """插件版本审计清单（formula/model 两表同构查询）。

    source="formula" 查 FormulaPlugin，否则 ModelPlugin（与
    save_plugin_with_registry 的 source 口径一致）；name 精确过滤
    同名版本族。排序 name+version（版本递增审计读序，原路由层
    内联 SQL 语义原样迁入）。
    """
    model = FormulaPlugin if source == "formula" else ModelPlugin
    engine = _engine_for(db_path)
    stmt = select(model).order_by(model.name, model.version)
    if name:
        stmt = stmt.where(model.name == name)
    with engine.connect() as conn:
        return [dict(r) for r in conn.execute(stmt).mappings()]


# ============================================================ 分析任务句柄

def save_analysis_task(
    db_path: Path, *, task_id: str, template: str, params: str,
) -> None:
    engine = _engine_for(db_path)
    with engine.begin() as conn:
        conn.execute(insert(AnalysisTask).values(
            task_id=task_id, template=template, params=params,
            status="pending", created_at=_now(),
        ))


def update_analysis_task(
    db_path: Path, *, task_id: str,
    status: Optional[str] = None,
    reason: Optional[str] = None,
    result_ref: Optional[str] = None,
) -> None:
    fields: Dict[str, Any] = {}
    if status is not None:
        fields["status"] = status
    if reason is not None:
        fields["reason"] = reason
    if result_ref is not None:
        fields["result_ref"] = result_ref
    if status in ("succeeded", "failed"):
        fields["finished_at"] = _now()
    if not fields:
        return
    engine = _engine_for(db_path)
    with engine.begin() as conn:
        conn.execute(update(AnalysisTask).where(
            AnalysisTask.task_id == task_id).values(**fields))


def get_analysis_task(db_path: Path, task_id: str) -> Optional[Dict[str, Any]]:
    engine = _engine_for(db_path)
    with engine.connect() as conn:
        row = conn.execute(
            select(AnalysisTask).where(AnalysisTask.task_id == task_id)
        ).mappings().first()
    return dict(row) if row is not None else None


def list_stale_running_tasks(
    db_path: Path, *, created_before: str,
) -> List[Dict[str, Any]]:
    """running 且 created_at 早于阈值的任务（API 启动中断回收用）。"""
    engine = _engine_for(db_path)
    with engine.connect() as conn:
        rows = conn.execute(
            select(AnalysisTask).where(
                AnalysisTask.status == "running",
                AnalysisTask.created_at < created_before,
            )
        ).mappings()
        return [dict(r) for r in rows]


def register_existing_plugin_signal(
    db_path: Path,
    *,
    source: str,
    plugin_id: str,
    name: str,
    version: int,
    column_name: str,
    extra: Optional[Dict[str, Any]] = None,
) -> str:
    """为已存在的插件行（如 pending→valid 的模型插件）补注册信号列。

    与 save_plugin_with_registry 的区别：不写插件表（行已存在），只做
    signal_registry 注册 + research_log 追加（同一事务）。
    """
    entry_id = _new_id()
    engine = _engine_for(db_path)
    now = _now()
    with engine.begin() as conn:
        conn.execute(insert(SignalRegistryEntry).values(
            entry_id=entry_id, source=source, plugin_id=plugin_id,
            column_name=column_name, version=version,
            strategy_visible=1, created_at=now))
        conn.execute(insert(ResearchLog).values(
            log_id=_new_id(), hypothesis_id=None, entry_type="plugin",
            ref_id=plugin_id,
            summary=f"{source} plugin {name} v{version}: valid"
                    + (f" ({json.dumps(extra, ensure_ascii=False)[:120]}" if extra else ""),
            created_at=now))
    return entry_id


# ============================================================ 功能需求单

def save_feature_request(
    db_path: Path,
    *,
    request_id: str,
    title: str,
    payload: str,
    status: str,
    created_at: str,
) -> None:
    """功能需求单落库（慢通道——只保证持久化与可查，不自动实现）。"""
    engine = _engine_for(db_path)
    with engine.begin() as conn:
        conn.execute(insert(FeatureRequest).values(
            request_id=request_id, title=title, payload=payload,
            status=status, created_at=created_at))


def list_feature_requests(
    db_path: Path, *, status: Optional[str] = None,
    offset: int = 0, limit: int = 50,
    cursor: Optional[Tuple[str, str]] = None,
    order: str = "asc",
) -> List[Dict[str, Any]]:
    """功能需求单列表（分页双轨：offset/limit 原样；cursor=
    (created_at, request_id) keyset 严格比较——HTTP 列表端点 desc+
    cursor 续页；status 精确过滤）。count 配套 count_feature_requests。"""
    engine = _engine_for(db_path)
    stmt = _apply_keyset(
        select(FeatureRequest),
        FeatureRequest.created_at, FeatureRequest.request_id,
        cursor, order,
    ).offset(offset).limit(limit)
    if status is not None:
        stmt = stmt.where(FeatureRequest.status == status)
    with engine.connect() as conn:
        return [dict(r) for r in conn.execute(stmt).mappings()]


def count_feature_requests(
    db_path: Path, *, status: Optional[str] = None,
) -> int:
    """功能需求单真总数（过滤口径与 list_feature_requests 一致；
    HTTP 层 total=真总数消费，形制照 strategy.count_strategies）。"""
    stmt = select(func.count()).select_from(FeatureRequest)
    if status is not None:
        stmt = stmt.where(FeatureRequest.status == status)
    engine = _engine_for(db_path)
    with engine.connect() as conn:
        return conn.execute(stmt).scalar_one()
