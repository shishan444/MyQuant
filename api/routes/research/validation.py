"""沙盒回测端点（retire-legacy-subsystems 任务 5.2——旧验证六端点退役：
validate-backtest/staged-validation/random-window-test/validation report/
translate/union-balance 随旧编排与假设链退役，唯 backtest-sandbox 保留
——快速试错统一流，验证记录唯一生产写入通道）。"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Dict

from fastapi import APIRouter, Depends

from api.deps import get_data_dir, get_db_path
from api.schemas import ReplayBacktestRequest, SandboxBacktestRequest
from core.analysis import validation_service as vs

logger = logging.getLogger(__name__)
router = APIRouter()

from api.routes.research.common import (
    require_halt_drawdown_pct,
    require_known_timeframe,
    require_task_cap_params,
)


@router.post("/backtest-sandbox")
def backtest_sandbox(
    payload: SandboxBacktestRequest,
    db_path: Path = Depends(get_db_path),
    data_dir: Path = Depends(get_data_dir),
) -> Dict[str, Any]:
    """沙盒回测：快速试错（可调 fee/slippage/init_cash），直返指标，
    不判门槛、不构成验证证据；每次执行同步写入验证记录统一流
    （validation_records level=sandbox_backtest 行，verdict 恒空串；
    响应回显 record_id）；强制落研究日志（挂假设区间进判定并集）。"""
    require_known_timeframe(payload.timeframe, "timeframe")
    # 任务级两参值域校验（D1：越界结构化 422 附 legal_range——
    # 硬约束 3x/60% 进参数域封顶）＋熔断阈值校验（D3①）
    require_task_cap_params(payload.leverage, payload.margin_cap)
    require_halt_drawdown_pct(payload.halt_drawdown_pct)
    return vs.run_sandbox_backtest(
        db_path, data_dir,
        strategy_id=payload.strategy_id,
        symbol=payload.symbol, timeframe=payload.timeframe,
        start=payload.start, end=payload.end,
        fee=payload.fee, slippage=payload.slippage,
        init_cash=payload.init_cash,
        hypothesis_id=payload.hypothesis_id,
        leverage=(int(payload.leverage)
                  if payload.leverage is not None else None),
        margin_cap=payload.margin_cap,
        halt_drawdown_pct=payload.halt_drawdown_pct)


def _replay_record_and_response(
    db_path: Path, payload: ReplayBacktestRequest, result: Any,
) -> Dict[str, Any]:
    """单路回放结果落记录+构造响应 dict（单路与并发组每路共用——
    沿单路语义：level=replay 行 hypothesis_id 缺省 adhoc、verdict
    恒空串；range_hash 掺参数全集+record_id——每次执行恒新；fail-
    loud（写失败整体报错，禁止记录缺而指标照常返回））。"""
    import hashlib
    import json as _json
    import uuid as _uuid

    from core.persistence.repository import research as rr

    record_id = f"rec_{_uuid.uuid4().hex[:12]}"
    record_params: Dict[str, Any] = {
        "symbol": result.symbol, "timeframe": result.timeframe,
        "start": result.start, "end": result.end,
        "fee": payload.fee, "slippage": payload.slippage,
        "init_cash": payload.initial_cash, "leverage": payload.leverage}
    rr.save_sandbox_validation_record(
        db_path,
        record_id=record_id,
        hypothesis_id="adhoc",
        strategy_id=result.strategy_id,
        params=_json.dumps(record_params, ensure_ascii=False),
        result=_json.dumps({
            "replay": {
                "task_id": result.task_id, "status": result.status,
                "stop_reason": result.stop_reason,
                "bars_processed": result.bars_processed,
                "total_trades": result.total_trades,
                "total_pnl": result.total_pnl,
                "final_balance": result.final_balance,
                "final_equity": result.final_equity}},
            ensure_ascii=False),
        range_hash=hashlib.sha256(_json.dumps(
            {**record_params, "record_id": record_id},
            sort_keys=True).encode()).hexdigest(),
        level="replay")
    return {
        "record_id": record_id,
        "task_id": result.task_id,
        "strategy_id": result.strategy_id,
        "symbol": result.symbol, "timeframe": result.timeframe,
        "start": result.start, "end": result.end,
        "status": result.status, "stop_reason": result.stop_reason,
        "bars_processed": result.bars_processed,
        "last_bar_time": result.last_bar_time,
        "win_count": result.win_count, "loss_count": result.loss_count,
        "total_trades": result.total_trades,
        "total_pnl": result.total_pnl,
        "final_balance": result.final_balance,
        "final_equity": result.final_equity,
        "halted_at": result.halted_at, "first_met_at": result.first_met_at,
    }


@router.post("/backtest-replay")
def backtest_replay(
    payload: ReplayBacktestRequest,
    db_path: Path = Depends(get_db_path),
    data_dir: Path = Depends(get_data_dir),
) -> Dict[str, Any]:
    """逐根回放回测（add-replay-backtest-mcp）：现役回测入口——
    底层 ReplayRunner（core/replay，纸盘执行核心逐根回放，与真实
    交易同一套执行逻辑）；symbol/timeframe 从策略 DNA 抄录不可
    覆写；费滑杠杆资金为任务覆写面。响应携带 ReplayResult summary
    组与明细引用（task_id+db 经既有 pt_* 查询通道）。装载失败
    （策略不存在/数据缺/warmup 不足/series 档等）结构化 4xx。
    并发组（P2 补面）：strategy_ids 多值（≥2）经进程池并发回放
    （每路独立实例独立 task_id、响应 results 列表每路一 record
    行；单值/缺省走 strategy_id 直调不进池）；max_workers 并发
    上限缺省 5 可配。"""
    from core.replay import ReplayError, ReplayRunner, run_replay_batch

    require_halt_drawdown_pct(payload.halt_drawdown_pct)
    # 并发组矛盾拒绝：task_id 是单路固定注入面，每路独立 task_id
    # 语义下不可共享（防静默丢参——传了即拒非忽略）
    if payload.strategy_ids and payload.task_id is not None:
        from core.analysis.errors import ResearchError

        raise ResearchError(
            "RESEARCH_PARAM_INVALID",
            "并发组（strategy_ids）不支持 task_id——每路独立生成",
            status=422)
    batch_ids = (
        payload.strategy_ids
        if payload.strategy_ids and len(payload.strategy_ids) >= 2
        else None)
    single_id = (
        payload.strategy_ids[0]
        if payload.strategy_ids and len(payload.strategy_ids) == 1
        else payload.strategy_id)
    try:
        if batch_ids is not None:
            results = run_replay_batch(
                db_path, batch_ids, payload.start, payload.end,
                data_dir=data_dir,
                fee=payload.fee, slippage=payload.slippage,
                initial_cash=payload.initial_cash,
                leverage=payload.leverage,
                window_bars=payload.window_bars,
                stop_loss_pct=payload.stop_loss_pct,
                halt_drawdown_pct=payload.halt_drawdown_pct,
                max_workers=(
                    payload.max_workers
                    if payload.max_workers is not None else 5))
        else:
            results = [ReplayRunner(data_dir).run(
                db_path, single_id, payload.start, payload.end,
                fee=payload.fee, slippage=payload.slippage,
                initial_cash=payload.initial_cash,
                leverage=payload.leverage,
                window_bars=payload.window_bars,
                stop_loss_pct=payload.stop_loss_pct,
                halt_drawdown_pct=payload.halt_drawdown_pct,
                task_id=payload.task_id)]
    except ReplayError as exc:
        # ReplayError -> ResearchError 重抛：未捕获冒泡至全局 handler
        # 经 research_error_to_http 单源转结构化 4xx（code/message/
        # detail）；STRATEGY_NOT_FOUND 语义映射 404（沿 REST 惯例），
        # 其余装载类拒绝 422
        from core.analysis.errors import ResearchError

        status = 404 if exc.code == "STRATEGY_NOT_FOUND" else 422
        raise ResearchError(
            exc.code, str(exc), status=status, detail=exc.detail) from exc
    # 单路/并发组每路统一经 _replay_record_and_response（level=replay
    # 记录行+响应 dict 共用——fail-loud 与 adhoc 语义沿单路不变）
    if batch_ids is not None:
        return {"results": [
            _replay_record_and_response(db_path, payload, r)
            for r in results]}
    return _replay_record_and_response(db_path, payload, results[0])
