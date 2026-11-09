"""箱体盘点模板（add-evaluation-ruler）。

语义：对给定区间以滚动窗高低幅度判定箱体态——hi=滚动窗最高价、
lo=滚动窗最低价、mid=收盘价滚动均值（非当根价）、width=(hi−lo)/mid
（分母为滚动均值——当根价与滚动均值数值不同，边界箱分类可翻转），
连续存活达 min_len 才成箱。产出箱体底账清单（结构存在性盘点）。

盘点口径与对照口径两口径并存且不可互换（文档明示义务）：盘点=hi/lo
幅度口径（结构存在性——本模板）；对照=分位沿口径（交易判定——
ruler_compare 的 q10/q90 通道），参数族各自独立。

段提取读法 A（D7）：段判定与起止仅取请求区间内 bar；区间首 bar 落
在长箱体中部时该段从区间首 bar 重新起算（dur=区间内长度），首段附
truncated_start 截断标记；warmup=window 前扩只用不算（rolling 在含
前缀的全 df 上计算保证区间首 bar 收敛，段提取切回区间内）。

回望性契约：同 wave_inventory——不适用假设关联（分派层统一拦）、
幂等、不作策略条件或实盘信号输入；zigzag 同款的跨查询可比性注意
事项同样适用（不同 start 的箱清单不可直接混用拼接）。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

import pandas as pd

from core.analysis.hypothesis_service import ResearchError
from core.analysis.result import AnalysisResult, build_result
from core.analysis.templates.param_guard import (
    _require_float_range, _require_int_range)
from core.analysis.warmup_loader import to_utc

# timeframe 感知冻结默认（demo 冻结值：1h=(120,48)/15m=(240,96)）
BOX_DEFAULT_PARAMS: dict[str, tuple[int, int]] = {"1h": (120, 48),
                                                  "15m": (240, 96)}
# 全轴默认宽度门槛（demo 冻结值 8%）
DEFAULT_WIDTH_THRESHOLD = 0.08


def resolve_box_params(
    timeframe: str,
    window: Optional[int],
    min_len: Optional[int],
) -> tuple[int, int]:
    """timeframe 感知默认参数解析（非冻结轴两参数必填）。

    不做 int() 转换、返回原始形：先行转换会把 120.7/'120' 洗成合法
    int 架空后续校验（静默截断）——类型/值域校验实效归
    validate_box_inventory_params，两函数恒成对调用（resolve→validate）。
    """
    defaults = BOX_DEFAULT_PARAMS.get(timeframe)
    if window is None:
        window = defaults[0] if defaults else None
    if min_len is None:
        min_len = defaults[1] if defaults else None
    if window is None or min_len is None:
        raise ResearchError(
            "RESEARCH_PARAM_INVALID",
            f"timeframe {timeframe!r} 无冻结默认参数——window 与 "
            f"min_len 必填（冻结默认仅 1h=(120,48)/15m=(240,96)）",
            detail={"param": "window|min_len", "timeframe": timeframe,
                    "frozen_defaults": {k: list(v)
                                        for k, v in
                                        BOX_DEFAULT_PARAMS.items()}})
    return window, min_len


def validate_box_inventory_params(
    window: int, min_len: int, width_thr: float
) -> None:
    """参数前置校验（路由装载前调用——window 参与前扩起点计算）。

    委托 param_guard 单源（bool 排除＋字符串数字拒——同 wave_inventory
    的静默转换收口）。"""
    _require_int_range(window, name="window", lo=1)
    _require_int_range(min_len, name="min_len", lo=1)
    _require_float_range(
        width_thr, name="width_thr", lo=0, hi=1,
        lo_excl=True, hi_excl=True)


def compute_box_segments(
    df: pd.DataFrame, *,
    request_start: Optional[pd.Timestamp],
    window: int, min_len: int, width_thr: float,
) -> List[Dict[str, Any]]:
    """滚动窗高低幅度收窄段提取（装载层消费 BoxRegime——段提取留模板）。

    计算核心单源在 BoxRegime 指标列（add-box-capability D3 装载层
    消费）：box_state/box_width 经 _compute_indicator 装载，与策略
    条件链同路；段提取/读法 A 区间语义（首 bar 重算＋截断标记）留
    模板层。段提取消费边界：只消费 box_state 列（禁用
    box_established——其段边界后移 min_len−1 根与 demo 箱锚不符）。

    Args:
        df: 含 high/low/close 列（DatetimeIndex UTC；rolling 在含
            warmup 前缀的全 df 上计算——区间首 bar 的窗口已收敛）。
        request_start: 请求区间起点（UTC 归一；None 时统计段=全 df）。
        window: 滚动窗宽（bar 数）。
        min_len: 连续存活达此长度才成箱。
        width_thr: 宽度门槛（含等号：width 恰等于门槛按箱体态处理）。

    Returns:
        箱字典列表（时间序）：start/end（区间内首/末 bar Timestamp）、
        dur（区间内长度）、box_height（段内 width 均值，小数形制）、
        truncated_start（段横跨区间起点时 True）。
    """
    from core.features.indicators import _compute_indicator
    from core.features.registry import resolve_indicator_column

    params = {"window": window, "width_thr": float(width_thr),
              "min_len": min_len}
    cols = _compute_indicator(df, "BoxRegime", params)
    # 布尔列数值形（True=1.0）回 bool 供段提取真值判定
    state = cols[resolve_indicator_column(
        "BoxRegime", params, "box_state")].astype(bool)
    width = cols[resolve_indicator_column(
        "BoxRegime", params, "box_width")]

    req_ts = (to_utc(pd.Timestamp(request_start))
              if request_start is not None else None)
    if req_ts is not None:
        in_mask = state.index >= req_ts
        state_in = state[in_mask]
        width_in = width[in_mask]
        state_before = state[~in_mask]
        first_bar = state_in.index[0] if len(state_in) else None
    else:
        state_in = state
        width_in = width
        state_before = state.iloc[:0]
        first_bar = None

    sv = state_in.values
    idx = state_in.index
    boxes: List[Dict[str, Any]] = []
    i, n = 0, len(sv)
    while i < n:
        if sv[i]:
            j = i
            while j < n and sv[j]:
                j += 1
            if j - i >= min_len:
                # 区间首 bar 起段且前缀末 bar 同为箱体态=横跨起点截断
                truncated = bool(
                    first_bar is not None and idx[i] == first_bar
                    and len(state_before)
                    and bool(state_before.iloc[-1]))
                boxes.append({
                    "start": idx[i], "end": idx[j - 1],
                    "dur": int(j - i),
                    "box_height": float(width_in.iloc[i:j].mean()),
                    "truncated_start": truncated,
                })
            i = j
        else:
            i += 1
    return boxes


def box_inventory(
    df: pd.DataFrame,
    *,
    data_ranges: list[dict[str, str]],
    timeframe: str,
    request_start: str | pd.Timestamp | None = None,
    window: int | None = None,
    min_len: int | None = None,
    width_thr: float = DEFAULT_WIDTH_THRESHOLD,
) -> AnalysisResult:
    """箱体盘点（纯计算模板——装载由路由层完成）。

    Args:
        df: 含 high/low/close 的 DataFrame（路由路径下为请求区间前扩
            window 根的装载结果）。
        data_ranges: 请求区间（end 必填——无开放右端形制）。
        timeframe: 请求周期（冻结默认参数解析键）。
        request_start: 请求区间起点（None 时统计段=全 df）。
        window/min_len: 滚动窗宽与成箱最小连续长度；1h/15m 缺省取
            冻结默认 (120,48)/(240,96)，其余轴两键必填。
        width_thr: 宽度门槛（默认 0.08，含等号）。

    Raises:
        ResearchError: 非冻结轴缺参（附冻结默认清单）；参数非法；
            data_range 缺 end；有效 bar<2；区间零箱（min_samples=1
            既有拒绝路径——提示扩区间）。
    """
    window_r, min_len_r = resolve_box_params(timeframe, window, min_len)
    validate_box_inventory_params(window_r, min_len_r, width_thr)
    for r in data_ranges:
        if not isinstance(r, dict) or not r.get("end"):
            raise ResearchError(
                "RESEARCH_PARAM_INVALID",
                "data_range 缺 end——箱体盘点无开放右端形制，"
                "右端必须为显式时间戳",
                detail={"param": "data_range.end",
                        "legal_form": "显式 UTC 时间戳（如 2026-11-07）"})
    if len(df) < 2:
        raise ResearchError(
            "RESEARCH_PARAM_INVALID",
            f"有效 bar 数 {len(df)} < 2——不足以判定箱体态",
            detail={"param": "data_range", "actual_bars": len(df),
                    "legal_range": ">=2"})

    boxes = compute_box_segments(
        df, request_start=request_start,
        window=window_r, min_len=min_len_r, width_thr=float(width_thr))

    heights = [b["box_height"] for b in boxes]
    # 月均分母=按箱起点月份去重计月（下限 1——demo 同构）
    months = max(len({b["start"].strftime("%Y-%m") for b in boxes}), 1)
    payload = {
        "timeframe": timeframe,
        "window": window_r,
        "min_len": min_len_r,
        "width_thr": float(width_thr),
        "boxes": [{
            "start": b["start"].isoformat(),
            "end": b["end"].isoformat(),
            "dur": b["dur"],
            "box_height": b["box_height"],
            "truncated_start": b["truncated_start"],
        } for b in boxes],
        "summary": {
            "n_boxes": len(boxes),
            "months_distinct": months,
            "boxes_per_month": (len(boxes) / months) if boxes else 0.0,
            "box_height_mean": (float(sum(heights) / len(heights))
                                if heights else None),
            "box_height_median": (float(pd.Series(heights).median())
                                  if heights else None),
            "n_truncated": sum(1 for b in boxes if b["truncated_start"]),
        },
        "caliber_note": (
            "盘点口径=hi/lo 滚动幅度（结构存在性）；对照口径=分位沿"
            "（ruler_compare 交易判定）——两口径参数族独立、不可互换"),
    }

    return build_result(
        "box_inventory",
        sample_size=len(boxes),
        confidence={
            "method": "rolling_range_width_threshold",
            # 确定性规则算法无抽样误差区间（键位保留 null 语义）
            "interval": None,
            "window": window_r,
            "min_len": min_len_r,
            "width_thr": float(width_thr),
            "deterministic": True,
        },
        data_ranges=data_ranges,
        payload=payload,
        min_samples=1,
    )
