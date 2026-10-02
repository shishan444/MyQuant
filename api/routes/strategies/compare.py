"""Strategy CRUD + backtest + compare routes."""
from __future__ import annotations

import json
import logging
import uuid

from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException

from ._common import _safe_error_message

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


@router.post("/compare")
def compare_strategies(
    payload: CompareRequest,
    db_path: Path = Depends(get_db_path),
    data_dir: Path = Depends(get_data_dir),
) -> CompareResponse:
    """Compare multiple strategies by running backtests."""
    # 命名白名单拒绝式校验（dataset_id 直拼读取路径，对齐 csv_importer 生成侧契约）
    from core.data.validation import validate_import_interval, validate_symbol

    try:
        parts = payload.dataset_id.rsplit("_", 1)
        if len(parts) != 2:
            raise ValueError(f"{payload.dataset_id!r}（须形如 SYMBOL_interval）")
        validate_symbol(parts[0])
        validate_import_interval(parts[1])
    except ValueError as exc:
        raise HTTPException(
            status_code=422,
            detail=f"Invalid dataset_id: {exc}",
        ) from exc
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
                error=_safe_error_message(exc),
            ))

    return CompareResponse(results=results)
