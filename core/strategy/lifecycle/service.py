"""策略状态迁移服务（build-strategy-lifecycle 2.1/2.3）。

transit=全系统唯一合法 strategy.status 变更通道（设计卡 2.1 契约）。
内部序（每边同构）：载入（404）→边校验（同态=not_migrated 事前入口；
异态不在矩阵=409 含合法后继集）→trigger 值域校验（非枚举=422，
CARD-04）→凭证分派（credential.py——缺失
422 含缺失清单/task_end 守卫 not_migrated）→CAS（rowcount=0 重查：
已处目标态=幂等成功响应，否则 409 RACE）→事件同事务→三族返回。

CAS+幂等沿 research.py promote 先例（rowcount=0 重查目标态）；事件
append-only（只 insert）。conn 可选透传=同事务复合能力（研究链/
挂盘外层事务内调用，两侧原子成败——外层回滚则迁移与事件皆无）。
compute_waterline=水位推导查询（max(事件全史序, 当前态序)——零写入
零缓存，与事件表纯净性原则同构）。
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Optional
from uuid import uuid4

from sqlalchemy import Connection, insert, select, update

from core.persistence.orm.engine import engine_for as _engine_for
from core.persistence.orm.models import Strategy, StrategyTransitionEvent
from core.persistence.timestamps import utc_now_iso as _now
from core.strategy.lifecycle import credential as _credential
from core.strategy.lifecycle.machine import (
    SUCCESSORS, TRANSITION_MATRIX, TRIGGERS, WATERLINE_ORDER, LifecycleError)

# not_migrated 原因封闭枚举（正当不触发——两来源：同态事前入口/
# task_end 非最后任务守卫）
NOT_MIGRATED_REASONS: frozenset = frozenset({
    "active_tasks_remain", "already_in_status"})


def transit(
    db_path: Path,
    strategy_id: str,
    *,
    target: str,
    trigger: str,
    evidence: Optional[Dict[str, Any]] = None,
    conn: Optional[Connection] = None,
) -> Dict[str, Any]:
    """执行一次策略状态迁移（唯一合法 status 变更通道）。

    Args:
        db_path: 数据库路径（conn 透传时仅用于兼容签名，不开引擎）。
        strategy_id: 策略 ID。
        target: 目标态（六值）。
        trigger: 触发枚举（九值）。
        evidence: 凭证载荷 dict（credential/qualify/task_end/refute
            必填；manual 边可空）。
        conn: 可选外部连接——透传则迁移与事件写入外层事务（同事务
            复合能力），否则自开单事务。

    Returns:
        三族之一：成功 {migrated: True, from_status/to_status/
        event_id/idempotent}；not_migrated {migrated: False,
        not_migrated_reason}。

    Raises:
        LifecycleError: 404 STRATEGY_NOT_FOUND / 409
            STRATEGY_ILLEGAL_TRANSITION / 422 STRATEGY_TRIGGER_INVALID /
            422 STRATEGY_CREDENTIAL_INSUFFICIENT /
            409 STRATEGY_TRANSITION_RACE。
    """
    if conn is not None:
        return _transit_on_conn(
            conn, strategy_id, target=target, trigger=trigger,
            evidence=evidence)
    engine = _engine_for(db_path)
    with engine.begin() as own:
        return _transit_on_conn(
            own, strategy_id, target=target, trigger=trigger,
            evidence=evidence)


def _not_migrated(
    strategy_id: str, from_status: str, target: str, reason: str,
) -> Dict[str, Any]:
    return {"migrated": False, "strategy_id": strategy_id,
            "not_migrated_reason": reason,
            "from_status": from_status, "to_status": target}


def _transit_on_conn(
    conn: Connection,
    strategy_id: str,
    *,
    target: str,
    trigger: str,
    evidence: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    now = _now()
    # ①载入（404）
    from_status = conn.execute(
        select(Strategy.status)
        .where(Strategy.strategy_id == strategy_id)
    ).scalar_one_or_none()
    if from_status is None:
        raise LifecycleError(
            code="STRATEGY_NOT_FOUND", message="策略不存在",
            status=404, detail={"strategy_id": strategy_id})
    # ②同态：正当不触发（事前入口——与 CAS 幂等分支统一为「已处
    # 目标态」的两入口，同态非错误）
    if from_status == target:
        return _not_migrated(strategy_id, from_status, target,
                             "already_in_status")
    # ③边校验（适用域=异态且不在矩阵封闭集）
    if (from_status, target) not in TRANSITION_MATRIX:
        raise LifecycleError(
            code="STRATEGY_ILLEGAL_TRANSITION",
            message=f"非法状态迁移：{from_status} -> {target}",
            status=409,
            detail={"strategy_id": strategy_id,
                    "from_status": from_status, "to_status": target,
                    "allowed_successors": sorted(SUCCESSORS[from_status])})
    # ④trigger 值域校验（CARD-04）：九值封闭为常量契约（machine.
    # TRIGGERS，Team Lead 定夺非契约改写）——非枚举值修复前走 manual
    # 通道放行并记入事件，审计事实源存在被任意字符串写入的通道
    if trigger not in TRIGGERS:
        raise LifecycleError(
            code="STRATEGY_TRIGGER_INVALID",
            message=f"非法迁移触发枚举：{trigger}",
            status=422,
            detail={"strategy_id": strategy_id, "trigger": trigger,
                    "valid_triggers": sorted(TRIGGERS)})
    # ⑤凭证分派（缺失→422 含缺失清单；task_end 守卫→not_migrated）
    missing = _credential.missing_evidence(trigger, evidence)
    if missing:
        raise LifecycleError(
            code="STRATEGY_CREDENTIAL_INSUFFICIENT",
            message=f"迁移凭证不足（trigger={trigger}）",
            status=422,
            detail={"strategy_id": strategy_id, "trigger": trigger,
                    "missing": missing})
    if (trigger == "credential"
            and not _credential.has_own_pass(conn, strategy_id)):
        raise LifecycleError(
            code="STRATEGY_CREDENTIAL_INSUFFICIENT",
            message="draft→active 凭证不足：无挂本策略的 pass 记录",
            status=422,
            detail={"strategy_id": strategy_id, "trigger": trigger,
                    "missing": ["validation_record(verdict=pass)"]})
    if (trigger == "task_end"
            and not _credential.has_no_active_tasks(conn, strategy_id)):
        return _not_migrated(strategy_id, from_status, target,
                             "active_tasks_remain")
    # ⑥CAS：WHERE 前态命中才写（rowcount=0=并发窗口——重查分流）
    result = conn.execute(
        update(Strategy)
        .where(Strategy.strategy_id == strategy_id,
               Strategy.status == from_status)
        .values(status=target, updated_at=now))
    if result.rowcount == 0:
        current = conn.execute(
            select(Strategy.status)
            .where(Strategy.strategy_id == strategy_id)
        ).scalar_one_or_none()
        if current == target:
            # 幂等（事后发现入口）：已处目标态——不产生第二事件，
            # 回填最近同边同 trigger 事件 id（CARD-05：同边双合法
            # trigger（如 draft→archived 的 manual_archive/refute）
            # 并发时归因到本 trigger 的事件，不误挂他者；次序键加
            # event_id 兼治同刻 tie-break——返回族与首迁同构）
            event_id = conn.execute(
                select(StrategyTransitionEvent.event_id)
                .where(StrategyTransitionEvent.strategy_id == strategy_id,
                       StrategyTransitionEvent.from_status == from_status,
                       StrategyTransitionEvent.to_status == target,
                       StrategyTransitionEvent.trigger == trigger)
                .order_by(StrategyTransitionEvent.created_at.desc(),
                          StrategyTransitionEvent.event_id.desc())
                .limit(1)
            ).scalar_one_or_none()
            return {"migrated": True, "strategy_id": strategy_id,
                    "from_status": from_status, "to_status": target,
                    "event_id": event_id, "idempotent": True}
        raise LifecycleError(
            code="STRATEGY_TRANSITION_RACE",
            message="状态迁移竞争：载入后前态已被并发变更",
            status=409,
            detail={"strategy_id": strategy_id,
                    "from_status": from_status, "to_status": target,
                    "current_status": current})
    # ⑦迁移事件（同事务，append-only）
    event_id = uuid4().hex[:12]
    conn.execute(insert(StrategyTransitionEvent).values(
        event_id=event_id, strategy_id=strategy_id,
        from_status=from_status, to_status=target, trigger=trigger,
        evidence_json=(json.dumps(evidence, ensure_ascii=False)
                       if evidence else None),
        created_at=now))
    # ⑧成功族
    return {"migrated": True, "strategy_id": strategy_id,
            "from_status": from_status, "to_status": target,
            "event_id": event_id, "idempotent": False}


def compute_waterline(db_path: Path, strategy_id: str) -> int:
    """最高水位推导查询（零写入零缓存）。

    waterline = max(事件表全部 to∈序集 的最大序, 当前 status∈序集
    的序)；任一侧空取另一侧，双侧空=0（存量 archived 行=0=「历史
    不可溯」诚实表达）。全史 max 非末条：往返（active→paper→active）
    后水位=2 不随回落降；revalidate 不归零（事件 append-only）。

    Args:
        db_path: 数据库路径。
        strategy_id: 策略 ID。

    Returns:
        水位序值（0-4；archived 不入序）。
    """
    engine = _engine_for(db_path)
    with engine.connect() as conn:
        return _waterline_on_conn(conn, strategy_id)


def _waterline_on_conn(conn: Connection, strategy_id: str) -> int:
    # 事件侧：全部 to_status 落序集的行取最大序（archived 出边不计）
    to_statuses = conn.execute(
        select(StrategyTransitionEvent.to_status)
        .where(StrategyTransitionEvent.strategy_id == strategy_id)
    ).scalars().all()
    event_values = [WATERLINE_ORDER[t] for t in to_statuses
                    if t in WATERLINE_ORDER]
    # 当前态侧（archived 不在序集=该侧空；行缺失同样空）
    status = conn.execute(
        select(Strategy.status)
        .where(Strategy.strategy_id == strategy_id)
    ).scalar_one_or_none()
    current_value = WATERLINE_ORDER.get(status) if status is not None else None
    values = [v for v in (max(event_values) if event_values else None,
                          current_value) if v is not None]
    return max(values) if values else 0
