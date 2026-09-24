"""backtest_result repository（B3b.1 迁移）。

行为等价锚点：save 裸 INSERT；list 过滤（strategy_id/symbol/run_source）
+ 排序（getattr 限定列名——旧轨动态拼列名，合法输入行为等价且消除了
非法输入的 SQL 注入面）+ limit（无 offset，同旧轨）。
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional

from sqlalchemy import insert, select

from core.persistence.orm.models import BacktestResult
from core.persistence.repository.dataset_meta import _engine_for, _now


def save_backtest_result(
    db_path: Path,
    *,
    result_id: str,
    strategy_id: str,
    symbol: str,
    timeframe: str,
    data_start: str,
    data_end: str,
    init_cash: float = 100000.0,
    fee: float = 0.001,
    slippage: float = 0.0005,
    total_return: float = 0.0,
    sharpe_ratio: float = 0.0,
    max_drawdown: float = 0.0,
    win_rate: Optional[float] = None,  # None=零交易缺失；DB 列 NOT NULL 入库归一 0.0
    total_trades: int = 0,
    total_score: float = 0.0,
    template_name: str = "explorer",
    dimension_scores: Optional[str] = None,
    equity_curve: Optional[str] = None,
    trades_json: Optional[str] = None,
    run_source: str = "lab",
    fitness: float = 0.0,
    qualified: int = 0,
    satisfaction_json: Optional[str] = None,
    session_id: Optional[str] = None,
) -> None:
    """Insert a backtest result record."""
    engine = _engine_for(db_path)
    with engine.begin() as conn:
        conn.execute(insert(BacktestResult).values(
            result_id=result_id, strategy_id=strategy_id, symbol=symbol,
            timeframe=timeframe, data_start=data_start, data_end=data_end,
            init_cash=init_cash, fee=fee, slippage=slippage,
            total_return=total_return, sharpe_ratio=sharpe_ratio,
            max_drawdown=max_drawdown,
            # DB 列 NOT NULL：缺失语义入库归一 0.0，读侧以 total_trades==0 识别
            win_rate=win_rate if win_rate is not None else 0.0,
            total_trades=total_trades, total_score=total_score,
            template_name=template_name, dimension_scores=dimension_scores,
            equity_curve=equity_curve, trades_json=trades_json,
            run_source=run_source, created_at=_now(),
            fitness=fitness, qualified=qualified,
            satisfaction_json=satisfaction_json, session_id=session_id,
        ))


def get_backtest_result(db_path: Path, result_id: str) -> Optional[Dict[str, Any]]:
    """Retrieve a single backtest result by ID, or None if not found."""
    engine = _engine_for(db_path)
    with engine.connect() as conn:
        row = conn.execute(
            select(BacktestResult).where(BacktestResult.result_id == result_id)
        ).mappings().first()
    return dict(row) if row is not None else None


def list_backtest_results(
    db_path: Path,
    *,
    strategy_id: Optional[str] = None,
    symbol: Optional[str] = None,
    run_source: Optional[str] = None,
    sort_by: str = "created_at",
    sort_order: str = "desc",
    limit: int = 100,
) -> List[Dict[str, Any]]:
    """List backtest results with optional filtering and sorting."""
    stmt = select(BacktestResult)
    if strategy_id is not None:
        stmt = stmt.where(BacktestResult.strategy_id == strategy_id)
    if symbol is not None:
        stmt = stmt.where(BacktestResult.symbol == symbol)
    if run_source is not None:
        stmt = stmt.where(BacktestResult.run_source == run_source)

    col = getattr(BacktestResult, sort_by)
    stmt = stmt.order_by(col.desc() if sort_order.lower() == "desc" else col.asc()).limit(limit)

    engine = _engine_for(db_path)
    with engine.connect() as conn:
        rows = conn.execute(stmt).mappings().all()
    return [dict(r) for r in rows]
