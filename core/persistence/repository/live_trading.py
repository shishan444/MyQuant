"""live_trading repository（B3c.1 收编，live persistence.py 退役）。

行为等价锚点：save 的 INSERT OR REPLACE（创建/重置语义）；update(**kwargs)
列白名单过滤（形制同 paper_trading——身份列 task_id/symbol/timeframe/
dna_json/direction/created_at 创建后不可改）；update_heartbeat 复用 update。
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional

from sqlalchemy import delete, insert, select, update

from core.persistence.orm.models import LiveTradingTask
from core.persistence.repository.dataset_meta import _engine_for, _now

# 可更新列（models.py LiveTradingTask 全列减身份列；updated_at 为内部
# 刷新键）——runner 持仓/账户同步、异常落 status、心跳与 API PATCH
# 的 status/config_json/stop_reason 全集
_ALLOWED_UPDATE_COLUMNS = frozenset({
    "status", "stop_reason", "config_json", "position_side",
    "position_qty", "position_entry", "position_sl", "position_tp",
    "balance", "equity", "unrealized_pnl", "total_trades",
    "total_pnl", "win_count", "loss_count", "last_bar_time",
    "heartbeat_at", "updated_at",
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
    stmt = select(LiveTradingTask)
    if status:
        stmt = stmt.where(LiveTradingTask.status == status)
    stmt = stmt.order_by(LiveTradingTask.created_at.desc())
    engine = _engine_for(db_path)
    with engine.connect() as conn:
        rows = conn.execute(stmt).mappings().all()
    return [dict(r) for r in rows]


def delete_live_trading_task(db_path: Path, task_id: str) -> bool:
    engine = _engine_for(db_path)
    with engine.begin() as conn:
        result = conn.execute(
            delete(LiveTradingTask).where(LiveTradingTask.task_id == task_id)
        )
        return result.rowcount > 0


def update_heartbeat(db_path: Path, task_id: str) -> None:
    """刷新心跳时间戳。"""
    update_live_trading_task(db_path, task_id, heartbeat_at=_now())
