"""对照指标模板（add-evaluation-ruler）——两种盈利模式的验收尺子。

模式一（浪模式）：策略交易清单 × 浪底账 → 识别率（策略吃到主浪没
有）/捕获率分布（吃到多少）＋净均值（费后）bootstrap CI＋胜率。
模式二（箱体模式）：交易清单 × 分位沿通道 → 先到对侧比例（箱体
单子是不是先到对侧再触止损）＋三分类计数＋净均值 CI＋月频。

交易清单来源唯一路：隔离验证记录（trade_source={kind:"validation",
record_id}）——沙盒回测不落库逐笔（平台防污染纪律），kind="backtest"
即 422 附指引；校验顺序=kind 值域拦截在前（纪律拦截优先于引用解析），
合法 kind 后做引用存在性校验。区间一致性双级校验：记录级（验证记录
request_range 与评估区间时间戳归一后全值相等，不等 422 附两侧归一值
——不做静默截断）＋逐笔级（entry_time/exit_time 落评估区间，持仓中
行除外、排除并披露计数）。

底账内部现算：浪模式复用 compute_zigzag_waves（不复制算法、不引用
既有任务持久化产物）；箱模式计算核心单源在 BoxChannel 指标列
（add-box-capability D3 装载层消费——upper/lower/break 经
_compute_indicator 装载，与策略条件链同路，零独立内核）。
装载差异面注记：demo 参考实现（r9 系列）的浪池以数据全历史起点计
zigzag；平台按评估区间前扩 warmup_bars（默认 500）装载，区间远端
短前扩下首段方向判定可能与全历史起点分立（zigzag 起点效应）——
复算对账以 ±1pp 门限承载该差异面。

回望性契约：全部对照指标为回望性统计——幂等可复算、不作策略条件
输入、不烧判定并集（hypothesis_id 由分派层统一拦截）。
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional

import pandas as pd

from core.analysis.errors import ResearchError
from core.analysis.result import build_result
from core.analysis.templates.param_guard import (
    _require_float_range, _require_int_range)
from core.analysis.warmup_loader import TF_MINUTES, to_utc
from core.scoring.statistics import BOOT_B_MAX

# 浪模式参数默认（demo 冻结值）
DEFAULT_THRESHOLD = 0.05
DEFAULT_IDENTIFY_WINDOW = 0.3
DEFAULT_COMPARE_WARMUP_BARS = 500
# 箱模式边沿参数冻结默认（demo 冻结值：edge_window 感知周期）
BOX_EDGE_WINDOW_DEFAULTS: dict[str, int] = {"1h": 240, "15m": 480}
DEFAULT_EDGE_Q_LOW = 0.10
DEFAULT_EDGE_Q_HIGH = 0.90
DEFAULT_BOX_BUFFER = 0.25
# bootstrap 附着默认（与 scoring 统计件一致）
DEFAULT_B = 3000
DEFAULT_SEED = 42

_BACKTEST_GUIDANCE = (
    "沙盒回测不落库逐笔（防污染纪律）；对照指标请引用隔离验证记录 "
    "record_id")


# ----------------------------------------------------------- 数据来源与校验

def validate_trade_source(trade_source: Any) -> str:
    """trade_source 唯一合法形制校验（kind 拦截在前——backtest 案不
    做 record_id 存在性解析，纪律拦截优先于引用解析）。"""
    if not isinstance(trade_source, dict):
        raise ResearchError(
            "RESEARCH_PARAM_INVALID",
            "trade_source 须为 {kind, record_id} 对象",
            detail={"param": "trade_source",
                    "legal_form": '{"kind": "validation", '
                                  '"record_id": "rec_..."}'})
    kind = trade_source.get("kind")
    if kind == "backtest":
        raise ResearchError(
            "RESEARCH_PARAM_INVALID",
            f"trade_source.kind=backtest 被拒绝：{_BACKTEST_GUIDANCE}",
            detail={"param": "trade_source.kind", "actual": kind,
                    "guidance": _BACKTEST_GUIDANCE})
    if kind != "validation":
        raise ResearchError(
            "RESEARCH_PARAM_INVALID",
            f"trade_source.kind 未知: {kind!r}",
            detail={"param": "trade_source.kind", "actual": kind,
                    "legal_form": '{"kind": "validation", '
                                  '"record_id": "rec_..."}'})
    record_id = trade_source.get("record_id")
    if not isinstance(record_id, str) or not record_id:
        raise ResearchError(
            "RESEARCH_PARAM_INVALID",
            "trade_source.record_id 须为非空字符串",
            detail={"param": "trade_source.record_id",
                    "legal_form": "隔离验证记录 record_id（rec_...）"})
    return record_id


def resolve_validation_record(
    db_path: Path, record_id: str, rng: Dict[str, str],
) -> tuple[Dict[str, str], float]:
    """验证记录存在性与记录级区间一致性校验（公共校验件——
    ruler_compare 与 ab_compare 同源消费，sandbox-and-report-supplement
    D4「校验链复用非复制」提取）。

    Returns:
        (rec_range_norm, init_cash)。rec_range_norm 为记录 request_range
        归一后形制 {symbol, timeframe, start, end}（时间戳 to_utc 归一、
        isoformat 出站——双记录对照的相等比较基准）。

    Raises:
        ResearchError: 引用不存在（附合法形制）；记录无单区间
            request_range；symbol/timeframe 不一致；记录级区间归一后
            不相等（附两侧归一值）；缺实际生效 init_cash。
    """
    from core.persistence.repository import research as rr

    row = rr.get_validation_record_by_id(db_path, record_id)
    if row is None:
        raise ResearchError(
            "RESEARCH_PARAM_INVALID",
            f"验证记录不存在: {record_id!r}",
            detail={"param": "trade_source.record_id", "actual": record_id,
                    "legal_form": "隔离验证记录 record_id（rec_...）"})
    try:
        params = json.loads(row["params"]) if row["params"] else {}
    except (json.JSONDecodeError, TypeError):
        params = {}
    req_range = params.get("request_range") if isinstance(params, dict) \
        else None
    if not isinstance(req_range, dict) or not all(
            req_range.get(k) for k in ("symbol", "timeframe",
                                       "start", "end")):
        raise ResearchError(
            "RESEARCH_PARAM_INVALID",
            "引用的验证记录无单区间 request_range（对照须记录级区间与"
            "评估区间全值相等——多窗/无区间形制记录不可用于对照）",
            detail={"param": "trade_source.record_id",
                    "record_id": record_id})
    for key in ("symbol", "timeframe"):
        if req_range[key] != rng[key]:
            raise ResearchError(
                "RESEARCH_PARAM_INVALID",
                f"验证记录 {key} 与评估区间不一致: 记录 "
                f"{req_range[key]!r} vs 评估 {rng[key]!r}",
                detail={"param": f"trade_source.record.{key}",
                        "record": req_range[key], "request": rng[key]})
    rec_start = to_utc(pd.Timestamp(req_range["start"]))
    rec_end = to_utc(pd.Timestamp(req_range["end"]))
    req_start = to_utc(pd.Timestamp(rng["start"]))
    req_end = to_utc(pd.Timestamp(rng["end"]))
    if (rec_start, rec_end) != (req_start, req_end):
        raise ResearchError(
            "RESEARCH_PARAM_INVALID",
            "验证记录区间与评估区间不一致（时间戳归一后须全值相等，"
            "不做静默截断）",
            detail={"param": "trade_source.record.range",
                    "record_normalized": [rec_start.isoformat(),
                                          rec_end.isoformat()],
                    "request_normalized": [req_start.isoformat(),
                                           req_end.isoformat()]})
    init_cash = params.get("init_cash")
    if not isinstance(init_cash, (int, float)) or init_cash <= 0:
        raise ResearchError(
            "RESEARCH_PARAM_INVALID",
            "引用的验证记录缺实际生效 init_cash（净均值口径分母）",
            detail={"param": "trade_source.record.init_cash",
                    "record_id": record_id})
    rec_range_norm = {
        "symbol": req_range["symbol"], "timeframe": req_range["timeframe"],
        "start": rec_start.isoformat(), "end": rec_end.isoformat()}
    return rec_range_norm, float(init_cash)


def load_record_trades(
    db_path: Path, record_id: str, rng: Dict[str, str],
) -> tuple[List[Dict[str, Any]], int]:
    """验证记录逐笔装载与逐笔级校验（公共校验件——持仓中排除/逐笔
    区间越界拒绝；ruler_compare 与 ab_compare 同源消费）。

    Args:
        rng: 评估区间（记录级一致性已由 resolve_validation_record
            前置保证——逐笔越界判定归一自评估区间）。

    Returns:
        (逐笔行列表, excluded_open_trades)。逐笔行含归一后的
        entry_time/exit_time（pd.Timestamp）/direction（long|short）
        /entry_price/exit_price/pnl/seq；持仓中行已排除。

    Raises:
        ResearchError: 记录无逐笔行；逐笔越界（附 seq+时间）；
            交易清单全部为持仓中行。
    """
    from core.persistence.repository import research as rr

    req_start = to_utc(pd.Timestamp(rng["start"]))
    req_end = to_utc(pd.Timestamp(rng["end"]))
    executions = rr.get_validation_executions(db_path, record_id)
    trades: List[Dict[str, Any]] = []
    for exec_i, exe in enumerate(executions):
        for t in exe.get("trades", []):
            entry_ts = to_utc(pd.Timestamp(t["entry_time"]))
            exit_raw = t.get("exit_time")
            exit_ts = (None if exit_raw in (None, "", "NaT")
                       else to_utc(pd.Timestamp(exit_raw)))
            trades.append({
                "seq": (t["seq"], exec_i),
                "entry_time": entry_ts, "exit_time": exit_ts,
                "entry_price": float(t["entry_price"]),
                "exit_price": float(t["exit_price"]),
                "size": float(t["size"]),
                "direction": str(t["direction"]).lower(),
                "pnl": (None if t.get("pnl") is None
                        else float(t["pnl"])),
            })
    if not trades:
        raise ResearchError(
            "RESEARCH_PARAM_INVALID",
            "引用的验证记录无逐笔行（对照需要交易清单）",
            detail={"param": "trade_source.record_id",
                    "record_id": record_id})

    open_excluded = 0
    closed: List[Dict[str, Any]] = []
    for t in trades:
        if t["exit_time"] is None:
            open_excluded += 1
            continue
        for key, ts in (("entry_time", t["entry_time"]),
                        ("exit_time", t["exit_time"])):
            if not (req_start <= ts < req_end):
                raise ResearchError(
                    "RESEARCH_PARAM_INVALID",
                    f"交易 {key} 落在评估区间外（seq={t['seq'][0]}, "
                    f"time={ts.isoformat()}）——不做静默截断",
                    detail={"param": "trades",
                            "seq": t["seq"][0],
                            "violating": key,
                            "time": ts.isoformat(),
                            "range": [req_start.isoformat(),
                                      req_end.isoformat()]})
        closed.append(t)
    if not closed and open_excluded:
        raise ResearchError(
            "RESEARCH_PARAM_INVALID",
            "交易清单全部为持仓中行（无已出场交易可对照）",
            detail={"param": "trades",
                    "excluded_open_trades": open_excluded})
    return closed, open_excluded


def load_validation_trades(
    db_path: Path, record_id: str, rng: Dict[str, str],
) -> tuple[List[Dict[str, Any]], float, int]:
    """验证记录读取与区间一致性双级校验（任务 5.1；组合壳——校验
    本体在 resolve_validation_record/load_record_trades 两公共件，
    供 ab_compare 双记录同源消费）。

    Returns:
        (逐笔行列表, init_cash, excluded_open_trades)。逐笔行含归一后
        的 entry_time/exit_time（pd.Timestamp）/direction（long|short）
        /entry_price/exit_price/pnl/seq；持仓中行已排除。

    Raises:
        ResearchError: 引用不存在（附合法形制）；记录无单区间
            request_range；symbol/timeframe 不一致；记录级区间归一后
            不相等（附两侧归一值）；逐笔越界（附 seq+时间）。
    """
    _, init_cash = resolve_validation_record(db_path, record_id, rng)
    trades, open_excluded = load_record_trades(db_path, record_id, rng)
    return trades, init_cash, open_excluded


# ----------------------------------------------------------- 浪模式核心

def compare_wave(
    trades: List[Dict[str, Any]],
    waves: List[Dict[str, Any]],
    *,
    identify_window: float = DEFAULT_IDENTIFY_WINDOW,
) -> Dict[str, Any]:
    """浪模式对照计算核心（纯函数——交易行×浪清单）。

    浪-笔匹配：入场时点落入 [浪起, 浪止] 闭区间且方向一致（多浪
    重叠取时间序第一匹配）；识别=入场距浪起 ≤ identify_window×浪长
    （时间比例，含等号）；识别率按浪去重；捕获=方向化价差÷浪幅。
    n<3 时 CI=null 附样本数（附着层契约——bootstrap 全在 net_stats）。
    """
    n_waves = len(waves)
    matched: List[Optional[Dict[str, Any]]] = []
    identified_wave_ids: set = set()
    unmatched_direction = 0
    for t in trades:
        hit = None
        for w_i, w in enumerate(waves):
            if (w["start"] <= t["entry_time"] <= w["end"]
                    and w["direction"] == t["direction"]):
                hit = (w_i, w)  # 时间序第一匹配（共享 pivot 的重叠段
                break           #  上方向不符的先跳过——demo 同构）
        if hit is None:
            # 闭区间内有浪但方向不一致，或区间无浪——细分披露
            in_any = any(w["start"] <= t["entry_time"] <= w["end"]
                         for w in waves)
            if in_any:
                unmatched_direction += 1
            matched.append(None)
            continue
        w_i, w = hit
        span = abs(w["end_price"] - w["start_price"])
        dur = (w["end"] - w["start"]) or pd.Timedelta(0)
        identified = (t["entry_time"] - w["start"]) <= \
            identify_window * dur
        if identified:
            identified_wave_ids.add(w_i)
        capture = None
        if span > 0:
            raw = ((t["exit_price"] - t["entry_price"]) if
                   t["direction"] == "long"
                   else (t["entry_price"] - t["exit_price"]))
            capture = raw / span
        matched.append({"wave_index": w_i, "identified": bool(identified),
                        "capture": capture})

    caps = [m["capture"] for m in matched
            if m is not None and m["capture"] is not None]
    ided_caps = [m["capture"] for m in matched
                 if m is not None and m["identified"]
                 and m["capture"] is not None]
    out: Dict[str, Any] = {
        "n_trades": len(trades),
        "n_waves": n_waves,
        "identify_window": float(identify_window),
        "unmatched_trades": sum(1 for m in matched if m is None),
        "unmatched_direction_trades": unmatched_direction,
        "no_waves": n_waves == 0,
        # 笔级明细（诊断披露：seq/匹配浪/识别/捕获——多品种研究
        # 分组调用后合并统计的口径依据）
        "per_trade": [
            {"seq": t["seq"][0], "matched": m is not None,
             **({"wave_index": m["wave_index"],
                 "identified": m["identified"],
                 "capture": m["capture"]} if m else {})}
            for t, m in zip(trades, matched)],
    }
    if n_waves == 0:
        out["identification_rate"] = None
        out["capture_median"] = None
        out["capture_mean"] = None
        out["disclosure"] = "区间无浪：识别率/捕获率 null（不伪造数值）"
    else:
        out["identification_rate"] = len(identified_wave_ids) / n_waves
        out["identified_waves"] = len(identified_wave_ids)
        out["capture_median"] = (float(pd.Series(ided_caps).median())
                                 if ided_caps else None)
        out["capture_mean"] = (float(sum(ided_caps) / len(ided_caps))
                               if ided_caps else None)
    out["capture_q25"] = (float(pd.Series(ided_caps).quantile(0.25))
                          if ided_caps else None)
    out["capture_q75"] = (float(pd.Series(ided_caps).quantile(0.75))
                          if ided_caps else None)
    return out


# ----------------------------------------------------------- 箱模式核心

def compare_box(
    trades: List[Dict[str, Any]],
    df: pd.DataFrame,
    *,
    edge_window: int,
    edge_q_low: float = DEFAULT_EDGE_Q_LOW,
    edge_q_high: float = DEFAULT_EDGE_Q_HIGH,
    buffer: float = DEFAULT_BOX_BUFFER,
) -> Dict[str, Any]:
    """箱模式对照计算核心（装载层消费 BoxChannel——add-box-capability
    D3 单源迁移：边沿计算核心单源在指标列，与策略条件链同路）。

    边沿=BoxChannel 指标列（upper/lower=滚动分位通道前一根取值
    shift(1)，防当根自引用）；对侧=逐 bar 动态沿当根判定（含入场
    当根即触）；止损推定线守门三句：①公式单源在 BoxChannel 列
    （break_low/break_high=沿±buffer×通道宽——本函数零独立公式）
    ②取值 e−1 根列值（入场前一根）③冻结语义在消费侧（入场时点
    冻结常数线，判定窗内不随 bar 滚动——禁逐 bar 消费滚动列值做
    分类判定）。序判定=逐 bar 时间序先触者归类（同 bar 对侧优先，
    demo 同构）；判定窗 [入场 bar, 出场 bar)（出场当根收盘已离场
    不判定）；至出场均未触=其他（计入分母并单独披露）。
    """
    from core.features.indicators import _compute_indicator
    from core.features.registry import resolve_indicator_column

    params = {"edge_window": edge_window, "q_low": float(edge_q_low),
              "q_high": float(edge_q_high), "buffer": float(buffer)}
    cols = _compute_indicator(df, "BoxChannel", params)
    q_low_s = cols[resolve_indicator_column("BoxChannel", params, "lower")]
    q_high_s = cols[resolve_indicator_column("BoxChannel", params, "upper")]
    bl_s = cols[resolve_indicator_column(
        "BoxChannel", params, "break_low")]
    bh_s = cols[resolve_indicator_column(
        "BoxChannel", params, "break_high")]

    n_opposite = n_stop = n_other = 0
    for t in trades:
        loc = df.index.get_indexer(
            [t["entry_time"], t["exit_time"]], method="nearest")
        e_i, x_i = int(loc[0]), int(loc[1])
        # 入场前一根破坏线列值（入场时点冻结常数线——守门三句 ②③；
        # e=0 时前一根无列值=无推定止损线，仅对侧判定——防御披露态）
        sl_line = None
        if e_i >= 1:
            sl_line = (float(bl_s.iloc[e_i - 1])
                       if t["direction"] == "long"
                       else float(bh_s.iloc[e_i - 1]))
        reason = "other"
        for j in range(e_i, x_i):  # 出场当根不判定（已离场）
            c = float(df["close"].iloc[j])
            q10_j = float(q_low_s.iloc[j])
            q90_j = float(q_high_s.iloc[j])
            if t["direction"] == "long":
                if c >= q90_j:
                    reason = "opposite"
                    break
                if sl_line is not None and c <= sl_line:
                    reason = "stop"
                    break
            else:
                if c <= q10_j:
                    reason = "opposite"
                    break
                if sl_line is not None and c >= sl_line:
                    reason = "stop"
                    break
        if reason == "opposite":
            n_opposite += 1
        elif reason == "stop":
            n_stop += 1
        else:
            n_other += 1

    n_total = len(trades)
    return {
        "n_trades": n_total,
        "n_opposite": n_opposite,
        "n_stop_line": n_stop,
        "n_other": n_other,
        "opposite_rate": (n_opposite / n_total) if n_total else None,
        "edge_window": edge_window,
        "edge_q_low": float(edge_q_low), "edge_q_high": float(edge_q_high),
        "buffer": float(buffer),
        # 月频口径单源在 monthly_frequency_stats（sandbox-report-
        # supplement 对质②提取——本处与 ab_compare 同源消费）
        **monthly_frequency_stats(trades),
    }


# ----------------------------------------------------------- 净值侧统计

def net_stats(
    trades: List[Dict[str, Any]], init_cash: float,
    *, B: int = DEFAULT_B, seed: int = DEFAULT_SEED,
) -> Dict[str, Any]:
    """费后净均值统计（公共件——ruler_compare 与 ab_compare 同源
    消费）：net_i = pnl_i（已含费滑）÷ init_cash ×100。"""
    from core.scoring.statistics import boot_ci

    nets = [t["pnl"] / init_cash * 100.0 for t in trades
            if t["pnl"] is not None]
    ci: Optional[list] = None
    if len(nets) >= 3:
        lo, hi = boot_ci(nets, B=B, seed=seed)
        ci = [lo, hi]
    return {
        "net_mean_pct": (sum(nets) / len(nets)) if nets else None,
        "net_ci_pct": ci,
        "n_for_ci": len(nets),
        "ci_method": "bootstrap_percentile" if ci else None,
        "win_rate": (sum(1 for v in nets if v > 0) / len(nets))
        if nets else None,
        "net_caliber": "逐笔 PnL（已含费滑）÷ init_cash（百分比形）",
    }


def monthly_frequency_stats(
    trades: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """月频统计（公共件——ruler_compare 箱模式与 ab_compare 两组指标
    同源消费，sandbox-and-report-supplement 对质②提取）：分母=交易
    月份去重计数（entry_time YYYY-MM，下限 1）。"""
    months = ({t["entry_time"].strftime("%Y-%m") for t in trades}
              if trades else set())
    n_total = len(trades)
    return {
        "monthly_frequency": (n_total / max(len(months), 1))
        if n_total else None,
        "trades_months_distinct": len(months),
    }


# ----------------------------------------------------------- 对照公共校验件

def validate_compare_numeric_params(
    identify_window: float, edge_q_low: float, edge_q_high: float,
    buffer: float, B: int, seed: int,
) -> None:
    """对照族数值参数校验单源（公共件——ruler_compare 与 ab_compare
    两壳同源消费，sandbox-and-report-supplement 对质①提取）：六参数
    域校验＋edge_q 联合＋B/seed 前置 422。mode/dimension 值校验各壳
    自留（值域不同构）。

    Raises:
        ResearchError: 任一参数越域（422——bool 排除＋字符串数字拒，
            委托 param_guard 单源；B/seed 非法不前置则裸穿 boot 统计
            件/np 层——bool 排除为 int 子类陷阱防御）。
    """
    # 数值参数校验委托 param_guard 单源（bool 排除＋字符串数字拒——
    # 原 float() 静默转换会让 'abc' 裸 ValueError 落 500 与 '0.5'
    # 静默放行并存；bool(True) 经 float 转 1.0 落域内同族陷阱）
    _require_float_range(
        identify_window, name="identify_window", lo=0, hi=1,
        lo_excl=True)
    _require_float_range(
        edge_q_low, name="edge_q_low", lo=0, hi=1,
        lo_excl=True, hi_excl=True)
    _require_float_range(
        edge_q_high, name="edge_q_high", lo=0, hi=1,
        lo_excl=True, hi_excl=True)
    if edge_q_low >= edge_q_high:
        raise ResearchError(
            "RESEARCH_PARAM_INVALID",
            "edge_q_low/edge_q_high 须在 (0,1) 且 low < high",
            detail={"param": "edge_q_low|edge_q_high",
                    "actual": [edge_q_low, edge_q_high],
                    "legal_range": "0 < edge_q_low < edge_q_high < 1"})
    _require_float_range(buffer, name="buffer", lo=0)
    # B/seed 前置 422（condition_stats significance 同族契约）：不前置
    # 则两条裸穿路径——n≥3 时 B/seed 非法直落 boot_ci/np 层裸
    # ValueError/TypeError；n<3 时 B=3000.5 走 confidence 披露 int(B)
    # 静默截断。bool 排除（int 子类陷阱——True 静默当 1 次重抽样）。
    if (isinstance(B, bool) or not isinstance(B, int)
            or not 1 <= B <= BOOT_B_MAX):
        raise ResearchError(
            "RESEARCH_PARAM_INVALID",
            f"B 须为 1~{BOOT_B_MAX} 正整数: {B!r}",
            detail={"param": "B", "actual": B,
                    "legal_range": f"1..{BOOT_B_MAX}"})
    if isinstance(seed, bool) or not isinstance(seed, int):
        raise ResearchError(
            "RESEARCH_PARAM_INVALID",
            f"seed 须为整数: {seed!r}",
            detail={"param": "seed", "actual": seed,
                    "legal_form": "整数（随机种子）"})


# ----------------------------------------------------------- 模板壳

def ruler_compare(
    db_path: Path, data_dir: Path, rng: Dict[str, str],
    *,
    mode: str,
    trade_source: Dict[str, Any],
    data_ranges: list[dict[str, str]],
    threshold: float = DEFAULT_THRESHOLD,
    identify_window: float = DEFAULT_IDENTIFY_WINDOW,
    edge_window: Optional[int] = None,
    edge_q_low: float = DEFAULT_EDGE_Q_LOW,
    edge_q_high: float = DEFAULT_EDGE_Q_HIGH,
    buffer: float = DEFAULT_BOX_BUFFER,
    B: int = DEFAULT_B,
    seed: int = DEFAULT_SEED,
) -> Dict[str, Any]:
    """对照指标统一入口（自装载模板壳——exit_sweep 先例：IO 面收敛
    在壳内，计算面委托 compute 核心）。

    Args:
        db_path: 研究库路径（验证记录读取）。
        data_dir: parquet 数据目录（底账现算装载）。
        rng: 评估区间 {symbol, timeframe, start, end}。
        mode: "wave" | "box"。
        trade_source: {"kind": "validation", "record_id": ...}。

    Returns:
        to_dict 兼容 dict（三要素齐备）。
    """
    from core.data.mtf_loader import load_and_prepare_df
    from core.analysis.templates.wave_inventory import (
        compute_zigzag_waves, validate_wave_inventory_params)

    if mode not in ("wave", "box"):
        raise ResearchError(
            "RESEARCH_PARAM_INVALID",
            f"mode 未知: {mode!r}",
            detail={"param": "mode", "actual": mode,
                    "legal_range": ["wave", "box"]})
    # 六参数域校验单源（对照族公共件——与 ab_compare 同源消费）
    validate_compare_numeric_params(
        identify_window, edge_q_low, edge_q_high, buffer, B, seed)
    record_id = validate_trade_source(trade_source)

    # 记录级+逐笔级校验（5.1）与交易清单装载
    trades, init_cash, excluded_open = load_validation_trades(
        db_path, record_id, rng)

    tf_minutes = TF_MINUTES.get(rng["timeframe"], 240)
    start_ts = to_utc(pd.Timestamp(rng["start"]))

    payload: Dict[str, Any] = {
        "mode": mode,
        "trade_source": {"kind": "validation", "record_id": record_id},
        "n_trades": len(trades),
        "excluded_open_trades": int(excluded_open),
    }

    if mode == "wave":
        validate_wave_inventory_params(threshold,
                                       DEFAULT_COMPARE_WARMUP_BARS)
        warmup = DEFAULT_COMPARE_WARMUP_BARS
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
        wave_stats = compare_wave(
            trades, waves, identify_window=identify_window)
        net = net_stats(trades, init_cash, B=B, seed=seed)
        payload["wave"] = wave_stats
        payload["threshold"] = float(threshold)
        payload.update(net)
        sample_size = len(trades)
    else:
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
        _require_int_range(edge_window, name="edge_window", lo=1)
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
        box_stats = compare_box(
            trades, df, edge_window=edge_window, edge_q_low=edge_q_low,
            edge_q_high=edge_q_high, buffer=buffer)
        net = net_stats(trades, init_cash, B=B, seed=seed)
        payload["box"] = box_stats
        payload.update(net)
        sample_size = len(trades)

    return build_result(
        "ruler_compare",
        sample_size=sample_size,
        confidence={
            "method": "bootstrap_percentile" if payload.get("net_ci_pct")
            else "descriptive_only",
            "interval": payload.get("net_ci_pct"),
            "B": int(B), "seed": int(seed),
            "deterministic": True,
        },
        data_ranges=data_ranges,
        payload=payload,
        min_samples=1,  # n_trades=0 走既有拒绝路径（422 提示）
    ).to_dict()
