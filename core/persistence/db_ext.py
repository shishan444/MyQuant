"""db_ext：对外兼容委托层（B5.2 起）。

历史职责分三层迁移终态：init/ALTER 链→core.persistence.legacy_init；
repository 访问→core.persistence.repository.*；本模块保留同名函数
一行委托（调用方零改动）。测试用的 _connect 由 legacy_init re-export。
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional

from core.persistence.legacy_init import _connect, init_db_ext  # noqa: F401  re-export

# ===================================================================
# Paper / Live Trading 委托
# ===================================================================

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
    """创建模拟盘任务（B3c.1 起委托 repository.paper_trading，签名不变）。"""
    from core.persistence.repository import paper_trading as repo
    repo.save_paper_trading_task(
        db_path, task_id=task_id, dna_json=dna_json, symbol=symbol,
        timeframe=timeframe, initial_cash=initial_cash, fee=fee,
        leverage=leverage, direction=direction, score_template=score_template,
        strategy_name=strategy_name,
        confidence_sizing_enabled=confidence_sizing_enabled,
        prediction_dna_json=prediction_dna_json,
    )


def get_paper_trading_task(db_path: Path, task_id: str) -> Optional[Dict[str, Any]]:
    """取模拟盘任务（委托 repository，不存在返回 None）。"""
    from core.persistence.repository import paper_trading as repo
    return repo.get_paper_trading_task(db_path, task_id)


def update_paper_trading_task(db_path: Path, task_id: str, **kwargs) -> None:
    """更新任务字段（委托 repository，白名单过滤语义不变）。"""
    from core.persistence.repository import paper_trading as repo
    repo.update_paper_trading_task(db_path, task_id, **kwargs)


def list_paper_trading_tasks(
    db_path: Path, status: Optional[str] = None, limit: int = 50, offset: int = 0,
) -> List[Dict[str, Any]]:
    """列模拟盘任务（委托 repository，status 过滤+分页）。"""
    from core.persistence.repository import paper_trading as repo
    return repo.list_paper_trading_tasks(db_path, status=status, limit=limit, offset=offset)


def count_paper_trading_tasks(
    db_path: Path, status: Optional[str] = None,
) -> int:
    """计模拟盘任务数（委托 repository，可按 status 过滤）。"""
    from core.persistence.repository import paper_trading as repo
    return repo.count_paper_trading_tasks(db_path, status=status)


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
    """写入单笔模拟成交（委托 repository；INSERT OR IGNORE 幂等，B1.3 唯一键）。"""
    from core.persistence.repository import paper_trading as repo
    repo.save_paper_trade(
        db_path, task_id=task_id, bar_time=bar_time, side=side, action=action,
        price=price, quantity=quantity, pnl=pnl, fee_paid=fee_paid, reason=reason,
    )


def list_paper_trades(
    db_path: Path, task_id: str, limit: int = 100,
) -> List[Dict[str, Any]]:
    """列模拟成交（委托 repository，bar_time 倒序+limit）。"""
    from core.persistence.repository import paper_trading as repo
    return repo.list_paper_trades(db_path, task_id, limit=limit)


def count_paper_trades(db_path: Path, task_id: str) -> int:
    """计模拟成交笔数（委托 repository）。"""
    from core.persistence.repository import paper_trading as repo
    return repo.count_paper_trades(db_path, task_id)


def save_equity_snapshots(
    db_path: Path, task_id: str, snapshots: list[dict],
) -> None:
    """批量写权益快照（委托 repository，OR IGNORE 幂等——同 (task_id, timestamp) 首写胜出）。"""
    from core.persistence.repository import paper_trading as repo
    repo.save_equity_snapshots(db_path, task_id, snapshots)


def list_equity_snapshots(
    db_path: Path, task_id: str,
) -> List[Dict[str, Any]]:
    """列权益快照（委托 repository，时间正序）。"""
    from core.persistence.repository import paper_trading as repo
    return repo.list_equity_snapshots(db_path, task_id)


def delete_paper_trading_task(db_path: Path, task_id: str) -> bool:
    """删除模拟盘任务及其关联数据（委托 repository，三表级联单事务）。"""
    from core.persistence.repository import paper_trading as repo
    return repo.delete_paper_trading_task(db_path, task_id)


def compute_trading_metrics(db_path: Path, task_id: str) -> Optional[Dict[str, Any]]:
    """计算绩效指标（委托 repository；公式单源 core.scoring）。"""
    from core.persistence.repository import paper_trading as repo
    return repo.compute_trading_metrics(db_path, task_id)


# Strategy CRUD
# ===================================================================

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
    """新增策略记录（B3b.1 起委托 repository.strategy，签名不变）。"""
    from core.persistence.repository import strategy as repo
    repo.save_strategy(
        db_path, strategy_id=strategy_id, dna_json=dna_json, symbol=symbol,
        timeframe=timeframe, name=name, source=source,
        source_task_id=source_task_id, best_score=best_score,
        generation=generation, parent_ids=parent_ids, tags=tags, notes=notes,
        metrics_json=metrics_json, gene_signature=gene_signature,
        status=status,
    )


def get_strategy(db_path: Path, strategy_id: str) -> Optional[Dict[str, Any]]:
    """按 ID 取单条策略（委托 repository，不存在返回 None）。"""
    from core.persistence.repository import strategy as repo
    return repo.get_strategy(db_path, strategy_id)


def count_strategies_by_tasks(
    db_path: Path,
    task_ids: List[str],
    min_score: float = 0,
) -> Dict[str, int]:
    """按任务批量计策略数（委托 repository）。"""
    from core.persistence.repository import strategy as repo
    return repo.count_strategies_by_tasks(db_path, task_ids, min_score)


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
    """计策略数（委托 repository，可过滤）。"""
    from core.persistence.repository import strategy as repo
    return repo.count_strategies(
        db_path, symbol=symbol, source=source, source_task_id=source_task_id,
        tags=tags, qualified=qualified, status=status,
    )


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
    """列策略（委托 repository，过滤+白名单排序+分页）。"""
    from core.persistence.repository import strategy as repo
    return repo.list_strategies(
        db_path, symbol=symbol, source=source, source_task_id=source_task_id,
        tags=tags, qualified=qualified, status=status,
        sort_by=sort_by, sort_order=sort_order,
        limit=limit, offset=offset,
    )


def update_strategy(
    db_path: Path,
    *,
    strategy_id: str,
    **fields: Any,
) -> None:
    """更新策略字段（委托 repository，白名单不变）。"""
    from core.persistence.repository import strategy as repo
    repo.update_strategy(db_path, strategy_id=strategy_id, **fields)


def delete_strategy(db_path: Path, strategy_id: str) -> None:
    """按 ID 删策略（委托 repository）。"""
    from core.persistence.repository import strategy as repo
    repo.delete_strategy(db_path, strategy_id)


# Backtest Result
# ===================================================================

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
    win_rate: float = 0.0,
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
    """写入回测结果记录（B3b.1 起委托 repository.backtest_result，签名不变）。"""
    from core.persistence.repository import backtest_result as repo
    repo.save_backtest_result(
        db_path, result_id=result_id, strategy_id=strategy_id, symbol=symbol,
        timeframe=timeframe, data_start=data_start, data_end=data_end,
        init_cash=init_cash, fee=fee, slippage=slippage,
        total_return=total_return, sharpe_ratio=sharpe_ratio,
        max_drawdown=max_drawdown, win_rate=win_rate, total_trades=total_trades,
        total_score=total_score, template_name=template_name,
        dimension_scores=dimension_scores, equity_curve=equity_curve,
        trades_json=trades_json, run_source=run_source, fitness=fitness,
        qualified=qualified, satisfaction_json=satisfaction_json,
        session_id=session_id,
    )


def get_backtest_result(db_path: Path, result_id: str) -> Optional[Dict[str, Any]]:
    """按 ID 取单条回测结果（委托 repository，不存在返回 None）。"""
    from core.persistence.repository import backtest_result as repo
    return repo.get_backtest_result(db_path, result_id)


def list_backtest_results(
    db_path: Path,
    *,
    strategy_id: Optional[str] = None,
    symbol: Optional[str] = None,
    run_source: Optional[str] = None,
    session_id: Optional[str] = None,
    sort_by: str = "created_at",
    sort_order: str = "desc",
    limit: int = 100,
) -> List[Dict[str, Any]]:
    """列回测结果（委托 repository，过滤+白名单排序+limit）。"""
    from core.persistence.repository import backtest_result as repo
    return repo.list_backtest_results(
        db_path, strategy_id=strategy_id, symbol=symbol, run_source=run_source,
        session_id=session_id, sort_by=sort_by, sort_order=sort_order, limit=limit,
    )


# Verify Session CRUD
# ===================================================================

def save_verify_session(
    db_path: Path,
    *,
    session_id: str,
    strategy_ids: str,
    data_ranges: str,
    init_cash: float = 100000.0,
    fee: float = 0.001,
    slippage: float = 0.0005,
    status: str = "running",
) -> None:
    """写入验证会话记录（B3b.1 起委托 repository.verify_session，签名不变）。"""
    from core.persistence.repository import verify_session as repo
    repo.save_verify_session(
        db_path, session_id=session_id, strategy_ids=strategy_ids,
        data_ranges=data_ranges, init_cash=init_cash, fee=fee,
        slippage=slippage, status=status,
    )


def get_verify_session(db_path: Path, session_id: str) -> Optional[Dict[str, Any]]:
    """按 ID 取验证会话（委托 repository，不存在返回 None）。"""
    from core.persistence.repository import verify_session as repo
    return repo.get_verify_session(db_path, session_id)


def update_verify_session(
    db_path: Path,
    session_id: str,
    *,
    status: Optional[str] = None,
    summary_json: Optional[str] = None,
    total_results: Optional[int] = None,
    total_strategies: Optional[int] = None,
    error_message: Optional[str] = None,
    completed_at: Optional[str] = None,
) -> None:
    """更新验证会话字段（委托 repository，仅显式非 None 字段语义不变）。"""
    from core.persistence.repository import verify_session as repo
    repo.update_verify_session(
        db_path, session_id, status=status, summary_json=summary_json,
        total_results=total_results, total_strategies=total_strategies,
        error_message=error_message, completed_at=completed_at,
    )


def list_verify_sessions(
    db_path: Path,
    limit: int = 20,
) -> List[Dict[str, Any]]:
    """列验证会话（委托 repository，created_at 倒序）。"""
    from core.persistence.repository import verify_session as repo
    return repo.list_verify_sessions(db_path, limit)


# ===================================================================
# Dataset Metadata 委托
# ===================================================================

def save_dataset_meta(
    db_path: Path,
    *,
    dataset_id: str,
    symbol: str,
    interval: str,
    parquet_path: str,
    row_count: int = 0,
    time_start: Optional[str] = None,
    time_end: Optional[str] = None,
    file_size_bytes: int = 0,
    source: str = "csv_import",
    format_detected: Optional[str] = None,
    timestamp_precision: Optional[str] = None,
    ohlcv_stats: Optional[str] = None,
    gap_count: int = 0,
    quality_status: str = "unknown",
    quality_notes: Optional[str] = None,
    import_batch_id: Optional[str] = None,
) -> None:
    """写入数据集元数据记录（B3a.3 起委托 repository.dataset_meta，签名不变）。"""
    from core.persistence.repository import dataset_meta as repo
    repo.save_dataset_meta(
        db_path, dataset_id=dataset_id, symbol=symbol, interval=interval,
        parquet_path=parquet_path, row_count=row_count, time_start=time_start,
        time_end=time_end, file_size_bytes=file_size_bytes, source=source,
        format_detected=format_detected,
        timestamp_precision=timestamp_precision, ohlcv_stats=ohlcv_stats,
        gap_count=gap_count, quality_status=quality_status,
        quality_notes=quality_notes, import_batch_id=import_batch_id,
    )


def get_dataset(db_path: Path, dataset_id: str) -> Optional[Dict[str, Any]]:
    """按 ID 取数据集（委托 repository，不存在返回 None）。"""
    from core.persistence.repository import dataset_meta as repo
    return repo.get_dataset(db_path, dataset_id)


def list_datasets(
    db_path: Path,
    *,
    symbol: Optional[str] = None,
    interval: Optional[str] = None,
    limit: int = 100,
) -> List[Dict[str, Any]]:
    """列数据集（委托 repository，可过滤）。"""
    from core.persistence.repository import dataset_meta as repo
    return repo.list_datasets(
        db_path, symbol=symbol, interval=interval, limit=limit,
    )


def update_dataset_stats(
    db_path: Path,
    *,
    dataset_id: str,
    **fields: Any,
) -> None:
    """更新数据集字段（委托 repository，白名单+updated_at 刷新不变）。"""
    from core.persistence.repository import dataset_meta as repo
    repo.update_dataset_stats(db_path, dataset_id=dataset_id, **fields)


def delete_dataset(db_path: Path, dataset_id: str) -> None:
    """按 ID 删数据集（委托 repository，不存在时无错误）。"""
    from core.persistence.repository import dataset_meta as repo
    repo.delete_dataset(db_path, dataset_id)
