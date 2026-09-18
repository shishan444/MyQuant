"""live_trading repository（B3c.1 收编，live persistence.py 退役）。

行为等价锚点：save 的 INSERT OR REPLACE（创建/重置语义）；update(**kwargs)
直接以 kwargs 为列（SQLAlchemy .values 对非法键 raise——合法输入等价且
消除拼列面）；update_heartbeat 复用 update。
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional

from sqlalchemy import delete, insert, select, update

from core.persistence.orm.models import LiveTradingTask
from core.persistence.repository.dataset_meta import _engine_for, _now


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
    """更新任务字段（自动刷新 updated_at）。"""
    if not kwargs:
        return
    kwargs["updated_at"] = _now()
    engine = _engine_for(db_path)
    with engine.begin() as conn:
        conn.execute(
            update(LiveTradingTask)
            .where(LiveTradingTask.task_id == task_id)
            .values(**kwargs)
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
