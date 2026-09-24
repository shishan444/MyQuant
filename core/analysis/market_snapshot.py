"""时点行情快照服务（add-market-snapshot 1.2）。

六步研究流程第①步的工程化：一次调用返回多周期（缺省 3d/1d/4h/1h）
各自的五维行情卡——trend/momentum/volatility/volume/derivatives。
读数为 as_of 时点（该根 bar）的当前值，90 日仅为分位回看窗口
（当前值在近 90 日同类值中的位置）；不产生区间聚合判断（区间
分段归 regime_split），不进入假设判定并集，每次调用落 research_log
留痕（entry_type=market_snapshot，hypothesis_id=None adhoc 形制）。

纯拼装纪律：七指标全部经 core.features.indicators._compute_indicator
现算（唯一指标定义源；列名经 resolve_indicator_column 解析，不在本层
复写命名规则）；装载复用 load_with_warmup（min_warmup_bars 叠加 90 日
分位窗口需求）；分位照 OIPriceDivergence 的 rolling rank 尾值/len
形式取时点等价实现。

as_of 防前视在装载层成立：start=end=as_of 闭端装载（load_and_prepare_df
的 data_end 为 df.index <= end），之后的数据不进入内存；as_of 缺省经
一次边界探测取最新一根 bar。as_of 指定历史时点时全部读数与分位窗口
截至该位置（决策 5：全序列计算后取 index <= as_of 的最后位置——
指标列已含 warmup 前缀记忆）。

derivatives 三态容缺（design 决策 9）：以 funding_rate/open_interest
原始列在分位窗内的有效覆盖为准——无 parquet → 无列 → 标注无数据；
有列但窗内原始值全 NaN（derivatives_merger ffill 头部缺口）→ 标注
无数据；禁止采信 FundingZScore 输出列 funding_zscore_30 的值（其
fillna(0) 在头部缺口区是假 0，indicators.py:298）；窗内有有效覆盖 →
正常读数，分位只用窗内原始值有效区的指标值，window_bars 如实声明
有效 bar 数。
"""
from __future__ import annotations

import json
import math
import re
from pathlib import Path
from typing import Any, Optional, Union

import pandas as pd

from core.analysis.hypothesis_service import ResearchError
from core.analysis.warmup_loader import TF_MINUTES, load_with_warmup

# 缺省四周期（决策 10：取值为 TF_MINUTES 在册周期的任意子集）
DEFAULT_TIMEFRAMES: tuple[str, ...] = ("3d", "1d", "4h", "1h")

_WINDOW_DAYS = 90             # 分位回看窗口（读数恒为 as_of 时点值）
_MAX_SERIALIZED_BYTES = 2048  # 响应体积硬约束（AI 报表纪律，超限结构化拒绝）

# 七指标口径（spec 契约参数，全部 INDICATOR_REGISTRY 在册）
_INDICATOR_SPECS: tuple[tuple[str, dict[str, Any]], ...] = (
    ("ADX", {"period": 14}),
    ("RSI", {"period": 14}),
    ("ATR", {"period": 14}),
    ("BB", {"period": 20, "std": 2.0}),
    ("RVOL", {"period": 20}),
    ("FundingZScore", {"period": 30}),
    ("OI_ChangeRate", {"period": 14}),
)
_INDICATOR_NAMES = [name for name, _ in _INDICATOR_SPECS]


def _to_utc(ts: pd.Timestamp) -> pd.Timestamp:
    """时间参数归一（语义对齐 warmup_loader._to_utc：naive 按 UTC 解释）。"""
    if ts.tzinfo is None:
        return ts.tz_localize("UTC")
    return ts.tz_convert("UTC")


def _normalize_timeframes(timeframes: Optional[list[str]]) -> list[str]:
    """timeframes 契约（决策 10）：None → 缺省四档；去重保序；
    空集或含非在册周期 → 结构化拒绝。"""
    if timeframes is None:
        return list(DEFAULT_TIMEFRAMES)
    tfs = list(dict.fromkeys(timeframes))
    if not tfs:
        raise ResearchError(
            "RESEARCH_PARAM_INVALID",
            "timeframes 不能为空集（缺省不传取四周期全量）",
            detail={"timeframes": timeframes})
    illegal = [t for t in tfs if t not in TF_MINUTES]
    if illegal:
        raise ResearchError(
            "RESEARCH_PARAM_INVALID",
            f"非法周期: {illegal}（在册: {sorted(TF_MINUTES)}）",
            detail={"illegal": illegal, "legal": sorted(TF_MINUTES)})
    return tfs


def _value_at(series: pd.Series, pos: int) -> Optional[float]:
    """时点读数：index <= as_of 的最后位置（决策 5）。"""
    v = series.iloc[pos]
    return None if pd.isna(v) else float(v)


def _round2(v: Optional[float]) -> Optional[float]:
    """数值报表纪律：读数/分位一律 2 位（体积约束）。"""
    return None if v is None else round(v, 2)


def _window_percentile(
    series: pd.Series, pos: int, window_bars: int,
) -> Optional[float]:
    """分位 = 当前值在近 window_bars 根同类值中的位置（rank 尾值/len）。

    OIPriceDivergence 形制（rolling(w, min_periods=2).apply）在 as_of
    位置的时点等价实现：取窗口切片后按同一公式计算——快照只消费单一
    时点，免去全序列 rolling.apply 的无效计算。窗口有效值 <2 无分位。
    """
    s = series.iloc[max(0, pos - window_bars + 1): pos + 1].dropna()
    if len(s) < 2:
        return None
    return round(float(s.rank().iloc[-1] / len(s)), 2)


def _adx_state(adx: Optional[float]) -> Optional[str]:
    """三态机械判读（代码规则可复现，刻意不做智能判读）：
    <20 震荡 / >25 趋势 / 之间过渡。"""
    if adx is None:
        return None
    if adx < 20:
        return "震荡"
    if adx > 25:
        return "趋势"
    return "过渡"


def _derivatives_leg(
    work: pd.DataFrame,
    raw_col: str,
    ind_col: str,
    pos: int,
    window_bars: int,
    key: str,
) -> dict[str, Any]:
    """衍生品子维三态（决策 9）：以原始列（funding_rate/open_interest）
    在分位窗内的有效覆盖为准，不采信指标输出列的 fillna(0) 假读数。"""
    if raw_col not in work.columns:
        return {"status": "no_data"}  # 无 parquet → 装载未合入该列
    start_i = max(0, pos - window_bars + 1)
    raw_win = work[raw_col].iloc[start_i: pos + 1]
    n_valid = int(raw_win.notna().sum())
    if n_valid == 0:
        return {"status": "no_data"}  # 有列但窗内原始值全 NaN（ffill 头部缺口）
    # 分位只用窗内原始值有效区的指标值（剔除输出列头部缺口区的假 0）
    ind_win = work[ind_col].iloc[start_i: pos + 1]
    s = ind_win[raw_win.notna()].dropna()
    pct = _round2(float(s.rank().iloc[-1] / len(s))) if len(s) >= 2 else None
    # 读数仅当 as_of 位置原始值有效（ffill 语义下窗内有覆盖即成立，防御保留）
    val = (_value_at(work[ind_col], pos)
           if not pd.isna(work[raw_col].iloc[pos]) else None)
    return {"status": "ok", key: _round2(val), "pct": pct,
            "window_bars": n_valid}


def _build_card(
    work: pd.DataFrame, pos: int, w90: int,
) -> dict[str, Any]:
    """五维行情卡组装（读数 + 分位 + 三态判读 + window_bars 声明）。"""
    from core.features.registry import resolve_indicator_column

    bb_params = {"period": 20, "std": 2.0}
    bb_u = resolve_indicator_column("BB", bb_params, field_name="upper")
    bb_m = resolve_indicator_column("BB", bb_params, field_name="middle")
    bb_l = resolve_indicator_column("BB", bb_params, field_name="lower")
    adx_col = resolve_indicator_column("ADX", {"period": 14})
    rsi_col = resolve_indicator_column("RSI", {"period": 14})
    atr_col = resolve_indicator_column("ATR", {"period": 14})
    rvol_col = resolve_indicator_column("RVOL", {"period": 20})
    fz_col = resolve_indicator_column("FundingZScore", {"period": 30})
    ocr_col = resolve_indicator_column("OI_ChangeRate", {"period": 14})

    # 报表派生量（决策 4：快照层内联，不注册新指标）
    atr_pct_series = work[atr_col] / work["close"] * 100.0
    # 带宽取带边界距离的绝对值：|upper-lower|/middle == 4σ/middle。
    # 实证 pandas_ta bbands 返回列序为 BBL/BBM/BBU，而 indicators.py
    # 以 iloc[0/1/2] 挂 upper/middle/lower 标签——列标签上下轨颠倒（存量
    # 缺陷，独立修复项，本变更契约禁改 indicators.py）；abs 形式对两种
    # 列序均输出正确带宽（非负），分位亦落在正确语义的序列上。
    bb_bw_series = (work[bb_u] - work[bb_l]).abs() / work[bb_m]

    adx = _round2(_value_at(work[adx_col], pos))
    rsi = _round2(_value_at(work[rsi_col], pos))
    rvol = _round2(_value_at(work[rvol_col], pos))

    return {
        "trend": {"adx": adx, "state": _adx_state(adx)},
        "momentum": {"rsi": rsi,
                     "pct": _window_percentile(work[rsi_col], pos, w90)},
        "volatility": {
            "atr_pct": _round2(_value_at(atr_pct_series, pos)),
            "atr_pct_pct": _window_percentile(atr_pct_series, pos, w90),
            "bb_bw": _round2(_value_at(bb_bw_series, pos)),
            "bb_bw_pct": _window_percentile(bb_bw_series, pos, w90),
        },
        "volume": {"rvol": rvol,
                   "pct": _window_percentile(work[rvol_col], pos, w90)},
        "derivatives": {
            "funding": _derivatives_leg(
                work, "funding_rate", fz_col, pos, w90, "funding_z"),
            "oi": _derivatives_leg(
                work, "open_interest", ocr_col, pos, w90, "oi_change_rate"),
        },
        "window_bars": min(w90, pos + 1),  # 窗口不足 90 日换算时如实声明
    }


def _snapshot_one(
    data_dir: Path,
    symbol: str,
    tf: str,
    as_of: Union[str, pd.Timestamp, None],
) -> dict[str, Any]:
    """单周期快照：边界探测 → as_of 解析 → 闭端前扩装载 → 指标现算 → 卡片。"""
    from core.data.mtf_loader import find_parquet
    from core.data.storage import load_parquet

    safe_symbol = re.sub(r"[^A-Za-z0-9]", "", symbol)
    path = find_parquet(data_dir, safe_symbol, tf)
    if path is None:
        raise ResearchError(
            "RESEARCH_SYMBOL_NOT_FOUND",
            f"无数据: {symbol} {tf}",
            detail={"symbol": symbol, "timeframe": tf})
    probe = load_parquet(path)
    if probe is None or len(probe) == 0:
        raise ResearchError(
            "RESEARCH_SYMBOL_NOT_FOUND",
            f"数据不可读: {symbol} {tf}",
            detail={"symbol": symbol, "timeframe": tf})
    if probe.index.tz is None:
        probe.index = probe.index.tz_localize("UTC")

    # as_of 缺省 = 最新一根 bar（探测边界，避免全量装载后再截尾）
    as_of_ts = probe.index[-1] if as_of is None else _to_utc(pd.Timestamp(as_of))
    if as_of_ts < probe.index[0]:
        raise ResearchError(
            "RESEARCH_DATA_INSUFFICIENT",
            f"as_of 早于数据起点: {as_of_ts.isoformat()} "
            f"< {probe.index[0].isoformat()}",
            detail={"as_of": as_of_ts.isoformat(),
                    "data_start": probe.index[0].isoformat()})

    # 90 日分位窗口换算（1h=2160/4h=540/1d=90/3d=30，通用 90*1440/tf 分钟）
    w90 = math.ceil(_WINDOW_DAYS * 1440 / TF_MINUTES[tf])
    df, _ = load_with_warmup(
        data_dir, symbol, tf,
        start=as_of_ts.isoformat(), end=as_of_ts.isoformat(),
        indicator_names=_INDICATOR_NAMES, min_warmup_bars=w90)

    pos = int(df.index.searchsorted(as_of_ts, side="right") - 1)
    if pos < 0:
        raise ResearchError(
            "RESEARCH_DATA_INSUFFICIENT",
            f"as_of 前无数据: {symbol} {tf} {as_of_ts.isoformat()}",
            detail={"as_of": as_of_ts.isoformat()})

    # 七指标现算（不改输入 df；全 df 含 warmup 前缀保证收敛——只用不算）
    from core.features.indicators import _compute_indicator

    work = df.copy()
    for name, params in _INDICATOR_SPECS:
        for col, series in _compute_indicator(work, name, dict(params)).items():
            work[col] = series

    card = _build_card(work, pos, w90)
    card["as_of"] = df.index[pos].isoformat()
    return card


def get_market_snapshot(
    db_path: Path,
    data_dir: Path,
    *,
    symbol: str,
    timeframes: Optional[list[str]] = None,
    as_of: Union[str, pd.Timestamp, None] = None,
) -> dict[str, Any]:
    """时点行情快照：多周期五维行情卡（读数 + 90 日分位 + 三态判读）。

    Args:
        db_path: research 库路径（调用留痕）。
        data_dir: parquet 数据目录。
        symbol: 交易对（如 BTCUSDT）。
        timeframes: 周期子集（TF_MINUTES 在册；去重；缺省四档全量）。
        as_of: 读数时点（该根 bar；缺省=最新；闭端截至，之后数据不进计算）。

    Returns:
        快照 dict（json 序列化后 <= 2KB；周期卡内 as_of 为该档实际
        读数 bar 时间——as_of 落在两根 bar 之间时取前一根）。

    Raises:
        ResearchError: RESEARCH_PARAM_INVALID（timeframes 非法/空集/体积
            超限）；RESEARCH_SYMBOL_NOT_FOUND（symbol/timeframe 无数据）；
            RESEARCH_DATA_INSUFFICIENT（as_of 早于数据起点）；
            RESEARCH_WARMUP_INSUFFICIENT（透传自 load_with_warmup）。
    """
    tfs = _normalize_timeframes(timeframes)
    cards = {tf: _snapshot_one(data_dir, symbol, tf, as_of) for tf in tfs}

    out: dict[str, Any] = {
        "symbol": symbol,
        "as_of": ("latest" if as_of is None
                  else _to_utc(pd.Timestamp(as_of)).isoformat()),
        "window_days": _WINDOW_DAYS,
        "notes": {
            "atr_pct": "ATR(14)/close*100",
            "bb_bw": "|bb_upper-bb_lower|/bb_middle",
        },
        "timeframes": cards,
    }

    serialized = json.dumps(out, ensure_ascii=False).encode("utf-8")
    if len(serialized) > _MAX_SERIALIZED_BYTES:
        raise ResearchError(
            "RESEARCH_PARAM_INVALID",
            f"快照体积超限: {len(serialized)} > {_MAX_SERIALIZED_BYTES} 字节",
            detail={"bytes": len(serialized),
                    "limit": _MAX_SERIALIZED_BYTES,
                    "timeframes": tfs})

    # 留痕不进判定并集：adhoc 条目供审计（漏报拦截的数据源）
    from core.persistence.repository import research as rr

    rr.append_research_log(
        db_path,
        hypothesis_id=None,
        entry_type="market_snapshot",
        ref_id=symbol,
        data_ranges=json.dumps({
            "symbol": symbol, "timeframes": tfs, "as_of": out["as_of"],
        }, ensure_ascii=False),
        summary=f"market_snapshot {symbol} {len(tfs)} tf as_of={out['as_of']}",
    )
    return out
