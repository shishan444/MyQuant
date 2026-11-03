"""live_trading repository（B3c.1 收编，live persistence.py 退役）。

行为等价锚点：save 的 INSERT OR REPLACE（创建/重置语义）；update(**kwargs)
列白名单过滤（形制同 paper_trading——身份列 task_id/symbol/timeframe/
dna_json/direction/created_at 创建后不可改）；update_heartbeat 复用 update。
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional

from sqlalchemy import delete, func, insert, select, update

from core.persistence.orm.models import LiveDecision, LiveTradingTask
from core.persistence.orm.engine import engine_for as _engine_for
from core.persistence.timestamps import utc_now_iso as _now

# 可更新列（models.py LiveTradingTask 全列减身份列；updated_at 为内部
# 刷新键）——runner 持仓/账户同步、异常落 status、心跳与 API PATCH
# 的 status/config_json/stop_reason 全集
_ALLOWED_UPDATE_COLUMNS = frozenset({
    "status", "stop_reason", "config_json", "position_side",
    "position_qty", "position_entry", "position_sl", "position_tp",
    "balance", "equity", "unrealized_pnl", "total_trades",
    "total_pnl", "win_count", "loss_count", "last_bar_time",
    "heartbeat_at", "error_detail", "updated_at",
})


def save_live_trading_task(
    db_path: Path,
    task_id: str,
    symbol: str,
    timeframe: str,
    dna_json: str,
    direction: str = "mixed",
    config_json: Optional[str] = None,
    status: str = "pending",
) -> None:
    """创建/重置任务记录（INSERT OR REPLACE，状态变更用 update）。"""
    now = _now()
    engine = _engine_for(db_path)
    with engine.begin() as conn:
        conn.execute(
            insert(LiveTradingTask).prefix_with("OR REPLACE").values(
                task_id=task_id, symbol=symbol, timeframe=timeframe,
                dna_json=dna_json, direction=direction, status=status,
                config_json=config_json, created_at=now, updated_at=now,
            )
        )


def get_live_trading_task(db_path: Path, task_id: str) -> Optional[Dict[str, Any]]:
    """按 ID 取实盘任务（不存在返回 None）。"""
    engine = _engine_for(db_path)
    with engine.connect() as conn:
        row = conn.execute(
            select(LiveTradingTask).where(LiveTradingTask.task_id == task_id)
        ).mappings().first()
    return dict(row) if row else None


def update_live_trading_task(db_path: Path, task_id: str, **kwargs: Any) -> None:
    """更新任务字段（白名单过滤，自动刷新 updated_at）。"""
    if not kwargs:
        return
    filtered = {k: v for k, v in kwargs.items() if k in _ALLOWED_UPDATE_COLUMNS}
    if not filtered:
        return
    filtered["updated_at"] = _now()
    engine = _engine_for(db_path)
    with engine.begin() as conn:
        conn.execute(
            update(LiveTradingTask)
            .where(LiveTradingTask.task_id == task_id)
            .values(**filtered)
        )


def list_live_trading_tasks(
    db_path: Path, status: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """列实盘任务（可按 status 过滤，created_at 倒序）。"""
    stmt = select(LiveTradingTask)
    if status:
        stmt = stmt.where(LiveTradingTask.status == status)
    stmt = stmt.order_by(LiveTradingTask.created_at.desc())
    engine = _engine_for(db_path)
    with engine.connect() as conn:
        rows = conn.execute(stmt).mappings().all()
    return [dict(r) for r in rows]


def delete_live_trading_task(db_path: Path, task_id: str) -> bool:
    """按 ID 删实盘任务（返回是否删除到行）。"""
    engine = _engine_for(db_path)
    with engine.begin() as conn:
        result = conn.execute(
            delete(LiveTradingTask).where(LiveTradingTask.task_id == task_id)
        )
        return result.rowcount > 0


def update_heartbeat(db_path: Path, task_id: str) -> None:
    """刷新心跳时间戳。"""
    update_live_trading_task(db_path, task_id, heartbeat_at=_now())


# ── be-live-trading-chain：状态迁移+决策快照 ──

class IllegalTransition(Exception):
    """非法状态迁移（路由层转 409——evolution 同名先例 detail=str(e)）。"""

    def __init__(self, current: str, action: str):
        self.current = current
        self.action = action
        super().__init__(
            f"illegal transition: cannot {action} from status={current}")


def transition_live_task(
    db_path: Path, task_id: str, action: str,
) -> Dict[str, Any]:
    """start/stop 状态迁移（D5 契约基线——单点）。

    start: pending→running（runner 装配由路由层挂 app.state——仓储只管
    状态面）；stop: running→stopped（stop_reason 落因；graceful 语义=
    runner 侧当前 bar 完退出，状态先行迁移可观察）。非法迁移 raise
    IllegalTransition；任务不存在 return {}（路由层判 404）。
    """
    row = get_live_trading_task(db_path, task_id)
    if row is None:
        return {}
    cur = row["status"]
    if action == "start":
        if cur not in ("pending",):
            raise IllegalTransition(cur, action)
        update_live_trading_task(
            db_path, task_id, status="running", error_detail=None)
    elif action == "stop":
        if cur not in ("running",):
            raise IllegalTransition(cur, action)
        update_live_trading_task(
            db_path, task_id, status="stopped",
            stop_reason="user_stop")
    else:
        raise ValueError(f"unknown action: {action}")
    out = get_live_trading_task(db_path, task_id)
    assert out is not None
    return out


def append_live_decision(
    db_path: Path, *, task_id: str, bar_time: str, action: str,
    price: Optional[float] = None, size: Optional[float] = None,
    reason_json: Optional[str] = None,
) -> str:
    """决策留痕追加（append-only 唯一写通道——runner 侧消费）。"""
    import uuid as _uuid
    decision_id = f"ld_{_uuid.uuid4().hex[:12]}"
    engine = _engine_for(db_path)
    with engine.begin() as conn:
        conn.execute(insert(LiveDecision).values(
            decision_id=decision_id, task_id=task_id,
            bar_time=bar_time, action=action, price=price, size=size,
            reason_json=reason_json, created_at=_now()))
    return decision_id


def list_live_decisions(
    db_path: Path, task_id: str, limit: int = 50, offset: int = 0,
) -> tuple:
    """决策快照分页（bar_time 升序审计读序——limit+1 探测由路由层）。"""
    engine = _engine_for(db_path)
    with engine.connect() as conn:
        total = conn.execute(
            select(func.count()).select_from(LiveDecision).where(
                LiveDecision.task_id == task_id)).scalar_one()
        rows = conn.execute(
            select(LiveDecision).where(LiveDecision.task_id == task_id)
            .order_by(LiveDecision.bar_time.asc())
            .limit(limit).offset(offset)).mappings().all()
    return [dict(r) for r in rows], int(total)
