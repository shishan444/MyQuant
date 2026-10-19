"""流式批回测入口（/api/strategies/batch-backtest*）。

职能：多策略×多区间批回测，流式端点经 socket.io /streams 推送
步级进度。服务对象：web 批量回测页（BatchBacktest.tsx）。
职责边界：任务编排与进度帧，逐步回测在 _BatchBacktestProcessor。
"""
from __future__ import annotations

import copy
import json
import logging
import uuid
import asyncio
import threading

from pathlib import Path
from typing import Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException

from ._common import _progress_payload, _safe_error_message

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
from core.services import backtest_service
from core.strategy.dna import StrategyDNA

logger = logging.getLogger(__name__)


router = APIRouter()


class _BatchBacktestProcessor:
    """批回测处理器（回测引擎口径）。

    经 BacktestEngine.batch_run 执行（Q-1=a 裁决，rebuild-paper-trading
    6.2 迁移）：同组 (N,M) 矩阵单次调用，与单策略回测同引擎同口径。
    """

    def __init__(self, payload: BatchBacktestRequest, db_path: Path, data_dir: Path):
        from core.data.mtf_loader import load_and_prepare_df, load_mtf_data
        from core.scoring.scorer import compute_fitness, RequirementsConfig

        self.payload = payload
        self.db_path = db_path
        self.data_dir = data_dir
        self._load_df = load_and_prepare_df
        self._load_mtf = load_mtf_data
        self._compute_fitness = compute_fitness
        self._RequirementsConfig = RequirementsConfig

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
                # 读库二道校验（recognize-plugin-source-dna T3）：库内
                # DNA 损坏（本机直改库可绕过写入时校验）→422 数据完整
                # 性错误，不静默跳过（HTTPException 经下方前置分支放行）。
                # 防线边界：覆盖「可解析但校验不过」；from_json 解析失败
                # 的更重损毁仍沿既有 except 分支静默跳过（非本防线覆盖）
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
                            "message": "策略含插件信号源，REST 批量回测通道"
                                       "不支持（引擎该通道未接插件信号集，"
                                       "结果将为零交易）——请经研究验证链"
                                       "沙盒回测"})
                group_key = f"{dna.execution_genes.symbol}_{dna.execution_genes.timeframe}"
                strategies_by_group.setdefault(group_key, []).append({
                    "strategy_id": sid, "dna": dna,
                    "symbol": dna.execution_genes.symbol,
                    "timeframe": dna.execution_genes.timeframe,
                    "row": row,
                })
                strategy_names[sid] = row.get("name") or sid[:8]
            except HTTPException:
                raise
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

        # 预生成步进清单：(group_key, group_strategies, date_range)
        self.steps = [
            (gk, gs, dr)
            for gk, gs in strategies_by_group.items()
            for dr in self.payload.data_ranges
        ]

    def _build_equity_json_from_curve(self, equity_curve) -> Optional[str]:
        """引擎权益曲线（Series）按索引直序列化为 JSON（无截尾对齐）。"""
        if equity_curve is None or len(equity_curve) == 0:
            return None
        return json.dumps([
            {"timestamp": str(ts)[:19], "value": round(float(v), 4)}
            for ts, v in equity_curve.items()
        ])

    def _build_signals_json_from_trades(self, trades_df) -> Optional[str]:
        """引擎逐笔记录（vectorbt 列）序列化为 JSON 信号（委托 backtest_service）。"""
        if trades_df is None or len(trades_df) == 0:
            return None
        return json.dumps(backtest_service.build_signals_from_trades_df(trades_df))

    def process_step(self, step_index: int) -> Dict:
        """处理一个 group×range 步（BacktestEngine.batch_run 执行）。"""
        from core.backtest.engine import BacktestEngine

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

            # 杠杆覆写（deepcopy 副本，不污染库内 DNA）→ 同组 dnas 清单
            dnas = []
            for s_info in group_strategies:
                dna = copy.deepcopy(s_info["dna"])
                if self.payload.leverage != 1:
                    dna.risk_genes.leverage = self.payload.leverage
                dnas.append(dna)

            # 同组 (N, M) 矩阵单次调用（进化评估链同款性能底座），
            # 结果与 dnas 同序
            engine = BacktestEngine(
                init_cash=self.payload.init_cash,
                fee=self.payload.fee,
                slippage=self.payload.slippage,
            )
            results = engine.batch_run(
                dnas, enhanced_df, dfs_by_timeframe=dfs_by_timeframe,
            )

            # 逐策略消费（与 dnas 同序；消费级异常单策略隔离）
            for s_info, result in zip(group_strategies, results):
                sid = s_info["strategy_id"]
                try:
                    # 指标直取引擎产物（fallback_metrics=metrics_dict 单源），
                    # win_rate 三态 None 透传（零交易合法缺失）
                    metrics = backtest_service.fallback_metrics(result)

                    score_result = self._compute_fitness(
                        metrics, requirements=self._RequirementsConfig(),
                        liquidated=result.liquidated,
                    )
                    fitness = score_result["fitness"]
                    qualified = score_result["qualified"]

                    result_id = str(uuid.uuid4())

                    equity_curve_json = self._build_equity_json_from_curve(
                        result.equity_curve
                    )
                    signals_json = self._build_signals_json_from_trades(
                        result.trades_df
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
                        total_trades=result.total_trades,
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
                        total_trades=result.total_trades,
                        profit_factor=metrics.get("profit_factor", 0.0),
                        fitness=fitness, qualified=qualified,
                        liquidated=result.liquidated,
                        total_funding_cost=result.total_funding_cost,
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
        """跨区间聚合逐策略摘要。"""
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

            # 由 all_results 组装逐区间结果条目
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
        """批回测无 session 表可更新（no-op）。"""
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
    """取批回测结果详情（含净值曲线与信号）。"""
    row = get_backtest_result(db_path, result_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Backtest result not found")

    # 解析落库的 equity_curve JSON
    equity_data = None
    if row.get("equity_curve"):
        try:
            equity_data = json.loads(row["equity_curve"])
        except (json.JSONDecodeError, TypeError):
            equity_data = None

    # 解析 trades_json 列的信号
    signals_data = None
    if row.get("trades_json"):
        try:
            signals_data = json.loads(row["trades_json"])
        except (json.JSONDecodeError, TypeError):
            signals_data = None

    # 解析 dimension_scores
    dimension_scores = None
    if row.get("dimension_scores"):
        try:
            dimension_scores = json.loads(row["dimension_scores"])
        except (json.JSONDecodeError, TypeError):
            pass

    # 解析 satisfaction
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
