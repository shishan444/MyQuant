"""纸盘达标判定器（paper-trading-revamp 组 B/C/D）。

四个子件（design §4.1）：
- 子件一 运行期两维实时：executor 每 bar 求值（append_bar 同事务），
  查询面=PaperTaskResponse.qualification_progress。
- 子件二 停止时三维终判：终态化路径挂点，report_json 第四键。
- 子件三 对照重放器：求值重放+对齐谓词 v2（见本文件 replay 段）。
- 子件四 凭证签发接线：qualified=true → lifecycle.transit。

QUALIFICATION_PROFILE 与研究域 DEFAULT_PROFILE（thresholds.py）语义分立：
数值巧合相同（0 与 5），但 REQ-4-01 判据为纸盘达标域判据、
DEFAULT_PROFILE 为研究验证域门槛——本模块自有常量，禁 import 研究域
profile（跨域引用会把研究域门槛变更误传导给纸盘达标）。
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Optional

# ---------------------------------------------------------------------------
# 常量（自有，语义分立于 core/analysis/thresholds.py DEFAULT_PROFILE）
# ---------------------------------------------------------------------------

QUALIFICATION_PROFILE: Dict[str, Any] = {
    "pnl_positive": {"op": ">", "value": 0.0},   # 收益>0 严格（==0 为 false）
    "trades_enough": {"op": ">=", "value": 5},   # 笔数≥5 含等（==5 为 true）
    "trigger_consistency_max": 0.10,             # 差异≤0.10 含等（==0.10 为 passed）
    "min_trades": 5,
}

# 纸盘侧触发集白名单（唯一操作定义——基因信号四类；close=基因信号出场）
SIGNAL_WHITELIST = frozenset({"open", "close", "add", "reduce"})

# 执行层出场六 kind（正当结构性缺失，双侧剔除不计差异；exit_max_loss
# 为金额防线出场、pt_signal 十值独有——pt_fill 九值无此值，最易漏）
EXECUTION_EXIT_KINDS = frozenset({
    "exit_sl", "exit_tp", "exit_max_loss",
    "exit_max_hold", "exit_time", "exit_liquidation",
})

# 重放 SignalSet 四类 → 白名单 action 语义映射
REPLAY_ACTION_MAP = {
    "entries": "open", "exits": "close", "adds": "add", "reduces": "reduce",
}


# ---------------------------------------------------------------------------
# 子件一：运行期两维（executor 每 bar 调用，同事务）
# ---------------------------------------------------------------------------

def evaluate_two_dimensions(equity: float, initial_cash: float,
                            total_trades: int) -> Dict[str, bool]:
    """两维判定（查询与逐 bar 共用同一函数——单源）。

    维一收益=当 bar equity > initial_cash（严格）；维二笔数=total_trades >= 5（含等）。
    equity 由调用方取 pt_equity 当 bar 行同源值（不自算第二种——契约登记表权益纪律）。
    """
    return {
        "pnl_positive": equity > initial_cash,
        "trades_enough": total_trades >= QUALIFICATION_PROFILE["min_trades"],
    }


def first_met_writable(first_met_at: Optional[str], two_dims: Dict[str, bool]) -> bool:
    """first_met_at 幂等写判定：仅 NULL→非 NULL 一次写入。"""
    return first_met_at is None and two_dims["pnl_positive"] and two_dims["trades_enough"]


# ---------------------------------------------------------------------------
# 子件三：对照重放器触发集提取（对齐谓词 v2——操作定义六点，design §4.1）
# ---------------------------------------------------------------------------

def paper_trigger_set(pt_signal_rows) -> list:
    """纸盘侧触发集：pt_signal 行集（全 outcome：filled/rejected/expired——
    求值产生即算触发，触发一致性≠执行一致性）过滤 kind∈白名单四值
    {open,close,add,reduce}；bar_time 取信号锚列（pt_signal.bar_time=信号
    产生 bar·决策锚，与重放集同锚零换算——pt_fill 执行锚恒差一根，禁止）。
    执行层出场六 kind 不在白名单内自然剔除（双侧排除）。
    """
    out = []
    for row in pt_signal_rows:
        kind = row["signal_kind"] if isinstance(row, dict) else row.signal_kind
        if kind in SIGNAL_WHITELIST:
            bt = row["bar_time"] if isinstance(row, dict) else row.bar_time
            out.append((bt, kind))
    return out


def replay_trigger_set(signal_set) -> list:
    """重放侧触发集：dna_to_signal_set 四类布尔 Series 的 True 点。

    action 语义映射 open↔entry/close↔exit/add↔add/reduce↔reduce；
    返回 [(bar_time, action)] 与纸盘侧同锚（信号 bar）零换算。
    """
    out = []
    for series_key, series in (
        ("entries", signal_set.entries),
        ("exits", signal_set.exits),
        ("adds", signal_set.adds),
        ("reduces", signal_set.reduces),
    ):
        if series is None:
            continue
        action = REPLAY_ACTION_MAP[series_key]
        for ts in series.index[series.astype(bool)]:
            out.append((str(ts), action))
    return out


def trigger_consistency(paper_triggers: list, replay_triggers: list) -> Dict[str, Any]:
    """对齐谓词 v2 差异率（操作定义第 5/6 点）。

    差异率=基因四类对称差基数/重放触发数（分母=重放侧基数）；
    重放触发数=0 → passed=NULL（三态：NULL≠false≠true——宁缺勿滥）；
    判定=差异率≤0.10 含等（==0.10 为 passed）。
    """
    paper_set = set(paper_triggers)
    replay_set = set(replay_triggers)
    denominator = len(replay_set)
    if denominator == 0:
        return {"passed": None, "deviation_ratio": None,
                "numerator": None, "denominator": 0,
                "detail": {"reason": "replay_triggers_empty"}}
    numerator = len(paper_set.symmetric_difference(replay_set))
    ratio = numerator / denominator
    return {
        "passed": ratio <= QUALIFICATION_PROFILE["trigger_consistency_max"],
        "deviation_ratio": ratio,
        "numerator": numerator,
        "denominator": denominator,
        "detail": {
            "paper_only": sorted(paper_set - replay_set),
            "replay_only": sorted(replay_set - paper_set),
        },
    }


# ---------------------------------------------------------------------------
# 子件二：三维终判（组 C 接挂点；本函数纯计算）
# ---------------------------------------------------------------------------

def final_judgment(final_equity: float, initial_cash: float, total_trades: int,
                   consistency: Dict[str, Any]) -> Dict[str, Any]:
    """三维判定式：收益>0 严格 AND 笔数≥5 含等 AND 差异≤0.10 含等。

    回撤=披露项不计入判定（REQ-4-01 原文）。consistency.passed=NULL
    （对照重放不可执行）时 qualified=false（差异不可判即不判达标）。
    """
    pnl_positive = final_equity > initial_cash
    trades_enough = total_trades >= QUALIFICATION_PROFILE["min_trades"]
    passed = consistency.get("passed")
    qualified = bool(pnl_positive and trades_enough and passed is True)
    return {
        "judged_at": None,  # 挂点侧填终态时点
        "pnl_positive": pnl_positive,
        "trades_enough": trades_enough,
        "trigger_consistency": consistency,
        "qualified": qualified,
        "profile_note": "qualification:REQ-4-01(纸盘达标域判据，语义分立于研究验证域 DEFAULT_PROFILE)",
    }


def run_trigger_consistency(db_path: Path, data_dir: Path, task: Dict[str, Any],
                            signal_rows: list) -> Dict[str, Any]:
    """对照重放器（子件三入口；终判时调用一次，无 REST 面）。

    装配链纪律（硬约束）：经组 A 共享函数 prepare_replay_inputs 与
    backtest_bridge 同源（load_and_prepare_df/load_mtf_data/
    assemble_signal_set/ensure_timeframe_consistent 逐函数同源）。
    重放窗=任务实际运行区间（首根 equity bar→last_bar_time）；
    warmup 沿偏差对照层同值 200（reporting DEVIATION_WARMUP_BARS）。
    对照面=pt_signal 信号锚（全 outcome 过滤白名单四值）；
    任何失败→passed=NULL（三态，宁缺勿滥）不抛出。
    """
    try:
        from core.analysis.backtest_bridge import prepare_replay_inputs
        from core.persistence.repository import paper as paper_repo
        from core.strategy.executor import dna_to_signal_set
        from core.paper_trading.kernel.types import StrategyDNA

        dna = StrategyDNA.from_json(task["dna_snapshot_json"])
        equity_rows = paper_repo.list_equity_snapshots(db_path, task["task_id"], limit=10**9)
        if not equity_rows or not task.get("last_bar_time"):
            return {"passed": None, "deviation_ratio": None, "numerator": None,
                    "denominator": 0, "detail": {"reason": "no_run_window"}}
        start = equity_rows[0]["bar_time"]
        end = task["last_bar_time"]
        bt_df, dfs_by_timeframe, _sig, _w = prepare_replay_inputs(
            db_path, data_dir, dna, task["symbol"], task["timeframe"],
            start, end, 200)
        sig_set = dna_to_signal_set(dna, bt_df, dfs_by_timeframe=dfs_by_timeframe)
        replay = replay_trigger_set(sig_set)
        paper = paper_trigger_set(signal_rows)
        return trigger_consistency(paper, replay)
    except Exception as exc:  # noqa: BLE001——终判解耦：重放失败不阻塞终态
        return {"passed": None, "deviation_ratio": None, "numerator": None,
                "denominator": 0, "detail": {"reason": f"replay_failed: {exc}"}}


def build_qualification_section(db_path: Path, data_dir: Path,
                                task: Dict[str, Any]) -> Dict[str, Any]:
    """子件二三维终判组装（reporting._assemble_report 第四键挂点）。

    equity 取权益快照末行同源值（与 pt_equity 行同源）；对照重放
    经 run_trigger_consistency（内部三态容错）；judged_at=调用时点。
    """
    from core.persistence.repository import paper as paper_repo
    from core.paper_trading.runtime.executor import utc_now_iso
    equity_rows = paper_repo.list_equity_snapshots(db_path, task["task_id"], limit=10**9)
    final_equity = float(equity_rows[-1]["equity"]) if equity_rows else float(task["initial_cash"])
    signal_rows = paper_repo.list_signals(db_path, task["task_id"], limit=10**9)
    consistency = run_trigger_consistency(db_path, data_dir, task, signal_rows)
    section = final_judgment(
        final_equity, float(task["initial_cash"]), int(task["total_trades"]),
        consistency)
    section["judged_at"] = utc_now_iso()
    section["transition"] = _maybe_transit_candidate(db_path, task, section)
    return section


def _maybe_transit_candidate(db_path: Path, task: Dict[str, Any],
                             section: Dict[str, Any]) -> Dict[str, Any]:
    """子件四凭证签发接线（仅终态+qualified=true 触发；解耦语义）。

    迁移失败不回滚终判、终判失败不阻塞迁移互不连带；多任务竞态=
    状态机单源裁决（先到迁移，后到 already_in_status 如实记录）；
    重复终判幂等（qualify 边 already_in_status 承载）。非终态
    （rolling 报告）attempted=false 零触碰。
    """
    out: Dict[str, Any] = {"attempted": False, "migrated": False,
                           "not_migrated_reason": None}
    terminal = {"completed", "stopped", "error"}
    if task.get("status") not in terminal or not section.get("qualified"):
        return out
    out["attempted"] = True
    try:
        from core.strategy.lifecycle.service import transit
        dna = None
        from core.paper_trading.kernel.types import StrategyDNA
        dna = StrategyDNA.from_json(task["dna_snapshot_json"])
        result = transit(
            db_path, task["strategy_id"],
            target="candidate", trigger="qualify",
            evidence={
                "task_id": task["task_id"],
                "strategy_id": task["strategy_id"],
                "version_ref": getattr(dna, "gene_signature", None) or task["task_id"],
                "judged_at": section.get("judged_at"),
                "criteria": {
                    "pnl_positive": section["pnl_positive"],
                    "trades_enough": section["trades_enough"],
                    "trigger_consistency": section.get("trigger_consistency", {}).get("passed"),
                },
                "profile_note": section.get("profile_note"),
            })
        out["migrated"] = bool(result.get("migrated"))
        if not out["migrated"]:
            out["not_migrated_reason"] = str(result.get("reason") or result)
    except Exception as exc:  # noqa: BLE001——迁移失败不回滚终判（解耦）
        out["not_migrated_reason"] = f"transit_failed: {exc}"
    return out


def qualification_progress_payload(equity: Optional[float], initial_cash: float,
                                   total_trades: int,
                                   first_met_at: Optional[str]) -> Dict[str, Any]:
    """查询面（PaperTaskResponse.qualification_progress）：查询期现算两维+读 first_met_at。"""
    if equity is None:
        return {"pnl_positive": False, "trades_enough": False, "first_met_at": first_met_at}
    dims = evaluate_two_dimensions(equity, initial_cash, total_trades)
    return {"pnl_positive": dims["pnl_positive"],
            "trades_enough": dims["trades_enough"],
            "first_met_at": first_met_at}
