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

import numpy as np
import pandas as pd

from core.analysis.hypothesis_service import ResearchError
from core.analysis.registry_meta import WARMUP_RULE_TEXT
from core.analysis.warmup_loader import TF_MINUTES, to_utc


def _collect_needed_tfs(dna, exec_tf: str) -> set:
    """装载集判据单源：layers ∪ 基因级 timeframe ∪ 执行周期。

    unblock-research-validation：基因级周期维并入——单层 DNA 带基因
    级 tf 同样触发装载（空集=纯基准周期不装载语义保持）。研究链
    （run_backtest_on_range）与工程端点群（backtest_service.
    compute_needed_tfs 逐 DNA 聚合）同款判据共用本单点。
    """
    tfs = set()
    if getattr(dna, "is_mtf", False) and dna.layers:
        tfs |= {layer.timeframe for layer in dna.layers}
    for gene in (getattr(dna, "signal_genes", None) or []):
        gene_tf = getattr(gene, "timeframe", None)
        if gene_tf is not None:
            tfs.add(gene_tf)
    return tfs | {exec_tf} if tfs else set()


def _slice_signal_set(sig_set, index: pd.Index):
    """SignalSet 序列字段按请求区间索引切回（enhanced ⊇ 请求区间，
    reindex 不产生缺失行）；非序列字段（degraded_layers/
    mtf_diagnostics/mtf_gated_bars）原样保留。引擎按 .values 取信号
    数组，切回后序列与 bt_df 严格等长对齐。"""
    from core.strategy.executor import SignalSet

    def _cut(s):
        return s.reindex(index) if s is not None else None

    # swallowed 行按区间过滤（CARD-04）：组装在 enhanced 全段（含
    # warmup 前缀）上跑，bar_time 落在请求区间外的行（warmup 段冲突
    # 行）不随份注入；全滤空归 None（SignalSet 语义：无行不建列表）
    if sig_set.swallowed is None:
        swallowed = None
    else:
        in_range = {str(ts) for ts in index}
        swallowed = ([row for row in sig_set.swallowed
                      if row.get("bar_time") in in_range] or None)

    return SignalSet(
        entries=_cut(sig_set.entries),
        exits=_cut(sig_set.exits),
        adds=_cut(sig_set.adds),
        reduces=_cut(sig_set.reduces),
        degraded_layers=sig_set.degraded_layers,
        entry_direction=_cut(sig_set.entry_direction),
        mtf_diagnostics=sig_set.mtf_diagnostics,
        confidence=_cut(sig_set.confidence),
        swallowed=swallowed,
        mtf_gated_bars=sig_set.mtf_gated_bars,
    )


def ensure_timeframe_consistent(
    dna, request_timeframe: str, *, strategy_id: str | None = None,
) -> None:
    """请求周期一致性守卫（fix-mtf-execution-consistency R3/D6 单源）。

    dna.execution_genes.timeframe != request_timeframe 时结构化拒绝
    （RESEARCH_TIMEFRAME_MISMATCH 422）——异轴执行的求值语义未定义
    （信号在 A 轴生产、warmup 按 B 轴折算、投影跨轴漂移），折算或
    披露均修不了语义漂移，故校验拒绝（项目纪律先例：跨币种/MTF×
    policy/低周期三处拒绝同族）。守卫只拦显式不一致；缺省链自洽
    调用（不传 timeframe → 缺省取策略行周期 == 执行周期）不在守卫
    面——存量漂移产物的修正归历史回扫重验通道（S 卡），两机制正交。

    双挂同一单源：①桥 run_backtest_on_range 入口第一校验（四通道
    隔离/随机/沙盒/分段全经桥=单挂全覆盖）；②工程回测端点 from_json
    后装载前。一致则静默通过。
    """
    exec_tf = dna.execution_genes.timeframe
    if exec_tf != request_timeframe:
        raise ResearchError(
            "RESEARCH_TIMEFRAME_MISMATCH",
            f"验证请求周期 {request_timeframe} ≠ 策略执行周期 {exec_tf}"
            "（异轴执行语义未定义，拒绝执行）",
            status=422,
            detail={
                "request_timeframe": request_timeframe,
                "dna_execution_timeframe": exec_tf,
                "strategy_id": strategy_id,
                "need_hint": (
                    "验证请求周期须等于策略执行周期（策略定义轴）；"
                    "换轴验证请回假设侧修改基准周期"
                    "（data_ranges.timeframe）后重新转化"),
            })


def prepare_replay_inputs(
    db_path: Path, data_dir: Path, dna, symbol: str, timeframe: str,
    start: str, end: str, warmup_bars: int,
    *, strategy_id: str | None = None,
):
    """[start-warmup, end) 装载算指标/组装插件信号 → 切回 [start, end)。

    装配链共享函数（paper-trading-revamp 组 A2 从 run_backtest_on_range
    提取）：backtest_bridge 与纸盘对照重放器（qualification.py）共同调用，
    逐函数同源（load_and_prepare_df/load_mtf_data/assemble_signal_set/
    ensure_timeframe_consistent）——装配逻辑单源，不复制。

    Returns:
        (bt_df, dfs_by_timeframe, signal_set, actual_warmup_bars)
    """
    from core.data.mtf_loader import load_and_prepare_df, load_mtf_data

    # 周期一致性守卫（D6）：入口第一校验（enhanced 装载前省 IO）——
    # 显式异轴拒绝；缺省链自洽调用（timeframe=策略行周期）自然通过
    ensure_timeframe_consistent(dna, timeframe, strategy_id=strategy_id)

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

    return bt_df, dfs_by_timeframe, signal_set, actual_warmup_bars


def run_backtest_on_range(
    db_path: Path, data_dir: Path, dna, symbol: str, timeframe: str,
    start: str, end: str, warmup_bars: int,
    *, init_cash: float = 100000.0,
    fee: float = 0.001, slippage: float = 0.0005,
    strategy_id: str | None = None,
    with_detail: bool = False,
    task_leverage: int | None = None,
    task_margin_cap: float | None = None,
    task_halt_drawdown_pct: float | None = None,
) -> (Tuple[Dict[str, Any], np.ndarray | None, int, int]
      | Tuple[Dict[str, Any], np.ndarray | None, int, int, Dict[str, Any]]):
    """[start-warmup, end) 装载算指标/组装插件信号 → 切回 [start,end) 回测。

    装配段经共享函数 prepare_replay_inputs（组 A2 提取——行为不变锚=
    bridge 固定样本 metrics 提取前后逐位一致）。

    Args:
        with_detail: True 时返回五元组（add-validation-detail B4），第
            五元素为明细轻量载荷 {equity, trades_df, init_cash, fee,
            slippage}——equity/trades_df 传 BacktestResult 对象引用不
            复制（明细组装在落盘层）；三参数键=实际生效实参值。缺省
            False 返回四元组逐位不变（六生产消费方+19 处测试解包零
            波及——硬约束，见 change design §3.2）。
        task_leverage, task_margin_cap: 任务级封顶两参
            （sandbox-and-report-supplement D1 ③层；None=不生效——
            存量逐位不变）。detail 载荷回显两键（传了才写——
            _params_exec_keys 五键化的值源）。

    Returns:
        (metrics, trade_returns, actual_warmup_bars, bars_used)——
        trade_returns 为每笔收益序列（judge 证据闸门 CI 判定输入；
        内存传递不落库，metrics 保持纯可序列化）；
        with_detail=True 时追加第五元素 detail 载荷。
    """
    from core.backtest import engine as bt_engine_mod

    # 周期一致性守卫（D6）在 prepare_replay_inputs 入口第一行（enhanced 装载前省 IO）
    bt_df, dfs_by_timeframe, signal_set, actual_warmup_bars = prepare_replay_inputs(
        db_path, data_dir, dna, symbol, timeframe, start, end, warmup_bars,
        strategy_id=strategy_id)

    engine = bt_engine_mod.BacktestEngine(
        init_cash=init_cash, fee=fee, slippage=slippage, db_path=db_path,
        task_leverage=task_leverage, task_margin_cap=task_margin_cap,
        halt_drawdown_pct=task_halt_drawdown_pct)
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
        # C3 成因区分（fix-mtf-execution-consistency）：请求区间信号
        # 计数四键——三态判读①entries==0=条件未触发（装配异常已被
        # R1 结构化拒绝前置拦截）②entries>0 且 total_trades==0=执行
        # 段无成交（资金/风控/时序）③正常；四通道回执自动携带
        "signal_counts": getattr(result, "signal_counts", None),
        # series 档止损价序列（hypothesis-workbench 组 2）：[{time,value}]
        # 可序列化形制（三入口 backtest/backtest-sandbox/inspect-cases 消费
        # 本 metrics 单点——一处透传全达）；非 series 档 None（null 透传）
        "sl_series": (
            [{"time": str(t), "value": None if pd.isna(v) else float(v)}
             for t, v in result.sl_series.items()]
            if getattr(result, "sl_series", None) is not None else None),
        # trail 档逐 bar 止损线真值（add-vr-signal-trace 组 5）：引擎
        # 缓冲单源（result.trail_series——禁事后重算），[{time,value}]
        # 形制与 sl_series 同位同款；非 trail 档 null 透传。份级归属经
        # metrics 内嵌自动成立（isolated 直嵌/random=windows[i].metrics/
        # staged=segments[i].metrics——三入口 result_json 组装零改动）
        "trail_series": (
            [{"time": str(t), "value": None if pd.isna(v) else float(v)}
             for t, v in result.trail_series.items()]
            if getattr(result, "trail_series", None) is not None else None),
    }
    # 任务级封顶披露（sandbox-and-report-supplement ③层）：③启用
    # （两参都传）时 metrics 纯增 position_sizing 键——引擎 disclosure
    # 单源（capped_by_task_cap 计数＋task_cap 生效面）；未传两参不写
    # 键，存量 metrics 键集与值逐位不变
    if task_leverage is not None and task_margin_cap is not None:
        _ps_face = (getattr(result, "metrics_dict", None) or {}).get(
            "position_sizing")
        if _ps_face is not None:
            metrics["position_sizing"] = _ps_face
    # 熔断三态披露（D3①）：halt_drawdown_pct 传入时 metrics 纯增
    # halt_info 键（未触发 {halted: false}／触发 +halted_at+halt_reason）；
    # 未配置不写键（存量逐位不变）。halt 不进 detail 执行参数面
    # （隔离验证路不进熔断——D3 范围界定）
    if task_halt_drawdown_pct is not None:
        metrics["halt_info"] = getattr(result, "halt_info", None) or {
            "halted": False}
    if with_detail:
        # B4 明细载荷（传引用不复制——组装/落盘在验证服务层）；
        # 三参数键=实际生效实参（显式传参与默认两形态同源）。
        # add-vr-signal-trace 追加四键（传引用）：swallowed=求值层吞没
        # 行（resolver 合并输入）/trace_buf=事件码矩阵（resolver 输入）
        # /mtf_gated_bars=份级门控计数（入口记录级聚合）/value_curve=
        # 原始组合价值曲线（resolver capped 重放单源——放大/资金费前）
        detail = {
            "equity": result.equity_curve,
            "trades_df": result.trades_df,
            "init_cash": init_cash,
            "fee": fee,
            "slippage": slippage,
            "swallowed": getattr(result, "swallowed_rows", None),
            "trace_buf": getattr(result, "trace_buf", None),
            "mtf_gated_bars": getattr(result, "mtf_gated_bars", None),
            "value_curve": getattr(result, "value_curve_raw", None),
        }
        # 任务级两参回显（sandbox-and-report-supplement A3 五键化值源：
        # 传了才写——未传两参 detail 逐位不变，_params_exec_keys 键集
        # 纯扩、init_cash 等三键语义零影响）
        if task_leverage is not None:
            detail["leverage"] = task_leverage
        if task_margin_cap is not None:
            detail["margin_cap"] = task_margin_cap
        return (metrics, result.trade_returns, actual_warmup_bars,
                len(bt_df), detail)
    return metrics, result.trade_returns, actual_warmup_bars, len(bt_df)
