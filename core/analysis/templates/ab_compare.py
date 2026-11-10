"""A/B 对照指标模板（sandbox-and-report-supplement D4）——两组隔离
验证记录的交易级结构对照。

两组各循 ruler_compare 唯一路契约（trade_source_a/b 各为
{kind:"validation", record_id}——kind 拦截/存在性/记录级区间一致/
逐笔越界/持仓中排除全同款，校验链经公共校验件同源消费非复制）；
双记录 data_range（request_range 归一后）必须全值相等——不等 422
附两侧值（对照前提不满足，不做静默截断）。

可选 dimension∈{none,wave,box}（默认 none）：wave/box 挂载两组各自
的结构指标（识别率/捕获率 或 先到对侧比例），底账（浪池/箱通道）
按评估区间现算一次、两组共享同一底账——同底账不同策略臂的结构
对照；计算核心 import 复用 ruler_compare 的 compare_wave/compare_box
（两盘点 compute 核心单源）。

输出：两组各自指标并排（净均值/胜率/月频＋组内 CI——n 三档披露
同款）＋组间净均值差 boot_diff CI（方向 a−b，消费 bootstrap 统计件
同默认 B=3000/ci=0.95/seed=42）。

装载差异面注记（同 ruler）：demo 参考实现（r9 系列）的浪池以数据
全历史起点计 zigzag；平台按评估区间前扩 warmup_bars（默认 500）
装载，zigzag 起点效应差异以 ±1pp 门限承载。组间差另有 rng 消耗
差异面：demo（r9_d4_final.py）的 boot_diff 是模块级共享 rng 流的
后续消耗（先耗五臂 boot_ci 再差值），平台为独立 seed=42 首耗——
bootstrap 端点蒙特卡洛差（实测 ~0.05pp 量级）同由 ±1pp 门限承载。

回望性契约：全部对照指标为回望性统计——幂等可复算、不作策略条件
输入、不烧判定并集（hypothesis_id 由分派层统一拦截）。
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional

import pandas as pd

from core.analysis.hypothesis_service import ResearchError
from core.analysis.result import build_result
from core.analysis.templates.ruler_compare import (
    BOX_EDGE_WINDOW_DEFAULTS, DEFAULT_B, DEFAULT_COMPARE_WARMUP_BARS,
    DEFAULT_IDENTIFY_WINDOW, DEFAULT_SEED, DEFAULT_THRESHOLD,
    compare_box, compare_wave, load_record_trades,
    monthly_frequency_stats, net_stats, resolve_validation_record,
    validate_compare_numeric_params, validate_trade_source)
from core.analysis.warmup_loader import TF_MINUTES, to_utc

_DIMENSIONS = ("none", "wave", "box")


def _group_stats(
    trades: List[Dict[str, Any]], init_cash: float,
    *, B: int = DEFAULT_B, seed: int = DEFAULT_SEED,
) -> Dict[str, Any]:
    """单组指标：net_stats（净均值/胜率/组内 CI——n 三档同款）＋月频
    （monthly_frequency_stats 公共件——与 ruler_compare 箱模式同源）。"""
    out: Dict[str, Any] = net_stats(trades, init_cash, B=B, seed=seed)
    out.update(monthly_frequency_stats(trades))
    return out


def diff_stats(
    trades_a: List[Dict[str, Any]], init_cash_a: float,
    trades_b: List[Dict[str, Any]], init_cash_b: float,
    *, B: int = DEFAULT_B, seed: int = DEFAULT_SEED,
) -> Dict[str, Any]:
    """组间净均值差统计（计算核心——B3 demo 对账直调面）：方向
    a_minus_b（boot_diff x_minus_y 语义同款——正区间支持 a 组均值
    大于 b 组）；两侧各 n≥3 才附着 CI，否则 null 附两侧样本数
    （n 三档披露）。"""
    from core.scoring.statistics import boot_diff

    nets_a = [t["pnl"] / init_cash_a * 100.0 for t in trades_a
              if t["pnl"] is not None]
    nets_b = [t["pnl"] / init_cash_b * 100.0 for t in trades_b
              if t["pnl"] is not None]
    mean_a = (sum(nets_a) / len(nets_a)) if nets_a else None
    mean_b = (sum(nets_b) / len(nets_b)) if nets_b else None
    ci: Optional[list] = None
    if len(nets_a) >= 3 and len(nets_b) >= 3:
        lo, hi = boot_diff(nets_a, nets_b, B=B, seed=seed)
        ci = [lo, hi]
    return {
        "diff_mean_pct": (mean_a - mean_b)
        if (mean_a is not None and mean_b is not None) else None,
        "diff_ci_pct": ci,
        "n_a_for_ci": len(nets_a),
        "n_b_for_ci": len(nets_b),
        "ci_method": "bootstrap_percentile" if ci else None,
        "direction": "a_minus_b",
        "diff_caliber": "组间净均值差（a−b；各组 net_i=逐笔 PnL（已含"
                        "费滑）÷ 该组 init_cash（百分比形）后求均值）",
    }


# ----------------------------------------------------------- 模板壳

def ab_compare(
    db_path: Path, data_dir: Path, rng: Dict[str, str],
    *,
    mode: str,
    trade_source_a: Dict[str, Any],
    trade_source_b: Dict[str, Any],
    data_ranges: list[dict[str, str]],
    dimension: str = "none",
    threshold: float = DEFAULT_THRESHOLD,
    identify_window: float = DEFAULT_IDENTIFY_WINDOW,
    edge_window: Optional[int] = None,
    edge_q_low: float = 0.10,
    edge_q_high: float = 0.90,
    buffer: float = 0.25,
    B: int = DEFAULT_B,
    seed: int = DEFAULT_SEED,
) -> Dict[str, Any]:
    """A/B 对照统一入口（自装载模板壳——ruler_compare 同款形制：
    IO 面收敛在壳内，计算面委托公共校验件与 compute 核心）。

    Args:
        db_path: 研究库路径（双验证记录读取）。
        data_dir: parquet 数据目录（dimension=wave/box 底账现算装载；
            none 不消费）。
        rng: 评估区间 {symbol, timeframe, start, end}。
        mode: "ab"（固定值——与 ruler_compare 的 mode 语义并列声明）。
        trade_source_a/trade_source_b: 各为
            {"kind": "validation", "record_id": ...}。
        dimension: "none" | "wave" | "box"（默认 none）。

    Returns:
        to_dict 兼容 dict（三要素齐备）。
    """
    from core.analysis.templates.wave_inventory import (
        validate_wave_inventory_params)

    if mode != "ab":
        raise ResearchError(
            "RESEARCH_PARAM_INVALID",
            f"mode 未知: {mode!r}",
            detail={"param": "mode", "actual": mode,
                    "legal_range": ["ab"]})
    if dimension not in _DIMENSIONS:
        raise ResearchError(
            "RESEARCH_PARAM_INVALID",
            f"dimension 未知: {dimension!r}",
            detail={"param": "dimension", "actual": dimension,
                    "legal_range": list(_DIMENSIONS)})
    # 六参数域校验单源（对照族公共件——与 ruler_compare 同源消费；
    # 统一前置校验面，dimension=none 下不消费同校验）
    validate_compare_numeric_params(
        identify_window, edge_q_low, edge_q_high, buffer, B, seed)
    record_id_a = validate_trade_source(trade_source_a)
    record_id_b = validate_trade_source(trade_source_b)

    # 双记录校验链同源消费：存在性/记录级区间一致性/init_cash（各循
    # ruler 唯一路契约）→ 双记录区间相等（对照前提）
    range_a, init_cash_a = resolve_validation_record(
        db_path, record_id_a, rng)
    range_b, init_cash_b = resolve_validation_record(
        db_path, record_id_b, rng)
    if range_a != range_b:
        raise ResearchError(
            "RESEARCH_PARAM_INVALID",
            "双记录 data_range 不相等（A/B 对照须两记录区间全值相等"
            "——对照前提不满足，不做静默截断）",
            detail={"param": "trade_source_a|trade_source_b",
                    "range_a": range_a, "range_b": range_b})
    trades_a, open_a = load_record_trades(db_path, record_id_a, rng)
    trades_b, open_b = load_record_trades(db_path, record_id_b, rng)

    payload: Dict[str, Any] = {
        "mode": "ab",
        "dimension": dimension,
        "trade_source_a": {"kind": "validation", "record_id": record_id_a},
        "trade_source_b": {"kind": "validation", "record_id": record_id_b},
        "excluded_open_trades_a": int(open_a),
        "excluded_open_trades_b": int(open_b),
        "group_a": _group_stats(trades_a, init_cash_a, B=B, seed=seed),
        "group_b": _group_stats(trades_b, init_cash_b, B=B, seed=seed),
        "group_diff": diff_stats(
            trades_a, init_cash_a, trades_b, init_cash_b, B=B, seed=seed),
    }

    if dimension == "wave":
        # 底账现算一次、两组共享（同底账不同策略臂的结构对照）——
        # 装载形制同 ruler_compare wave 分支（warmup 前扩只用不算）
        from core.data.mtf_loader import load_and_prepare_df
        from core.analysis.templates.wave_inventory import (
            compute_zigzag_waves)

        validate_wave_inventory_params(threshold,
                                       DEFAULT_COMPARE_WARMUP_BARS)
        warmup = DEFAULT_COMPARE_WARMUP_BARS
        tf_minutes = TF_MINUTES.get(rng["timeframe"], 240)
        start_ts = to_utc(pd.Timestamp(rng["start"]))
        load_start = (start_ts - pd.Timedelta(
            minutes=tf_minutes * warmup)).isoformat()
        df = load_and_prepare_df(
            data_dir, rng["symbol"], rng["timeframe"],
            data_start=load_start, data_end=rng["end"],
            compute_indicators=False)
        if df is None:
            raise ResearchError(
                "RESEARCH_DATA_INSUFFICIENT",
                f"区间数据不足: {rng['symbol']} {rng['timeframe']} "
                f"[{load_start}, {rng['end']})")
        all_waves = compute_zigzag_waves(df["close"], threshold=threshold)
        waves = [w for w in all_waves if w["start"] >= start_ts]
        payload["group_a"]["wave"] = compare_wave(
            trades_a, waves, identify_window=identify_window)
        payload["group_b"]["wave"] = compare_wave(
            trades_b, waves, identify_window=identify_window)
        payload["threshold"] = float(threshold)
    elif dimension == "box":
        # 箱通道装载一次、两组共享（BoxChannel 计算核心单源在指标列
        # ——compare_box 内经 _compute_indicator 装载）
        from core.data.mtf_loader import load_and_prepare_df

        if edge_window is None:
            edge_window = BOX_EDGE_WINDOW_DEFAULTS.get(rng["timeframe"])
            if edge_window is None:
                raise ResearchError(
                    "RESEARCH_PARAM_INVALID",
                    f"timeframe {rng['timeframe']!r} 无冻结默认 "
                    f"edge_window——必填（冻结默认仅 1h=240/15m=480）",
                    detail={"param": "edge_window",
                            "timeframe": rng["timeframe"],
                            "frozen_defaults": BOX_EDGE_WINDOW_DEFAULTS})
        from core.analysis.templates.param_guard import _require_int_range
        _require_int_range(edge_window, name="edge_window", lo=1)
        tf_minutes = TF_MINUTES.get(rng["timeframe"], 240)
        start_ts = to_utc(pd.Timestamp(rng["start"]))
        load_start = (start_ts - pd.Timedelta(
            minutes=tf_minutes * edge_window)).isoformat()
        df = load_and_prepare_df(
            data_dir, rng["symbol"], rng["timeframe"],
            data_start=load_start, data_end=rng["end"],
            compute_indicators=False)
        if df is None:
            raise ResearchError(
                "RESEARCH_DATA_INSUFFICIENT",
                f"区间数据不足: {rng['symbol']} {rng['timeframe']} "
                f"[{load_start}, {rng['end']})")
        payload["group_a"]["box"] = compare_box(
            trades_a, df, edge_window=edge_window, edge_q_low=edge_q_low,
            edge_q_high=edge_q_high, buffer=buffer)
        payload["group_b"]["box"] = compare_box(
            trades_b, df, edge_window=edge_window, edge_q_low=edge_q_low,
            edge_q_high=edge_q_high, buffer=buffer)

    diff = payload["group_diff"]
    return build_result(
        "ab_compare",
        sample_size=len(trades_a) + len(trades_b),
        confidence={
            "method": "bootstrap_percentile" if diff["diff_ci_pct"]
            else "descriptive_only",
            "interval": diff["diff_ci_pct"],
            "B": int(B), "seed": int(seed),
            "deterministic": True,
        },
        data_ranges=data_ranges,
        payload=payload,
        min_samples=1,  # 0 笔记录在 load 层已拒（422 提示）
    ).to_dict()
