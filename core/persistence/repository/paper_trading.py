"""paper_trading repository（B3c.1 迁移，B3c.0 冻结映射）。

行为等价锚点：save_task 硬编码初值（status='pending'/execution_model=
'v2'/balance=initial_cash）；save_trade 的 INSERT OR IGNORE 幂等
（B1.3 唯一键）；save_equity_snapshots 批量 executemany+OR IGNORE；
update(**kwargs) 列白名单过滤；delete 三表级联删除单事务（原子性保持）；
compute_trading_metrics 公式委托 core.scoring（纯计算单源不动）。
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional

from sqlalchemy import delete, func, insert, select, update

from core.persistence.orm.models import PaperEquitySnapshot, PaperTrade, PaperTradingTask
from core.persistence.orm.engine import engine_for as _engine_for
from core.persistence.timestamps import utc_now_iso as _now

_ALLOWED_UPDATE_COLUMNS = frozenset({
    "status", "started_at", "stopped_at", "stop_reason", "heartbeat_at",
    "position_side", "position_entry", "position_quantity", "position_margin",
    "position_funding", "position_open_cost", "balance", "unrealized_pnl",
    "total_trades", "total_pnl", "win_count", "loss_count", "bars_held",
    "last_bar_time", "last_bar_close", "execution_model", "pending_decision_json",
    "prediction_dna_json", "updated_at",
})


def save_paper_trading_task(
    db_path: Path,
    *,
    task_id: str,
    dna_json: str,
    symbol: str = "BTCUSDT",
    timeframe: str = "4h",
    initial_cash: float = 100_000,
    fee: float = 0.001,
    leverage: int = 1,
    direction: str = "long",
    score_template: str = "explorer",
    strategy_name: Optional[str] = None,
    confidence_sizing_enabled: bool = False,
    prediction_dna_json: Optional[str] = None,
) -> None:
    now = _now()
    engine = _engine_for(db_path)
    with engine.begin() as conn:
        conn.execute(insert(PaperTradingTask).values(
            task_id=task_id, status="pending", strategy_name=strategy_name,
            symbol=symbol, timeframe=timeframe, initial_cash=initial_cash,
            fee=fee, leverage=leverage, direction=direction, dna_json=dna_json,
            score_template=score_template, created_at=now, updated_at=now,
            balance=initial_cash, execution_model="v2",
            confidence_sizing_enabled=int(confidence_sizing_enabled),
            prediction_dna_json=prediction_dna_json,
        ))


def get_paper_trading_task(db_path: Path, task_id: str) -> Optional[Dict[str, Any]]:
    engine = _engine_for(db_path)
    with engine.connect() as conn:
        row = conn.execute(
            select(PaperTradingTask).where(PaperTradingTask.task_id == task_id)
        ).mappings().first()
    return dict(row) if row else None


def update_paper_trading_task(db_path: Path, task_id: str, **kwargs) -> None:
    if not kwargs:
        return
    filtered = {k: v for k, v in kwargs.items() if k in _ALLOWED_UPDATE_COLUMNS}
    if not filtered:
        return
    filtered["updated_at"] = _now()
    engine = _engine_for(db_path)
    with engine.begin() as conn:
        conn.execute(
            update(PaperTradingTask)
            .where(PaperTradingTask.task_id == task_id)
            .values(**filtered)
        )


def list_paper_trading_tasks(
    db_path: Path, status: Optional[str] = None, limit: int = 50, offset: int = 0,
) -> List[Dict[str, Any]]:
    stmt = select(PaperTradingTask)
    if status:
        stmt = stmt.where(PaperTradingTask.status == status)
    stmt = stmt.order_by(PaperTradingTask.created_at.desc()).limit(limit).offset(offset)
    engine = _engine_for(db_path)
    with engine.connect() as conn:
        rows = conn.execute(stmt).mappings().all()
    return [dict(r) for r in rows]


def count_paper_trading_tasks(
    db_path: Path, status: Optional[str] = None,
) -> int:
    stmt = select(func.count()).select_from(PaperTradingTask)
    if status:
        stmt = stmt.where(PaperTradingTask.status == status)
    engine = _engine_for(db_path)
    with engine.connect() as conn:
        return conn.execute(stmt).scalar_one()


def save_paper_trade(
    db_path: Path,
    *,
    task_id: str,
    bar_time: str,
    side: str,
    action: str,
    price: float,
    quantity: float,
    pnl: Optional[float] = None,
    fee_paid: float = 0.0,
    reason: Optional[str] = None,
) -> None:
    """B1.3: INSERT OR IGNORE——重放同一 (task_id, bar_time, action) 不重复落库。"""
    engine = _engine_for(db_path)
    with engine.begin() as conn:
        conn.execute(
            insert(PaperTrade).prefix_with("OR IGNORE").values(
                task_id=task_id, bar_time=bar_time, side=side, action=action,
                price=price, quantity=quantity, pnl=pnl, fee_paid=fee_paid,
                reason=reason,
            )
        )


def list_paper_trades(
    db_path: Path, task_id: str, limit: int = 100,
) -> List[Dict[str, Any]]:
    engine = _engine_for(db_path)
    with engine.connect() as conn:
        rows = conn.execute(
            select(PaperTrade).where(PaperTrade.task_id == task_id)
            .order_by(PaperTrade.bar_time.desc()).limit(limit)
        ).mappings().all()
    return [dict(r) for r in rows]


def count_paper_trades(db_path: Path, task_id: str) -> int:
    engine = _engine_for(db_path)
    with engine.connect() as conn:
        return conn.execute(
            select(func.count()).where(PaperTrade.task_id == task_id)
        ).scalar_one()


def save_equity_snapshots(
    db_path: Path, task_id: str, snapshots: list[dict],
) -> None:
    """Batch insert equity snapshots（executemany + OR IGNORE 幂等，同旧轨）。"""
    if not snapshots:
        return
    engine = _engine_for(db_path)
    with engine.begin() as conn:
        conn.execute(
            insert(PaperEquitySnapshot).prefix_with("OR IGNORE"),
            [
                {
                    "task_id": task_id, "timestamp": s["timestamp"],
                    "equity": s["equity"], "balance": s["balance"],
                    "unrealized_pnl": s.get("unrealized_pnl", 0.0),
                    "position_side": s.get("position_side", "flat"),
                }
                for s in snapshots
            ],
        )


def list_equity_snapshots(
    db_path: Path, task_id: str,
) -> List[Dict[str, Any]]:
    """Return all equity snapshots for a task in chronological order."""
    engine = _engine_for(db_path)
    with engine.connect() as conn:
        rows = conn.execute(
            select(PaperEquitySnapshot).where(PaperEquitySnapshot.task_id == task_id)
            .order_by(PaperEquitySnapshot.timestamp.asc())
        ).mappings().all()
    return [dict(r) for r in rows]


def delete_paper_trading_task(db_path: Path, task_id: str) -> bool:
    """Delete a paper trading task and all associated data（三表级联单事务）。"""
    engine = _engine_for(db_path)
    with engine.begin() as conn:
        exists = conn.execute(
            select(PaperTradingTask.task_id).where(PaperTradingTask.task_id == task_id)
        ).first()
        if exists is None:
            return False
        conn.execute(delete(PaperEquitySnapshot).where(PaperEquitySnapshot.task_id == task_id))
        conn.execute(delete(PaperTrade).where(PaperTrade.task_id == task_id))
        conn.execute(delete(PaperTradingTask).where(PaperTradingTask.task_id == task_id))
        return True


def compute_trading_metrics(db_path: Path, task_id: str) -> Optional[Dict[str, Any]]:
    """Compute performance metrics（公式委托 core.scoring 单源，读侧换 ORM）。"""
    from core.scoring import trading_metrics

    task = get_paper_trading_task(db_path, task_id)
    if task is None:
        return None

    initial_cash = task["initial_cash"]
    balance = task.get("balance") if task.get("balance") is not None else initial_cash

    engine = _engine_for(db_path)
    with engine.connect() as conn:
        close_rows = conn.execute(
            select(PaperTrade.pnl).where(
                PaperTrade.task_id == task_id,
                PaperTrade.pnl.is_not(None),
                PaperTrade.action == "close",
            )
        ).all()
        eq_rows = conn.execute(
            select(PaperEquitySnapshot.equity)
            .where(PaperEquitySnapshot.task_id == task_id)
            .order_by(PaperEquitySnapshot.timestamp.asc())
        ).all()

    metrics = trading_metrics.compute_trading_metrics(
        trading_metrics.TradingMetricsInput(
            initial_cash=initial_cash,
            balance=balance,
            total_trades=task.get("total_trades", 0),
            win_count=task.get("win_count", 0),
            loss_count=task.get("loss_count", 0),
            realized_pnl=task.get("total_pnl", 0.0) or 0.0,
            unrealized_pnl=task.get("unrealized_pnl", 0.0) or 0.0,
            position_side=task.get("position_side"),
            position_margin=task.get("position_margin") or 0.0,
            close_pnls=[r[0] for r in close_rows],
            equity_snapshots=[r[0] for r in eq_rows],
        )
    )
    metrics["task_id"] = task_id
    return metrics


# ---------------------------------------------------------------------------
# B3d.2 收口：trading runner 原裸 SQL 的等价迁移
# ---------------------------------------------------------------------------

def recover_stale_trading_tasks(db_path: Path) -> int:
    """Mark running paper trading tasks as stopped (crash recovery)——返回清理数。"""
    engine = _engine_for(db_path)
    with engine.begin() as conn:
        result = conn.execute(
            update(PaperTradingTask)
            .where(PaperTradingTask.status == "running")
            .values(status="stopped", stop_reason="crash_recovery",
                    updated_at=_now())
        )
        return result.rowcount


def find_pending_trading_task(db_path: Path) -> Optional[Dict[str, Any]]:
    """取最早创建的 pending 模拟盘任务（runner 拾取序）。"""
    engine = _engine_for(db_path)
    with engine.connect() as conn:
        row = conn.execute(
            select(PaperTradingTask)
            .where(PaperTradingTask.status == "pending")
            .order_by(PaperTradingTask.created_at.asc()).limit(1)
        ).mappings().first()
    return dict(row) if row is not None else None
