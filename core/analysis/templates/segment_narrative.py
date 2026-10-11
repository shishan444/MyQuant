"""区间事件摘要模板（add-research-analysis-tools 0.4.1 R3）。

语义：AI 阅读任意时间区间行情的符号化通道——把区间内发生的市场事件
（形态触发/指标交叉/轨道触碰/持续涨跌段）以时间线清单+结构统计呈现，
工程算事件、AI 读事件。事件全部由现有指标注册表现算结果判定，不新增
指标计算语义；载荷无原始 OHLCV 行（序列不外发——报表压缩契约同族）。

事件四类（条目两类形制——时点 {type, source, timestamp, value} ／
段 {type, source, start_timestamp, end_timestamp, bars, value}）：
- pattern_trigger：pattern 类布尔输出列 False→True 时点；
- indicator_cross：封闭交叉对清单 CROSS_PAIRS（当前三对 MACD(macd,
  signal)/Stochastic(k,d)/Aroon(up,down)——常量扩项走常量更新+spec
  版本化，与指标注册表同维护纪律）上/下穿时点；
- band_position：close 进/出轨道上下轨（布林/唐奇安/Keltner，进出
  双向——状态=close 越过轨道线，状态翻转即事件）；
- sustained_run：close 连续同向变动段（同向变动 ≥2 次，段条目四字段
  start/end/bars/value=段涨跌幅、source="close"）。

默认事件源（未传 indicators 时）由注册表机械推导：category ∈
{trend, momentum, pattern} 全指标 default 参数（与 screen "all" 同款
排除 guard_only/lazy）+ 轨道族点名 {BB, Keltner, Donchian}（=
volatility 减 ATR）；不入默认集类别（trend_strength/volume/
structure/ml/derivatives）在结果中披露口径，显式点名不受限。

warmup「只用不算」：request_start 非 None 时 df 为前扩装载结果，事件
提取限制在统计段（时点类事件的边界前状态取自 warmup 前缀——跨段
翻转不漏检；段事件在统计段内自成一体）。双上限（资源保护参数可调）：
区间 bar ≤5000（装载前——路由侧 index-only 计数）、事件数 ≤500
（提取后组装前）。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd

from core.analysis.hypothesis_service import ResearchError
from core.analysis.result import AnalysisResult, build_result

# 封闭交叉对清单（spec 契约——扩项走常量更新+spec 版本化）
CROSS_PAIRS: tuple[tuple[str, str, str], ...] = (
    ("MACD", "macd", "signal"),
    ("Stochastic", "k", "d"),
    ("Aroon", "aroon_up", "aroon_down"),
)
# 轨道族（band_position 事件源；= volatility 类减 ATR——显式点名补齐）
BAND_INDICATORS: tuple[str, ...] = ("BB", "Keltner", "Donchian")
# 默认事件源类别（spec 契约——机械推导不依赖人工挑选代表）
DEFAULT_SOURCE_CATEGORIES: tuple[str, ...] = ("trend", "momentum", "pattern")
# 资源保护参数（可调——与载荷压缩契约同精神）
MAX_RANGE_BARS = 5000
MAX_EVENTS = 500


def default_source_names() -> List[str]:
    """默认事件源指标名清单（注册表机械推导：默认类别 ∪ 轨道族）。

    与 screen_patterns "all" 同款排除 guard_only 与 lazy（筛选家族
    一致）；轨道族点名补齐 volatility 减 ATR。
    """
    from core.features.registry import INDICATOR_REGISTRY

    names = [
        name for name, d in INDICATOR_REGISTRY.items()
        if d.category in DEFAULT_SOURCE_CATEGORIES
        and not d.guard_only and d.compute_mode != "lazy"]
    for band in BAND_INDICATORS:
        if band not in names:
            names.append(band)
    return names


def excluded_categories() -> List[str]:
    """不入默认集的类别清单（结果披露口径用——注册表现存类别全集减
    默认事件源实际覆盖的类别；轨道族点名使 volatility 属默认集而非
    排除类）。"""
    from core.features.registry import INDICATOR_REGISTRY

    all_cats = {d.category for d in INDICATOR_REGISTRY.values()}
    covered = {INDICATOR_REGISTRY[n].category for n in default_source_names()}
    return sorted(all_cats - covered)


def _encoded_col(indicator: str, field: str) -> str:
    """默认参数下输出列精确名（resolve_column_ref 权威命名）。"""
    from core.analysis.column_ref import resolve_column_ref
    from core.analysis.registry_meta import default_params_for

    return resolve_column_ref({
        "indicator": indicator, "params": default_params_for(indicator),
        "field": field}).encoded_name()


def _point_event(
    events: List[Dict[str, Any]], type_: str, source: str,
    timestamp: pd.Timestamp, value: float,
) -> None:
    events.append({"type": type_, "source": source,
                   "timestamp": timestamp.isoformat(), "value": float(value)})


def _transition_mask(series: pd.Series) -> pd.Series:
    """False→True 翻转掩码（全序列判定——统计段边界前状态取自 warmup
    前缀；首 bar 无前态不计）。"""
    b = series.fillna(False).astype(bool)
    return b & ~b.shift(fill_value=False)


def _extract_events(
    work: pd.DataFrame, stat_mask: pd.Series,
) -> tuple[List[Dict[str, Any]], Dict[str, int]]:
    """四类事件提取（全序列判翻转、统计段过滤；段事件统计段内自洽）。"""
    events: List[Dict[str, Any]] = []
    counts = {"pattern_trigger": 0, "indicator_cross": 0,
              "band_position": 0, "sustained_run": 0}

    # ① pattern_trigger：输出列名以 pattern_ 开头的布尔列
    for col in work.columns:
        if not col.startswith("pattern_"):
            continue
        rising = _transition_mask(work[col])
        for ts in work.index[rising.values & stat_mask.values]:
            _point_event(events, "pattern_trigger", col, ts,
                         float(work.at[ts, col]))
            counts["pattern_trigger"] += 1

    # ② indicator_cross：封闭交叉对上/下穿
    for indicator, field_a, field_b in CROSS_PAIRS:
        col_a, col_b = _encoded_col(indicator, field_a), _encoded_col(
            indicator, field_b)
        if col_a not in work.columns or col_b not in work.columns:
            continue  # 非事件源（显式点名未含该指标）
        a = work[col_a].astype(float)
        b = work[col_b].astype(float)
        above = (a > b) & (a.shift() <= b.shift())
        below = (a < b) & (a.shift() >= b.shift())
        for mask, direction in ((above, "cross_above"),
                                (below, "cross_below")):
            hit = mask.fillna(False) & stat_mask
            for ts in work.index[hit.values]:
                _point_event(events, "indicator_cross",
                             f"{indicator}:{field_a}/{field_b}:{direction}",
                             ts, float(work.at[ts, col_a]))
                counts["indicator_cross"] += 1

    # ③ band_position：close 进/出轨道上下轨（状态翻转即事件）
    for indicator in BAND_INDICATORS:
        col_up, col_lo = _encoded_col(indicator, "upper"), _encoded_col(
            indicator, "lower")
        if col_up not in work.columns or col_lo not in work.columns:
            continue
        close = work["close"]
        for col, side in ((col_up, "upper"), (col_lo, "lower")):
            band = work[col].astype(float)
            if side == "upper":
                outside = close >= band
            else:
                outside = close <= band
            for flip_mask, direction in (
                    (_transition_mask(outside), f"enter_{side}"),
                    (_transition_mask(~outside), f"exit_{side}")):
                hit = flip_mask & stat_mask
                for ts in work.index[hit.values]:
                    _point_event(events, "band_position",
                                 f"{indicator}:{direction}", ts,
                                 float(close.loc[ts]))
                    counts["band_position"] += 1

    # ④ sustained_run：统计段内 close 连续同向变动段（同向变动 ≥2 次
    # 才成段——单根变动不构成「持续」）。游程在 diff 位置制上结算：
    # 同向 diff 位置区间 [p, q] ↔ bar 区间 [p-1, q]。
    stat = work.loc[stat_mask]
    if len(stat) >= 3:
        close_arr = stat["close"].to_numpy(dtype=float)
        sign = np.sign(np.diff(close_arr))  # 位置 i = bar(i)→bar(i+1)
        run_start, cur = None, 0  # run_start=当前游程首个 diff 位置

        def _emit(p: int, q: int) -> None:
            events.append({
                "type": "sustained_run", "source": "close",
                "start_timestamp": stat.index[p].isoformat(),
                "end_timestamp": stat.index[q + 1].isoformat(),
                "bars": int(q - p + 2),
                "value": float(close_arr[q + 1] / close_arr[p] - 1.0)})
            counts["sustained_run"] += 1

        for i, s in enumerate(sign):
            s = int(s)
            if s != 0 and s == cur:
                continue  # 延伸当前游程
            if run_start is not None and cur != 0 and i - run_start >= 2:
                _emit(run_start, i - 1)
            if s != 0:
                run_start, cur = i, s
            else:
                run_start, cur = None, 0
        if run_start is not None and cur != 0 \
                and len(sign) - run_start >= 2:
            _emit(run_start, len(sign) - 1)

    # 时间序稳定（提取按类进行——组装前按时间排序，同刻按类型名）
    events.sort(key=lambda e: (e.get("timestamp") or e["start_timestamp"],
                               e["type"]))
    return events, counts


def segment_narrative(
    df: pd.DataFrame,
    *,
    data_ranges: list[dict[str, str]],
    request_start: str | pd.Timestamp | None = None,
    indicators: Optional[List[str]] = None,
    hypothesis_id: str | None = None,
) -> AnalysisResult:
    """区间事件摘要（时间线事件清单 + 结构统计）。

    Args:
        df: 路由路径下为 load_with_warmup 前扩装载结果（含 warmup
            前缀）；模板在含前缀数据上现算事件源指标。
        request_start: 请求区间起点（None 时统计段=全 df）。
        indicators: 显式点名事件源指标清单（任何在册指标——含默认集
            外类别；限定事件来源范围）。缺省=默认事件源（注册表机械
            推导）。

    Raises:
        ResearchError: RESEARCH_PARAM_INVALID——indicators 非法（非列表/
            空/含未知名）；事件数超上限 MAX_EVENTS。
    """
    from core.analysis.condition_kernel import stat_window_mask
    from core.features.indicators import _compute_indicator
    from core.features.registry import INDICATOR_REGISTRY

    # 事件源解析：显式点名（在册校验——不让装载层 KeyError 裸穿）
    if indicators is not None:
        if not isinstance(indicators, list) or not indicators:
            raise ResearchError(
                "RESEARCH_PARAM_INVALID",
                "indicators 须为非空指标名列表（缺省走默认事件源）",
                detail={"param": "indicators",
                        "legal_form": "指标名列表（list_indicators 查询）"})
        for name in indicators:
            if name not in INDICATOR_REGISTRY:
                raise ResearchError(
                    "RESEARCH_PARAM_INVALID",
                    f"indicators 含未知指标: {name!r}",
                    detail={"param": "indicators", "value": name,
                            "available_hint": "经 list_indicators 查询注册表"})
        source_names = list(indicators)
        default_set = False
    else:
        source_names = default_source_names()
        default_set = True

    # 现算事件源指标（全 df——含 warmup 前缀保证收敛；不改输入 df）
    work = df.copy()
    for name in source_names:
        from core.analysis.registry_meta import default_params_for
        for col, series in _compute_indicator(
                work, name, default_params_for(name)).items():
            work[col] = series

    stat_mask = stat_window_mask(work, request_start)
    stat = work.loc[stat_mask]
    if stat.empty:
        raise ResearchError(
            "RESEARCH_DATA_INSUFFICIENT",
            f"统计段为空: request_start={request_start}")

    events, counts = _extract_events(work, stat_mask)
    if len(events) > MAX_EVENTS:
        raise ResearchError(
            "RESEARCH_PARAM_INVALID",
            f"事件数超上限: {len(events)} > {MAX_EVENTS}（缩小区间或指定"
            "更窄 indicators 指标集）",
            detail={"param": "events", "actual": len(events),
                    "limit": MAX_EVENTS,
                    "hint": "区间过密或指标集过宽——缩小区间/收窄指标集"})

    # 结构统计（区间涨跌幅首尾 close / 波动区间高低点 / 各类事件计数）
    close_stat = stat["close"]
    interval_return = float(close_stat.iloc[-1] / close_stat.iloc[0] - 1.0)
    high_idx, low_idx = stat["high"].idxmax(), stat["low"].idxmin()
    structure = {
        "interval_return": interval_return,
        "high": float(stat["high"].max()),
        "high_time": high_idx.isoformat(),
        "low": float(stat["low"].min()),
        "low_time": low_idx.isoformat(),
        "close_quantiles": {
            f"p{pt}": float(np.percentile(close_stat.values, pt))
            for pt in (25, 50, 75)},
        "event_counts": counts,
    }

    # 三要素（样本量=区间 bar 数；confidence=事件率的 Wilson 区间）
    n_bars = int(len(stat))
    n_events = len(events)
    # successes 口径=含至少一个事件的 bar 数（时点事件锚 timestamp、段事件
    # 锚 start_timestamp 去重）：单 bar 可产多事件（多指标同刻触发），直接以
    # 总事件数作 successes 可超 total（wilson_interval 计数矛盾）；事件全部
    # 落在统计段内，去重后恒 ≤n_bars
    events_bars = len({
        e.get("timestamp") or e["start_timestamp"] for e in events})
    from core.scoring.statistics import wilson_interval
    confidence = {
        "method": "wilson_interval",
        "interval": list(wilson_interval(events_bars, n_bars)),
        "for": "event_rate",
        "n_events": n_events,
        "events_bars": events_bars,
        "n_bars": n_bars,
    }

    # 默认集口径披露（不入集类别；显式点名标注其类别归属）
    payload: Dict[str, Any] = {
        "events": events,
        "structure": structure,
        "source": {
            "indicators": source_names,
            "default_set": default_set,
            "default_categories": list(DEFAULT_SOURCE_CATEGORIES),
            "excluded_categories": excluded_categories(),
        },
        "limits": {"max_range_bars": MAX_RANGE_BARS, "max_events": MAX_EVENTS},
        "total_bars": int(len(work)),  # 全 df（含 warmup 前缀）
    }
    if request_start is not None:
        from core.analysis.warmup_loader import to_utc
        req_ts = to_utc(pd.Timestamp(request_start))
        payload["warmup_bars"] = int((work.index < req_ts).sum())

    return build_result(
        "segment_narrative",
        sample_size=n_bars,
        confidence=confidence,
        data_ranges=data_ranges,
        payload=payload,
        min_samples=0,  # 空事件合法（区间统计无小样本拒绝面）
        hypothesis_id=hypothesis_id,
    )
