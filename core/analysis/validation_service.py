"""隔离回测与随机窗口编排（add-ai-research-mcp 3.2 / 3.3）。

隔离回测判定流（附.3）：
终态拒验 → 幂等查询 → 判定并集求交（重叠 409 附 judged_ranges）→
warmup 推导（max(200, 用到指标最大周期×3)）→ 数据起点不足 422 →
[warmup 起点装载算指标 → 切回请求区间回测]（指标记忆保留、结果只统计
请求区间）→ 门槛表机械判定 → validation_records+research_log 原子双落。

随机窗口（3.3）：同种子可复现（seed 进 params 持久化）；每窗独立
warmup 前扩（与隔离回测同语义）。
"""
from __future__ import annotations

import hashlib
import json
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd

from core.analysis.hypothesis_service import ResearchError
from core.analysis.isolation import judged_ranges_union, overlaps_with_request
from core.analysis.registry_meta import derive_warmup
from core.analysis.thresholds import DEFAULT_PROFILE, judge
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


def _run_backtest_on_range(
    data_dir: Path, dna, symbol: str, timeframe: str,
    start: str, end: str, warmup_bars: int,
    *, init_cash: float = 100000.0,
    fee: float = 0.001, slippage: float = 0.0005,
) -> Tuple[Dict[str, Any], int, int]:
    """[start-warmup, end) 装载算指标 → 切回 [start,end) 回测。

    Returns:
        (metrics, actual_warmup_bars, bars_used)
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

    engine = bt_engine_mod.BacktestEngine(
        init_cash=init_cash, fee=fee, slippage=slippage)
    result = engine.run(dna, bt_df, dfs_by_timeframe=dfs_by_timeframe)
    metrics = {
        "total_return": float(result.total_return),
        "sharpe_ratio": float(result.sharpe_ratio),
        "max_drawdown": float(result.max_drawdown),
        "win_rate": float(result.win_rate) if result.win_rate is not None else None,
        "total_trades": int(result.total_trades),
        "liquidated": bool(result.liquidated),
    }
    return metrics, actual_warmup_bars, len(bt_df)


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

    # 策略与 DNA
    dna, strat_row = _load_strategy_dna(db_path, strategy_id)
    symbol = request_range.get("symbol") or strat_row.get("symbol", "BTCUSDT")
    timeframe = request_range.get("timeframe") or strat_row.get("timeframe", "4h")

    # warmup 推导
    indicator_names = _dna_indicator_names(dna)
    try:
        warmup_bars = derive_warmup(indicator_names)
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
    metrics, actual_warmup, bars_used = _run_backtest_on_range(
        data_dir, dna, symbol, timeframe,
        request_range["start"], request_range["end"], warmup_bars)

    # 门槛机械判定（组合用收紧 profile）
    verdict, details = judge(metrics, effective_profile)

    # 双落（原子）
    record_id = f"rec_{uuid.uuid4().hex[:12]}"
    result_json = json.dumps({
        "metrics": metrics,
        "threshold_details": details,
        "warmup": {"bars": actual_warmup, "derived": warmup_bars,
                   "basis": "max(200, max_indicator_period*3)",
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
                   "basis": "max(200, max_indicator_period*3)"},
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
    indicator_names = _dna_indicator_names(dna)
    warmup_bars = derive_warmup(indicator_names)
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
        metrics, actual_warmup, bars = _run_backtest_on_range(
            data_dir, dna, symbol, timeframe,
            str(w_start.isoformat()), str(w_end.isoformat()), warmup_bars)
        verdict, details = judge(metrics, threshold_profile)
        windows.append({
            "start": w_start.isoformat(), "end": w_end.isoformat(),
            "metrics": metrics, "verdict": verdict,
        })

    returns = sorted(w["metrics"]["total_return"] for w in windows)
    import numpy as _np
    worst = min(windows, key=lambda w: w["metrics"]["total_return"])

    summary = {
        "n_windows": n_windows,
        "window_bars": window_bars,
        "returns_q25": float(_np.quantile(returns, 0.25)),
        "returns_median": float(_np.quantile(returns, 0.50)),
        "returns_q75": float(_np.quantile(returns, 0.75)),
        "worst_window": worst,
        "all_pass": all(w["verdict"] == "pass" for w in windows),
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
        verdict="pass" if summary["all_pass"] else "fail", range_hash=rh)

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
    indicator_names = _dna_indicator_names(dna)
    warmup_bars = derive_warmup(indicator_names)

    metrics, actual_warmup, bars_used = _run_backtest_on_range(
        data_dir, dna, symbol, timeframe, start, end, warmup_bars,
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
    """缺独立分量验证记录（level=component、params.column 匹配）的源清单。"""
    component_records = [
        r for r in rr.list_validation_records(db_path, limit=200)
        if r["level"] == "component"
    ]
    evidenced = set()
    for r in component_records:
        try:
            params = json.loads(r["params"])
            evidenced.add(params.get("column"))
        except (json.JSONDecodeError, TypeError):
            continue
    return [s["ref"] for s in plugin_sources if s["ref"] not in evidenced]
