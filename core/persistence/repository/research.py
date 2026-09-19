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

from pathlib import Path
from typing import Any, Dict, List, Optional
from uuid import uuid4

from sqlalchemy import insert, select, update

from core.persistence.orm.models import (
    AnalysisTask,
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
) -> None:
    """原子双写：research_hypotheses + research_log(register)。

    log_id 可注入（测试原子性用）；第二条 insert 失败时假设行一并回滚。
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
) -> List[Dict[str, Any]]:
    engine = _engine_for(db_path)
    stmt = select(ResearchHypothesis).order_by(
        ResearchHypothesis.created_at, ResearchHypothesis.hypothesis_id
    ).offset(offset).limit(limit)
    if status is not None:
        stmt = stmt.where(ResearchHypothesis.status == status)
    with engine.connect() as conn:
        return [dict(r) for r in conn.execute(stmt).mappings()]


def transition_hypothesis(
    db_path: Path,
    hypothesis_id: str,
    *,
    target_status: str,
    terminal_ref: str,
    superseded_by: Optional[str] = None,
    log_id: Optional[str] = None,
) -> int:
    """条件迁移（WHERE status='active'）+ 迁移日志，同一事务。

    Returns:
        影响行数：1=迁移成功；0=非 active（调用方重查判读幂等/终态冲突）。
    """
    engine = _engine_for(db_path)
    now = _now()
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
            return 0
        conn.execute(insert(ResearchLog).values(
            log_id=log_id or _new_id(),
            hypothesis_id=hypothesis_id,
            entry_type="transition",
            ref_id=terminal_ref,
            summary=f"active -> {target_status} (ref={terminal_ref})",
            created_at=now,
        ))
        return result.rowcount


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
    offset: int = 0, limit: int = 50,
) -> List[Dict[str, Any]]:
    """按 hypothesis_id 过滤的日志列表（created_at+id 双键稳定排序）。"""
    engine = _engine_for(db_path)
    stmt = select(ResearchLog).order_by(
        ResearchLog.created_at, ResearchLog.log_id
    ).offset(offset).limit(limit)
    if hypothesis_id is not None:
        stmt = stmt.where(ResearchLog.hypothesis_id == hypothesis_id)
    with engine.connect() as conn:
        return [dict(r) for r in conn.execute(stmt).mappings()]


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
) -> List[Dict[str, Any]]:
    engine = _engine_for(db_path)
    stmt = select(ValidationRecord).order_by(
        ValidationRecord.created_at, ValidationRecord.record_id
    ).offset(offset).limit(limit)
    if hypothesis_id is not None:
        stmt = stmt.where(ValidationRecord.hypothesis_id == hypothesis_id)
    if strategy_id is not None:
        stmt = stmt.where(ValidationRecord.strategy_id == strategy_id)
    with engine.connect() as conn:
        return [dict(r) for r in conn.execute(stmt).mappings()]


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
