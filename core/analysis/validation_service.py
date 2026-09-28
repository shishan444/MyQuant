"""隔离回测与随机窗口编排（add-ai-research-mcp 3.2 / 3.3）。

隔离回测判定流（附.3）：
终态拒验 → 幂等查询 → 判定并集求交（重叠 409 附 judged_ranges）→
warmup 推导（max(200, 用到指标最大周期×3, 插件公式 lookback)）→
数据起点不足 422 → [warmup 起点装载算指标/组装插件信号 → 切回请求
区间回测]（指标与插件列记忆保留、结果只统计请求区间）→ 门槛表机械判定 → validation_records+research_log 原子双落。

随机窗口（3.3）：同种子可复现（seed 进 params 持久化）；每窗独立
warmup 前扩（与隔离回测同语义）。
"""
from __future__ import annotations

import hashlib
import json
import re
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd

from core.analysis.hypothesis_service import ResearchError
from core.analysis.isolation import (
    _parse_ts,
    judged_ranges_union,
    overlaps_with_request,
)
from core.analysis.registry_meta import derive_warmup
from core.analysis.thresholds import (
    DEFAULT_PROFILE,
    _aggregate_verdicts,
    judge,
)
from core.persistence.repository import research as rr

# 时间频率换算（warmup bar 数 → 时间差）
_TF_MINUTES = {
    "15m": 15, "30m": 30, "1h": 60, "4h": 240, "1d": 1440, "3d": 4320,
}

# 沙盒单次 bar 上限（M6：防爆超时——须在 MCP write_timeout=60s 内
# 留足余量；50k 为默认起点，按回测速度实施校准）
SANDBOX_MAX_BARS = 50_000


def _range_hash(request_range: Dict[str, str]) -> str:
    return hashlib.sha256(json.dumps(request_range, sort_keys=True).encode()).hexdigest()


def _screen_reuse_check(
    db_path: Path, hypothesis_id: str, request_range: Dict[str, str],
) -> None:
    """扫描候选精确复用反向核查（add-screen-patterns 四件套之④）。

    在既有判定并集拦截未命中后执行（screen_use 谱系已被并集拦下，
    本步只覆盖不挂假设的 screen 日志）：全局 screen 留痕中「同品种
    同周期且时间重叠」的任务，其候选指纹清单与假设条件指纹集求交，
    交非空即 409 拒绝——不依赖任何声明，只拦精确复用；实质修改条件
    后指纹不同，天然放行。

    解析容错（D8/G4）：单条日志 summary 损坏跳过并记 warning
    （可用性优先，不因单条坏记录挡住全部验证）；指纹按请求 timeframe
    现算（timeframe 参与指纹——跨周期复用不算精确复用，D4）。
    """
    import logging

    from core.analysis.condition_identity import canonical_condition_hash
    from core.analysis.isolation import _parse_ts

    logger = logging.getLogger(__name__)
    hyp = rr.get_hypothesis(db_path, hypothesis_id)
    if hyp is None:
        return
    try:
        conditions = json.loads(hyp["conditions"])
    except (json.JSONDecodeError, TypeError):
        return  # 结构异常由后续验证流程自然失败，本步不越权拒绝

    tf = str(request_range.get("timeframe", ""))
    cond_fps: set = set()
    for group in ("when_conditions", "then_conditions"):
        for c in conditions.get(group, []) if isinstance(conditions, dict) else []:
            try:
                cond_fps.add(canonical_condition_hash(c, tf))
            except (ValueError, TypeError):
                continue
    if not cond_fps:
        return

    rs = _parse_ts(request_range["start"])
    re_ = _parse_ts(request_range["end"])
    for entry in rr.list_research_log(db_path, entry_type="screen", limit=500):
        try:
            entry_ranges = json.loads(entry.get("data_ranges") or "[]")
            summary = json.loads(entry.get("summary") or "{}")
        except (json.JSONDecodeError, TypeError):
            logger.warning("screen 日志解析失败，反向核查跳过该条: %s",
                           entry.get("log_id"))
            continue
        if not isinstance(summary, dict):
            continue
        # 同品种同周期 + 时间重叠（半开区间相交）
        overlap = False
        for r in entry_ranges if isinstance(entry_ranges, list) else []:
            if not isinstance(r, dict):
                continue
            if (r.get("symbol") != request_range.get("symbol")
                    or str(r.get("timeframe")) != tf):
                continue
            try:
                s, e = _parse_ts(r["start"]), _parse_ts(r["end"])
            except (KeyError, ValueError):
                continue
            if max(rs, s) < min(re_, e):
                overlap = True
                break
        if not overlap:
            continue
        task_fps = set(summary.get("top_fingerprints") or [])
        hits = sorted(cond_fps & task_fps)
        if hits:
            raise ResearchError(
                "SCREEN_CANDIDATE_REUSE",
                "验证区间与已扫描区间重叠且条件精确复用扫描候选"
                "（该区间数据已被扫描任务看过）",
                status=409,
                detail={"screen_task_id": entry.get("ref_id"),
                        "hit_fingerprints": hits,
                        "hint": "实质修改条件后登记新假设，或换未扫描区间验证"})


def _to_utc(ts: pd.Timestamp) -> pd.Timestamp:
    """时间参数入口归一：naive 按 UTC 解释（语义对齐 isolation._parse_ts，
    不引入第三种 tz 语义——date-only 输入不再与 tz-aware 索引比较崩溃）。"""
    if ts.tzinfo is None:
        return ts.tz_localize("UTC")
    return ts.tz_convert("UTC")


def _collect_needed_tfs(dna, exec_tf: str) -> set:
    """收集 DNA 声明的全部时间层 ∪ 执行周期（is_mtf 即装载语义）。"""
    if not (getattr(dna, "is_mtf", False) and dna.layers):
        return set()
    return {layer.timeframe for layer in dna.layers} | {exec_tf}


def _dna_indicator_names(dna) -> List[str]:
    """提取 DNA 实际用到的指标名（signal_genes + 各 MTF layer）。"""
    names = set()
    for gene in dna.signal_genes or []:
        if getattr(gene, "indicator", None):
            names.add(gene.indicator)
    for layer in dna.layers or []:
        for gene in getattr(layer, "signal_genes", []) or []:
            if getattr(gene, "indicator", None):
                names.add(gene.indicator)
    return sorted(names)


def _load_strategy_dna(db_path: Path, strategy_id: str):
    from core.persistence.db_ext import get_strategy
    from core.strategy.dna import StrategyDNA

    row = get_strategy(db_path, strategy_id)
    if row is None:
        raise ResearchError("RESEARCH_STRATEGY_NOT_FOUND",
                            f"策略不存在: {strategy_id}", status=404)
    return StrategyDNA.from_json(row["dna_json"]), row


def _derive_dna_warmup(db_path: Path, dna) -> Tuple[int, List[str]]:
    """DNA warmup 推导：max(derive_warmup(内置), 插件公式 lookback, 200)。

    插件 lookback 按依赖闭包递归取 max（fx_b 引用 fx_a → 两窗口都
    计入）；model 源 walk-forward 训练窗在求值端自理，不占 warmup。
    插件列不存在时贡献 0（求值端显式拒绝兜底）。仅含插件源时
    derive_warmup([]) 的 floor 200 保底。

    Raises:
        KeyError: 内置指标未知（透传给调用方决定拒绝语义）。
    """
    indicator_names = _dna_indicator_names(dna)
    warmup = derive_warmup(indicator_names)  # 自带 floor 200
    formula_refs = {s["ref"] for s in (dna.signal_sources or [])
                    if s.get("type") == "formula" and s.get("ref")}
    if formula_refs:
        from core.analysis.plugin_service import plugin_columns_lookback
        warmup = max(warmup, plugin_columns_lookback(db_path, formula_refs))
    return warmup, indicator_names


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


def _run_backtest_on_range(
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

    tf_minutes = _TF_MINUTES.get(timeframe, 240)
    warmup_delta = pd.Timedelta(minutes=tf_minutes * warmup_bars)
    start_ts = _to_utc(pd.Timestamp(start))
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
            f"{actual_warmup_bars} bar（数据起点 {enhanced.index[0]}）")

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


def run_isolated_backtest(
    db_path: Path,
    data_dir: Path,
    *,
    hypothesis_id: str,
    strategy_id: str,
    request_range: Dict[str, str],
    threshold_profile: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """隔离回测（判定流见模块 docstring）。

    request_range: {symbol, timeframe, start, end}（半开、UTC）。
    Returns:
        {record_id, verdict, metrics, threshold_details, warmup,
         duplicated, judged_ranges}
    """
    # 终态拒验
    hyp = rr.get_hypothesis(db_path, hypothesis_id)
    if hyp is None:
        raise ResearchError("RESEARCH_HYPOTHESIS_NOT_FOUND",
                            f"假设不存在: {hypothesis_id}", status=404)
    if hyp["status"] != "active":
        raise ResearchError("RESEARCH_HYPOTHESIS_TERMINAL",
                            f"假设已终态（{hyp['status']}），拒绝验证；请登记新假设",
                            status=409)

    # 幂等
    rh = _range_hash(request_range)
    existing = rr.find_validation_by_identity(db_path, hypothesis_id, strategy_id, rh)
    if existing is not None:
        return {
            "record_id": existing["record_id"],
            "verdict": existing["verdict"],
            "duplicated": True,
            "result": json.loads(existing["result"]) if existing["result"] else None,
        }

    # 判定并集求交
    judged = judged_ranges_union(db_path, hypothesis_id)
    overlaps = overlaps_with_request(judged, request_range["start"], request_range["end"])
    if overlaps:
        raise ResearchError(
            "RESEARCH_RANGE_OVERLAP",
            "请求区间与判定区间重叠（判定并集=自报∪日志，漏报被日志拦截）",
            status=409,
            detail={"judged_ranges": judged, "overlaps": overlaps})

    # 扫描候选精确复用反向核查（并集未命中时执行——见 _screen_reuse_check）
    _screen_reuse_check(db_path, hypothesis_id, request_range)

    # 策略与 DNA
    dna, strat_row = _load_strategy_dna(db_path, strategy_id)
    symbol = request_range.get("symbol") or strat_row.get("symbol", "BTCUSDT")
    timeframe = request_range.get("timeframe") or strat_row.get("timeframe", "4h")

    # warmup 推导（内置 ×3 与插件公式 lookback 取大，floor 200）
    try:
        warmup_bars, indicator_names = _derive_dna_warmup(db_path, dna)
    except KeyError as exc:
        raise ResearchError("RESEARCH_CONDITION_INVALID", str(exc)) from exc

    # 组合策略加严门之一（6.5）：分量证据前置检查（先于回测执行——
    # 缺证据不浪费计算；模型分量 walk-forward 由组装器强制）
    plugin_sources = [s for s in (dna.signal_sources or [])
                      if s.get("type") in ("formula", "model")]
    components_json = None
    effective_profile = threshold_profile
    if plugin_sources:
        missing = _missing_component_evidence(db_path, plugin_sources)
        if missing:
            raise ResearchError(
                "RESEARCH_COMPONENT_EVIDENCE_MISSING",
                "多信号源策略缺分量级证据（每分量须独立验证记录）",
                status=409,
                detail={"missing": missing})
        from core.analysis.thresholds import tighten_profile
        effective_profile = threshold_profile or tighten_profile()
        components_json = json.dumps(
            [s["ref"] for s in plugin_sources], ensure_ascii=False)

    # 回测（warmup 前扩装载）
    metrics, trade_returns, actual_warmup, bars_used = _run_backtest_on_range(
        db_path, data_dir, dna, symbol, timeframe,
        request_range["start"], request_range["end"], warmup_bars)

    # 门槛机械判定（组合用收紧 profile；三段闸门含证据闸门/CI）
    verdict, details = judge(metrics, effective_profile,
                             trade_returns=trade_returns)

    # 双落（原子）
    record_id = f"rec_{uuid.uuid4().hex[:12]}"
    result_json = json.dumps({
        "metrics": metrics,
        "threshold_details": details,
        "warmup": {"bars": actual_warmup, "derived": warmup_bars,
                   "basis": "max(200, max_indicator_period*3, plugin_formula_lookback)",
                   "indicators": indicator_names},
        "bars_used": bars_used,
        "symbol": symbol, "timeframe": timeframe,
    }, ensure_ascii=False)
    params_json = json.dumps({
        "request_range": request_range, "range_hash": rh,
        "threshold_profile": (effective_profile or DEFAULT_PROFILE)["name"],
    }, ensure_ascii=False)
    rr.save_validation_with_log(
        db_path, record_id=record_id, hypothesis_id=hypothesis_id,
        strategy_id=strategy_id, level="isolated_backtest",
        params=params_json, result=result_json, verdict=verdict, range_hash=rh,
        components=components_json)

    return {
        "record_id": record_id,
        "verdict": verdict,
        "duplicated": False,
        "metrics": metrics,
        "threshold_details": details,
        "warmup": {"bars": actual_warmup, "derived": warmup_bars,
                   "basis": "max(200, max_indicator_period*3, plugin_formula_lookback)"},
        "judged_ranges": judged,
    }


def random_window_test(
    db_path: Path,
    data_dir: Path,
    *,
    strategy_id: str,
    symbol: str,
    timeframe: str,
    data_start: str,
    data_end: str,
    window_bars: int = 200,
    n_windows: int = 8,
    seed: int = 42,
    threshold_profile: Optional[Dict[str, Any]] = None,
    hypothesis_id: Optional[str] = None,
) -> Dict[str, Any]:
    """随机窗口抽查（种子可复现）。

    每窗独立 warmup 前扩（与隔离回测同语义）；窗口在
    [data_start, data_end] 内随机抽样（同 rng 种子 → 同窗口集）。
    """
    import numpy as np

    dna, _ = _load_strategy_dna(db_path, strategy_id)
    warmup_bars, _ = _derive_dna_warmup(db_path, dna)
    tf_minutes = _TF_MINUTES.get(timeframe, 240)
    bar_delta = pd.Timedelta(minutes=tf_minutes)

    # 可抽窗的候选区间：[data_start, data_end - window]
    ds = _to_utc(pd.Timestamp(data_start))
    de = _to_utc(pd.Timestamp(data_end))
    span_bars = int((de - ds) / bar_delta)
    usable = span_bars - window_bars
    if usable < n_windows:
        raise ResearchError("RESEARCH_DATA_INSUFFICIENT",
                            f"区间仅容 {usable} 个不重叠窗，需 {n_windows}")

    # 幂等：同 (strategy, range_hash) 二次请求返回既有记录（复现审计）
    rh = _range_hash({"symbol": symbol, "timeframe": timeframe,
                      "start": data_start, "end": data_end,
                      "window_bars": window_bars, "n": n_windows, "seed": seed})
    existing = rr.find_validation_by_identity(db_path, hypothesis_id or "adhoc",
                                              strategy_id, rh)
    if existing is not None:
        return {"record_id": existing["record_id"], "seed": seed,
                "duplicated": True,
                "result": json.loads(existing["result"]) if existing["result"] else None}

    rng = np.random.default_rng(seed)
    # 顺序抽样保证不重叠且可复现
    offsets = sorted(rng.choice(usable, size=n_windows, replace=False).tolist())

    windows = []
    for off in offsets:
        w_start = ds + off * bar_delta
        w_end = w_start + window_bars * bar_delta
        metrics, w_returns, actual_warmup, bars = _run_backtest_on_range(
            db_path, data_dir, dna, symbol, timeframe,
            str(w_start.isoformat()), str(w_end.isoformat()), warmup_bars)
        verdict, details = judge(metrics, threshold_profile,
                                 trade_returns=w_returns)
        windows.append({
            "start": w_start.isoformat(), "end": w_end.isoformat(),
            "metrics": metrics, "verdict": verdict,
        })

    returns = sorted(w["metrics"]["total_return"] for w in windows)
    import numpy as _np
    worst = min(windows, key=lambda w: w["metrics"]["total_return"])

    # 总判定最差聚合（0.2.11 三态）：任一 fail → fail；无 fail 有
    # inconclusive → inconclusive；全 pass → pass（保守性不低于原两态
    # 聚合）；all_pass 收紧为严格全 pass（A1）
    agg = _aggregate_verdicts(w["verdict"] for w in windows)
    summary = {
        "n_windows": n_windows,
        "window_bars": window_bars,
        "returns_q25": float(_np.quantile(returns, 0.25)),
        "returns_median": float(_np.quantile(returns, 0.50)),
        "returns_q75": float(_np.quantile(returns, 0.75)),
        "worst_window": worst,
        "all_pass": agg == "pass",
    }

    # 落验证记录（level=random_window；种子在 params 中持久化 → 可复现审计）
    record_id = f"rec_{uuid.uuid4().hex[:12]}"
    rr.save_validation_with_log(
        db_path, record_id=record_id, hypothesis_id=hypothesis_id or "adhoc",
        strategy_id=strategy_id, level="random_window",
        params=json.dumps({"seed": seed, "window_bars": window_bars,
                           "n_windows": n_windows, "data_start": data_start,
                           "data_end": data_end, "symbol": symbol,
                           "timeframe": timeframe}, ensure_ascii=False),
        result=json.dumps({"windows": windows, "summary": summary},
                          ensure_ascii=False),
        verdict=agg, range_hash=rh)

    return {"record_id": record_id, "seed": seed,
            "windows": windows, "summary": summary}


def run_sandbox_backtest(
    db_path: Path,
    data_dir: Path,
    *,
    strategy_id: str,
    symbol: str,
    timeframe: str,
    start: str,
    end: str,
    fee: float = 0.001,
    slippage: float = 0.0005,
    init_cash: float = 100000.0,
    hypothesis_id: Optional[str] = None,
) -> Dict[str, Any]:
    """沙盒回测（improve-mcp-agent-loop M6）：快速试错，不判门槛、不入验证记录。

    纪律内嵌：每次沙盒强制落研究日志——挂 hypothesis_id 时区间进判定
    并集（封堵"先偷看后验证"），不挂时以 adhoc 日志留痕。
    区间 bar 数超上限拒绝（防爆超时，引导走隔离验证协议）。

    Returns:
        {"strategy_id", "symbol", "timeframe", "metrics", "bars_used",
         "hypothesis_id", "sandbox": True}
    """
    # bars 上限（MCP write_timeout=60s 内留余量；50k 起点实施时校准）
    tf_minutes = _TF_MINUTES.get(timeframe, 240)
    start_ts = _to_utc(pd.Timestamp(start))
    end_ts = _to_utc(pd.Timestamp(end))
    bars = int((end_ts - start_ts).total_seconds() / 60 / tf_minutes)
    if bars > SANDBOX_MAX_BARS:
        raise ResearchError(
            "RESEARCH_SANDBOX_LIMIT",
            f"沙盒区间 {bars} bar 超上限 {SANDBOX_MAX_BARS}——隔离验证"
            "协议支持任意区间（分区间验证）",
            status=422,
            detail={"bars": bars, "max": SANDBOX_MAX_BARS,
                    "slow_path": "run_isolated_validation 支持大区间"})

    dna, strat_row = _load_strategy_dna(db_path, strategy_id)
    symbol = symbol or strat_row.get("symbol", "BTCUSDT")
    timeframe = timeframe or strat_row.get("timeframe", "4h")
    warmup_bars, _ = _derive_dna_warmup(db_path, dna)

    # trade_returns 不进沙盒响应（不判门槛不披露 CI——_ 占位解包）
    metrics, _trade_returns, actual_warmup, bars_used = _run_backtest_on_range(
        db_path, data_dir, dna, symbol, timeframe, start, end, warmup_bars,
        init_cash=init_cash, fee=fee, slippage=slippage)

    # 强制落研究日志（挂假设 → 区间进判定并集；机制：judged_ranges_union
    # 取该假设全部日志条目 data_ranges，现成无需改动）
    range_entry = {"symbol": symbol, "timeframe": timeframe,
                   "start": start, "end": end}
    rr.append_research_log(
        db_path,
        hypothesis_id=hypothesis_id,  # None → adhoc 留痕
        entry_type="sandbox_backtest",
        ref_id=strategy_id,
        data_ranges=json.dumps([range_entry], ensure_ascii=False),
        summary=f"sandbox backtest {symbol} {timeframe} [{start},{end}) "
                f"fee={fee} slippage={slippage}")

    return {"strategy_id": strategy_id, "symbol": symbol, "timeframe": timeframe,
            "metrics": metrics, "bars_used": bars_used,
            "warmup_bars": actual_warmup,
            "hypothesis_id": hypothesis_id, "sandbox": True,
            "note": "沙盒结果不构成验证证据；关联假设的区间已计入判定并集"}


def _missing_component_evidence(
    db_path: Path, plugin_sources: List[Dict[str, Any]],
) -> List[str]:
    """缺独立分量验证记录（level=component、params.column 匹配）的源清单。

    分量证据须 verdict=pass（0.2.11 D3 收紧——fail/inconclusive 的分量
    记录不算有效证据，组合加严门不开后门；当前无 component 级写入点，
    收紧落在查询语义上、零行为差异）。
    """
    component_records = [
        r for r in rr.list_validation_records(db_path, limit=200)
        if r["level"] == "component" and r["verdict"] == "pass"
    ]
    evidenced = set()
    for r in component_records:
        try:
            params = json.loads(r["params"])
            evidenced.add(params.get("column"))
        except (json.JSONDecodeError, TypeError):
            continue
    return [s["ref"] for s in plugin_sources if s["ref"] not in evidenced]


# ---------------------------------------------------------------- 分段验证
# （add-staged-validation；MCP 0.2.10「考试完整」开门件）
#
# 时段稳健性证据第二形态：与 random_window_test（盲切抽查）互补——
# 分段按市场状态归因（哪类行情不行）。九步编排全部复用既有权威：
# build_segments 切段（第二消费者）/ _run_backtest_on_range 逐段回测
# （每段独立装载=零状态渗透）/ judge 段级门槛（同一把尺量每类行情）。
#
# 关键裁决（七环节评估链冻结）：
# - Q1 可引用·叠加：挂假设且全段达标产生 level="staged_validation" 的
#   pass 记录（transition 沿用 record_id+verdict 引用语义，无 level 过滤
#   ——hypothesis_service 查证）；与隔离回测并列不替代。
# - Q2 段级门槛：judge 前置预判 total_trades<5 → insufficient_sample
#   （judge 是通用规则引擎无 insufficient 语义，不预判短段会被误判
#   fail——thresholds.py 查证）；「标注不是第三 verdict」不动全局两态。
# - Q3 双模互斥：auto（机械判读 gates 内建）与 segments 同传拒。
# - 写入双路径：挂假设 → save_validation_with_log 一体（记录+日志+
#   入并集）；adhoc → append_research_log 纯日志（validation_records.
#   hypothesis_id NOT NULL+FK——无挂靠物理不产生记录，verdict 照报、
#   record_id=None 无可引用凭据）。
# - 字段映射：build_segments 输出的段类型键 type 映射统一 label
#   （explicit 段天然 label——输出单键结构稳定）；判读统计三键随
#   auto 段透传，explicit 段天然无。

_SEGMENTATION_MODES = frozenset({"auto", "explicit"})
_REGIME_HELPER_NAMES = ("ADX", "ATR")


def _validate_explicit_segments(
    segments: List[Dict[str, Any]],
    req_start: str, req_end: str,
) -> List[Dict[str, Any]]:
    """显式段列表校验（恰好切分语义：段在请求区间内、并集恰覆盖、
    相邻相接不重叠、时间升序；三键齐备）——违反拒第一违规段。
    """
    if not segments:
        raise ResearchError(
            "RESEARCH_PARAM_INVALID",
            "explicit 模式 segments 不能为空",
            detail={"param": "segments"})
    rs, re_ = _parse_ts(req_start), _parse_ts(req_end)
    prev_end: Optional[datetime] = None
    out: List[Dict[str, Any]] = []
    for i, seg in enumerate(segments):
        missing = [k for k in ("label", "start", "end") if not seg.get(k)]
        if missing:
            raise ResearchError(
                "RESEARCH_PARAM_INVALID",
                f"段 #{i} 缺键: {missing}（须三键 {{label, start, end}}）",
                detail={"param": f"segments[{i}]", "missing": missing})
        try:
            s, e = _parse_ts(seg["start"]), _parse_ts(seg["end"])
        except Exception as exc:
            raise ResearchError(
                "RESEARCH_PARAM_INVALID",
                f"段 #{i} 时间不可解析: {seg['start']}..{seg['end']}",
                detail={"param": f"segments[{i}]"}) from exc
        if not s < e:
            raise ResearchError(
                "RESEARCH_PARAM_INVALID",
                f"段 #{i} 起点不早于终点",
                detail={"param": f"segments[{i}]", "start": seg["start"],
                        "end": seg["end"]})
        if prev_end is not None and s < prev_end:
            raise ResearchError(
                "RESEARCH_PARAM_INVALID",
                f"段 #{i} 与前段重叠或乱序（本段起点早于前段终点）",
                detail={"param": f"segments[{i}]",
                        "prev_end": prev_end.isoformat(),
                        "start": seg["start"]})
        out.append({"label": str(seg["label"]), "start": s, "end": e})
        prev_end = e
    # 恰好覆盖：首段起于请求起点、末段止于请求终点、相邻相接（缝隙
    # 表现为某段起点晚于前段终点——上面重叠检查放行 s >= prev_end，
    # 缝隙在此汇总核：并集须恰等于请求区间）
    if out[0]["start"] > rs or out[-1]["end"] < re_:
        raise ResearchError(
            "RESEARCH_PARAM_INVALID",
            "段列表未完整覆盖请求区间（首段起点/末段终点越界）",
            detail={"param": "segments",
                    "request": [req_start, req_end],
                    "covered": [out[0]["start"].isoformat(),
                                out[-1]["end"].isoformat()]})
    for i in range(1, len(out)):
        if out[i]["start"] > out[i - 1]["end"]:
            raise ResearchError(
                "RESEARCH_PARAM_INVALID",
                f"段 #{i} 与前段之间存在缝隙",
                detail={"param": f"segments[{i}]",
                        "gap": [out[i - 1]["end"].isoformat(),
                                out[i]["start"].isoformat()]})
    return out


def _auto_segments(
    data_dir: Path, symbol: str, timeframe: str,
    start: str, end: str,
) -> List[Dict[str, Any]]:
    """auto 切段：装载请求区间 → ADX/ATR default 现算 → build_segments
    → type→label 映射（判读统计三键透传；bar 数由位置差重算）。"""
    from core.analysis.regime_segments import build_segments
    from core.analysis.registry_meta import default_params_for
    from core.data.mtf_loader import find_parquet, load_and_prepare_df

    safe_symbol = re.sub(r"[^A-Za-z0-9]", "", symbol)
    if find_parquet(data_dir, safe_symbol, timeframe) is None:
        raise ResearchError(
            "RESEARCH_SYMBOL_NOT_FOUND",
            f"无数据: {symbol} {timeframe}",
            detail={"symbol": symbol, "timeframe": timeframe})
    df = load_and_prepare_df(
        data_dir, symbol, timeframe,
        data_start=start, data_end=end, compute_indicators=False)
    if df is None or df.empty:
        raise ResearchError(
            "RESEARCH_DATA_INSUFFICIENT",
            f"区间数据不足: {symbol} {timeframe} [{start}, {end})")
    if df.index.tz is None:
        df.index = df.index.tz_localize("UTC")
    from core.features.indicators import _compute_indicator
    for helper in _REGIME_HELPER_NAMES:
        for col, series in _compute_indicator(
                df, helper, default_params_for(helper)).items():
            df[col] = series
    adx_col = next(c for c in df.columns if c.startswith("adx_"))
    atr_col = next(c for c in df.columns if c.startswith("atr_"))
    raw = build_segments(df, adx_col, atr_col)
    if not raw:
        raise ResearchError(
            "RESEARCH_DATA_INSUFFICIENT",
            "区间太短切不出段（机械判读未产出任何段）",
            detail={"symbol": symbol, "timeframe": timeframe})
    return [{"label": seg["type"], "start": seg["start"], "end": seg["end"],
             "bars": int(seg["bars"]),
             "adx_mean": seg.get("adx_mean"),
             "atr_pct_mean": seg.get("atr_pct_mean"),
             "atr_pct_pct_mean": seg.get("atr_pct_pct_mean")}
            for seg in raw]


def run_staged_validation(
    db_path: Path,
    data_dir: Path,
    *,
    strategy_id: str,
    hypothesis_id: Optional[str] = None,
    symbol: str,
    timeframe: str,
    start: str,
    end: str,
    segmentation: str = "auto",
    segments: Optional[List[Dict[str, str]]] = None,
) -> Dict[str, Any]:
    """分段验证（检查⑤的分题型成绩单）。

    Args:
        strategy_id: 策略（不在册拒 STRATEGY_NOT_FOUND）。
        hypothesis_id: 可缺省——挂假设走完整证据路径（终态拒验/幂等/
            并集/扫描复用 + verdict 记录）；缺省 adhoc 纯日志留痕。
        symbol / timeframe / start / end: 请求区间（半开 UTC）。
        segmentation: "auto"（机械判读）或 "explicit"；与 segments
            同传拒（双模互斥）。
        segments: explicit 段列表 [{label, start, end}]（恰好覆盖请求
            区间、相接不重叠、升序）。

    Returns:
        {record_id, verdict, segments[], summary{worst_segment,
        spread, all_pass}, segmentation, judged_ranges}——verdict 三态
        {pass, fail, inconclusive}（最差聚合；全段样本不足=inconclusive，
        None 特例已退役）；record_id 挂假设时存在（inconclusive 记录
        record_id 照常但不可被 transition 引用——证据不足不可定论）。

    Raises:
        ResearchError: RESEARCH_STRATEGY_NOT_FOUND /
        RESEARCH_HYPOTHESIS_NOT_FOUND / RESEARCH_HYPOTHESIS_TERMINAL /
        RESEARCH_RANGE_OVERLAP / SCREEN_CANDIDATE_REUSE /
        RESEARCH_PARAM_INVALID（段列表非法/双模互斥/枚举）/
        RESEARCH_DATA_INSUFFICIENT（数据不足/切不出段）。
    """
    # ---- 校验链（固定顺序：枚举/互斥 → 挂假设前置 → 策略 → 段） ----
    if segmentation not in _SEGMENTATION_MODES:
        raise ResearchError(
            "RESEARCH_PARAM_INVALID",
            f"segmentation 须为 auto 或 explicit: {segmentation!r}",
            detail={"param": "segmentation", "value": segmentation,
                    "legal_values": sorted(_SEGMENTATION_MODES)})
    if segments is not None and segmentation == "auto":
        raise ResearchError(
            "RESEARCH_PARAM_INVALID",
            "双模互斥：auto 模式（或缺省）不得同时传 segments",
            detail={"param": "segments",
                    "hint": "显式分段须 segmentation='explicit'"})

    request_range = {"symbol": symbol, "timeframe": timeframe,
                     "start": start, "end": end}
    rh = _range_hash(request_range)

    if hypothesis_id is not None:
        # 终态拒验（isolated 同款）
        hyp = rr.get_hypothesis(db_path, hypothesis_id)
        if hyp is None:
            raise ResearchError("RESEARCH_HYPOTHESIS_NOT_FOUND",
                                f"假设不存在: {hypothesis_id}", status=404)
        if hyp["status"] != "active":
            raise ResearchError(
                "RESEARCH_HYPOTHESIS_TERMINAL",
                f"假设已终态（{hyp['status']}），拒绝验证；请登记新假设",
                status=409)
        # 幂等（isolated 同款——验证族纪律）
        existing = rr.find_validation_by_identity(
            db_path, hypothesis_id, strategy_id, rh)
        if existing is not None:
            return {
                "record_id": existing["record_id"],
                "verdict": existing["verdict"],
                "duplicated": True,
                "result": json.loads(existing["result"])
                if existing["result"] else None,
            }
        # 判定并集 + 扫描复用（isolated 同款）
        judged = judged_ranges_union(db_path, hypothesis_id)
        overlaps = overlaps_with_request(judged, start, end)
        if overlaps:
            raise ResearchError(
                "RESEARCH_RANGE_OVERLAP",
                "请求区间与判定区间重叠（判定并集=自报∪日志，漏报被"
                "日志拦截）",
                status=409,
                detail={"judged_ranges": judged, "overlaps": overlaps})
        _screen_reuse_check(db_path, hypothesis_id, request_range)
    else:
        judged = []

    # ---- 策略与 warmup ----
    dna, strat_row = _load_strategy_dna(db_path, strategy_id)
    try:
        warmup_bars, _indicator_names = _derive_dna_warmup(db_path, dna)
    except KeyError as exc:
        raise ResearchError("RESEARCH_CONDITION_INVALID", str(exc)) from exc

    # ---- 段清单（双模统一结构） ----
    if segmentation == "explicit":
        seg_list = _validate_explicit_segments(
            segments, start, end)
    else:
        seg_list = _auto_segments(data_dir, symbol, timeframe, start, end)

    # ---- 逐段回测与段级门槛 ----
    seg_results: List[Dict[str, Any]] = []
    for seg in seg_list:
        seg_start = seg["start"].isoformat()
        seg_end = seg["end"].isoformat()
        entry: Dict[str, Any] = {
            "label": seg["label"], "start": seg_start, "end": seg_end,
            "bars": seg.get("bars"),
        }
        for stat_key in ("adx_mean", "atr_pct_mean", "atr_pct_pct_mean"):
            if seg.get(stat_key) is not None:
                entry[stat_key] = seg[stat_key]
        # 段回测可行性由 warmup 前扩自然判定（前缀不足=请求区间整体
        # 前缀不足，第一段即暴露——isolated 同款语义）；段级判定统一
        # 走 judge 三段闸门（0.2.11 Q1a：样本不足段判 inconclusive，
        # 原前置预判与闸门同条件重叠已删；threshold_details 恒带——
        # 三态都有明细供归因）
        metrics, seg_returns, _actual_warmup, bars_used = _run_backtest_on_range(
            db_path, data_dir, dna, symbol, timeframe,
            seg_start, seg_end, warmup_bars)
        entry["metrics"] = metrics
        entry["bars_used"] = int(bars_used)
        seg_verdict, seg_details = judge(metrics,
                                         trade_returns=seg_returns)
        entry["segment_verdict"] = seg_verdict
        entry["threshold_details"] = seg_details
        seg_results.append(entry)

    # ---- 汇总与 verdict 映射（最差聚合三态，与随机窗口同族） ----
    # worst/spread=描述统计（全部段含 inconclusive 段按 total_return 取）；
    # all_pass=判定统计（严格全 pass）——两口径并存各有其义，无 None 特例
    verdict = _aggregate_verdicts(r["segment_verdict"] for r in seg_results)
    worst = min(seg_results, key=lambda r: r["metrics"]["total_return"])
    best = max(seg_results, key=lambda r: r["metrics"]["total_return"])
    summary = {
        "worst_segment": {"label": worst["label"],
                          "total_return": worst["metrics"]["total_return"]},
        "spread": best["metrics"]["total_return"]
        - worst["metrics"]["total_return"],
        "all_pass": verdict == "pass",
    }

    # ---- 写入双路径 ----
    record_id: Optional[str] = None
    if hypothesis_id is not None:
        record_id = f"rec_{uuid.uuid4().hex[:12]}"
        rr.save_validation_with_log(
            db_path, record_id=record_id, hypothesis_id=hypothesis_id,
            strategy_id=strategy_id, level="staged_validation",
            params=json.dumps({
                "request_range": request_range, "range_hash": rh,
                "segmentation": segmentation, "segments": [
                    {"label": seg["label"],
                     "start": seg["start"].isoformat()
                     if hasattr(seg["start"], "isoformat") else seg["start"],
                     "end": seg["end"].isoformat()
                     if hasattr(seg["end"], "isoformat") else seg["end"]}
                    for seg in seg_list]},
                ensure_ascii=False),
            result=json.dumps({"segments": seg_results, "summary": summary},
                              ensure_ascii=False),
            verdict=verdict, range_hash=rh)
    else:
        # adhoc（或无 verdict）纯日志留痕——验证记录 hypothesis_id 为
        # 非空外键，无挂靠物理不产生记录；verdict 照报、record_id=None
        rr.append_research_log(
            db_path, hypothesis_id=hypothesis_id,
            entry_type="staged_validation",
            data_ranges=json.dumps(request_range, ensure_ascii=False),
            summary=json.dumps({"segments": [
                {"label": r["label"], "verdict": r["segment_verdict"]}
                for r in seg_results], "summary": summary},
                ensure_ascii=False))
    if hypothesis_id is not None:
        judged = judged_ranges_union(db_path, hypothesis_id)

    return {
        "record_id": record_id,
        "verdict": verdict,
        "strategy_id": strategy_id,
        "hypothesis_id": hypothesis_id,
        "segmentation": segmentation,
        "request_range": request_range,
        "warmup_bars": int(warmup_bars),
        "segments": seg_results,
        "summary": summary,
        "judged_ranges": judged,
    }
