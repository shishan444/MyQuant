"""Strategy CRUD + backtest + compare routes."""
from __future__ import annotations

import json
import logging
import uuid
import asyncio
from datetime import datetime

import pandas as pd
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse

from core.persistence.db_ext import (
    count_strategies,
    delete_strategy,
    get_backtest_result,
    get_strategy,
    get_verify_session,
    list_backtest_results,
    list_strategies,
    list_verify_sessions,
    save_backtest_result,
    save_strategy,
    save_verify_session,
    update_strategy,
    update_verify_session,
)
from api.deps import get_data_dir, get_db_path
from api.schemas import (
    BacktestRequest,
    BacktestResponse,
    BatchBacktestRequest,
    BatchBacktestResultItem,
    BatchBacktestSummaryItem,
    CompareRequest,
    CompareResponse,
    CompareResultItem,
    DNAModel,
    StrategyCreate,
    StrategyListResponse,
    StrategyMetrics,
    StrategyResponse,
    StrategyUpdate,
    VerifyRequest,
    VerifyResponse,
    VerifyResultItem,
    VerifySummaryItem,
    VerifyPeriodSummary,
    VerifyHistoryItem,
    VerifyHistoryResponse,
    VerifySessionResponse,
    VerifySessionListResponse,
)
from core.backtest import engine as _bt_engine_mod
from core.scoring.scorer import score_strategy
from core.services import backtest_service
from core.strategy.dna import StrategyDNA

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/strategies", tags=["strategies"])


def _dna_model_to_dna(dna_model: DNAModel) -> StrategyDNA:
    """Convert a Pydantic DNAModel to a core StrategyDNA."""
    data = dna_model.model_dump()
    return StrategyDNA.from_dict(data)


def _strategy_row_to_response(row: Dict[str, Any]) -> StrategyResponse:
    """Convert a DB row dict to StrategyResponse."""
    dna = None
    if row.get("dna_json"):
        try:
            dna_dict = json.loads(row["dna_json"])
            dna = DNAModel.model_validate(dna_dict)
        except (json.JSONDecodeError, Exception):
            dna = None

    metrics = None
    if row.get("metrics_json"):
        try:
            metrics = StrategyMetrics(**json.loads(row["metrics_json"]))
        except Exception:
            pass

    return StrategyResponse(
        strategy_id=row["strategy_id"],
        name=row.get("name"),
        dna=dna,
        symbol=row["symbol"],
        timeframe=row["timeframe"],
        source=row.get("source", "manual"),
        source_task_id=row.get("source_task_id"),
        best_score=row.get("best_score"),
        best_fitness=row.get("best_fitness"),
        qualified=bool(row.get("qualified", 0)) if row.get("qualified") is not None else None,
        metrics=metrics,
        generation=row.get("generation", 0),
        parent_ids=row.get("parent_ids"),
        tags=row.get("tags"),
        notes=row.get("notes"),
        verify_count=row.get("verify_count", 0) or 0,
        verify_avg_score=row.get("verify_avg_score"),
        verify_best_score=row.get("verify_best_score"),
        last_verified_at=row.get("last_verified_at"),
        verify_star=row.get("verify_star"),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


@router.post("", status_code=201)
def create_strategy(
    payload: StrategyCreate,
    db_path: Path = Depends(get_db_path),
) -> StrategyResponse:
    """Create a new strategy."""
    strategy_id = str(uuid.uuid4())
    dna = _dna_model_to_dna(payload.dna)

    save_strategy(
        db_path,
        strategy_id=strategy_id,
        name=payload.name,
        dna_json=dna.to_json(),
        symbol=payload.symbol,
        timeframe=payload.timeframe,
        source=payload.source,
        source_task_id=payload.source_task_id,
        tags=payload.tags,
        notes=payload.notes,
    )

    row = get_strategy(db_path, strategy_id)
    if row is None:
        raise HTTPException(status_code=500, detail="Failed to create strategy")
    return _strategy_row_to_response(row)


@router.get("")
def list_strategies_endpoint(
    symbol: Optional[str] = None,
    source: Optional[str] = None,
    tags: Optional[str] = None,
    qualified: Optional[bool] = None,
    sort_by: str = "created_at",
    sort_order: str = "desc",
    limit: int = 100,
    offset: int = 0,
    db_path: Path = Depends(get_db_path),
) -> StrategyListResponse:
    """List strategies with filtering, sorting, and pagination."""
    try:
        rows = list_strategies(
            db_path,
            symbol=symbol,
            source=source,
            tags=tags,
            qualified=qualified,
            sort_by=sort_by,
            sort_order=sort_order,
            limit=limit,
            offset=offset,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    total = count_strategies(
        db_path, symbol=symbol, source=source, tags=tags, qualified=qualified,
    )
    items = [_strategy_row_to_response(r) for r in rows]
    return StrategyListResponse(items=items, total=total)


@router.get("/{strategy_id}")
def get_strategy_endpoint(
    strategy_id: str,
    db_path: Path = Depends(get_db_path),
) -> StrategyResponse:
    """Get strategy details by ID."""
    row = get_strategy(db_path, strategy_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Strategy not found")
    return _strategy_row_to_response(row)


@router.put("/{strategy_id}")
def update_strategy_endpoint(
    strategy_id: str,
    payload: StrategyUpdate,
    db_path: Path = Depends(get_db_path),
) -> StrategyResponse:
    """Update an existing strategy."""
    existing = get_strategy(db_path, strategy_id)
    if existing is None:
        raise HTTPException(status_code=404, detail="Strategy not found")

    fields: Dict[str, Any] = {}
    if payload.name is not None:
        fields["name"] = payload.name
    if payload.tags is not None:
        fields["tags"] = payload.tags
    if payload.notes is not None:
        fields["notes"] = payload.notes
    if payload.best_score is not None:
        fields["best_score"] = payload.best_score
    if payload.dna is not None:
        dna = _dna_model_to_dna(payload.dna)
        fields["dna_json"] = dna.to_json()

    if fields:
        update_strategy(db_path, strategy_id=strategy_id, **fields)

    row = get_strategy(db_path, strategy_id)
    return _strategy_row_to_response(row)


@router.delete("/{strategy_id}", status_code=204)
def delete_strategy_endpoint(
    strategy_id: str,
    db_path: Path = Depends(get_db_path),
) -> None:
    """Delete a strategy by ID."""
    existing = get_strategy(db_path, strategy_id)
    if existing is None:
        raise HTTPException(status_code=404, detail="Strategy not found")
    delete_strategy(db_path, strategy_id)


@router.post("/backtest")
def backtest_strategy(
    payload: BacktestRequest,
    db_path: Path = Depends(get_db_path),
    data_dir: Path = Depends(get_data_dir),
) -> BacktestResponse:
    """Run a backtest for a strategy.

    Supports two modes:
    - strategy_id: Load DNA from saved strategy
    - dna + symbol + timeframe: Use DNA directly (for Lab page)
    """
    # Resolve DNA and metadata
    if payload.strategy_id:
        row = get_strategy(db_path, payload.strategy_id)
        if row is None:
            raise HTTPException(status_code=404, detail="Strategy not found")
        dna = StrategyDNA.from_json(row["dna_json"])
        symbol = row["symbol"]
        timeframe = row["timeframe"]
        strategy_id = payload.strategy_id
    elif payload.dna:
        dna = StrategyDNA.from_json(payload.dna.model_dump_json())
        symbol = payload.symbol or "UNKNOWN"
        timeframe = payload.timeframe or "1d"
        strategy_id = "inline"
    else:
        raise HTTPException(
            status_code=400,
            detail="Must provide either strategy_id or dna",
        )

    # Try to load the dataset
    parquet_path = data_dir / f"{payload.dataset_id}.parquet"
    if not parquet_path.exists():
        raise HTTPException(
            status_code=404,
            detail=f"Dataset {payload.dataset_id} not found",
        )

    from core.data.mtf_loader import load_and_prepare_df, load_mtf_data

    engine = _bt_engine_mod.BacktestEngine(
        init_cash=payload.init_cash,
        fee=payload.fee,
        slippage=payload.slippage,
    )

    # Use mtf_loader for enhanced data loading (full indicators + date slicing)
    enhanced_df = load_and_prepare_df(
        data_dir, symbol, timeframe,
        data_start=payload.data_start,
        data_end=payload.data_end,
    )

    # Fallback: load raw data and compute indicators per-gene (legacy path)
    if enhanced_df is None:
        from core.data.storage import load_parquet

        df = load_parquet(parquet_path)
        from core.features.indicators import _compute_indicator
        for gene in dna.signal_genes:
            try:
                indicator_name = gene.indicator
                params = {k: v for k, v in gene.params.items()}
                indicator_df = _compute_indicator(df, indicator_name, params)
                for col in indicator_df.columns:
                    if col not in df.columns:
                        df[col] = indicator_df[col]
            except Exception:
                continue
        enhanced_df = df

    # Load multi-timeframe data if DNA has layers or timeframe_pool provided
    dfs_by_timeframe = None
    needed_tfs: set[str] = set()
    if dna.is_mtf and dna.layers:
        needed_tfs = {layer.timeframe for layer in dna.layers}
        # Always include execution timeframe for cross-timeframe signal evaluation
        needed_tfs.add(timeframe)
    elif payload.timeframe_pool and len(payload.timeframe_pool) > 1:
        needed_tfs = set(payload.timeframe_pool)

    if needed_tfs and len(needed_tfs) > 1:
        dfs_by_timeframe = load_mtf_data(
            data_dir, symbol, timeframe, enhanced_df,
            needed_tfs, payload.data_start, payload.data_end,
        )

    result = engine.run(dna, enhanced_df, dfs_by_timeframe=dfs_by_timeframe)

    metrics = backtest_service.fallback_metrics(result)
    score_result = score_strategy(
        metrics, template_name=payload.score_template,
        liquidated=result.liquidated,
    )

    # Save result
    result_id = str(uuid.uuid4())
    if payload.strategy_id:
        save_backtest_result(
            db_path,
            result_id=result_id,
            strategy_id=strategy_id,
            symbol=symbol,
            timeframe=timeframe,
            data_start=str(enhanced_df.index.min()) if len(enhanced_df) > 0 else "",
            data_end=str(enhanced_df.index.max()) if len(enhanced_df) > 0 else "",
            init_cash=payload.init_cash,
            fee=payload.fee,
            slippage=payload.slippage,
            total_return=result.total_return,
            sharpe_ratio=result.sharpe_ratio,
            max_drawdown=result.max_drawdown,
            win_rate=result.win_rate,
            total_trades=result.total_trades,
            total_score=score_result["total_score"],
            template_name=payload.score_template,
            dimension_scores=json.dumps(score_result.get("dimension_scores", {})),
            run_source="lab",
        )

    # Build equity curve
    equity_data = None
    if result.equity_curve is not None and len(result.equity_curve) > 0:
        eq = result.equity_curve
        equity_data = [
            {"timestamp": str(idx), "value": float(val)}
            for idx, val in eq.items()
        ]

    # Build signals from trades (direction-aware: short entry = sell, short exit = buy)
    signals_data = None
    if result.trades_df is not None and len(result.trades_df) > 0:
        signals_data = backtest_service.build_signals_from_trades_df(result.trades_df)

    return BacktestResponse(
        result_id=result_id,
        strategy_id=strategy_id,
        symbol=symbol,
        timeframe=timeframe,
        data_start=str(enhanced_df.index.min()) if len(enhanced_df) > 0 else None,
        data_end=str(enhanced_df.index.max()) if len(enhanced_df) > 0 else None,
        init_cash=payload.init_cash,
        fee=payload.fee,
        slippage=payload.slippage,
        total_return=result.total_return,
        sharpe_ratio=result.sharpe_ratio,
        max_drawdown=result.max_drawdown,
        win_rate=result.win_rate,
        total_trades=result.total_trades,
        total_score=score_result["total_score"],
        fitness=score_result.get("fitness", 0.0),
        qualified=score_result.get("qualified", False),
        satisfaction=score_result.get("satisfaction"),
        template_name=payload.score_template,
        dimension_scores=score_result.get("dimension_scores"),
        run_source="lab",
        equity_curve=equity_data,
        signals=signals_data,
        total_funding_cost=result.total_funding_cost,
        liquidated=result.liquidated,
    )


@router.post("/compare")
def compare_strategies(
    payload: CompareRequest,
    db_path: Path = Depends(get_db_path),
    data_dir: Path = Depends(get_data_dir),
) -> CompareResponse:
    """Compare multiple strategies by running backtests."""
    parquet_path = data_dir / f"{payload.dataset_id}.parquet"
    if not parquet_path.exists():
        raise HTTPException(
            status_code=404,
            detail=f"Dataset {payload.dataset_id} not found",
        )

    from core.data.storage import load_parquet
    from core.data.mtf_loader import load_and_prepare_df, load_mtf_data

    engine = _bt_engine_mod.BacktestEngine(
        init_cash=payload.init_cash,
        fee=payload.fee,
        slippage=payload.slippage,
    )

    # Load raw data first to get symbol/timeframe info
    df = load_parquet(parquet_path)
    results: List[CompareResultItem] = []

    for sid in payload.strategy_ids:
        row = get_strategy(db_path, sid)
        if row is None:
            results.append(CompareResultItem(
                strategy_id=sid,
                error="Strategy not found",
            ))
            continue

        try:
            dna = StrategyDNA.from_json(row["dna_json"])
            symbol = dna.execution_genes.symbol
            timeframe = dna.execution_genes.timeframe

            # Use enhanced data with indicators (same as backtest endpoint)
            enhanced_df = load_and_prepare_df(
                data_dir, symbol, timeframe,
                data_start=payload.data_start,
                data_end=payload.data_end,
            )
            if enhanced_df is None:
                enhanced_df = df

            # Load MTF data if strategy uses multiple timeframes
            dfs_by_timeframe = None
            if dna.is_mtf:
                needed_tfs = backtest_service.compute_needed_tfs([dna], timeframe)
                dfs_by_timeframe = load_mtf_data(
                    data_dir, symbol, timeframe, enhanced_df,
                    needed_tfs,
                    data_start=payload.data_start,
                    data_end=payload.data_end,
                )

            bt_result = engine.run(
                dna, enhanced_df,
                dfs_by_timeframe=dfs_by_timeframe,
            )
            metrics = backtest_service.fallback_metrics(bt_result)
            score_result = score_strategy(
                metrics, template_name=payload.score_template,
                liquidated=bt_result.liquidated,
            )

            result_id = str(uuid.uuid4())
            save_backtest_result(
                db_path,
                result_id=result_id,
                strategy_id=sid,
                symbol=row["symbol"],
                timeframe=row["timeframe"],
                data_start=str(df.index.min()) if len(df) > 0 else "",
                data_end=str(df.index.max()) if len(df) > 0 else "",
                init_cash=payload.init_cash,
                fee=payload.fee,
                slippage=payload.slippage,
                total_return=bt_result.total_return,
                sharpe_ratio=bt_result.sharpe_ratio,
                max_drawdown=bt_result.max_drawdown,
                win_rate=bt_result.win_rate,
                total_trades=bt_result.total_trades,
                total_score=score_result["total_score"],
                template_name=payload.score_template,
                dimension_scores=json.dumps(
                    score_result.get("dimension_scores", {})
                ),
                run_source="lab",
            )

            results.append(CompareResultItem(
                strategy_id=sid,
                result_id=result_id,
                total_return=bt_result.total_return,
                sharpe_ratio=bt_result.sharpe_ratio,
                max_drawdown=bt_result.max_drawdown,
                win_rate=bt_result.win_rate,
                total_trades=bt_result.total_trades,
                total_score=score_result["total_score"],
                fitness=score_result.get("fitness", 0.0),
                satisfaction=score_result.get("satisfaction"),
                dimension_scores=score_result.get("dimension_scores"),
            ))
        except Exception as exc:
            results.append(CompareResultItem(
                strategy_id=sid,
                error=str(exc),
            ))

    return CompareResponse(results=results)


def compute_verify_star(avg_fitness: float, qualified_count: int, total_periods: int) -> int:
    """Compute 1-5 star rating from verification results.

    Returns 0 (no star) if not all periods qualified.
    Thresholds calibrated for evolved-strategy backtest Sharpe distribution.
    """
    if total_periods == 0 or qualified_count != total_periods:
        return 0
    if avg_fitness >= 6.0:
        return 5
    if avg_fitness >= 4.5:
        return 4
    if avg_fitness >= 3.0:
        return 3
    if avg_fitness >= 2.0:
        return 2
    if avg_fitness > 0:
        return 1
    return 0


def _update_strategy_verify_fields(db_path: Path, summary) -> None:
    """Update strategy verification summary fields（B3d.2 收口 repository）。"""
    from core.persistence.repository import strategy as repo
    from api.routes.strategies import compute_verify_star  # noqa: F401 语义注记：star 公式单源
    try:
        for item in summary:
            star = compute_verify_star(
                item.avg_fitness, item.qualified_count, item.total_periods,
            )
            repo.update_strategy_verify_stats(
                db_path, item.strategy_id,
                new_score=item.comprehensive_score,
                verify_star=star,
                verified_at=datetime.now().strftime("%Y-%m-%dT%H:%M:%SZ"),
            )
    except Exception:
        logger.warning("Failed to update verify fields for strategies", exc_info=True)

@router.post("/verify")
def verify_strategies(
    payload: VerifyRequest,
    db_path: Path = Depends(get_db_path),
    data_dir: Path = Depends(get_data_dir),
) -> VerifyResponse:
    """Verify strategies across multiple user-defined date ranges.

    For each (strategy, date_range) pair, runs a backtest and computes
    fitness. Then aggregates per-strategy results into a comprehensive
    score: avg_fitness * qualified_ratio * consistency_bonus.
    """
    if not payload.data_ranges:
        raise HTTPException(status_code=400, detail="data_ranges cannot be empty")

    from core.data.mtf_loader import load_and_prepare_df, load_mtf_data
    from core.scoring.scorer import compute_fitness, RequirementsConfig

    engine = _bt_engine_mod.BacktestEngine(
        init_cash=payload.init_cash,
        fee=payload.fee,
        slippage=payload.slippage,
    )

    # Load strategies and group by symbol/timeframe
    strategies_by_group: Dict[str, List[Dict]] = {}
    strategy_names: Dict[str, str] = {}

    for sid in payload.strategy_ids:
        row = get_strategy(db_path, sid)
        if row is None:
            continue
        try:
            dna = StrategyDNA.from_json(row["dna_json"])
            symbol = dna.execution_genes.symbol
            timeframe = dna.execution_genes.timeframe
            group_key = f"{symbol}_{timeframe}"
            strategies_by_group.setdefault(group_key, []).append({
                "strategy_id": sid,
                "dna": dna,
                "symbol": symbol,
                "timeframe": timeframe,
                "row": row,
            })
            strategy_names[sid] = row.get("name", sid[:8])
        except Exception:
            continue

    if not strategies_by_group:
        raise HTTPException(status_code=404, detail="No valid strategies found")

    all_results: List[VerifyResultItem] = []
    # Per-strategy per-period metrics for summary
    strategy_period_data: Dict[str, List[Dict]] = {
        sid: [] for group in strategies_by_group.values() for sid in [s["strategy_id"] for s in group]
    }

    session_id = str(uuid.uuid4())
    save_verify_session(
        db_path, session_id=session_id,
        strategy_ids=json.dumps(payload.strategy_ids),
        data_ranges=json.dumps([dr.model_dump() for dr in payload.data_ranges]),
        init_cash=payload.init_cash, fee=payload.fee, slippage=payload.slippage,
    )

    for group_key, group_strategies in strategies_by_group.items():
        symbol = group_strategies[0]["symbol"]
        timeframe = group_strategies[0]["timeframe"]
        dnas = [s["dna"] for s in group_strategies]
        if payload.leverage != 1:
            import copy
            dnas = [copy.deepcopy(d) for d in dnas]
        # Override leverage if specified
        if payload.leverage != 1:
            for d in dnas:
                if hasattr(d, 'risk_genes') and d.risk_genes is not None:
                    d.risk_genes.leverage = payload.leverage
        strategy_ids = [s["strategy_id"] for s in group_strategies]
        rows = {s["strategy_id"]: s["row"] for s in group_strategies}

        for dr in payload.data_ranges:
            try:
                enhanced_df = load_and_prepare_df(
                    data_dir, symbol, timeframe,
                    data_start=dr.start,
                    data_end=dr.end,
                )
                if enhanced_df is None or len(enhanced_df) < 10:
                    for sid in strategy_ids:
                        all_results.append(VerifyResultItem(
                            strategy_id=sid,
                            data_start=dr.start,
                            data_end=dr.end,
                            error="Insufficient data for this period",
                        ))
                        strategy_period_data[sid].append({
                            "data_start": dr.start,
                            "data_end": dr.end,
                            "fitness": 0.0,
                            "qualified": False,
                            "total_return": 0.0,
                            "sharpe_ratio": 0.0,
                            "max_drawdown": 0.0,
                        })
                    continue

                dfs_by_timeframe = None
                needed_tfs = backtest_service.compute_needed_tfs(dnas, timeframe)
                if needed_tfs:
                    dfs_by_timeframe = load_mtf_data(
                        data_dir, symbol, timeframe, enhanced_df,
                        needed_tfs,
                        data_start=dr.start,
                        data_end=dr.end,
                    )

                # batch_run for all strategies in this group
                bt_results = engine.batch_run(
                    dnas, enhanced_df,
                    dfs_by_timeframe=dfs_by_timeframe,
                )

                actual_start = str(enhanced_df.index.min())[:10]
                actual_end = str(enhanced_df.index.max())[:10]

                for sid, dna, bt_result in zip(strategy_ids, dnas, bt_results):
                    try:
                        metrics = backtest_service.fallback_metrics(bt_result)
                        req = RequirementsConfig()
                        score_result = compute_fitness(
                            metrics, requirements=req,
                            liquidated=bt_result.liquidated,
                        )
                        fitness = score_result["fitness"]
                        qualified = score_result["qualified"]

                        result_id = str(uuid.uuid4())
                        save_backtest_result(
                            db_path,
                            result_id=result_id,
                            strategy_id=sid,
                            symbol=symbol,
                            timeframe=timeframe,
                            data_start=actual_start,
                            data_end=actual_end,
                            init_cash=payload.init_cash,
                            fee=payload.fee,
                            slippage=payload.slippage,
                            total_return=bt_result.total_return,
                            sharpe_ratio=bt_result.sharpe_ratio,
                            max_drawdown=bt_result.max_drawdown,
                            win_rate=bt_result.win_rate,
                            total_trades=bt_result.total_trades,
                            total_score=score_result.get("total_score", 0.0),
                            template_name="explorer",
                            dimension_scores=json.dumps(
                                score_result.get("dimension_scores", {})
                            ),
                            run_source="verify",
                            fitness=fitness,
                            qualified=1 if qualified else 0,
                            satisfaction_json=json.dumps(
                                score_result.get("satisfaction", {})
                            ) if score_result.get("satisfaction") else None,
                            session_id=session_id,
                        )

                        all_results.append(VerifyResultItem(
                            strategy_id=sid,
                            data_start=actual_start,
                            data_end=actual_end,
                            total_return=bt_result.total_return,
                            sharpe_ratio=bt_result.sharpe_ratio,
                            max_drawdown=bt_result.max_drawdown,
                            win_rate=bt_result.win_rate,
                            total_trades=bt_result.total_trades,
                            profit_factor=metrics.get("profit_factor", 0.0),
                            fitness=fitness,
                            qualified=qualified,
                        ))
                        strategy_period_data[sid].append({
                            "data_start": actual_start,
                            "data_end": actual_end,
                            "fitness": fitness,
                            "qualified": qualified,
                            "total_return": bt_result.total_return,
                            "sharpe_ratio": bt_result.sharpe_ratio,
                            "max_drawdown": bt_result.max_drawdown,
                        })
                    except Exception as exc:
                        all_results.append(VerifyResultItem(
                            strategy_id=sid,
                            data_start=dr.start,
                            data_end=dr.end,
                            error=str(exc),
                        ))
                        strategy_period_data[sid].append({
                            "data_start": dr.start,
                            "data_end": dr.end,
                            "fitness": 0.0,
                            "qualified": False,
                            "total_return": 0.0,
                            "sharpe_ratio": 0.0,
                            "max_drawdown": 0.0,
                        })

            except Exception as exc:
                for sid in strategy_ids:
                    all_results.append(VerifyResultItem(
                        strategy_id=sid,
                        data_start=dr.start,
                        data_end=dr.end,
                        error=str(exc),
                    ))
                    strategy_period_data[sid].append({
                        "data_start": dr.start,
                        "data_end": dr.end,
                        "fitness": 0.0,
                        "qualified": False,
                        "total_return": 0.0,
                        "sharpe_ratio": 0.0,
                        "max_drawdown": 0.0,
                    })

    # Build summary with comprehensive scores
    summary: List[VerifySummaryItem] = []
    for sid, periods in strategy_period_data.items():
        if not periods:
            continue
        valid_periods = [p for p in periods if p.get("fitness") is not None]
        if not valid_periods:
            summary.append(VerifySummaryItem(
                strategy_id=sid,
                strategy_name=strategy_names.get(sid, sid[:8]),
                comprehensive_score=0.0,
                avg_fitness=0.0,
                qualified_count=0,
                total_periods=len(periods),
                per_period_metrics=[VerifyPeriodSummary(**p) for p in periods],
            ))
            continue

        fitnesses = [p["fitness"] for p in valid_periods]
        avg_fitness = sum(fitnesses) / len(fitnesses)
        qualified_count = sum(1 for p in valid_periods if p["qualified"])
        total_periods = len(valid_periods)
        qualified_ratio = qualified_count / total_periods if total_periods > 0 else 0.0
        consistency_bonus = 1.2 if qualified_count == total_periods and total_periods > 0 else 1.0
        comprehensive_score = avg_fitness * qualified_ratio * consistency_bonus

        summary.append(VerifySummaryItem(
            strategy_id=sid,
            strategy_name=strategy_names.get(sid, sid[:8]),
            comprehensive_score=round(comprehensive_score, 4),
            avg_fitness=round(avg_fitness, 4),
            qualified_count=qualified_count,
            total_periods=total_periods,
            per_period_metrics=[VerifyPeriodSummary(**p) for p in periods],
        ))

    # Sort summary by comprehensive score descending
    summary.sort(key=lambda x: x.comprehensive_score, reverse=True)

    update_verify_session(
        db_path, session_id,
        status="completed",
        summary_json=json.dumps([s.model_dump() for s in summary]),
        total_results=len(all_results),
        total_strategies=len(strategy_period_data),
        completed_at=datetime.now().strftime("%Y-%m-%dT%H:%M:%SZ"),
    )

    # Update strategy verification summary fields
    _update_strategy_verify_fields(db_path, summary)

    return VerifyResponse(results=all_results, summary=summary)


class _VerifyProcessor:
    """Encapsulates verify logic for SSE streaming. Each process_step() call handles one group×range."""

    def __init__(self, payload: VerifyRequest, db_path: Path, data_dir: Path):
        from core.data.mtf_loader import load_and_prepare_df, load_mtf_data
        from core.scoring.scorer import compute_fitness, RequirementsConfig

        self.payload = payload
        self.db_path = db_path
        self.data_dir = data_dir
        self._load_df = load_and_prepare_df
        self._load_mtf = load_mtf_data
        self._compute_fitness = compute_fitness
        self._RequirementsConfig = RequirementsConfig

        self.engine = _bt_engine_mod.BacktestEngine(
            init_cash=payload.init_cash, fee=payload.fee, slippage=payload.slippage,
        )
        self.strategies_by_group: Dict[str, List[Dict]] = {}
        self.strategy_names: Dict[str, str] = {}
        self.all_results: List[VerifyResultItem] = []
        self.strategy_period_data: Dict[str, List[Dict]] = {}
        self.session_id = ""
        self.steps: List[tuple] = []

    def init(self):
        strategies_by_group: Dict[str, List[Dict]] = {}
        strategy_names: Dict[str, str] = {}

        for sid in self.payload.strategy_ids:
            row = get_strategy(self.db_path, sid)
            if row is None:
                continue
            try:
                dna = StrategyDNA.from_json(row["dna_json"])
                group_key = f"{dna.execution_genes.symbol}_{dna.execution_genes.timeframe}"
                strategies_by_group.setdefault(group_key, []).append({
                    "strategy_id": sid, "dna": dna,
                    "symbol": dna.execution_genes.symbol,
                    "timeframe": dna.execution_genes.timeframe,
                    "row": row,
                })
                strategy_names[sid] = row.get("name", sid[:8])
            except Exception:
                continue

        if not strategies_by_group:
            raise HTTPException(status_code=404, detail="No valid strategies found")

        self.strategies_by_group = strategies_by_group
        self.strategy_names = strategy_names
        self.strategy_period_data = {
            sid: [] for group in strategies_by_group.values()
            for sid in [s["strategy_id"] for s in group]
        }

        self.session_id = str(uuid.uuid4())
        save_verify_session(
            self.db_path, session_id=self.session_id,
            strategy_ids=json.dumps(self.payload.strategy_ids),
            data_ranges=json.dumps([dr.model_dump() for dr in self.payload.data_ranges]),
            init_cash=self.payload.init_cash, fee=self.payload.fee,
            slippage=self.payload.slippage,
        )

        # Pre-compute step list: (group_key, group_strategies, date_range)
        self.steps = [
            (gk, gs, dr)
            for gk, gs in strategies_by_group.items()
            for dr in self.payload.data_ranges
        ]

    def process_step(self, step_index: int) -> Dict:
        """Process one group×range step. Returns progress data."""
        group_key, group_strategies, dr = self.steps[step_index]
        symbol = group_strategies[0]["symbol"]
        timeframe = group_strategies[0]["timeframe"]
        dnas = [s["dna"] for s in group_strategies]
        # Override leverage if specified (deep copy to avoid mutating cached objects)
        if self.payload.leverage != 1:
            import copy
            dnas = [copy.deepcopy(d) for d in dnas]
            for d in dnas:
                if hasattr(d, 'risk_genes') and d.risk_genes is not None:
                    d.risk_genes.leverage = self.payload.leverage
        strategy_ids = [s["strategy_id"] for s in group_strategies]
        batch_results: List[Dict] = []

        try:
            enhanced_df = self._load_df(
                self.data_dir, symbol, timeframe,
                data_start=dr.start, data_end=dr.end,
            )
            if enhanced_df is None or len(enhanced_df) < 10:
                for sid in strategy_ids:
                    self.all_results.append(VerifyResultItem(
                        strategy_id=sid, data_start=dr.start, data_end=dr.end,
                        error="Insufficient data for this period",
                    ))
                    self.strategy_period_data[sid].append({
                        "data_start": dr.start, "data_end": dr.end,
                        "fitness": 0.0, "qualified": False,
                        "total_return": 0.0, "sharpe_ratio": 0.0, "max_drawdown": 0.0,
                    })
                    batch_results.append({"strategy_id": sid, "error": "Insufficient data"})
                return self._progress_payload(step_index, group_key, dr, batch_results)

            dfs_by_timeframe = None
            needed_tfs = backtest_service.compute_needed_tfs(dnas, timeframe)
            if needed_tfs:
                dfs_by_timeframe = self._load_mtf(
                    self.data_dir, symbol, timeframe, enhanced_df,
                    needed_tfs, data_start=dr.start, data_end=dr.end,
                )

            bt_results = self.engine.batch_run(dnas, enhanced_df, dfs_by_timeframe=dfs_by_timeframe)
            actual_start = str(enhanced_df.index.min())[:10]
            actual_end = str(enhanced_df.index.max())[:10]

            for sid, dna, bt_result in zip(strategy_ids, dnas, bt_results):
                try:
                    metrics = backtest_service.fallback_metrics(bt_result)
                    score_result = self._compute_fitness(
                        metrics, requirements=self._RequirementsConfig(),
                        liquidated=bt_result.liquidated,
                    )
                    fitness = score_result["fitness"]
                    qualified = score_result["qualified"]

                    result_id = str(uuid.uuid4())
                    save_backtest_result(
                        self.db_path, result_id=result_id, strategy_id=sid,
                        symbol=symbol, timeframe=timeframe,
                        data_start=actual_start, data_end=actual_end,
                        init_cash=self.payload.init_cash, fee=self.payload.fee,
                        slippage=self.payload.slippage,
                        total_return=bt_result.total_return,
                        sharpe_ratio=bt_result.sharpe_ratio,
                        max_drawdown=bt_result.max_drawdown,
                        win_rate=bt_result.win_rate,
                        total_trades=bt_result.total_trades,
                        total_score=score_result.get("total_score", 0.0),
                        template_name="explorer",
                        dimension_scores=json.dumps(score_result.get("dimension_scores", {})),
                        run_source="verify",
                        fitness=fitness, qualified=1 if qualified else 0,
                        satisfaction_json=json.dumps(score_result.get("satisfaction", {}))
                            if score_result.get("satisfaction") else None,
                        session_id=self.session_id,
                    )

                    item = VerifyResultItem(
                        strategy_id=sid, data_start=actual_start, data_end=actual_end,
                        total_return=bt_result.total_return,
                        sharpe_ratio=bt_result.sharpe_ratio,
                        max_drawdown=bt_result.max_drawdown,
                        win_rate=bt_result.win_rate,
                        total_trades=bt_result.total_trades,
                        profit_factor=metrics.get("profit_factor", 0.0),
                        fitness=fitness, qualified=qualified,
                    )
                    self.all_results.append(item)
                    self.strategy_period_data[sid].append({
                        "data_start": actual_start, "data_end": actual_end,
                        "fitness": fitness, "qualified": qualified,
                        "total_return": bt_result.total_return,
                        "sharpe_ratio": bt_result.sharpe_ratio,
                        "max_drawdown": bt_result.max_drawdown,
                    })
                    batch_results.append(item.model_dump())
                except Exception as exc:
                    self.all_results.append(VerifyResultItem(
                        strategy_id=sid, data_start=dr.start, data_end=dr.end,
                        error=str(exc),
                    ))
                    self.strategy_period_data[sid].append({
                        "data_start": dr.start, "data_end": dr.end,
                        "fitness": 0.0, "qualified": False,
                        "total_return": 0.0, "sharpe_ratio": 0.0, "max_drawdown": 0.0,
                    })
                    batch_results.append({"strategy_id": sid, "error": str(exc)})

        except Exception as exc:
            for sid in strategy_ids:
                self.all_results.append(VerifyResultItem(
                    strategy_id=sid, data_start=dr.start, data_end=dr.end,
                    error=str(exc),
                ))
                self.strategy_period_data[sid].append({
                    "data_start": dr.start, "data_end": dr.end,
                    "fitness": 0.0, "qualified": False,
                    "total_return": 0.0, "sharpe_ratio": 0.0, "max_drawdown": 0.0,
                })
                batch_results.append({"strategy_id": sid, "error": str(exc)})

        return self._progress_payload(step_index, group_key, dr, batch_results)

    def finalize(self) -> Dict:
        summary: List[VerifySummaryItem] = []
        for sid, periods in self.strategy_period_data.items():
            if not periods:
                continue
            valid_periods = [p for p in periods if p.get("fitness") is not None]
            if not valid_periods:
                summary.append(VerifySummaryItem(
                    strategy_id=sid,
                    strategy_name=self.strategy_names.get(sid, sid[:8]),
                    comprehensive_score=0.0, avg_fitness=0.0,
                    qualified_count=0, total_periods=len(periods),
                    per_period_metrics=[VerifyPeriodSummary(**p) for p in periods],
                ))
                continue
            fitnesses = [p["fitness"] for p in valid_periods]
            avg_fitness = sum(fitnesses) / len(fitnesses)
            qualified_count = sum(1 for p in valid_periods if p["qualified"])
            total_periods = len(valid_periods)
            qualified_ratio = qualified_count / total_periods if total_periods > 0 else 0.0
            consistency_bonus = 1.2 if qualified_count == total_periods and total_periods > 0 else 1.0
            comprehensive_score = avg_fitness * qualified_ratio * consistency_bonus
            summary.append(VerifySummaryItem(
                strategy_id=sid,
                strategy_name=self.strategy_names.get(sid, sid[:8]),
                comprehensive_score=round(comprehensive_score, 4),
                avg_fitness=round(avg_fitness, 4),
                qualified_count=qualified_count,
                total_periods=total_periods,
                per_period_metrics=[VerifyPeriodSummary(**p) for p in periods],
            ))
        summary.sort(key=lambda x: x.comprehensive_score, reverse=True)

        update_verify_session(
            self.db_path, self.session_id, status="completed",
            summary_json=json.dumps([s.model_dump() for s in summary]),
            total_results=len(self.all_results),
            total_strategies=len(self.strategy_period_data),
            completed_at=datetime.now().strftime("%Y-%m-%dT%H:%M:%SZ"),
        )

        # Update strategy verification summary fields
        _update_strategy_verify_fields(self.db_path, summary)

        return {
            "session_id": self.session_id,
            "summary": [s.model_dump() for s in summary],
            "results": [r.model_dump() for r in self.all_results],
        }

    def mark_failed(self, error_message: str):
        try:
            update_verify_session(
                self.db_path, self.session_id,
                status="failed", error_message=error_message,
            )
        except Exception:
            # B1.4: 失败状态的兜底写入自身失败必须可见——静默吞掉会让
            # session 永久卡 running 且无任何排查线索
            logger.error(
                "verify session %s mark_failed 写入失败（session 将停留在 "
                "running，原始错误: %s）",
                self.session_id, error_message,
                exc_info=True,
            )

    def _progress_payload(self, step_index: int, group_key: str,
                          dr, batch_results: list) -> Dict:
        return {
            "current": step_index + 1,
            "total": len(self.steps),
            "group": group_key,
            "range_start": dr.start,
            "range_end": dr.end,
            "batch_results": batch_results,
        }


@router.post("/verify/stream")
async def verify_strategies_stream(
    payload: VerifyRequest,
    db_path: Path = Depends(get_db_path),
    data_dir: Path = Depends(get_data_dir),
):
    """SSE streaming variant of /verify. Yields progress events per group×range step."""
    processor = _VerifyProcessor(payload, db_path, data_dir)
    await asyncio.to_thread(processor.init)

    async def event_stream():
        try:
            for i in range(len(processor.steps)):
                progress = await asyncio.to_thread(processor.process_step, i)
                yield f"event: progress\ndata: {json.dumps(progress, ensure_ascii=False)}\n\n"

            complete = await asyncio.to_thread(processor.finalize)
            yield f"event: complete\ndata: {json.dumps(complete, ensure_ascii=False)}\n\n"
        except asyncio.CancelledError:
            await asyncio.to_thread(processor.mark_failed, "Client disconnected")
        except Exception as exc:
            await asyncio.to_thread(processor.mark_failed, str(exc))
            yield f"event: error\ndata: {json.dumps({'message': str(exc)}, ensure_ascii=False)}\n\n"

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.get("/verify/history", response_model=VerifyHistoryResponse)
def get_verify_history(
    strategy_id: Optional[str] = None,
    limit: int = 100,
    db_path: Path = Depends(get_db_path),
) -> VerifyHistoryResponse:
    """Return past verify results from backtest_result (run_source='verify')."""
    results = list_backtest_results(
        db_path,
        strategy_id=strategy_id,
        run_source="verify",
        limit=limit,
    )
    strategy_names: Dict[str, str] = {}
    items = []
    for row in results:
        sid = row["strategy_id"]
        if sid not in strategy_names:
            strat = get_strategy(db_path, sid)
            strategy_names[sid] = strat["name"] if strat else None
        items.append(VerifyHistoryItem(
            result_id=row["result_id"],
            strategy_id=sid,
            strategy_name=strategy_names[sid],
            symbol=row["symbol"],
            timeframe=row["timeframe"],
            data_start=row["data_start"],
            data_end=row["data_end"],
            total_return=row.get("total_return", 0.0),
            sharpe_ratio=row.get("sharpe_ratio", 0.0),
            max_drawdown=row.get("max_drawdown", 0.0),
            fitness=row.get("fitness", 0.0),
            qualified=row.get("qualified", 0),
            created_at=row["created_at"],
        ))
    return VerifyHistoryResponse(items=items, total=len(items))


@router.get("/verify/sessions", response_model=VerifySessionListResponse)
def list_sessions(
    limit: int = 20,
    db_path: Path = Depends(get_db_path),
) -> VerifySessionListResponse:
    """Return verification sessions ordered by creation time."""
    sessions = list_verify_sessions(db_path, limit=limit)
    items = [
        VerifySessionResponse(**{k: s[k] for k in VerifySessionResponse.model_fields})
        for s in sessions
    ]
    return VerifySessionListResponse(items=items, total=len(items))


@router.get("/verify/sessions/{session_id}/results", response_model=VerifyHistoryResponse)
def get_session_results(
    session_id: str,
    db_path: Path = Depends(get_db_path),
) -> VerifyHistoryResponse:
    """Return backtest results for a specific verify session."""
    session = get_verify_session(db_path, session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    results = list_backtest_results(
        db_path,
        run_source="verify",
        limit=500,
    )
    results = [r for r in results if r.get("session_id") == session_id]
    strategy_names: Dict[str, str] = {}
    items = []
    for row in results:
        sid = row["strategy_id"]
        if sid not in strategy_names:
            strat = get_strategy(db_path, sid)
            strategy_names[sid] = strat["name"] if strat else None
        items.append(VerifyHistoryItem(
            result_id=row["result_id"],
            strategy_id=sid,
            strategy_name=strategy_names[sid],
            symbol=row["symbol"],
            timeframe=row["timeframe"],
            data_start=row["data_start"],
            data_end=row["data_end"],
            total_return=row.get("total_return", 0.0),
            sharpe_ratio=row.get("sharpe_ratio", 0.0),
            max_drawdown=row.get("max_drawdown", 0.0),
            fitness=row.get("fitness", 0.0),
            qualified=row.get("qualified", 0),
            created_at=row["created_at"],
        ))
    return VerifyHistoryResponse(items=items, total=len(items))


# ===================================================================
# Batch Backtest
# ===================================================================


class _BatchBacktestProcessor:
    """Batch backtest using DecisionPipeline (realistic simulation engine).

    Uses ReplayRunner for bar-by-bar execution with open-price fills,
    limit orders, ATR stops, and real-time margin tracking.
    """

    def __init__(self, payload: BatchBacktestRequest, db_path: Path, data_dir: Path):
        from core.data.mtf_loader import load_and_prepare_df, load_mtf_data
        from core.scoring.scorer import compute_fitness, RequirementsConfig
        from core.scoring.metrics import compute_metrics

        self.payload = payload
        self.db_path = db_path
        self.data_dir = data_dir
        self._load_df = load_and_prepare_df
        self._load_mtf = load_mtf_data
        self._compute_fitness = compute_fitness
        self._RequirementsConfig = RequirementsConfig
        self._compute_metrics = compute_metrics

        self.strategies_by_group: Dict[str, List[Dict]] = {}
        self.strategy_names: Dict[str, str] = {}
        self.all_results: List[BatchBacktestResultItem] = []
        self.strategy_period_data: Dict[str, List[Dict]] = {}
        self.steps: List[tuple] = []

    def init(self):
        strategies_by_group: Dict[str, List[Dict]] = {}
        strategy_names: Dict[str, str] = {}

        for sid in self.payload.strategy_ids:
            row = get_strategy(self.db_path, sid)
            if row is None:
                continue
            try:
                dna = StrategyDNA.from_json(row["dna_json"])
                group_key = f"{dna.execution_genes.symbol}_{dna.execution_genes.timeframe}"
                strategies_by_group.setdefault(group_key, []).append({
                    "strategy_id": sid, "dna": dna,
                    "symbol": dna.execution_genes.symbol,
                    "timeframe": dna.execution_genes.timeframe,
                    "row": row,
                })
                strategy_names[sid] = row.get("name", sid[:8])
            except Exception:
                continue

        if not strategies_by_group:
            raise HTTPException(status_code=404, detail="No valid strategies found")

        self.strategies_by_group = strategies_by_group
        self.strategy_names = strategy_names
        self.strategy_period_data = {
            sid: [] for group in strategies_by_group.values()
            for sid in [s["strategy_id"] for s in group]
        }

        # Pre-compute step list: (group_key, group_strategies, date_range)
        self.steps = [
            (gk, gs, dr)
            for gk, gs in strategies_by_group.items()
            for dr in self.payload.data_ranges
        ]

    def _build_equity_json_from_curve(
        self, equity_curve: list, df: pd.DataFrame, bars_processed: int,
    ) -> Optional[str]:
        """Serialize equity curve + DataFrame timestamps to JSON."""
        if not equity_curve or bars_processed <= 0:
            return None
        timestamps = df.index[-bars_processed:]
        return json.dumps([
            {"timestamp": str(ts)[:19], "value": round(float(v), 4)}
            for ts, v in zip(timestamps, equity_curve)
        ])

    def _build_signals_json_from_events(self, events_log: list) -> Optional[str]:
        """Serialize position_closed events to JSON signals (delegates to backtest_service)."""
        return backtest_service.build_signals_from_events(events_log)

    def _bars_per_year(self, timeframe: str) -> int:
        mapping = {"1m": 525600, "5m": 105120, "15m": 35040, "30m": 17520,
                   "1h": 8760, "2h": 4380, "4h": 2190, "1d": 365}
        return mapping.get(timeframe, 2190)

    def process_step(self, step_index: int) -> Dict:
        """Process one group x range step using DecisionPipeline."""
        from core.trading.replay import ReplayRunner

        group_key, group_strategies, dr = self.steps[step_index]
        symbol = group_strategies[0]["symbol"]
        timeframe = group_strategies[0]["timeframe"]
        strategy_ids = [s["strategy_id"] for s in group_strategies]
        batch_results: List[Dict] = []

        try:
            enhanced_df = self._load_df(
                self.data_dir, symbol, timeframe,
                data_start=dr.start, data_end=dr.end,
            )
            if enhanced_df is None or len(enhanced_df) < 10:
                for sid in strategy_ids:
                    self.all_results.append(BatchBacktestResultItem(
                        result_id="", strategy_id=sid,
                        strategy_name=self.strategy_names.get(sid, ""),
                        symbol=symbol, timeframe=timeframe,
                        data_start=dr.start, data_end=dr.end,
                        error="Insufficient data for this period",
                    ))
                    self.strategy_period_data[sid].append({
                        "data_start": dr.start, "data_end": dr.end,
                        "fitness": 0.0, "qualified": False,
                        "total_return": 0.0, "sharpe_ratio": 0.0,
                        "max_drawdown": 0.0, "result_id": "",
                    })
                    batch_results.append({"strategy_id": sid, "error": "Insufficient data"})
                return self._progress_payload(step_index, group_key, dr, batch_results)

            actual_start = str(enhanced_df.index.min())[:10]
            actual_end = str(enhanced_df.index.max())[:10]

            dfs_by_timeframe = None
            needed_tfs = backtest_service.compute_needed_tfs(
                [s["dna"] for s in group_strategies], timeframe)
            if needed_tfs:
                dfs_by_timeframe = self._load_mtf(
                    self.data_dir, symbol, timeframe, enhanced_df,
                    needed_tfs, data_start=dr.start, data_end=dr.end,
                )

            # Run each strategy through ReplayRunner (DecisionPipeline)
            for s_info in group_strategies:
                sid = s_info["strategy_id"]
                dna = s_info["dna"]
                try:
                    import copy
                    dna = copy.deepcopy(dna)
                    if self.payload.leverage != 1:
                        dna.risk_genes.leverage = self.payload.leverage

                    runner = ReplayRunner(
                        init_cash=self.payload.init_cash,
                        fee=self.payload.fee,
                        slippage=self.payload.slippage,
                    )
                    result = runner.run(dna, enhanced_df, dfs_by_timeframe=dfs_by_timeframe)

                    # Compute metrics from equity curve
                    eq_curve = result.equity_curve
                    total_trades = result.total_trades
                    if eq_curve and total_trades > 0:
                        eq_series = pd.Series(eq_curve)
                        closed_events = [
                            e for e in result.events_log
                            if e.get("type") == "position_closed"
                        ]
                        metrics = self._compute_metrics(
                            eq_series,
                            total_trades=total_trades,
                            bars_per_year=self._bars_per_year(timeframe),
                            trade_win_rate=(
                                sum(1 for e in closed_events if (e.get("pnl") or 0) > 0)
                                / len(closed_events)
                            ) if closed_events else None,
                        )
                    else:
                        metrics = {
                            "annual_return": 0.0, "sharpe_ratio": 0.0,
                            "max_drawdown": 0.0, "win_rate": 0.0,
                            "profit_factor": 0.0,
                        }

                    # Check for liquidation in events
                    liquidated = any(
                        e.get("exit_reason") == "liquidation"
                        for e in result.events_log
                        if e.get("type") == "position_closed"
                    )

                    score_result = self._compute_fitness(
                        metrics, requirements=self._RequirementsConfig(),
                        liquidated=liquidated,
                    )
                    fitness = score_result["fitness"]
                    qualified = score_result["qualified"]

                    result_id = str(uuid.uuid4())

                    equity_curve_json = self._build_equity_json_from_curve(
                        result.equity_curve, enhanced_df, result.bars_processed
                    )
                    signals_json = self._build_signals_json_from_events(
                        result.events_log
                    )

                    save_backtest_result(
                        self.db_path, result_id=result_id, strategy_id=sid,
                        symbol=symbol, timeframe=timeframe,
                        data_start=actual_start, data_end=actual_end,
                        init_cash=self.payload.init_cash, fee=self.payload.fee,
                        slippage=self.payload.slippage,
                        total_return=result.total_return,
                        sharpe_ratio=metrics.get("sharpe_ratio", 0.0),
                        max_drawdown=metrics.get("max_drawdown", 0.0),
                        win_rate=metrics.get("win_rate", 0.0),
                        total_trades=total_trades,
                        total_score=score_result.get("total_score", 0.0),
                        template_name="explorer",
                        dimension_scores=json.dumps(score_result.get("dimension_scores", {})),
                        run_source="batch_backtest",
                        fitness=fitness, qualified=1 if qualified else 0,
                        satisfaction_json=json.dumps(score_result.get("satisfaction", {}))
                            if score_result.get("satisfaction") else None,
                        equity_curve=equity_curve_json,
                        trades_json=signals_json,
                    )

                    item = BatchBacktestResultItem(
                        result_id=result_id, strategy_id=sid,
                        strategy_name=self.strategy_names.get(sid, ""),
                        symbol=symbol, timeframe=timeframe,
                        data_start=actual_start, data_end=actual_end,
                        total_return=result.total_return,
                        sharpe_ratio=metrics.get("sharpe_ratio", 0.0),
                        max_drawdown=metrics.get("max_drawdown", 0.0),
                        win_rate=metrics.get("win_rate", 0.0),
                        total_trades=total_trades,
                        profit_factor=metrics.get("profit_factor", 0.0),
                        fitness=fitness, qualified=qualified,
                        liquidated=liquidated,
                        total_funding_cost=0.0,
                    )
                    self.all_results.append(item)
                    self.strategy_period_data[sid].append({
                        "data_start": actual_start, "data_end": actual_end,
                        "fitness": fitness, "qualified": qualified,
                        "total_return": result.total_return,
                        "sharpe_ratio": metrics.get("sharpe_ratio", 0.0),
                        "max_drawdown": metrics.get("max_drawdown", 0.0),
                        "result_id": result_id,
                    })
                    batch_results.append(item.model_dump())

                except Exception as exc:
                    self.all_results.append(BatchBacktestResultItem(
                        result_id="", strategy_id=sid,
                        strategy_name=self.strategy_names.get(sid, ""),
                        symbol=symbol, timeframe=timeframe,
                        data_start=dr.start, data_end=dr.end,
                        error=str(exc),
                    ))
                    self.strategy_period_data[sid].append({
                        "data_start": dr.start, "data_end": dr.end,
                        "fitness": 0.0, "qualified": False,
                        "total_return": 0.0, "sharpe_ratio": 0.0,
                        "max_drawdown": 0.0, "result_id": "",
                    })
                    batch_results.append({"strategy_id": sid, "error": str(exc)})

        except Exception as exc:
            for sid in strategy_ids:
                self.all_results.append(BatchBacktestResultItem(
                    result_id="", strategy_id=sid,
                    strategy_name=self.strategy_names.get(sid, ""),
                    symbol=symbol, timeframe=timeframe,
                    data_start=dr.start, data_end=dr.end,
                    error=str(exc),
                ))
                self.strategy_period_data[sid].append({
                    "data_start": dr.start, "data_end": dr.end,
                    "fitness": 0.0, "qualified": False,
                    "total_return": 0.0, "sharpe_ratio": 0.0,
                    "max_drawdown": 0.0, "result_id": "",
                })
                batch_results.append({"strategy_id": sid, "error": str(exc)})

        return self._progress_payload(step_index, group_key, dr, batch_results)

    def finalize(self) -> Dict:
        """Aggregate per-strategy summary across all periods."""
        summary: List[BatchBacktestSummaryItem] = []
        for sid, periods in self.strategy_period_data.items():
            if not periods:
                continue
            valid_periods = [p for p in periods if p.get("result_id")]
            if not valid_periods:
                summary.append(BatchBacktestSummaryItem(
                    strategy_id=sid,
                    strategy_name=self.strategy_names.get(sid, sid[:8]),
                    avg_total_return=0.0,
                    avg_sharpe_ratio=0.0,
                    worst_max_drawdown=0.0,
                    avg_fitness=0.0,
                    qualified_count=0,
                    total_periods=len(periods),
                    per_period_results=[BatchBacktestResultItem(
                        result_id=p.get("result_id", ""),
                        strategy_id=sid,
                        strategy_name=self.strategy_names.get(sid, ""),
                        data_start=p["data_start"],
                        data_end=p["data_end"],
                    ) for p in periods],
                ))
                continue

            returns = [p["total_return"] for p in valid_periods]
            sharpes = [p["sharpe_ratio"] for p in valid_periods]
            drawdowns = [p["max_drawdown"] for p in valid_periods]
            fitnesses = [p["fitness"] for p in valid_periods]
            qualified_count = sum(1 for p in valid_periods if p["qualified"])

            # Build per-period BatchBacktestResultItem list from all_results
            period_result_ids = {p.get("result_id") for p in valid_periods}
            per_period_items = [
                r for r in self.all_results
                if r.strategy_id == sid and r.result_id in period_result_ids
            ]

            summary.append(BatchBacktestSummaryItem(
                strategy_id=sid,
                strategy_name=self.strategy_names.get(sid, sid[:8]),
                symbol=per_period_items[0].symbol if per_period_items else "",
                timeframe=per_period_items[0].timeframe if per_period_items else "",
                avg_total_return=round(sum(returns) / len(returns), 4),
                avg_sharpe_ratio=round(sum(sharpes) / len(sharpes), 4),
                worst_max_drawdown=round(max(drawdowns), 4),
                avg_fitness=round(sum(fitnesses) / len(fitnesses), 4),
                qualified_count=qualified_count,
                total_periods=len(periods),
                per_period_results=per_period_items,
            ))

        summary.sort(key=lambda x: x.avg_fitness, reverse=True)

        return {
            "summary": [s.model_dump() for s in summary],
            "results": [r.model_dump() for r in self.all_results],
        }

    def mark_failed(self, error_message: str):
        """No session table to update for batch backtest."""
        pass

    def _progress_payload(self, step_index: int, group_key: str,
                          dr, batch_results: list) -> Dict:
        return {
            "current": step_index + 1,
            "total": len(self.steps),
            "group": group_key,
            "range_start": dr.start,
            "range_end": dr.end,
            "batch_results": batch_results,
        }


@router.post("/batch-backtest/stream")
async def batch_backtest_stream(
    payload: BatchBacktestRequest,
    db_path: Path = Depends(get_db_path),
    data_dir: Path = Depends(get_data_dir),
):
    """SSE streaming endpoint for batch backtesting multiple strategies across date ranges."""
    processor = _BatchBacktestProcessor(payload, db_path, data_dir)
    await asyncio.to_thread(processor.init)

    async def event_stream():
        try:
            for i in range(len(processor.steps)):
                progress = await asyncio.to_thread(processor.process_step, i)
                yield f"event: progress\ndata: {json.dumps(progress, ensure_ascii=False)}\n\n"

            complete = await asyncio.to_thread(processor.finalize)
            yield f"event: complete\ndata: {json.dumps(complete, ensure_ascii=False)}\n\n"
        except asyncio.CancelledError:
            await asyncio.to_thread(processor.mark_failed, "Client disconnected")
        except Exception as exc:
            await asyncio.to_thread(processor.mark_failed, str(exc))
            yield f"event: error\ndata: {json.dumps({'message': str(exc)}, ensure_ascii=False)}\n\n"

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.get("/batch-backtest/{result_id}")
def get_batch_backtest_detail(
    result_id: str,
    db_path: Path = Depends(get_db_path),
):
    """Get full batch backtest result detail including equity curve and signals."""
    row = get_backtest_result(db_path, result_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Backtest result not found")

    # Parse equity_curve from stored JSON
    equity_data = None
    if row.get("equity_curve"):
        try:
            equity_data = json.loads(row["equity_curve"])
        except (json.JSONDecodeError, TypeError):
            equity_data = None

    # Parse signals from trades_json column
    signals_data = None
    if row.get("trades_json"):
        try:
            signals_data = json.loads(row["trades_json"])
        except (json.JSONDecodeError, TypeError):
            signals_data = None

    # Parse dimension_scores
    dimension_scores = None
    if row.get("dimension_scores"):
        try:
            dimension_scores = json.loads(row["dimension_scores"])
        except (json.JSONDecodeError, TypeError):
            pass

    # Parse satisfaction
    satisfaction = None
    if row.get("satisfaction_json"):
        try:
            satisfaction = json.loads(row["satisfaction_json"])
        except (json.JSONDecodeError, TypeError):
            pass

    return BacktestResponse(
        result_id=row["result_id"],
        strategy_id=row["strategy_id"],
        symbol=row["symbol"],
        timeframe=row["timeframe"],
        data_start=row.get("data_start"),
        data_end=row.get("data_end"),
        init_cash=row.get("init_cash", 100000.0),
        fee=row.get("fee", 0.001),
        slippage=row.get("slippage", 0.0005),
        total_return=row.get("total_return", 0.0),
        sharpe_ratio=row.get("sharpe_ratio", 0.0),
        max_drawdown=row.get("max_drawdown", 0.0),
        win_rate=row.get("win_rate", 0.0),
        total_trades=row.get("total_trades", 0),
        total_score=row.get("total_score", 0.0),
        fitness=row.get("fitness", 0.0),
        qualified=bool(row.get("qualified", 0)),
        satisfaction=satisfaction,
        template_name=row.get("template_name", "explorer"),
        dimension_scores=dimension_scores,
        run_source=row.get("run_source", "batch_backtest"),
        equity_curve=equity_data,
        signals=signals_data,
        total_funding_cost=row.get("total_funding_cost", 0.0),
        liquidated=row.get("liquidated", False),
    )
