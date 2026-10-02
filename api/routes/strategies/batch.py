"""Strategy CRUD + backtest + compare routes."""
from __future__ import annotations

import json
import logging
import uuid
import asyncio
import threading

import pandas as pd
from pathlib import Path
from typing import Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException

from ._common import _progress_payload, _safe_error_message

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
from core.scoring.metrics import BARS_PER_YEAR, DEFAULT_BARS_PER_YEAR
from core.services import backtest_service
from core.strategy.dna import StrategyDNA

logger = logging.getLogger(__name__)


router = APIRouter()


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
        # 公共单一权威源（metrics.py，与回测引擎同源）；补全 3d 档，
        # 缺省 2190（=4h 档）不变
        return BARS_PER_YEAR.get(timeframe, DEFAULT_BARS_PER_YEAR)

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
                return _progress_payload(self.steps, step_index, group_key, dr, batch_results)

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
                            "max_drawdown": 0.0, "win_rate": None,
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
                        win_rate=metrics.get("win_rate"),
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
                        win_rate=metrics.get("win_rate"),
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
                        error=_safe_error_message(exc),
                    ))
                    self.strategy_period_data[sid].append({
                        "data_start": dr.start, "data_end": dr.end,
                        "fitness": 0.0, "qualified": False,
                        "total_return": 0.0, "sharpe_ratio": 0.0,
                        "max_drawdown": 0.0, "result_id": "",
                    })
                    batch_results.append({"strategy_id": sid, "error": _safe_error_message(exc)})

        except Exception as exc:
            for sid in strategy_ids:
                self.all_results.append(BatchBacktestResultItem(
                    result_id="", strategy_id=sid,
                    strategy_name=self.strategy_names.get(sid, ""),
                    symbol=symbol, timeframe=timeframe,
                    data_start=dr.start, data_end=dr.end,
                    error=_safe_error_message(exc),
                ))
                self.strategy_period_data[sid].append({
                    "data_start": dr.start, "data_end": dr.end,
                    "fitness": 0.0, "qualified": False,
                    "total_return": 0.0, "sharpe_ratio": 0.0,
                    "max_drawdown": 0.0, "result_id": "",
                })
                batch_results.append({"strategy_id": sid, "error": _safe_error_message(exc)})

        return _progress_payload(self.steps, step_index, group_key, dr, batch_results)

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


@router.post("/batch-backtest/stream")
async def batch_backtest_stream(
    payload: BatchBacktestRequest,
    db_path: Path = Depends(get_db_path),
    data_dir: Path = Depends(get_data_dir),
):
    """B8.3 流式批：POST 返回 session_id，进度经 socket.io /streams 下发。

    batch 无持久 session（SSE 时代逐请求）——session_id 为本请求生成的
    room 键；取消/断线语义同 verify（B8.0 ①②）。
    """
    from api import realtime, stream_control

    processor = _BatchBacktestProcessor(payload, db_path, data_dir)
    # 并发槽位满即拒绝（429）；init 抛 404 等异常时先归还槽位
    if not stream_control.acquire_stream_slot():
        raise HTTPException(status_code=429, detail="Too many concurrent streams")
    try:
        await asyncio.to_thread(processor.init)
    except BaseException:
        stream_control.release_stream_slot()
        raise
    session_id = f"batch_{uuid.uuid4().hex[:12]}"
    cancel_event = threading.Event()
    stream_control.register(session_id, cancel_event.set)

    async def _run_stream() -> None:
        try:
            for i in range(len(processor.steps)):
                if cancel_event.is_set():
                    if hasattr(processor, "mark_failed"):
                        await asyncio.to_thread(processor.mark_failed, "Cancelled by client")
                    realtime.broadcast("/streams", session_id, "error", {"message": "Cancelled by client"})
                    return
                progress = await asyncio.to_thread(processor.process_step, i)
                realtime.broadcast("/streams", session_id, "progress", progress)
            complete = await asyncio.to_thread(processor.finalize)
            realtime.broadcast("/streams", session_id, "complete", complete)
        except Exception as exc:
            if hasattr(processor, "mark_failed"):
                await asyncio.to_thread(processor.mark_failed, _safe_error_message(exc))
            realtime.broadcast("/streams", session_id, "error", {"message": _safe_error_message(exc)})
        finally:
            stream_control.unregister(session_id)
            stream_control.release_stream_slot()

    asyncio.create_task(_run_stream())
    return {"session_id": session_id}


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
        # 基准对照五键：存库行不含新列（本次无 DB 迁移）→ 恒 None；
        # 键显式带出保证响应形制与 lab 端点一致（None 安全，向后兼容）
        benchmark_return=row.get("benchmark_return"),
        excess_return=row.get("excess_return"),
        alpha=row.get("alpha"),
        market_annual_return=row.get("market_annual_return"),
        profit_factor=row.get("profit_factor"),
    )
