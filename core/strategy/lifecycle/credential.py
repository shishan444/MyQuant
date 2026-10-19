"""迁移凭证判定（build-strategy-lifecycle 2.2）。

判定函数单点纪律：draft→active 的 pass 判定沿现状 promote 语义原样
（research.py promote 循环同式查询——REQ-3-01 落地时在此单点收紧
判定，通道形制不变）；task_end 多任务守卫供纸盘回调（5.2a）与回补
扫描（5.2c）共享本单点，不另立第二判定。

evidence 必填分派（trigger→缺失清单）住本模块：service 凭证校验
消费；manual 边可空（操作者标识可选），mount 载荷沿 5.1 传入不强制。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import Connection, select

from core.persistence.orm.models import PtTask, Strategy, ValidationRecord

# 必填 evidence 的 trigger 集（credential/qualify/task_end/refute）
EVIDENCE_REQUIRED_TRIGGERS: frozenset = frozenset({
    "credential", "qualify", "task_end", "refute"})

# task_end 凭证必填键（task_id+终态）
_TASK_END_REQUIRED_KEYS: Tuple[str, ...] = ("task_id", "final_status")


def _active_statuses() -> frozenset:
    """pt_task 活跃集（非终态）：paper 单点派生常量直返（零推导
    消费——原 import 前身图后重写派生公式已收敛为单点常量，
    refactor-paper-runtime-data-access C 卡；薄壳保留防调用面散改）。"""
    from core.persistence.repository.paper import PT_STATUS_ACTIVE
    return PT_STATUS_ACTIVE


def missing_evidence(
    trigger: str, evidence: Optional[Dict[str, Any]],
) -> List[str]:
    """trigger 的 evidence 缺失清单（空清单=通过）。

    必填 trigger：evidence 缺失/非 dict/空 dict → 点名 "evidence"；
    task_end 另做键级校验（task_id/final_status）。manual 边恒通过。

    Args:
        trigger: 触发枚举（九值）。
        evidence: 凭证载荷（dict——record_id 清单/任务 ID/操作者标识）。

    Returns:
        缺失项清单（供 422 detail「缺失凭证清单」）。
    """
    if trigger not in EVIDENCE_REQUIRED_TRIGGERS:
        return []
    if not isinstance(evidence, dict) or not evidence:
        return ["evidence"]
    if trigger == "task_end":
        return [k for k in _TASK_END_REQUIRED_KEYS if k not in evidence]
    return []


def has_own_pass(conn: Connection, strategy_id: str) -> bool:
    """draft→active 凭证判定：挂 (hypothesis_id, strategy_id) 的
    verdict=pass 记录存在。

    hypothesis_id 取 strategy.parent_ids（谱系挂点——现状 promote
    语义原样：promote 循环按 parent_ids==hypothesis_id 圈谱系、按
    (hypothesis_id, strategy_id, verdict=pass) 查自身凭证）。REQ-3-01
    单点收紧位：未来收紧判定只改本函数，通道与调用方零变化。

    Args:
        conn: 数据库连接（与迁移同事务——判定与 CAS 同快照）。
        strategy_id: 策略 ID。

    Returns:
        True=存在 pass 记录（凭证成立）。
    """
    parent = conn.execute(
        select(Strategy.parent_ids)
        .where(Strategy.strategy_id == strategy_id)
    ).scalar_one_or_none()
    if not parent:
        return False
    row = conn.execute(
        select(ValidationRecord.record_id)
        .where(ValidationRecord.hypothesis_id == parent,
               ValidationRecord.strategy_id == strategy_id,
               ValidationRecord.verdict == "pass")
        .limit(1)
    ).first()
    return row is not None


def has_no_active_tasks(conn: Connection, strategy_id: str) -> bool:
    """task_end 多任务守卫：该策略当前无活跃（非终态）pt_task。

    已终态任务（含 evidence 指向的本任务）天然不在活跃集——「无其他
    活跃任务」= 活跃集查询零命中，无需排除参数。非最后任务时守卫
    返回 False → 调用方按 not_migrated('active_tasks_remain') 处理
    （正当不触发非错误）。

    Args:
        conn: 数据库连接（与迁移同事务）。
        strategy_id: 策略 ID。

    Returns:
        True=无活跃任务（最后任务，可回流 active）。
    """
    active = sorted(_active_statuses())
    row = conn.execute(
        select(PtTask.task_id)
        .where(PtTask.strategy_id == strategy_id,
               PtTask.status.in_(active))
        .limit(1)
    ).first()
    return row is None
