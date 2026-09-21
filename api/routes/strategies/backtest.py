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


router = APIRouter()


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
    if dna.is_mtf and dna.layers:
        # is_mtf 即装载（不用 len>1 守卫：单层同 tf 策略曾在此必 500——
        # 守卫跳过装载而 executor 要求 is_mtf 必有 dfs_by_timeframe；
        # load_mtf_data 内建 exec tf 帧，单层同 tf 零额外 IO 退化）
        needed_tfs: set[str] = {layer.timeframe for layer in dna.layers}
        needed_tfs.add(timeframe)
        dfs_by_timeframe = load_mtf_data(
            data_dir, symbol, timeframe, enhanced_df,
            needed_tfs, payload.data_start, payload.data_end,
        )
    elif payload.timeframe_pool and len(payload.timeframe_pool) > 1:
        dfs_by_timeframe = load_mtf_data(
            data_dir, symbol, timeframe, enhanced_df,
            set(payload.timeframe_pool), payload.data_start, payload.data_end,
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
