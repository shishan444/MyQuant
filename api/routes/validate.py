"""假设/规则验证入口（/api/validate*）。

职能：WHEN/THEN 假设验证与进出场规则评估的 REST 入口，路由层构建
多周期求值上下文（MtfEvalContext）后委托 core.validation 执行。
服务对象：web Lab 验证页。
职责边界：传输校验与 ResearchError→HTTP 映射，判定逻辑在引擎层。
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional, Union

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from api.deps import get_db_path, get_data_dir
from core.analysis.hypothesis_service import ResearchError
from core.validation.engine import validate_hypothesis
from core.validation.rule_engine import evaluate_rules


router = APIRouter(prefix="/api", tags=["validation"])


from api.schemas import IndicatorRef


class ConditionInput(BaseModel):
    """0.2.13 混合形制（与 ConditionItem 同语义）：参数化指标结构化
    IndicatorRef / 基础列字符串精确名。"""
    subject: Union[str, "IndicatorRef"]
    action: str
    target: str = ""
    window: Optional[int] = None
    logic: str = "AND"
    timeframe: Optional[str] = None


class ValidateRequest(BaseModel):
    pair: str
    timeframe: str
    start: str
    end: str
    when: List[ConditionInput]
    then: List[ConditionInput]
    indicator_params: Optional[Dict[str, Any]] = None
    base_timeframe: Optional[str] = None


class TriggerRecordResponse(BaseModel):
    id: int
    time: str
    trigger_price: float
    change_pct: float
    matched: bool
    indicator_values: Dict[str, float] = {}


class ValidateResponse(BaseModel):
    match_rate: float
    total_count: int
    match_count: int
    mismatch_count: int
    triggers: List[TriggerRecordResponse] = []
    distribution: List[Dict[str, Any]] = []
    percentiles: Dict[str, float] = {}
    concentration: Dict[str, Any] = {}
    signal_frequency: Dict[str, float] = {}
    extremes: List[Dict[str, Any]] = []
    warnings: List[str] = []


# ---------------------------------------------------------------------------
# 规则验证模型
# ---------------------------------------------------------------------------

class RuleConditionInput(BaseModel):
    logic: str = "AND"  # "IF", "AND", "OR"
    timeframe: str  # "3d", "4h", "15m" etc.
    subject: str
    action: str
    target: str = ""


class RuleValidateRequest(BaseModel):
    pair: str
    timeframe: str  # 执行周期（池内最短者）
    start: str
    end: str
    entry_conditions: List[RuleConditionInput]
    exit_conditions: List[RuleConditionInput]


class RuleSignalResponse(BaseModel):
    time: str
    price: float
    type: str  # "buy" or "sell"


class TradeRecordResponse(BaseModel):
    entry_time: str
    entry_price: float
    exit_time: str
    exit_price: float
    return_pct: float
    is_win: bool


class RuleValidateResponse(BaseModel):
    buy_signals: List[RuleSignalResponse] = []
    sell_signals: List[RuleSignalResponse] = []
    trades: List[TradeRecordResponse] = []
    total_trades: int = 0
    win_trades: int = 0
    loss_trades: int = 0
    # 零交易为缺失语义（None，46339bc 裁决不折叠为 0）
    win_rate: Optional[float] = None
    total_return_pct: float = 0.0
    avg_return_pct: float = 0.0
    warnings: List[str] = []


@router.post("/validate", response_model=ValidateResponse)
def run_validation(
    payload: ValidateRequest,
    data_dir: Path = Depends(get_data_dir),
) -> ValidateResponse:
    """对历史数据验证 WHEN->THEN 假设。"""
    # 0.2.14：Lab 链掩码投影——路由层构建多周期求值上下文（F2：
    # base_tf=effective_base，与 referenced_tfs 收集同基准）
    from core.analysis.condition_kernel import MtfEvalContext

    mtf_ctx = MtfEvalContext(
        data_dir=data_dir, symbol=payload.pair,
        base_tf=payload.base_timeframe or payload.timeframe)
    try:
        result = validate_hypothesis(
            pair=payload.pair,
            timeframe=payload.timeframe,
            start=payload.start,
            end=payload.end,
            when_conditions=[c.model_dump() for c in payload.when],
            then_conditions=[c.model_dump() for c in payload.then],
            indicator_params=payload.indicator_params,
            data_dir=str(data_dir),
            base_timeframe=payload.base_timeframe,
            mtf=mtf_ctx,
        )
    except ResearchError as exc:
        # 0.2.14：高周期条件结构化拒绝（不可达/warmup 不足/低周期键）
        # 转结构化 422，不裸穿 500
        from api.routes.research.common import research_error_to_http
        raise research_error_to_http(exc) from exc

    return ValidateResponse(
        match_rate=result.match_rate,
        total_count=result.total_count,
        match_count=result.match_count,
        mismatch_count=result.mismatch_count,
        triggers=[
            TriggerRecordResponse(
                id=t.id,
                time=t.time,
                trigger_price=t.trigger_price,
                change_pct=t.change_pct,
                matched=t.matched,
                indicator_values=t.indicator_values,
            )
            for t in result.triggers
        ],
        distribution=[
            {
                "range": [b.range_start, b.range_end],
                "match_count": b.match_count,
                "mismatch_count": b.mismatch_count,
                "total_count": b.total_count,
            }
            for b in result.distribution
        ],
        percentiles=result.percentiles,
        concentration=result.concentration,
        signal_frequency=result.signal_frequency,
        extremes=result.extremes,
        warnings=result.warnings,
    )


@router.post("/validate/rules", response_model=RuleValidateResponse)
def run_rule_validation(
    payload: RuleValidateRequest,
    data_dir: Path = Depends(get_data_dir),
) -> RuleValidateResponse:
    """评估进出场规则条件，返回配对交易。"""
    # 0.2.14：Lab 规则链掩码投影（执行周期=最短周期即基准）
    from core.analysis.condition_kernel import MtfEvalContext

    mtf_ctx = MtfEvalContext(
        data_dir=data_dir, symbol=payload.pair, base_tf=payload.timeframe)
    try:
        result = evaluate_rules(
            symbol=payload.pair,
            timeframe=payload.timeframe,
            start=payload.start,
            end=payload.end,
            entry_conditions=[c.model_dump() for c in payload.entry_conditions],
            exit_conditions=[c.model_dump() for c in payload.exit_conditions],
            data_dir=str(data_dir),
            mtf=mtf_ctx,
        )
    except ResearchError as exc:
        from api.routes.research.common import research_error_to_http
        raise research_error_to_http(exc) from exc

    return RuleValidateResponse(
        buy_signals=[
            RuleSignalResponse(time=s.time, price=s.price, type=s.type)
            for s in result.buy_signals
        ],
        sell_signals=[
            RuleSignalResponse(time=s.time, price=s.price, type=s.type)
            for s in result.sell_signals
        ],
        trades=[
            TradeRecordResponse(
                entry_time=t.entry_time,
                entry_price=t.entry_price,
                exit_time=t.exit_time,
                exit_price=t.exit_price,
                return_pct=t.return_pct,
                is_win=t.is_win,
            )
            for t in result.trades
        ],
        total_trades=result.total_trades,
        win_trades=result.win_trades,
        loss_trades=result.loss_trades,
        # API 契约保持百分比口径（Lab 页直接拼 % 展示）；内部计算层为小数
        win_rate=(result.win_rate * 100) if result.win_rate is not None else None,
        total_return_pct=result.total_return_pct,
        avg_return_pct=result.avg_return_pct,
        warnings=result.warnings,
    )
