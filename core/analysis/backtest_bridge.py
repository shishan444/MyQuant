"""受控回测桥（add-research-analysis-tools 0.4.1 S3——搬家保真）。

``run_backtest_on_range`` 自 validation_service 私有符号提升为共享
模块级（去下划线；函数体与两个仅被其消费的私有助手逐字保真零行为
变化——搬家不重写）。落位新文件的理由：validation_service 保持薄、
分析域模板（exit_sweep）不反向 import validation_service（避免模板
域向验证域反向依赖）。双消费方：validation_service 四处调用点
（isolated/random_window/sandbox/staged）+ exit_sweep（0.4.1 R6）。
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Tuple

import pandas as pd

from core.analysis.hypothesis_service import ResearchError
from core.analysis.registry_meta import WARMUP_RULE_TEXT
from core.analysis.warmup_loader import TF_MINUTES, to_utc


def _collect_needed_tfs(dna, exec_tf: str) -> set:
    """收集 DNA 声明的全部时间层 ∪ 执行周期（is_mtf 即装载语义）。"""
    if not (getattr(dna, "is_mtf", False) and dna.layers):
        return set()
    return {layer.timeframe for layer in dna.layers} | {exec_tf}


def _slice_signal_set(sig_set, index: pd.Index):
    """SignalSet 序列字段按请求区间索引切回（enhanced ⊇ 请求区间，
    reindex 不产生缺失行）；非序列字段（degraded_layers/
    mtf_diagnostics）原样保留。引擎按 .values 取信号数组，切回后
    序列与 bt_df 严格等长对齐。"""
    from core.strategy.executor import SignalSet

    def _cut(s):
        return s.reindex(index) if s is not None else None

    return SignalSet(
        entries=_cut(sig_set.entries),
        exits=_cut(sig_set.exits),
        adds=_cut(sig_set.adds),
        reduces=_cut(sig_set.reduces),
        degraded_layers=sig_set.degraded_layers,
        entry_direction=_cut(sig_set.entry_direction),
        mtf_diagnostics=sig_set.mtf_diagnostics,
        confidence=_cut(sig_set.confidence),
    )


def run_backtest_on_range(
    db_path: Path, data_dir: Path, dna, symbol: str, timeframe: str,
    start: str, end: str, warmup_bars: int,
    *, init_cash: float = 100000.0,
    fee: float = 0.001, slippage: float = 0.0005,
) -> Tuple[Dict[str, Any], int, int]:
    """[start-warmup, end) 装载算指标/组装插件信号 → 切回 [start,end) 回测。

    Returns:
        (metrics, trade_returns, actual_warmup_bars, bars_used)——
        trade_returns 为每笔收益序列（judge 证据闸门 CI 判定输入；
        内存传递不落库，metrics 保持纯可序列化）。
    """
    from core.backtest import engine as bt_engine_mod
    from core.data.mtf_loader import load_and_prepare_df, load_mtf_data

    tf_minutes = TF_MINUTES.get(timeframe, 240)
    warmup_delta = pd.Timedelta(minutes=tf_minutes * warmup_bars)
    start_ts = to_utc(pd.Timestamp(start))
    load_start = (start_ts - warmup_delta).isoformat()

    enhanced = load_and_prepare_df(
        data_dir, symbol, timeframe, data_start=load_start, data_end=end,
    )
    if enhanced is None:
        raise ResearchError("RESEARCH_DATA_INSUFFICIENT",
                            f"区间数据不足: {symbol} {timeframe} [{load_start}, {end})")

    # warmup 可得性：装载后起点必须真正早于 start（warmup 段有数据）
    actual_warmup_bars = int((enhanced.index < start_ts).sum())
    if actual_warmup_bars < warmup_bars:
        raise ResearchError(
            "RESEARCH_WARMUP_INSUFFICIENT",
            f"warmup 不足：需要 {warmup_bars} bar，数据起点前实际可得 "
            f"{actual_warmup_bars} bar（数据起点 {enhanced.index[0]}）",
            detail={"warmup_bars": warmup_bars,
                    "available_bars": actual_warmup_bars,
                    "data_start": enhanced.index[0].isoformat(),
                    "rule": WARMUP_RULE_TEXT})

    bt_df = enhanced[enhanced.index >= start_ts]  # 切回请求区间（指标列带记忆）

    # MTF：is_mtf 即装载（不用 len>1 守卫——那会让单层同 tf 策略复现
    # REST 路径的既有 500；load_mtf_data 内建 {exec_tf: enhanced_df}，
    # 单层同 tf 时零额外 IO 退化，恰好满足 executor 的装载要求）
    dfs_by_timeframe = None
    needed_tfs = _collect_needed_tfs(dna, timeframe)
    if needed_tfs:
        dfs_by_timeframe = load_mtf_data(
            data_dir, symbol, timeframe, enhanced, needed_tfs,
            load_start, end)

    # 组装注入（断点 B）：DNA 声明 formula/model 源时，在 enhanced
    # 全段（含 warmup 前缀）上求值组装——公式/模型列带前缀计算保证
    # 请求区间头部收敛；组装结果按请求区间索引切回后注入引擎
    # （引擎统一 shift(1)，组装层不平移）。退化 DNA（无插件源）
    # signal_set=None 走引擎原路径，行为零变化。
    signal_set = None
    if any(s.get("type") in ("formula", "model")
           for s in (dna.signal_sources or [])):
        from core.analysis.signal_assembly import assemble_signal_set
        assembled = assemble_signal_set(
            db_path, dna, enhanced, dfs_by_timeframe=dfs_by_timeframe)
        signal_set = _slice_signal_set(assembled, bt_df.index)

    engine = bt_engine_mod.BacktestEngine(
        init_cash=init_cash, fee=fee, slippage=slippage)
    result = engine.run(dna, bt_df, dfs_by_timeframe=dfs_by_timeframe,
                        signal_set=signal_set)
    metrics = {
        "total_return": float(result.total_return),
        "sharpe_ratio": float(result.sharpe_ratio),
        "max_drawdown": float(result.max_drawdown),
        "win_rate": float(result.win_rate) if result.win_rate is not None else None,
        "total_trades": int(result.total_trades),
        "liquidated": bool(result.liquidated),
        # 基准对照四键（improve-backtest-benchmark-disclosure 2.1，6→10 键）：
        # engine 已算（dataclass 字段），None=无基准（容缺；门槛判定不读新键，
        # 行为零变化）。result_json 随 metrics 形制自动携带，随机窗口同。
        "alpha": float(result.alpha),
        "market_annual_return": float(result.market_annual_return),
        "benchmark_return": (float(result.benchmark_return)
                             if result.benchmark_return is not None else None),
        "excess_return": (float(result.excess_return)
                          if result.excess_return is not None else None),
    }
    return metrics, result.trade_returns, actual_warmup_bars, len(bt_df)
