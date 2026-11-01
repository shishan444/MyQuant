"""GET /api/workbench/pending-events 待裁决事件聚合端点（workbench-observation 组 B）。

01 页 B1 区唯一数据源：只读现算零持久化（事件=派生视图，权威在各自源）。
三类事件+fail-open degraded+C5 判定器降级 availability 标注。
"""
from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel

from api.deps import get_db_path, get_data_dir

logger = logging.getLogger("workbench")

router = APIRouter(prefix="/api/workbench", tags=["workbench"])


class PendingEvent(BaseModel):
    type: str
    # 载荷字段按事件类型不同（union_exhausted/qualify_pending/trigger_deviation）
    # ——dict 松形制（观察面聚合，不立三套模型）
    payload: Dict[str, Any]


class Availability(BaseModel):
    qualify_pending: Optional[str] = None   # None=可用；"unavailable"+reason=源缺失
    trigger_deviation: Optional[str] = None
    reason: Optional[str] = None


class PendingEventsResponse(BaseModel):
    events: List[PendingEvent]
    degraded: int = 0          # 单源失败跳过计数（fail-open 观察面）
    availability: Availability = Availability()


def _union_exhausted_events(db_path, data_dir) -> tuple[List[PendingEvent], int]:
    """并集耗尽：active 态假设逐个 query_union_balance，remaining_bars==0（§7.3 单字段判定式）。"""
    from core.analysis.validation_service import query_union_balance
    from core.persistence.repository import research as rr
    events: List[PendingEvent] = []
    degraded = 0
    for hyp in rr.list_hypotheses(db_path, status="active"):
        try:
            # data_ranges 仓储层为 JSON 字符串（探针实测）——先 loads 再迭代
            # （消费方 loads 系既有约定：research/hypotheses.py:142 先例）；
            # 损坏串 loads 失败落入本 except 的 degraded 降级（真源失败降级，
            # 非 TypeError 伪降级——P0 退回件：字符串逐字符迭代必 TypeError）
            ranges = json.loads(hyp.get("data_ranges") or "[]")
            for dr in ranges:
                bal = query_union_balance(
                    db_path, data_dir,
                    symbol=dr["symbol"], timeframe=dr["timeframe"],
                    hypothesis_id=hyp["hypothesis_id"])
                if bal.get("remaining_bars") == 0:
                    events.append(PendingEvent(type="union_exhausted", payload={
                        "hypothesis_id": hyp["hypothesis_id"],
                        "symbol": dr["symbol"], "timeframe": dr["timeframe"],
                        "remaining_bars": 0}))
                    break  # 一假设一事件（首个耗尽区间即报）
        except Exception as exc:  # noqa: BLE001——fail-open 观察面
            degraded += 1
            logger.warning("union_exhausted 扫描跳过 %s: %s",
                           hyp.get("hypothesis_id"), exc)
    return events, degraded


def _qualify_pending_events(db_path) -> tuple[List[PendingEvent], int]:
    """达标待停：运行中 pt_task 两维同时满足。

    两维现算（qualification.evaluate_two_dimensions 单源——与 API 层
    qualification_progress 同一函数）；equity=balance+margin+upnl 行内
    同源合成（契约登记表「权益公式」纪律）；first_met_at 直读行。
    """
    from core.paper_trading.qualification import evaluate_two_dimensions
    from core.persistence.repository import paper as paper_repo
    events: List[PendingEvent] = []
    degraded = 0
    for task in paper_repo.list_tasks(db_path, status="running", limit=1000):
        try:
            if task.get("balance") is None:
                continue  # 初启未跑 bar
            equity = (float(task["balance"])
                      + float(task.get("position_margin") or 0.0)
                      + float(task.get("unrealized_pnl") or 0.0))
            dims = evaluate_two_dimensions(
                equity, float(task["initial_cash"]), int(task["total_trades"]))
            if dims["pnl_positive"] and dims["trades_enough"]:
                events.append(PendingEvent(type="qualify_pending", payload={
                    "task_id": task["task_id"],
                    "strategy_id": task["strategy_id"],
                    "first_met_at": task.get("first_met_at"),
                    "pnl_positive": True, "trades_enough": True}))
        except Exception as exc:  # noqa: BLE001
            degraded += 1
            logger.warning("qualify_pending 扫描跳过 %s: %s",
                           task.get("task_id"), exc)
    return events, degraded


def _trigger_deviation_events(db_path) -> tuple[List[PendingEvent], int]:
    """触发差异：终态任务最新报告 qualification.trigger_consistency.passed==False 回看。"""
    from core.paper_trading.reporting import get_final_report
    from core.persistence.repository import paper as paper_repo
    events: List[PendingEvent] = []
    degraded = 0
    for status_t in ("completed", "stopped", "error"):
        for task in paper_repo.list_tasks(db_path, status=status_t, limit=1000):
            try:
                rep = get_final_report(db_path, task["task_id"])
                if rep is None:
                    continue
                report_json = rep.get("report") or {}
                if isinstance(report_json, str):
                    report_json = json.loads(report_json)
                qual = report_json.get("qualification") or {}
                tc = qual.get("trigger_consistency") or {}
                if tc.get("passed") is False:  # 判据边界：passed=None 不产
                    events.append(PendingEvent(type="trigger_deviation", payload={
                        "task_id": task["task_id"],
                        "strategy_id": task["strategy_id"],
                        "deviation_ratio": tc.get("deviation_ratio")}))
            except Exception as exc:  # noqa: BLE001
                degraded += 1
                logger.warning("trigger_deviation 扫描跳过 %s: %s",
                               task.get("task_id"), exc)
    return events, degraded


@router.get("/pending-events", response_model=PendingEventsResponse)
def pending_events(
    request: Request,
    db_path=Depends(get_db_path),
    data_dir=Depends(get_data_dir),
) -> PendingEventsResponse:
    """全库现算聚合（响应时点快照；处理即消隐=前端据动作结果重拉）。"""
    availability = Availability()

    events_u, deg_u = _union_exhausted_events(db_path, data_dir)

    # C5 判定器源可用性探测：qualification_progress 键在运行任务面缺席=降级
    try:
        events_q, deg_q = _qualify_pending_events(db_path)
    except Exception:  # noqa: BLE001——端点整体 fail-open
        events_q, deg_q = [], 1
    if not _qualify_source_available(db_path):
        availability.qualify_pending = "unavailable"
        availability.reason = "C5 判定器源未建（旧库行无 qualification_progress）"

    try:
        events_t, deg_t = _trigger_deviation_events(db_path)
    except Exception:  # noqa: BLE001
        events_t, deg_t = [], 1

    return PendingEventsResponse(
        events=events_u + events_q + events_t,
        degraded=deg_u + deg_q + deg_t,
        availability=availability)


def _qualify_source_available(db_path) -> bool:
    """C5 源可用性：first_met_at 列在行（C5 迁移建列；行 dict 含键即库已迁移）。"""
    try:
        from core.persistence.repository import paper as paper_repo
        for task in paper_repo.list_tasks(db_path, limit=1):
            return "first_met_at" in task
        return True  # 无任务行=无从判源（不标降级——空态合法）
    except Exception:  # noqa: BLE001
        return False
