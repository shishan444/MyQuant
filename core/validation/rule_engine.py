"""进出场规则求值引擎：信号生成与交易配对。

服务对象：/api/validate/rules 端点（前端规则验证页经此调
evaluate_rules）与 tests/consistency 等契约测试直调。职责边界：
entry/exit 条件掩码（复用 validation.engine 的 _evaluate_conditions
共用实现）→ 买卖信号 → 顺序配对成交易 → 统计；条件求值语义
不在本模块自持。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, List, Optional

import pandas as pd

from core.data.storage import load_parquet
from core.features.indicators import compute_all_indicators
from core.validation.engine import (
    _evaluate_conditions,
    _evaluate_single_condition,
    _resolve_subject,
    _resolve_target,
)
from core.validation.mtf import load_mtf_data, merge_to_base, get_timeframe_minutes

if TYPE_CHECKING:
    from core.analysis.condition_kernel import MtfEvalContext


# ---------------------------------------------------------------------------
# 数据结构
# ---------------------------------------------------------------------------

@dataclass
class RuleSignal:
    """单个进/出场信号（时间/价格/方向）。"""
    time: str
    price: float
    type: str  # "buy" 或 "sell"


@dataclass
class TradeRecord:
    """一笔已完结交易（进出场时间价格与收益率）。"""
    entry_time: str
    entry_price: float
    exit_time: str
    exit_price: float
    return_pct: float
    is_win: bool


@dataclass
class RuleResult:
    """规则求值完整输出（信号/配对交易/统计）。"""
    buy_signals: List[RuleSignal] = field(default_factory=list)
    sell_signals: List[RuleSignal] = field(default_factory=list)
    trades: List[TradeRecord] = field(default_factory=list)
    total_trades: int = 0
    win_trades: int = 0
    loss_trades: int = 0
    win_rate: Optional[float] = None
    total_return_pct: float = 0.0
    avg_return_pct: float = 0.0
    warnings: List[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# 核心求值
# ---------------------------------------------------------------------------

def evaluate_rules(
    symbol: str,
    timeframe: str,
    start: str,
    end: str,
    entry_conditions: List[Dict[str, Any]],
    exit_conditions: List[Dict[str, Any]],
    data_dir: Optional[str] = None,
    mtf: Optional["MtfEvalContext"] = None,
) -> RuleResult:
    """求值进出场规则条件并生成配对交易。

    Args:
        symbol: 交易对如 "BTCUSDT"。
        timeframe: 基准（执行）周期如 "4h"。
        start: 起始日 "2024-01-01"。
        end: 截止日 "2024-12-31"。
        entry_conditions: 进场条件 dict 列表。
        exit_conditions: 出场条件 dict 列表。
        data_dir: 可选行情数据目录。

    Returns:
        RuleResult：信号、配对交易与统计。
    """
    warnings: List[str] = []

    if data_dir is None:
        data_dir = str(Path(__file__).resolve().parent.parent.parent / "data" / "market")

    # ------------------------------------------------------------------
    # 1. 装载基准周期数据
    # ------------------------------------------------------------------
    # B4.1 共享路径发现：safe_symbol 归一 + 别名兜底；未知 timeframe
    # 结构化拒绝而非静默空结果
    from core.analysis.hypothesis_service import ResearchError
    from core.data.mtf_loader import safe_symbol, find_parquet

    try:
        parquet_path = find_parquet(Path(data_dir), safe_symbol(symbol), timeframe)
    except ValueError as exc:
        raise ResearchError(
            "RESEARCH_PARAM_INVALID",
            f"timeframe 未知: {timeframe!r}",
            detail={"param": "timeframe", "value": timeframe,
                    "legal_range": "TF_SECONDS 已知周期"},
        ) from exc

    if parquet_path is None:
        warnings.append(f"Data file not found: {safe_symbol(symbol)}_{timeframe}.parquet")
        return RuleResult(warnings=warnings)

    df = load_parquet(parquet_path)
    if df is None or len(df) == 0:
        warnings.append("Loaded data is empty")
        return RuleResult(warnings=warnings)

    # 日期区间过滤
    if start:
        df = df[df.index >= start]
    if end:
        df = df[df.index <= end]

    if len(df) == 0:
        warnings.append("No data in requested date range")
        return RuleResult(warnings=warnings)

    # ------------------------------------------------------------------
    # 2. 基准周期指标计算
    # ------------------------------------------------------------------
    enhanced_df = compute_all_indicators(df)

    # ------------------------------------------------------------------
    # 3. MTF：发现条件引用的高周期并合并其数据
    # ------------------------------------------------------------------
    all_conditions = list(entry_conditions) + list(exit_conditions)
    referenced_tfs: set[str] = set()
    for cond in all_conditions:
        cond_tf = cond.get("timeframe", "")
        if cond_tf and cond_tf != timeframe:
            referenced_tfs.add(cond_tf)

    if referenced_tfs:
        try:
            mtf_data = load_mtf_data(symbol, list(referenced_tfs), start, end, data_dir)
            if mtf_data:
                mtf_with_indicators: Dict[str, pd.DataFrame] = {}
                for tf_key, tf_df in mtf_data.items():
                    mtf_with_indicators[tf_key] = compute_all_indicators(tf_df)

                base_tf_minutes = get_timeframe_minutes(timeframe)
                tf_minutes_map = {tf_key: get_timeframe_minutes(tf_key) for tf_key in mtf_with_indicators}
                enhanced_df = merge_to_base(
                    enhanced_df, mtf_with_indicators,
                    base_tf_minutes, tf_minutes_map,
                )
        except Exception as exc:
            warnings.append(f"MTF data loading failed, falling back to single timeframe: {exc}")

    # ------------------------------------------------------------------
    # 4. 进出场条件求值
    # ------------------------------------------------------------------
    entry_mask = _evaluate_conditions(enhanced_df, entry_conditions, warnings, mtf=mtf)
    exit_mask = _evaluate_conditions(enhanced_df, exit_conditions, warnings, mtf=mtf)

    # ------------------------------------------------------------------
    # 5. 生成信号
    # ------------------------------------------------------------------
    buy_signals: List[RuleSignal] = []
    sell_signals: List[RuleSignal] = []

    for idx in enhanced_df.index[entry_mask]:
        buy_signals.append(RuleSignal(
            time=str(idx),
            price=float(enhanced_df.loc[idx, "close"]),
            type="buy",
        ))

    for idx in enhanced_df.index[exit_mask]:
        sell_signals.append(RuleSignal(
            time=str(idx),
            price=float(enhanced_df.loc[idx, "close"]),
            type="sell",
        ))

    # ------------------------------------------------------------------
    # 6. 进场→出场配对成交易
    # ------------------------------------------------------------------
    trades = _pair_trades(enhanced_df, entry_mask, exit_mask)

    # ------------------------------------------------------------------
    # 7. 统计计算
    # ------------------------------------------------------------------
    total_trades = len(trades)
    win_trades = sum(1 for t in trades if t.is_win)
    loss_trades = total_trades - win_trades
    # 计算层统一小数口径；零交易为缺失语义（None）
    win_rate = round(win_trades / total_trades, 4) if total_trades > 0 else None

    total_return_pct = round(sum(t.return_pct for t in trades), 2)
    avg_return_pct = round(total_return_pct / total_trades, 2) if total_trades > 0 else 0.0

    return RuleResult(
        buy_signals=buy_signals,
        sell_signals=sell_signals,
        trades=trades,
        total_trades=total_trades,
        win_trades=win_trades,
        loss_trades=loss_trades,
        win_rate=win_rate,
        total_return_pct=total_return_pct,
        avg_return_pct=avg_return_pct,
        warnings=warnings,
    )


# ---------------------------------------------------------------------------
# 交易配对
# ---------------------------------------------------------------------------

def _pair_trades(
    df: pd.DataFrame,
    entry_mask: pd.Series,
    exit_mask: pd.Series,
) -> List[TradeRecord]:
    """进出场信号顺序配对成交易。

    逻辑：
    1. 从当前扫描位起找第一个进场信号。
    2. 在该进场之后（严格后向）找第一个出场信号。
    3. 进出场同 bar 触发时出场优先（先平旧仓再开新仓）。
    4. 记录该笔交易，从出场后一根起继续搜索。
    """
    trades: List[TradeRecord] = []

    entry_indices = set(df.index[entry_mask])
    exit_indices = set(df.index[exit_mask])

    all_index = df.index
    n = len(all_index)

    pos = 0  # 当前扫描位（整数下标）

    while pos < n:
        # 阶段 1：找下一个进场信号
        entry_loc = None
        for i in range(pos, n):
            if all_index[i] in entry_indices:
                entry_loc = i
                break

        if entry_loc is None:
            break

        entry_time = all_index[entry_loc]
        entry_price = float(df.loc[entry_time, "close"])

        # 阶段 2：找进场之后（严格后向）的下一个出场信号
        exit_loc = None
        for i in range(entry_loc + 1, n):
            if all_index[i] in exit_indices:
                exit_loc = i
                break

        if exit_loc is None:
            # 无出场信号——持仓未平，跳过不记
            break

        exit_time = all_index[exit_loc]
        exit_price = float(df.loc[exit_time, "close"])

        return_pct = round((exit_price - entry_price) / entry_price * 100, 2)
        is_win = return_pct > 0

        trades.append(TradeRecord(
            entry_time=str(entry_time),
            entry_price=entry_price,
            exit_time=str(exit_time),
            exit_price=exit_price,
            return_pct=return_pct,
            is_win=is_win,
        ))

        # 从出场后一根起继续扫描
        pos = exit_loc + 1

    return trades
