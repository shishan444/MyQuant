"""扫描模板组口径适配（add-replay-backtest-mcp 任务 2.2）。

exit_sweep 逐组底层切换为逐根回放引擎后的组口径桥：回放落库明细
（pt_equity/pt_fill）→ 模板消费的 metrics 三键+逐笔收益序列。
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Tuple

from core.persistence.repository import paper


def replay_group_metrics(
    db_path: Path, result,
) -> Tuple[Dict[str, Any], List[float]]:
    """回放组结果→exit_sweep 组口径（metrics 三键+逐笔收益序列）。

    - max_drawdown：pt_equity 快照序列的峰谷回撤（比例口径）；
    - trade_returns：pt_fill 逐笔 realized_pnl 序列（绝对金额语义——
      与旧引擎每笔收益率的符号与判定方向一致〔净期望>0 ⟺ 率>0〕，
      三道门真伪门谓词不受尺度影响）；
    - win_rate/total_trades：ReplayResult summary 直取。
    """
    eqs = paper.list_equity_snapshots(
        db_path, result.task_id, limit=1_000_000)
    peak = 0.0
    mdd = 0.0
    for e in eqs:
        v = float(e["equity"])
        if v > peak:
            peak = v
        if peak > 0:
            mdd = max(mdd, (peak - v) / peak)
    fills = paper.list_fills(db_path, result.task_id, limit=1_000_000)
    rets = [
        float(f["realized_pnl"]) for f in fills
        if f.get("realized_pnl") is not None]
    metrics = {
        "win_rate": (
            result.win_count / result.total_trades
            if result.total_trades else 0.0),
        "max_drawdown": mdd,
        "total_trades": result.total_trades,
    }
    return metrics, rets
