"""多周期验证入口（/api/strategies/verify*）。

职能：策略在多日期区间逐段回测，聚合综合评分与星级；含同步端点
与流式端点（经 socket.io /streams 推进度）及历史/会话查询。
服务对象：web 验证页（Verify.tsx）；策略页仅消费 verify_star 字段。
职责边界：区间编排与聚合，单段回测语义与 backtest 一致
（执行体收口 _VerifyProcessor）。
"""
from __future__ import annotations

import json
import logging
import uuid
import asyncio
import threading
from datetime import datetime, timezone

from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query

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
from core.backtest import engine as _bt_engine_mod
from core.services import backtest_service
from core.strategy.dna import StrategyDNA

logger = logging.getLogger(__name__)


router = APIRouter()




def compute_verify_star(avg_fitness: float, qualified_count: int, total_periods: int) -> int:
    """由验证结果计算 1-5 星评级。

    非全区间达标回 0（无星）；阈值按进化策略的回测 Sharpe 分布标定。
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
    """更新策略验证摘要字段（B3d.2 收口 repository）。"""
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
                verified_at=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            )
    except Exception:
        logger.warning("Failed to update verify fields for strategies", exc_info=True)

@router.post("/verify")
def verify_strategies(
    payload: VerifyRequest,
    db_path: Path = Depends(get_db_path),
    data_dir: Path = Depends(get_data_dir),
) -> VerifyResponse:
    """多日期区间验证策略。

    逐 (strategy, date_range) 对回测并计算 fitness；按策略聚合综合
    评分 = avg_fitness × qualified_ratio × consistency_bonus。
    执行体收口 _VerifyProcessor（与流式端点共用）。
    """
    if not payload.data_ranges:
        raise HTTPException(status_code=400, detail="data_ranges cannot be empty")

    # 收口 _VerifyProcessor（A1）：同步端点与流式端点共用同一执行体，
    # 消除约 250 行双份业务（曾漂移：leverage 覆盖双 if 分裂）
    processor = _VerifyProcessor(payload, db_path, data_dir)
    processor.init()  # 无有效策略抛 404（语义保持）
    for i in range(len(processor.steps)):
        processor.process_step(i)
    finalized = processor.finalize()
    return VerifyResponse(
        results=processor.all_results,
        summary=[VerifySummaryItem(**s) for s in finalized["summary"]],
    )



class _VerifyProcessor:
    """验证执行体（同步/流式两入口共用）。每次 process_step() 处理一个 group×range 步。"""

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

        # 预生成步进清单：(group_key, group_strategies, date_range)
        self.steps = [
            (gk, gs, dr)
            for gk, gs in strategies_by_group.items()
            for dr in self.payload.data_ranges
        ]

    def process_step(self, step_index: int) -> Dict:
        """处理一个 group×range 步，返回进度帧数据。"""
        group_key, group_strategies, dr = self.steps[step_index]
        symbol = group_strategies[0]["symbol"]
        timeframe = group_strategies[0]["timeframe"]
        dnas = [s["dna"] for s in group_strategies]
        # 指定杠杆时覆写（深拷贝防污染缓存对象）
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
                return _progress_payload(self.steps, step_index, group_key, dr, batch_results)

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
                        error=_safe_error_message(exc),
                    ))
                    self.strategy_period_data[sid].append({
                        "data_start": dr.start, "data_end": dr.end,
                        "fitness": 0.0, "qualified": False,
                        "total_return": 0.0, "sharpe_ratio": 0.0, "max_drawdown": 0.0,
                    })
                    batch_results.append({"strategy_id": sid, "error": _safe_error_message(exc)})

        except Exception as exc:
            for sid in strategy_ids:
                self.all_results.append(VerifyResultItem(
                    strategy_id=sid, data_start=dr.start, data_end=dr.end,
                    error=_safe_error_message(exc),
                ))
                self.strategy_period_data[sid].append({
                    "data_start": dr.start, "data_end": dr.end,
                    "fitness": 0.0, "qualified": False,
                    "total_return": 0.0, "sharpe_ratio": 0.0, "max_drawdown": 0.0,
                })
                batch_results.append({"strategy_id": sid, "error": _safe_error_message(exc)})

        return _progress_payload(self.steps, step_index, group_key, dr, batch_results)

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
            completed_at=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        )

        # 更新策略验证摘要字段
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
    from api import realtime, stream_control

    processor = _VerifyProcessor(payload, db_path, data_dir)
    # 并发槽位满即拒绝（429）；init 抛 404 等异常时先归还槽位
    if not stream_control.acquire_stream_slot():
        raise HTTPException(status_code=429, detail="Too many concurrent streams")
    try:
        await asyncio.to_thread(processor.init)
    except BaseException:
        stream_control.release_stream_slot()
        raise
    session_id = processor.session_id
    cancel_event = threading.Event()
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
            await asyncio.to_thread(processor.mark_failed, _safe_error_message(exc))
            realtime.broadcast("/streams", session_id, "error", {"message": _safe_error_message(exc)})
        finally:
            stream_control.unregister(session_id)
            stream_control.release_stream_slot()

    asyncio.create_task(_run_stream())
    return {"session_id": session_id}


def _history_items(
    db_path: Path, results: List[Dict[str, Any]],
) -> List[VerifyHistoryItem]:
    """backtest_result 行转历史条目（策略名逐行缓存）。"""
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
    return items


@router.get("/verify/history", response_model=VerifyHistoryResponse)
def get_verify_history(
    strategy_id: Optional[str] = None,
    limit: int = Query(100, ge=1, le=500),
    db_path: Path = Depends(get_db_path),
) -> VerifyHistoryResponse:
    """历史验证结果（backtest_result 按 run_source='verify' 过滤）。"""
    results = list_backtest_results(
        db_path,
        strategy_id=strategy_id,
        run_source="verify",
        limit=limit,
    )
    items = _history_items(db_path, results)
    return VerifyHistoryResponse(items=items, total=len(items))


@router.get("/verify/sessions", response_model=VerifySessionListResponse)
def list_sessions(
    limit: int = Query(20, ge=1, le=500),
    db_path: Path = Depends(get_db_path),
) -> VerifySessionListResponse:
    """验证会话列表（按创建时间序）。"""
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
    """指定验证会话的回测结果。"""
    session = get_verify_session(db_path, session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    results = list_backtest_results(
        db_path,
        run_source="verify",
        session_id=session_id,
        limit=500,
    )
    items = _history_items(db_path, results)
    return VerifyHistoryResponse(items=items, total=len(items))
