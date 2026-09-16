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


router = APIRouter()




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
    """B8.3 流式批：POST 返回 session_id，进度经 socket.io /streams 下发。

    断线语义（B8.0 ②）：任务跑完并落库（verify_session 持久化在），
    重连恢复面=GET /verify/sessions/{session_id}/results。
    取消语义（B8.0 ①）：C→S cancel 事件协作取消（步粒度）。
    """
    import asyncio as _asyncio
    import threading as _threading

    from api import realtime, stream_control

    processor = _VerifyProcessor(payload, db_path, data_dir)
    await asyncio.to_thread(processor.init)
    session_id = processor.session_id
    cancel_event = _threading.Event()
    stream_control.register(session_id, cancel_event.set)

    async def _run_stream() -> None:
        try:
            for i in range(len(processor.steps)):
                if cancel_event.is_set():
                    await asyncio.to_thread(processor.mark_failed, "Cancelled by client")
                    realtime.broadcast("/streams", session_id, "error", {"message": "Cancelled by client"})
                    return
                progress = await asyncio.to_thread(processor.process_step, i)
                realtime.broadcast("/streams", session_id, "progress", progress)
            complete = await asyncio.to_thread(processor.finalize)
            realtime.broadcast("/streams", session_id, "complete", complete)
        except Exception as exc:
            await asyncio.to_thread(processor.mark_failed, str(exc))
            realtime.broadcast("/streams", session_id, "error", {"message": str(exc)})
        finally:
            stream_control.unregister(session_id)

    _asyncio.create_task(_run_stream())
    return {"session_id": session_id}


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
