"""多策略对比入口（POST /api/strategies/compare）。

职能：多策略同数据集回测对比，逐策略返回指标与失败隔离。
服务对象：web 策略页（对比面板）。职责边界：传输与循环编排，
单策略回测语义与 backtest 模块一致（回退路径不同：裸 df 无指标）。
"""
from __future__ import annotations

import json
import logging
import uuid

from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException

from ._common import _safe_error_message

from core.persistence.repository.backtest_result import (
    get_backtest_result,
    list_backtest_results,
    save_backtest_result,
)
from core.persistence.repository.strategy import (
    count_strategies,
    delete_strategy,
    get_strategy,
    list_strategies,
    save_strategy,
    update_strategy,
)
from core.persistence.repository.verify_session import (
    get_verify_session,
    list_verify_sessions,
    save_verify_session,
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
    """多策略回测对比（逐策略执行，失败条目隔离不拖累整体）。"""
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

    from core.data.mtf_loader import load_and_prepare_df, load_mtf_data

    engine = _bt_engine_mod.BacktestEngine(
        init_cash=payload.init_cash,
        fee=payload.fee,
        slippage=payload.slippage,
    )

    # 延迟装载：df 仅为 load_and_prepare_df 未命中时的回退底座，
    # 主路径（enhanced 命中）不再无条件全量读 parquet
    df = None
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
            # 读库二道校验（recognize-plugin-source-dna T3）：库内 DNA
            # 损坏（本机直改库可绕过写入时校验）→422 数据完整性错误
            # （HTTPException 经下方前置分支放行，不进 per-item error）
            from core.strategy.validator import validate_dna
            _vr = validate_dna(dna)
            if not _vr.is_valid:
                raise HTTPException(
                    status_code=422,
                    detail={"code": "DNA_VALIDATION_FAILED",
                            "message": "Library DNA invalid（库内 DNA 结构/值域非法）",
                            "errors": _vr.errors})
            # 插件源能力边界判据（T4，第二问·乙）：在场即拒（含混合
            # 形态）；错误码分立（补丁①）
            from core.strategy.validator import has_plugin_sources
            if has_plugin_sources(dna):
                raise HTTPException(
                    status_code=422,
                    detail={
                        "code": "PLUGIN_SOURCES_UNSUPPORTED",
                        "message": "策略含插件信号源，REST 对比回测通道不支持"
                                   "（引擎该通道未接插件信号集，结果将为零"
                                   "交易）——请经研究验证链沙盒回测"})
            symbol = dna.execution_genes.symbol
            timeframe = dna.execution_genes.timeframe

            # 增强数据（含指标）——回退与 backtest 端点不同：裸 df
            # 不做逐基因指标计算
            enhanced_df = load_and_prepare_df(
                data_dir, symbol, timeframe,
                data_start=payload.data_start,
                data_end=payload.data_end,
            )
            if enhanced_df is None:
                if df is None:
                    from core.data.storage import load_parquet
                    df = load_parquet(parquet_path)
                enhanced_df = df

            # MTF 策略装载多周期数据
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
            # 落库统计单列读（与延迟装载解耦：主路径 enhanced 命中时 df 不装载）
            import pyarrow.parquet as _pq
            _pf = _pq.ParquetFile(parquet_path)
            _names = _pf.schema_arrow.names
            _ts = next(
                (c for c in ("timestamp", "date", "__index_level_0__")
                 if c in _names), None,
            )
            if _ts is not None and _pf.metadata.num_rows > 0:
                _col = _pf.read(columns=[_ts]).column(0).to_pandas()
                data_start_str = str(_col.min())
                data_end_str = str(_col.max())
            else:
                data_start_str = ""
                data_end_str = ""
            save_backtest_result(
                db_path,
                result_id=result_id,
                strategy_id=sid,
                symbol=row["symbol"],
                timeframe=row["timeframe"],
                data_start=data_start_str,
                data_end=data_end_str,
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
        except HTTPException:
            raise
        except Exception as exc:
            results.append(CompareResultItem(
                strategy_id=sid,
                error=_safe_error_message(exc),
            ))

    return CompareResponse(results=results)
