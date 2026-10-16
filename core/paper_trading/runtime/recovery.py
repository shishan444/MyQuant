"""恢复器（recovery——design §5.4）+终态资金费回补例程（tasks 3.5）。

恢复触发：app 启动期扫 status=running（scheduler.startup_recover——
lifespan 挂载段直调，恢复执行器起线程续跑）+运行期心跳超时任务
（调度器 _check_heartbeats 移交）。

恢复校验四判据（任一命中→error+error_detail=recovery_untrusted+保留
数据——「不可信状态不静默续跑」的旧裁决内核）：
1. last_bar_time 为空而 bars_processed>0（游标缺失）；
2. balance 为 NULL（账户态不完整）；
3. 持仓字段组不完整（部分 NULL 部分非 NULL——核心七字段
   side/entry/quantity/margin/open_cost/trail_peak/entry_bar_seq 全有
   或全无；sl_price/tp_price 是开仓时点披露值、语义可空豁免）；
4. pending_intent_json 解析失败。

可信恢复：重建 Ledger+pending 意图+游标，续跑跳过 ≤last_bar_time 的
bar 从次根继续（E3 行为变化显式：现状标 stopped 不续跑）。重放幂等：
恢复后首 bar 与库内最后 fill 同 bar_time→唯一键 OR IGNORE 吸收+游标
判重跳过。

终态后补记（Q-2 封账设计）：backfill_terminal_funding 扫全部任务
（含终态）的 pending 行，parquet 可达则回填 rate/amount 但不动
balance（append-only 封账），status→settled_disclosure_only，触发
报告修订版回调（恒 version=2 重算覆盖——组 4 接）。
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

import pandas as pd

from core.paper_trading.kernel.ledger import funding_amount
from core.paper_trading.runtime.executor import (
    PaperTaskExecutor,
    intents_from_json,
)
from core.paper_trading.runtime.feed import snap_to_funding_grid
from core.persistence.repository import paper as paper_repo

logger = logging.getLogger("paper_runtime")

# 持仓核心字段组（组完整性判据——sl/tp 披露值可空豁免，见模块 docstring）
_POSITION_CORE_FIELDS = (
    "position_side", "position_entry", "position_quantity",
    "position_margin", "position_open_cost", "position_trail_peak",
    "position_entry_bar_seq",
)


def validate_recovery(task_row: Dict[str, Any]) -> Optional[str]:
    """恢复校验四判据（返回 error_detail；None=可信可续跑）。"""
    if (task_row.get("last_bar_time") is None
            and int(task_row.get("bars_processed") or 0) > 0):
        return "游标缺失：bars_processed>0 而 last_bar_time 为空"
    if task_row.get("balance") is None:
        return "账户态不完整：balance 为 NULL"
    non_null = [f for f in _POSITION_CORE_FIELDS
                if task_row.get(f) is not None]
    if 0 < len(non_null) < len(_POSITION_CORE_FIELDS):
        return (f"持仓字段组不完整（{len(non_null)}/"
                f"{len(_POSITION_CORE_FIELDS)} 核心字段非 NULL）")
    try:
        intents_from_json(task_row.get("pending_intent_json"))
    except Exception as exc:
        return f"意图不可解析：{exc}"
    return None


def recover_task(
    db_path: Path,
    task_row: Dict[str, Any],
    **executor_kwargs: Any,
) -> Optional[PaperTaskExecutor]:
    """恢复单任务：校验→error（保留数据）或返回重建执行器（未启动）。

    续跑由调用方（调度器）起工作线程执行 executor.run()——重放幂等
    由游标判重（bar_time≤last_bar_time 跳过）+唯一键 OR IGNORE 兜底。

    Args:
        db_path: research.db 库路径。
        task_row: running 任务行。
        **executor_kwargs: PaperTaskExecutor 构造注入面（registry/
            push/on_concluded）。

    Returns:
        Optional[PaperTaskExecutor]: 可信→重建执行器；不可信→None
        （已写 error=recovery_untrusted）。
    """
    detail = validate_recovery(task_row)
    if detail is not None:
        paper_repo.update_task(
            db_path, task_row["task_id"], status="error",
            error_detail=f"recovery_untrusted: {detail}")
        logger.error(
            "纸盘任务 %s 恢复不可信（保留数据）: %s",
            task_row["task_id"], detail)
        return None
    try:
        return PaperTaskExecutor.from_task_row(
            db_path, task_row, **executor_kwargs)
    except Exception as exc:
        paper_repo.update_task(
            db_path, task_row["task_id"], status="error",
            error_detail=f"recovery_untrusted: 重建失败 {exc}")
        logger.exception(
            "纸盘任务 %s 恢复重建失败", task_row["task_id"])
        return None


def backfill_terminal_funding(
    db_path: Path,
    *,
    data_dir: Path,
    on_revision: Optional[Callable[[str], None]] = None,
) -> Dict[str, int]:
    """终态后资金费回补例程（Q-2 封账设计——调度器守护循环定期调）。

    扫全部任务（含终态）的 pt_funding pending 行：funding 归档可达则
    回填 rate/amount（amount=挂起快照公式：带符号数量×结算价×真实
    费率，kernel.funding_amount 单点）但不动 balance（终态封账
    append-only——结题时点 balance/曲线/指标不重写），status→
    settled_disclosure_only；回填任务触发报告修订版回调（恒
    version=2 重算覆盖+终态后补记披露段——组 4 reporting 接入）。

    Args:
        db_path: research.db 库路径。
        data_dir: funding parquet 目录（必传显式——CARD-05：历史
            路径算术缺省指向不存在的 core/data/market 使回补静默
            死链；装配真值由调用方（scheduler）经 registry.data_dir
            传入，不做推算）。
        on_revision: 报告修订版回调（task_id）。

    Returns:
        Dict[str, int]: {"tasks_backfilled": 回填任务数,
        "rows_backfilled": 回填行数}。
    """
    symbol_indexes: Dict[str, Dict[pd.Timestamp, float]] = {}
    tasks_backfilled = rows_backfilled = 0
    for task in paper_repo.list_tasks(db_path, limit=100_000):
        pendings = [
            f for f in paper_repo.list_funding_records(
                db_path, task["task_id"], limit=100_000)
            if f["status"] == "pending"]
        if not pendings:
            continue
        symbol = task["symbol"]
        if symbol not in symbol_indexes:  # 按任务 symbol 读对应归档
            symbol_indexes[symbol] = _load_funding_index(data_dir, symbol)
        rate_index = symbol_indexes[symbol]
        updates: List[Dict[str, Any]] = []
        for row in pendings:
            point = snap_to_funding_grid(pd.Timestamp(row["settle_time"]))
            rate = rate_index.get(point)
            if rate is None:
                continue
            # 金额公式单点引用 kernel.funding_amount（B1 改接——防
            # 三处漂移：apply_funding/补旧账/终态回补同链路）
            amount = funding_amount(
                row["position_quantity"], row["settle_price"], rate)
            # rows 形制 [{settle_time, rate, amount}]——目标态恒
            # 'settled_disclosure_only' 由仓库接口定死（对质②形制
            # 收口：不传 status）
            updates.append({
                "settle_time": row["settle_time"], "rate": rate,
                "amount": amount})
        if not updates:
            continue
        # 实际回填数（status='pending' 前置——已处理行不命中，重放
        # 幂等：二次例程 filled=0 不重复触发修订）
        filled = paper_repo.backfill_funding_rows(
            db_path, task["task_id"], updates)
        if filled == 0:
            continue
        rows_backfilled += filled
        tasks_backfilled += 1
        if on_revision is not None:
            try:
                on_revision(task["task_id"])
            except Exception:
                logger.exception(
                    "任务 %s 报告修订回调失败（回填已生效）",
                    task["task_id"])
        logger.info(
            "终态后补记：任务 %s 回填 %d 点（未计入账户余额）",
            task["task_id"], len(updates))
    return {"tasks_backfilled": tasks_backfilled,
            "rows_backfilled": rows_backfilled}


def _load_funding_index(
    data_dir: Path, symbol: str,
) -> Dict[pd.Timestamp, float]:
    """读 {symbol}_funding.parquet 建该 symbol 的 8h 网格索引。

    只读既有归档（拉取刷新是 feed 侧节流职责——本例程不做网络 IO）。
    不同 symbol 同结算点费率不同——索引按 symbol 隔离，不跨文件合并。
    """
    from core.data.storage import load_parquet

    index: Dict[pd.Timestamp, float] = {}
    from core.data.validation import validate_symbol

    try:
        validate_symbol(symbol)  # 直拼前命名白名单（CARD-01②）
    except ValueError:
        logger.warning("funding 索引装载跳过非法 symbol: %r", symbol)
        return index  # 单任务坏 symbol 不拖死整例程（其余任务照常回补）
    path = data_dir / f"{symbol}_funding.parquet"
    if not path.exists():
        return index
    try:
        frame = load_parquet(path)
    except Exception:
        logger.warning("funding parquet 读取失败：%s", path)
        return index
    if frame is None or frame.empty:
        return index
    for ts, rate in frame["funding_rate"].items():
        index[snap_to_funding_grid(ts)] = float(rate)
    return index
