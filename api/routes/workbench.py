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


# ─────────────────────────────────────────────────────────────────────
# be-workbench-support（前端原型还原⑥）：D1 进程状态 / D9 假设余量聚合 /
# D11 舰队汇总——观察台支撑三端点。沿 pending-events 先例：只读现算零
# 持久化/响应时点快照/fail-open degraded。
# ─────────────────────────────────────────────────────────────────────

def _now_iso() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()


@router.get("/process-status")
def process_status() -> Dict[str, Any]:
    """D1 进程状态：api.online 恒 true（响应本身即存活证明）；mcp=TCP
    探活 127.0.0.1:{MYQUANT_MCP_PORT 缺省 8800}（mcpserver/server.py:96
    同源环境变量），connect 超时 500ms。探活失败≠端点错误——200 返回
    离线事实（前端呈现离线态）。纯 GET 幂等零副作用。"""
    import os
    import socket
    mcp_online, mcp_detail = True, None
    try:
        # S-1 修复（t3r6-security）：env 解析并入探活 try——非法端口值
        # 走 offline 分支（detail 注明配置非法）而非端点 500（端点自身
        # 5xx 仅组装异常的契约自我一致）
        port = int(os.environ.get("MYQUANT_MCP_PORT", "8800"))
        with socket.create_connection(("127.0.0.1", port), timeout=0.5):
            pass
    except Exception as exc:  # noqa: BLE001——探活失败/配置非法均离线事实
        mcp_online, mcp_detail = False, str(exc)[:200]
    return {
        "api": {"online": True, "detail": None},
        "mcp": {"online": mcp_online, "detail": mcp_detail},
        "generated_at": _now_iso(),
    }


@router.get("/hypothesis-union-remains")
def hypothesis_union_remains(
    db_path=Depends(get_db_path),
    data_dir=Depends(get_data_dir),
) -> Dict[str, Any]:
    """D9 active 假设并集余量聚合：query_union_balance 单源复用全量披露
    （union_exhausted 扫描的余量披露版——exhausted 只报 remaining==0，
    本端点逐区间披露余量数值）。iter_all_pages 全量翻页+bounds_cache
    请求内缓存（与 _union_exhausted_events 同形制）；fail-open per-假设
    degraded 计数。消费方=②观察台假设卡组余量列。遍历骨架经
    _iter_union_balances 共享（QA-1 提取——与 union_exhausted 单源）。"""
    items: List[Dict[str, Any]] = []
    dg = {"count": 0}
    for hyp, dr, bal in _iter_union_balances(db_path, data_dir, dg):
        items.append({
            "hypothesis_id": hyp["hypothesis_id"],
            "symbol": dr["symbol"], "timeframe": dr["timeframe"],
            "remaining_bars": bal.get("remaining_bars"),
            "judged_bars": bal.get("judged_bars"),
        })
    return {"items": items, "degraded": dg["count"],
            "generated_at": _now_iso()}


@router.get("/fleet-summary")
def fleet_summary(db_path=Depends(get_db_path)) -> Dict[str, Any]:
    """D11 舰队汇总（paper-overview 扩形）。

    三契约要素：①口径双分解（active=running 主呈现/all=全部副呈现，
    今日增量仅 active——终态任务无当日 bar 推进）；②当日基点三分支
    （当日新建无当日快照→initial_cash；跨日→当日首条快照；无当日快照
    非当日新建→no_bar_today+基点=最新快照；UTC 00:00 日界）；③公式
    单源（equity=_latest_equity_of import 复用——禁第二份）。
    两合并约束：tasks 行单点复用 _task_response_from（D11 增量字段
    叠加不重写字段映射——保 /trading 同源同值）；runner-status 不并入
    （fleet.active.task_count 取 DB 查询非 scheduler）。funding_sum=
    funding 表 SQL 批量聚合（N+1 禁）；dims 两维现算单源。
    """
    from datetime import datetime, time as dtime, timezone
    from api.routes.paper_trading import (
        _latest_equity_of, _tasks_to_responses)
    from core.paper_trading.qualification import evaluate_two_dimensions
    from core.persistence.repository import paper as paper_repo

    all_rows = paper_repo.list_tasks(db_path, limit=TERMINAL_SCAN_LIMIT)
    running_rows = [r for r in all_rows if r.get("status") == "running"]
    terminal_rows = [r for r in all_rows
                     if r.get("status") in paper_repo.PT_STATUS_TERMINAL]

    # 当日基点计算（UTC 00:00 日界——全库 UTC 约定）
    today = datetime.now(timezone.utc).date()
    day_start = datetime.combine(today, dtime.min, tzinfo=timezone.utc)
    day_start_iso = day_start.isoformat()

    def _equity_at_or_after(task_id: str, since_iso: str):
        row = paper_repo.first_equity_at_or_after(db_path, task_id, since_iso)
        return float(row["equity"]) if row else None

    def _latest_equity(task_id: str):
        row = paper_repo.latest_equity_snapshot(db_path, task_id)
        return float(row["equity"]) if row else None

    # funding_sum SQL 批量（N+1 禁——一次 group by 全任务）
    funding_sums = paper_repo.sum_settled_funding_by_tasks(
        db_path, {r["task_id"] for r in all_rows})

    # TD-B 修复（t3r6）：tasks 行 research_status 走 _tasks_to_responses
    # 批查现算（与 /trading 列表同源同值——原快照列恒传在策略状态随动后
    # 两端口分歧；批查 2 查常数形制同款）
    responses_by_tid = {
        r.task_id: r for r in _tasks_to_responses(db_path, all_rows)}

    degraded = 0
    task_items: List[Dict[str, Any]] = []
    active_equity = 0.0
    active_day_start_total = 0.0
    all_equity_total = 0.0
    for row in all_rows:
        try:
            tid = row["task_id"]
            equity = _latest_equity_of(row)  # 行内合成（任务行当前权益）
            is_running = row.get("status") == "running"
            created = row.get("created_at") or ""
            created_today = created[:10] == today.isoformat()
            day_first = _equity_at_or_after(tid, day_start_iso)
            no_bar_today = False
            if day_first is not None:
                baseline = day_first          # 跨日任务：当日首条快照
            elif created_today:
                baseline = float(row["initial_cash"])  # 当日新建无当日快照
            else:
                no_bar_today = True           # 无当日快照非当日新建
                baseline = _latest_equity(tid) or equity  # 最新快照（无推进）
            day_delta = (equity - baseline) if (
                equity is not None and baseline is not None) else None
            funding_sum = funding_sums.get(tid, 0.0)

            dims = {"pnl_positive": None, "trades_enough": None,
                    "first_met_at": row.get("first_met_at")}
            if equity is not None:
                d2 = evaluate_two_dimensions(
                    equity, float(row["initial_cash"]),
                    int(row.get("total_trades") or 0))
                dims.update(d2)

            # 约束 a：响应构造单点复用（_tasks_to_responses 产物按
            # task_id 索引——model_dump 归一 dict 后叠加 D11 增量字段）
            base = responses_by_tid[tid].model_dump()
            base.update({
                "equity": equity, "day_delta": day_delta,
                "realized_pnl": row.get("total_pnl"),
                "funding_sum": funding_sum, "dims": dims,
                "no_bar_today": no_bar_today,
            })
            task_items.append(base)

            if equity is not None:
                all_equity_total += equity
            if is_running and equity is not None:
                active_equity += equity
                active_day_start_total += (
                    baseline if baseline is not None else equity)
        except Exception as exc:  # noqa: BLE001——fail-open per-task
            degraded += 1
            logger.warning("fleet_summary 跳过 %s: %s",
                           row.get("task_id"), exc)

    # recent_terminal：近终态行（stopped_at desc 截 10——TD-A 修复：
    # 原继承 created_at desc，>10 终态任务时截取集合错位为「最近创建
    # 的终态」非「最近停止的」）
    recent_terminal = [
        {"task_id": r["task_id"], "status": r["status"],
         "stopped_at": r.get("stopped_at"), "total_pnl": r.get("total_pnl")}
        for r in sorted(
            terminal_rows,
            key=lambda r: r.get("stopped_at") or "", reverse=True)[:10]]

    fleet = {
        "active": {
            "equity": active_equity,
            "day_start_equity": active_day_start_total,
            "day_delta": active_equity - active_day_start_total,
            "task_count": len(running_rows),
        },
        "all": {
            "equity": all_equity_total,
            "task_count": len(all_rows),
        },
    }
    return {"fleet": fleet, "tasks": task_items,
            "recent_terminal": recent_terminal,
            "degraded": degraded,
            # TD-C 披露（t3r6）：达扫描上限可能漏扫（CARD-07 哲学——
            # >1000 任务时 fleet 计数为截断值，如实告知非静默）
            "truncated": len(all_rows) >= TERMINAL_SCAN_LIMIT,
            "generated_at": _now_iso()}
