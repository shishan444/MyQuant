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
from api.schemas import SandboxBacktestRequest
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
