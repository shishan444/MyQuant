"""纸盘任务对外接口（rebuild-paper-trading 5.1，/api/paper-trading/*）。

职能：纸盘交易新域的 REST 端点族（design §7.1 十二端点）——任务
创建/查询/停止/克隆/删除与成交、权益、资金费流水、验证报告、调度器
状态。服务对象：web 交易页与 mcpserver 工具组（经 api_client 白名单）。
职责边界：REST 只发请求不写状态机（唯一写者=调度器/执行器，design
§5.1——stop 经调度器 stop_task 分派、创建只落 pending 行）。

错误族遵守 api-error-contract：422=Pydantic 校验明细数组；404/409 等
4xx=业务语义字符串 detail。创建拒绝面仅两类（上限排队 spec）：404
strategy not found、422 校验数组；受理恒 201（受理≠运行，超限停留
pending 排队）。语义参数（direction/timeframe/信号基因）不在创建参数
面——extra="forbid" 使传入即 422（spec「语义不可覆写」场景）。
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field, field_validator

from api.deps import get_data_dir, get_db_path
from core.data.validation import validate_symbol
from core.paper_trading.reporting import (
    FINAL_STATUSES,
    TaskNotConcludedError,
    build_report,
    get_final_report,
)
from core.persistence.repository import paper
from core.strategy.validator import MAX_LEVERAGE

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/paper-trading", tags=["paper-trading"])


# ── 请求/响应模型（新域内聚本文件；旧 schemas.py 属旧域不动） ──


class PaperTaskCreate(BaseModel):
    """创建纸盘任务（上盘流转：REST 只收 strategy_id，DNA 由后端从策略
    库流转快照——design §11 偏差 2「消灭手工抄写」）。

    window_bars 四形态编码（spec 上盘流转 Requirement）：不传=缺省 180；
    显式 null=不限期（不自动结题）；显式 N≥1=N 根；0/负/非整=422。
    leverage/symbol 缺省取 DNA；timeframe 恒从 DNA 抄录（无参数位）。
    """

    model_config = ConfigDict(extra="forbid")

    strategy_id: str
    initial_cash: float = Field(100_000, gt=0)
    leverage: Optional[int] = Field(None, ge=1, le=MAX_LEVERAGE)
    fee: float = Field(0.001, ge=0)
    slippage: float = Field(0.0005, ge=0)
    symbol: Optional[str] = None
    window_bars: Optional[int] = Field(180, ge=1)
    stop_loss_pct: Optional[float] = Field(None, gt=0, le=1)

    @field_validator("symbol")
    @classmethod
    def _validate_symbol_whitelist(
            cls, v: Optional[str]) -> Optional[str]:
        """symbol 入口白名单（CARD-01①）：用户可控 symbol 直落任务行、
        下游 parquet 路径直拼——接 core/data/validation.validate_symbol
        单源（与写路径 updater 同点），非法 422 校验数组拒绝；None=
        缺省取 DNA 不经此面。"""
        if v is None:
            return v
        return validate_symbol(v)


class PaperTaskResponse(BaseModel):
    task_id: str
    status: str
    strategy_id: str
    symbol: str
    timeframe: str
    initial_cash: float
    fee: float
    slippage: float
    leverage: int
    window_bars: Optional[int] = None
    stop_loss_pct: Optional[float] = None
    bars_processed: int
    last_bar_time: Optional[str] = None
    # 账户运行态组
    balance: Optional[float] = None
    position_side: Optional[str] = None
    position_entry: Optional[float] = None
    position_quantity: Optional[float] = None
    position_margin: Optional[float] = None
    position_open_cost: Optional[float] = None
    position_sl_price: Optional[float] = None
    position_tp_price: Optional[float] = None
    position_trail_peak: Optional[float] = None
    position_entry_bar_seq: Optional[int] = None
    unrealized_pnl: Optional[float] = None
    # 统计组
    total_trades: int
    win_count: int
    loss_count: int
    total_pnl: float
    # 快照与生命周期
    research_status_snapshot: str
    created_at: str
    updated_at: str
    started_at: Optional[str] = None
    stopped_at: Optional[str] = None
    stop_reason: Optional[str] = None
    heartbeat_at: Optional[str] = None
    error_detail: Optional[str] = None


class PaperTaskListResponse(BaseModel):
    tasks: List[PaperTaskResponse]
    total: int


class PaperFillResponse(BaseModel):
    id: int
    task_id: str
    bar_time: str
    seq: int
    kind: str
    side: Optional[str] = None
    price: Optional[float] = None
    quantity: Optional[float] = None
    fee: Optional[float] = None
    realized_pnl: Optional[float] = None
    reason: Optional[str] = None


class PaperFillListResponse(BaseModel):
    fills: List[PaperFillResponse]
    limit: int
    offset: int
    has_more: bool


class PaperEquityResponse(BaseModel):
    id: int
    task_id: str
    bar_time: str
    equity: float
    balance: float
    unrealized_pnl: Optional[float] = None
    position_side: Optional[str] = None
    cumulative_funding: Optional[float] = None


class PaperEquityListResponse(BaseModel):
    snapshots: List[PaperEquityResponse]
    limit: int
    offset: int
    has_more: bool


class PaperFundingResponse(BaseModel):
    id: int
    task_id: str
    settle_time: str
    bar_time: str
    status: str
    rate: Optional[float] = None
    amount: Optional[float] = None
    position_quantity: Optional[float] = None
    settle_price: Optional[float] = None


class PaperFundingListResponse(BaseModel):
    records: List[PaperFundingResponse]
    limit: int
    offset: int
    has_more: bool


class FinalReportResponse(BaseModel):
    task_id: str
    report_version: int
    generated_at: str
    as_of: str
    report: dict


def _task_to_response(row: dict) -> PaperTaskResponse:
    return PaperTaskResponse(
        task_id=row["task_id"], status=row["status"],
        strategy_id=row["strategy_id"], symbol=row["symbol"],
        timeframe=row["timeframe"], initial_cash=row["initial_cash"],
        fee=row["fee"], slippage=row["slippage"],
        leverage=row["leverage"], window_bars=row["window_bars"],
        stop_loss_pct=row["stop_loss_pct"],
        bars_processed=row["bars_processed"],
        last_bar_time=row["last_bar_time"],
        balance=row["balance"], position_side=row["position_side"],
        position_entry=row["position_entry"],
        position_quantity=row["position_quantity"],
        position_margin=row["position_margin"],
        position_open_cost=row["position_open_cost"],
        position_sl_price=row["position_sl_price"],
        position_tp_price=row["position_tp_price"],
        position_trail_peak=row["position_trail_peak"],
        position_entry_bar_seq=row["position_entry_bar_seq"],
        unrealized_pnl=row["unrealized_pnl"],
        total_trades=row["total_trades"], win_count=row["win_count"],
        loss_count=row["loss_count"], total_pnl=row["total_pnl"],
        research_status_snapshot=row["research_status_snapshot"],
        created_at=row["created_at"], updated_at=row["updated_at"],
        started_at=row["started_at"], stopped_at=row["stopped_at"],
        stop_reason=row["stop_reason"], heartbeat_at=row["heartbeat_at"],
        error_detail=row["error_detail"])


def _require_task(db_path: Path, task_id: str) -> dict:
    """任务存在性守门（GET 族/stop/clone/DELETE 共用 404 面）。"""
    row = paper.get_task(db_path, task_id)
    if row is None:
        raise HTTPException(status_code=404, detail="paper task not found")
    return row


def _paged(rows: list, limit: int) -> tuple[list, bool]:
    """limit+1 探测式分页（明细表无 count 面——多取一行判 has_more）。"""
    return rows[:limit], len(rows) > limit


# ── 十二端点（design §7.1 顺序） ──


@router.post("/tasks", response_model=PaperTaskResponse, status_code=201)
def create_task(
    body: PaperTaskCreate,
    db_path: Path = Depends(get_db_path),
) -> PaperTaskResponse:
    """上盘（创建恒 201 返 pending 行——受理≠运行，超限停留 pending
    排队不拒绝；拒绝面仅 404 strategy not found 与 422 校验数组）。"""
    from core.persistence.repository.strategy import get_strategy

    strategy = get_strategy(db_path, body.strategy_id)
    if strategy is None:
        raise HTTPException(status_code=404, detail="strategy not found")

    # DNA 由策略库流转：快照取库内 dna_json 原文（保真不重序列化）；
    # timeframe/symbol 缺省从 execution_genes 抄录、leverage 缺省取
    # risk_genes（任务覆写面 design §7.1 端点 1）
    from core.strategy.dna import StrategyDNA

    dna = StrategyDNA.from_json(strategy["dna_json"])
    task_id = paper.create_task(
        db_path,
        strategy_id=body.strategy_id,
        dna_snapshot_json=strategy["dna_json"],
        symbol=body.symbol or dna.execution_genes.symbol,
        timeframe=dna.execution_genes.timeframe,
        research_status_snapshot=paper.resolve_research_status(strategy),
        initial_cash=body.initial_cash, fee=body.fee,
        slippage=body.slippage,
        leverage=(body.leverage if body.leverage is not None
                  else dna.risk_genes.leverage),
        window_bars=body.window_bars,
        stop_loss_pct=body.stop_loss_pct)
    return _task_to_response(paper.get_task(db_path, task_id))


@router.get("/tasks", response_model=PaperTaskListResponse)
def list_tasks(
    status: Optional[str] = None,
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    db_path: Path = Depends(get_db_path),
) -> PaperTaskListResponse:
    """任务列表（created_at 倒序分页+total 真总数；status 过滤）。"""
    tasks = paper.list_tasks(db_path, status=status, limit=limit,
                             offset=offset)
    total = paper.count_tasks(db_path, status=status)
    return PaperTaskListResponse(
        tasks=[_task_to_response(t) for t in tasks], total=total)


@router.get("/tasks/{task_id}", response_model=PaperTaskResponse)
def get_task(
    task_id: str,
    db_path: Path = Depends(get_db_path),
) -> PaperTaskResponse:
    """任务详情（含运行态组+统计组）。"""
    return _task_to_response(_require_task(db_path, task_id))


@router.post("/tasks/{task_id}/stop", response_model=PaperTaskResponse)
def stop_task(
    task_id: str,
    request: Request,
    db_path: Path = Depends(get_db_path),
) -> PaperTaskResponse:
    """stop 请求三态分派（状态机写者=调度器，REST 只转发）：
    pending→直接 stopped（未拾取撤销）；running→协作停止（bar 边界
    退出，行仍 running 至执行器迁移）；终态→409。"""
    scheduler = getattr(request.app.state, "paper_scheduler", None)
    if scheduler is None:
        raise HTTPException(status_code=503,
                            detail="paper scheduler not available")
    result = scheduler.stop_task(task_id)
    if result == "not_found":
        raise HTTPException(status_code=404, detail="paper task not found")
    if result == "terminal":
        raise HTTPException(
            status_code=409,
            detail=f"task already concluded: "
                   f"{_require_task(db_path, task_id)['status']}")
    return _task_to_response(_require_task(db_path, task_id))


@router.post("/tasks/{task_id}/clone",
             response_model=PaperTaskResponse, status_code=201)
def clone_task(
    task_id: str,
    db_path: Path = Depends(get_db_path),
) -> PaperTaskResponse:
    """任意终态克隆重跑（同 DNA 快照同任务参数新建 pending 行）；
    运行/pending→409；strategy 已删→404（快照无法重算）。"""
    row = _require_task(db_path, task_id)
    if row["status"] not in FINAL_STATUSES:
        raise HTTPException(status_code=409, detail="task not concluded")
    try:
        new_row = paper.clone_task(db_path, task_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return _task_to_response(new_row)


@router.delete("/tasks/{task_id}")
def delete_task(
    task_id: str,
    db_path: Path = Depends(get_db_path),
) -> dict:
    """删除任务（仅终态；五表级联单事务）；运行/pending→409 stop first。"""
    row = _require_task(db_path, task_id)
    if row["status"] not in FINAL_STATUSES:
        raise HTTPException(status_code=409, detail="stop first")
    paper.delete_task(db_path, task_id)
    return {"deleted": True}


@router.get("/tasks/{task_id}/fills", response_model=PaperFillListResponse)
def get_fills(
    task_id: str,
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    db_path: Path = Depends(get_db_path),
) -> PaperFillListResponse:
    """成交流水（bar_time 升序，含 seq/kind/reason 审计字段）。"""
    _require_task(db_path, task_id)
    rows, has_more = _paged(
        paper.list_fills(db_path, task_id, limit=limit + 1, offset=offset),
        limit)
    return PaperFillListResponse(
        fills=[PaperFillResponse(**r) for r in rows],
        limit=limit, offset=offset, has_more=has_more)


@router.get("/tasks/{task_id}/equity", response_model=PaperEquityListResponse)
def get_equity(
    task_id: str,
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    db_path: Path = Depends(get_db_path),
) -> PaperEquityListResponse:
    """权益曲线（bar_time 升序）。"""
    _require_task(db_path, task_id)
    rows, has_more = _paged(
        paper.list_equity_snapshots(
            db_path, task_id, limit=limit + 1, offset=offset),
        limit)
    return PaperEquityListResponse(
        snapshots=[PaperEquityResponse(**r) for r in rows],
        limit=limit, offset=offset, has_more=has_more)


@router.get("/tasks/{task_id}/funding",
            response_model=PaperFundingListResponse)
def get_funding(
    task_id: str,
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    db_path: Path = Depends(get_db_path),
) -> PaperFundingListResponse:
    """资金费明细（settle_time 升序；对账披露面——三态可见）。"""
    _require_task(db_path, task_id)
    rows, has_more = _paged(
        paper.list_funding_records(
            db_path, task_id, limit=limit + 1, offset=offset),
        limit)
    return PaperFundingListResponse(
        records=[PaperFundingResponse(**r) for r in rows],
        limit=limit, offset=offset, has_more=has_more)


@router.get("/tasks/{task_id}/report")
def get_report(
    task_id: str,
    db_path: Path = Depends(get_db_path),
    data_dir: Path = Depends(get_data_dir),
) -> dict:
    """滚动报告（实时计算不落库；任意状态可取——running 中随时取
    当前版，三层退化自解释）。"""
    try:
        return build_report(db_path, data_dir, task_id)
    except ValueError as exc:
        raise HTTPException(status_code=404,
                            detail="paper task not found") from exc


@router.get("/tasks/{task_id}/report/final",
            response_model=FinalReportResponse)
def get_final(
    task_id: str,
    version: Optional[int] = Query(None, ge=1),
    db_path: Path = Depends(get_db_path),
) -> FinalReportResponse:
    """结题固化版：?version 缺省最新版、?version=1 取结题初版（Q-2
    版本机制）；未结题→409 "task not concluded"；终态无固化版→404。"""
    try:
        result = get_final_report(db_path, task_id, version)
    except TaskNotConcludedError as exc:
        raise HTTPException(status_code=409,
                            detail="task not concluded") from exc
    except ValueError as exc:
        raise HTTPException(status_code=404,
                            detail="paper task not found") from exc
    if result is None:
        raise HTTPException(status_code=404,
                            detail="final report not generated")
    return FinalReportResponse(**result)


@router.get("/runner-status")
def runner_status(request: Request) -> dict:
    """调度器状态（活跃任务清单/上限/当前运行数/pending 深度——队列
    可见性面，design §7.1 端点 12）。"""
    scheduler = getattr(request.app.state, "paper_scheduler", None)
    if scheduler is None:
        return {"max_running_tasks": None, "running_count": 0,
                "active_task_ids": [], "pending_depth": 0}
    return scheduler.status()
