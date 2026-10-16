"""验证报告器（rebuild-paper-trading 4.1）：三层组装聚合函数。

报告=数据视图（派生物，非第二事实源——design §5.5；补记历史的权威
事实在 pt_funding 行）：

- 绝对绩效层——compute_metrics 单源引用（core/scoring/metrics.py 回测
  族，与回测报告同族可横向比较，零新公式），六输入由 pt_equity/pt_fill
  组装；「出场类」判据=realized_pnl 非 NULL（kernel 对 close/exit_*/reduce
  全部回填 realized_pnl，与回测引擎 vbt trades 含部分出场记录的口径
  对齐——受控等价层 8.1 前置）；
- 偏差对照层——同策略同任务参数在任务运行区间以回测引擎计算预期成绩
  （run_backtest_on_range 桥；dna_override=deepcopy 快照后写入
  task.leverage——bridge 无 leverage 参数，任务覆写经 DNA 副本传入，
  bridge 零改动）；对照失败（ResearchError）该层标 unavailable 而不使
  整份报告失败；
- 证据强度层——60/5 双阈值（constants 单源）+research_status 快照
  （§9.2 四值，创建时快照透传）+funding 三态两计数。

报告时机：滚动=实时计算（running/pending/终态均可取，不落库）；结题
=completed/stopped/error 固化落 pt_report（error 态保留已产出数据同样
出报告）。终态后补记触发修订版恒 version=2 重算覆盖（design §4.5 版本
机制：不做版本链，补记历史的权威在 pt_funding）。报告结构无
verdict/feasible/recommendation 自动判定字段（考场无观点，D3/D10）。
"""
from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd

from core.analysis.errors import ResearchError
from core.data.timeframes import TF_SECONDS
from core.paper_trading.constants import PAPER_MIN_BARS, PAPER_MIN_TRADES
from core.persistence.repository import paper
from core.persistence.timestamps import utc_now_iso
from core.scoring.metrics import compute_metrics

# 结题终态（固化/修订/取 final 的统一门槛；未结题 → TaskNotConcludedError，
# REST 层组 5 转 409 "task not concluded"）
FINAL_STATUSES: frozenset[str] = frozenset({"completed", "stopped", "error"})

# 偏差对照 warmup 前扩根数（design §6.4 契约值）
DEVIATION_WARMUP_BARS = 200

# diff 关键指标差值键集（design §6.1；差值=纸盘−回测，None 侧透传 None）
_DIFF_KEYS: tuple[str, ...] = (
    "total_return", "sharpe_ratio", "max_drawdown", "win_rate")

# funding「已结算」侧两态（design §6.3 三态：settled=正常/运行中补记已
# 扣款｜pending=挂起｜settled_disclosure_only=终态后回填未扣款——两态
# 证据均已补齐，合计计入 funding_settled_points）
_SETTLED_STATUSES: frozenset[str] = frozenset(
    {"settled", "settled_disclosure_only"})

# 聚合全量拉取上限（窗口级数据量——分页是 REST 流水面，聚合不适用）
_NO_LIMIT = 10**9


class TaskNotConcludedError(ValueError):
    """任务未结题（pending/running）而请求结题固化版——组 5 转 409。"""

    def __init__(self, task_id: str, status: str) -> None:
        super().__init__(
            f"paper task {task_id!r} not concluded (status={status!r})")
        self.task_id = task_id
        self.status = status


def build_report(
    db_path: Path, data_dir: Path, task_id: str, *,
    generated_at: Optional[str] = None,
) -> Dict[str, Any]:
    """滚动报告（实时计算不落库；任意状态可取——running 中随时取当前版）。

    Args:
        db_path: 工程数据库路径。
        data_dir: 行情数据目录（偏差对照层装载回测区间用）。
        task_id: 纸盘任务 ID。
        generated_at: 报告生成时点（缺省当前 UTC；测试注入定值）。

    Returns:
        三层结构报告 dict（design §6.1 schema）。

    Raises:
        ValueError: 任务不存在。
    """
    task = _require_task(db_path, task_id)
    return _assemble_report(
        db_path, data_dir, task, generated_at or utc_now_iso())


def finalize_report(
    db_path: Path, data_dir: Path, task_id: str, *,
    generated_at: Optional[str] = None,
) -> Dict[str, Any]:
    """结题固化（version=1）：三终态时生成完整报告落 pt_report。

    调用方=调度器/执行器在终态迁移点（组 3 接线）；error 态保留已产出
    数据同样出报告。

    Raises:
        ValueError: 任务不存在。
        TaskNotConcludedError: 任务未结题。
    """
    task = _require_final(_require_task(db_path, task_id))
    ts = generated_at or utc_now_iso()
    report = _assemble_report(db_path, data_dir, task, ts)
    paper.save_report(
        db_path, task_id, report_version=1, generated_at=ts, as_of=ts,
        report_json=json.dumps(report, ensure_ascii=False))
    return report


def regenerate_final_report(
    db_path: Path, data_dir: Path, task_id: str, *,
    generated_at: Optional[str] = None,
) -> Dict[str, Any]:
    """终态后补记触发的修订版（design §4.5：恒 version=2 重算覆盖）。

    纯聚合重跑零副作用：全量重查三明细表（内容自然累积全部已补记点），
    OR REPLACE 覆盖 version=2 行（多次触发不增版）；as_of=重算时点；
    终态后补记披露段=evidence.funding_post_terminal_backfill（初版恒
    None）。终态后补记不改成绩（不动 balance/曲线），v1/v2 的
    performance 层逐键相同——报告由此自解释「为何存在 v2 而数字未变」。

    Raises:
        ValueError: 任务不存在。
        TaskNotConcludedError: 任务未结题。
    """
    task = _require_final(_require_task(db_path, task_id))
    ts = generated_at or utc_now_iso()
    report = _assemble_report(db_path, data_dir, task, ts)
    paper.save_report(
        db_path, task_id, report_version=2, generated_at=ts, as_of=ts,
        report_json=json.dumps(report, ensure_ascii=False))
    return report


def get_final_report(
    db_path: Path, task_id: str, version: Optional[int] = None,
) -> Optional[Dict[str, Any]]:
    """取结题固化版：version 缺省最新版、显式取该版（?version=1 初版）。

    Returns:
        {task_id, report_version, generated_at, as_of, report}；终态而
        无固化版返回 None（结题即固化为调度器契约，此处不代偿补算）。

    Raises:
        ValueError: 任务不存在。
        TaskNotConcludedError: 任务未结题（组 5 转 409）。
    """
    task = _require_final(_require_task(db_path, task_id))
    row = paper.get_report(db_path, task["task_id"], version)
    if row is None:
        return None
    return {
        "task_id": row["task_id"],
        "report_version": row["report_version"],
        "generated_at": row["generated_at"],
        "as_of": row["as_of"],
        "report": json.loads(row["report_json"]),
    }


# ── 内部组装 ──────────────────────────────────────────────


def _require_task(db_path: Path, task_id: str) -> Dict[str, Any]:
    task = paper.get_task(db_path, task_id)
    if task is None:
        raise ValueError(f"paper task not found: {task_id!r}")
    return task


def _require_final(task: Dict[str, Any]) -> Dict[str, Any]:
    if task["status"] not in FINAL_STATUSES:
        raise TaskNotConcludedError(task["task_id"], task["status"])
    return task


def _assemble_report(
    db_path: Path, data_dir: Path, task: Dict[str, Any],
    generated_at: str,
) -> Dict[str, Any]:
    """三层组装（全量读三明细表→三层→顶层 schema）。"""
    tid = task["task_id"]
    equity_rows = paper.list_equity_snapshots(db_path, tid, limit=_NO_LIMIT)
    fill_rows = paper.list_fills(db_path, tid, limit=_NO_LIMIT)
    funding_rows = paper.list_funding_records(db_path, tid, limit=_NO_LIMIT)
    # 出场类=realized_pnl 已回填行（list_fills 按 bar_time+seq 升序，
    # trade_returns 顺序稳定）
    exit_fills = [f for f in fill_rows if f["realized_pnl"] is not None]

    performance, paper_side = _performance_layer(
        task, equity_rows, exit_fills)
    evidence = _evidence_layer(task, equity_rows, exit_fills, funding_rows)
    deviation = _deviation_layer(
        db_path, data_dir, task, equity_rows, paper_side, evidence)
    return {
        "task_id": tid,
        "strategy_id": task["strategy_id"],
        "status": task["status"],
        "generated_at": generated_at,
        "performance": performance,
        "deviation": deviation,
        "evidence": evidence,
    }


def _bars_per_year(timeframe: str) -> int:
    """年化换算（design §6.2）：365×86400/tf_seconds，TF_SECONDS 单源。

    4h=2190 恰为 compute_metrics 缺省档（等值锚）；非 4h 档由本函数显式
    换算传入（1h=8760），否则年化错周期。timeframe 从 DNA 抄录（上游
    validate_dna 把关），非法档 KeyError 直炸不设回退。
    """
    return 365 * 86400 // TF_SECONDS[timeframe]


def _performance_layer(
    task: Dict[str, Any],
    equity_rows: List[Dict[str, Any]],
    exit_fills: List[Dict[str, Any]],
) -> tuple[Dict[str, Any], Dict[str, Any]]:
    """绝对绩效层：compute_metrics 六输入组装（公式单源，零新公式）。

    Returns:
        (performance, paper_side)——paper_side 供偏差对照层 diff 计算
        （total_return 为区间口径直算，compute_metrics 不返回该键）。
    """
    # DatetimeIndex 对齐回测口径（monthly_consistency 的 resample 需要）
    equity_curve = pd.Series(
        [float(r["equity"]) for r in equity_rows],
        index=pd.to_datetime([r["bar_time"] for r in equity_rows]),
        dtype=float,
    )
    n_closed = len(exit_fills)
    pnls = [float(f["realized_pnl"]) for f in exit_fills]
    trade_win_rate = (
        sum(1 for p in pnls if p > 0) / n_closed if n_closed else None)
    trade_returns = (
        np.array([p / task["initial_cash"] for p in pnls], dtype=float)
        if n_closed else None)
    metrics = compute_metrics(
        equity_curve,
        total_trades=n_closed,
        bars_per_year=_bars_per_year(task["timeframe"]),
        trade_win_rate=trade_win_rate,
        trade_returns=trade_returns,
        # benchmark 由偏差对照层供——本层不填（design §6.2）
    )
    total_return = (
        float(equity_curve.iloc[-1] / equity_curve.iloc[0]) - 1
        if len(equity_curve) >= 2 else None)
    paper_side = {
        "total_return": total_return,
        "sharpe_ratio": metrics["sharpe_ratio"],
        "max_drawdown": metrics["max_drawdown"],
        "win_rate": metrics["win_rate"],
    }
    return metrics, paper_side


def _deviation_layer(
    db_path: Path,
    data_dir: Path,
    task: Dict[str, Any],
    equity_rows: List[Dict[str, Any]],
    paper_side: Dict[str, Any],
    evidence: Dict[str, Any],
) -> Dict[str, Any]:
    """偏差对照层（design §6.4）：任务区间回测预期成绩+diff+归因披露。

    对照失败（ResearchError——数据不足/warmup 不足）该层标 unavailable
    （backtest=null+unavailable_reason），整份报告不失败。
    """
    notes = _attribution_notes(evidence)
    empty_diff = {k: None for k in _DIFF_KEYS}
    started_at = task["started_at"]
    if started_at is None or not equity_rows:
        return {
            "backtest": None,
            "diff": empty_diff,
            "unavailable_reason": "任务尚未开始或无权益数据，无对照区间",
            "attribution_notes": notes,
        }

    # start=任务 started_at 所在 bar（floor 到周期网格——bridge 按
    # index >= start 切区间，对齐到所在 bar 开盘时刻才包含该 bar）；
    # end=当前最后 bar（滚动）/结题 bar（终态）——同为 pt_equity 末行
    tf_seconds = TF_SECONDS[task["timeframe"]]
    start = pd.Timestamp(started_at).floor(f"{tf_seconds}s").isoformat()
    end = equity_rows[-1]["bar_time"]

    from core.analysis.backtest_bridge import run_backtest_on_range
    from core.strategy.dna import StrategyDNA

    # 杠杆缝隙处理（design §6.4）：bridge 无 leverage 参数，回测引擎读
    # DNA risk_genes.leverage——deepcopy 快照后写入任务覆写值，bridge 零改动
    dna_override = deepcopy(
        StrategyDNA.from_dict(json.loads(task["dna_snapshot_json"])))
    dna_override.risk_genes.leverage = int(task["leverage"])

    try:
        bt_metrics, _returns, _warmup, _bars = run_backtest_on_range(
            db_path, data_dir, dna_override,
            task["symbol"], task["timeframe"], start, end,
            DEVIATION_WARMUP_BARS,
            init_cash=task["initial_cash"], fee=task["fee"],
            slippage=task["slippage"],
        )
    except ResearchError as exc:
        return {
            "backtest": None,
            "diff": empty_diff,
            "unavailable_reason": f"{exc.code}: {exc.message}",
            "attribution_notes": notes,
        }

    diff = {
        key: (
            None if paper_side[key] is None or bt_metrics.get(key) is None
            else float(paper_side[key] - bt_metrics[key]))
        for key in _DIFF_KEYS
    }
    return {
        "backtest": bt_metrics,
        "diff": diff,
        "unavailable_reason": None,
        "attribution_notes": notes,
    }


def _attribution_notes(evidence: Dict[str, Any]) -> List[str]:
    """归因披露固定四项（design §6.4：两引擎已知口径差+挂起补记披露）。"""
    settled = evidence["funding_settled_points"]
    pending = evidence["funding_pending_points"]
    return [
        "资金费：纸盘按真实资金费率逐点结算，回测按固定 0.001 估算",
        "清算：纸盘按仓位口径逐 bar 分层判定，回测为无杠杆曲线等效线",
        "对照区间边界：回测按 bar 闭合序列取区间，纸盘按任务起止 bar",
        (f"资金费挂起补记：{settled} 点已补记、"
         f"{pending if pending is not None else 0} 点待补"
         "（补记时点前权益略高）"),
    ]


def _evidence_layer(
    task: Dict[str, Any],
    equity_rows: List[Dict[str, Any]],
    exit_fills: List[Dict[str, Any]],
    funding_rows: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """证据强度层：60/5 双阈值+研究状态快照+funding 三态计数。

    insufficient_evidence = bars_run<60 OR closed_trades<5（任一命中即
    整份报告标注；position_bars 共同披露但不参与判定）。
    """
    bars_run = len(equity_rows)
    closed_trades = len(exit_fills)
    entry_seq = task["position_entry_bar_seq"]
    position_bars = (
        task["bars_processed"] - entry_seq if entry_seq is not None else 0)
    settled = sum(
        1 for r in funding_rows if r["status"] in _SETTLED_STATUSES)
    pending = sum(1 for r in funding_rows if r["status"] == "pending")
    # 终态后补记披露段（design §6.3）：N 点、合计金额 M——未计入账户余额
    disclosure = [
        r for r in funding_rows if r["status"] == "settled_disclosure_only"]
    backfill = (
        {"points": len(disclosure),
         "total_amount": float(sum(r["amount"] or 0.0 for r in disclosure))}
        if disclosure else None)
    return {
        "bars_run": bars_run,
        "closed_trades": closed_trades,
        "position_bars": position_bars,
        "insufficient_evidence": (
            bars_run < PAPER_MIN_BARS or closed_trades < PAPER_MIN_TRADES),
        "research_status": task["research_status_snapshot"],
        "funding_settled_points": settled,
        # 无 funding 行=None（「无资金费数据」与「0 笔挂起」语义区分）
        "funding_pending_points": pending if funding_rows else None,
        "funding_post_terminal_backfill": backfill,
    }
