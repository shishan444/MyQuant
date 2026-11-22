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

# 终态域扫描上限（CARD-07）：页满即披露 truncated（可能漏扫→显式告知；
# count>limit 严格判定需 count_tasks 扩 status_in——最小面取舍，文案
# 如实说「达上限可能有遗漏」非断言确漏）
TERMINAL_SCAN_LIMIT = 1000


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
    scanned_tasks: int = 0     # 本轮实际扫描终态任务数（CARD-07 披露）
    scanned_running_tasks: int = 0  # 本轮实际扫描运行中任务数（批次三补件）
    truncated: bool = False    # 达扫描上限（终态或运行中任一面）——可能漏扫


def _iter_union_balances(db_path, data_dir, degraded_out: dict):
    """active 假设×区间并集现算共享流（QA-1 提取——t3r6：D9 与
    union_exhausted 原 25 行主体逐字双写，单侧修复必漂移）。

    产 (假设行, 区间 dict, balance dict) 三元组流；per-假设失败即时
    写入 degraded_out["count"]（holder 形制——早断消费方也能读到已
    发生失败数；原函数对象属性形制在早断/多请求下失真，弃）。CARD-01
    全量翻页/CARD-02 bounds_cache「失败不缓存」两形制原样承载。
    """
    from core.analysis.isolation import iter_all_pages
    from core.analysis.validation_service import (
        load_data_bounds, query_union_balance)
    from core.persistence.repository import research as rr
    degraded = 0
    bounds_cache: Dict[tuple, tuple] = {}
    all_active = iter_all_pages(
        lambda off, lim: rr.list_hypotheses(
            db_path, status="active", offset=off, limit=lim, order="asc"))
    for hyp in all_active:
        try:
            # data_ranges 仓储层为 JSON 字符串（探针实测）——先 loads 再迭代
            # （消费方 loads 系既有约定：research/hypotheses.py:142 先例）；
            # 损坏串 loads 失败落入本 except 的 degraded 降级（真源失败降级，
            # 非 TypeError 伪降级——P0 退回件：字符串逐字符迭代必 TypeError）
            ranges = json.loads(hyp.get("data_ranges") or "[]")
            for dr in ranges:
                key = (dr["symbol"], dr["timeframe"])
                if key not in bounds_cache:
                    bounds_cache[key] = load_data_bounds(
                        data_dir, dr["symbol"], dr["timeframe"])
                bal = query_union_balance(
                    db_path, data_dir,
                    symbol=dr["symbol"], timeframe=dr["timeframe"],
                    hypothesis_id=hyp["hypothesis_id"],
                    data_bounds=bounds_cache[key])
                yield hyp, dr, bal
        except Exception as exc:  # noqa: BLE001——fail-open 观察面
            degraded += 1
            degraded_out["count"] = degraded  # 即时写（早断可读）
            logger.warning("union_balance 扫描跳过 %s: %s",
                           hyp.get("hypothesis_id"), exc)


def _union_exhausted_events(db_path, data_dir) -> tuple[List[PendingEvent], int]:
    """并集耗尽：active 态假设逐区间 remaining_bars==0（§7.3 单字段判定式
    ——首命中即报，一假设一事件）。遍历骨架经 _iter_union_balances 共享
    （QA-1 提取后本函数只余消费判定）。"""
    events: List[PendingEvent] = []
    dg = {"count": 0}
    produced: set = set()
    for hyp, dr, bal in _iter_union_balances(db_path, data_dir, dg):
        hid = hyp["hypothesis_id"]
        if hid in produced:
            continue  # 一假设一事件——已报假设的剩余区间跳过，
            # 后续假设继续扫（原 break 仅跳区间循环的语义保全——QA-1
            # 提取时误断整流致第二假设漏判，bounds_cache 用例拦截）
        if bal.get("remaining_bars") == 0:
            events.append(PendingEvent(type="union_exhausted", payload={
                "hypothesis_id": hid,
                "symbol": dr["symbol"], "timeframe": dr["timeframe"],
                "remaining_bars": 0}))
            produced.add(hid)
    return events, dg["count"]


def _qualify_pending_events(db_path) -> tuple[List[PendingEvent], int, int]:
    """达标待停：运行中 pt_task 两维同时满足。

    两维现算（qualification.evaluate_two_dimensions 单源——与 API 层
    qualification_progress 同一函数）；equity=balance+margin+upnl 行内
    同源合成（契约登记表「权益公式」纪律）；first_met_at 直读行。
    返回 (events, degraded, scanned)——running 面同界披露（批次三补件：
    len==TERMINAL_SCAN_LIMIT 时 truncated 由组装段置位，与终态面同款）。
    """
    from core.paper_trading.qualification import evaluate_two_dimensions
    from core.persistence.repository import paper as paper_repo
    events: List[PendingEvent] = []
    degraded = 0
    tasks = paper_repo.list_tasks(
        db_path, status="running", limit=TERMINAL_SCAN_LIMIT)
    for task in tasks:
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
    return events, degraded, len(tasks)


def _trigger_deviation_events(db_path) -> tuple[List[PendingEvent], int, int]:
    """触发差异：终态任务最新报告 qualification.trigger_consistency.passed==False 回看。

    返回 (events, degraded, scanned)——scanned=本轮扫描终态任务数
    （CARD-07 披露：页满=TERMINAL_SCAN_LIMIT 时 truncated 由组装段判定）。
    """
    from core.paper_trading.reporting import get_final_report
    from core.persistence.repository import paper as paper_repo
    events: List[PendingEvent] = []
    degraded = 0
    # 终态域单查（CARD-06）：status_in=PT_STATUS_TERMINAL SQL 下推替
    # 三循环逐态扫描（paper.py:81 派生视图单源——三态字面量消重复）
    tasks = paper_repo.list_tasks(
        db_path, status_in=paper_repo.PT_STATUS_TERMINAL, limit=TERMINAL_SCAN_LIMIT)
    for task in tasks:
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
    return events, degraded, len(tasks)


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
        events_q, deg_q, scanned_q = _qualify_pending_events(db_path)
    except Exception:  # noqa: BLE001——端点整体 fail-open
        events_q, deg_q, scanned_q = [], 1, 0
    if not _qualify_source_available(db_path):
        availability.qualify_pending = "unavailable"
        availability.reason = "C5 判定器源未建（旧库行无 qualification_progress）"

    try:
        events_t, deg_t, scanned_t = _trigger_deviation_events(db_path)
    except Exception:  # noqa: BLE001
        events_t, deg_t, scanned_t = [], 1, 0
    if not _trigger_source_available(db_path):
        availability.trigger_deviation = "unavailable"
        # reason 单字段按需拼接两半说明（architect 卡面——非分源双 reason）
        availability.reason = (
            (availability.reason + "；" if availability.reason else "")
            + "trigger 回看源未建（终态报告无 qualification 键——C5 前旧报告）")

    return PendingEventsResponse(
        events=events_u + events_q + events_t,
        degraded=deg_u + deg_q + deg_t,
        availability=availability,
        scanned_tasks=scanned_t,
        scanned_running_tasks=scanned_q,
        truncated=(scanned_t >= TERMINAL_SCAN_LIMIT
                   or scanned_q >= TERMINAL_SCAN_LIMIT))


def _qualify_source_available(db_path) -> bool:
    """C5 源可用性：first_met_at 列在行（C5 迁移建列；行 dict 含键即库已迁移）。"""
    try:
        from core.persistence.repository import paper as paper_repo
        for task in paper_repo.list_tasks(db_path, limit=1):
            return "first_met_at" in task
        return True  # 无任务行=无从判源（不标降级——空态合法）
    except Exception:  # noqa: BLE001
        return False


def _trigger_source_available(db_path) -> bool:
    """trigger 半边源可用性（workbench-observation CARD-05）：终态任务最新
    报告 qualification 键在场（paper-trading-revamp 组 C 第四键——C5 前
    旧终态报告无此键）。

    三态：无终态任务=空态合法不标；任一最新报告含键=源已建（混合库同构
    qualify 半边「任一行含列即视为已建」）；全部缺键=unavailable。
    """
    try:
        from core.paper_trading.reporting import get_final_report
        from core.persistence.repository import paper as paper_repo
        tasks = paper_repo.list_tasks(
            db_path, status_in=paper_repo.PT_STATUS_TERMINAL,
            limit=TERMINAL_SCAN_LIMIT)
        if not tasks:
            return True  # 无终态任务=空态合法不标
        for task in tasks:
            rep = get_final_report(db_path, task["task_id"])
            if rep is None:
                continue
            report_json = rep.get("report") or {}
            if isinstance(report_json, str):
                report_json = json.loads(report_json)
            if "qualification" in report_json:
                return True  # 任一含键=源已建
        return False  # 全缺键=unavailable
    except Exception:  # noqa: BLE001
        return False
