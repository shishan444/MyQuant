"""行情分段模板（add-regime-split；MCP 0.2.7「能辨真假」检查②）。

状态序列直切（regime_segments 权威模块的模板薄封装）：切带类型的段
（趋势/震荡/高波动——判读与行情快照同一把尺），可选携带结构化条件
逐段统计前向收益（condition_stats 同口径），回答「候选规律在各行情
段是否一致」。

段定义权威在 regime_segments（0.2.10 分段考试消费同一切段函数——
检查侧与考试侧段边界同源）。判读参数内建不可调，输出 gates 披露
（0.2.6 三道门全族纪律）。段样本 <30 标 insufficient_sample（与
param_sweep 资格门同口径——证据不足不等于无效）。explicit 模式直接
按传入边界分段（type 缺省按判读补标），跳过自动切段。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

import pandas as pd

from core.analysis.condition_kernel import _mask_for, _p_value_for
from core.analysis.hypothesis_service import ResearchError
from core.analysis.regime_segments import GATES, build_segments
from core.analysis.result import AnalysisResult, build_result

_MIN_SEGMENT_SAMPLES = 30  # 逐段统计资格门（与 param_sweep 同口径）


def _validate_segments_input(
    segmentation: Any, condition: Any, indicator: Any,
    forward_bars: int, n_df: int,
) -> None:
    if isinstance(segmentation, str):
        if segmentation != "auto":
            raise ResearchError(
                "RESEARCH_PARAM_INVALID",
                f"segmentation 非法: {segmentation!r}",
                detail={"param": "segmentation",
                        "legal_range": '"auto" 或显式段列表 '
                                       '[{start, end, type?}]'})
    elif isinstance(segmentation, list):
        if not segmentation:
            raise ResearchError(
                "RESEARCH_PARAM_INVALID", "显式段列表为空",
                detail={"param": "segmentation", "legal_range": "非空列表"})
        for seg in segmentation:
            if not isinstance(seg, dict) or not {"start", "end"} <= set(seg):
                raise ResearchError(
                    "RESEARCH_PARAM_INVALID",
                    f"显式段结构非法: {seg!r}",
                    detail={"param": "segmentation",
                            "legal_range": "每段含 start/end（type 可选）"})
    else:
        raise ResearchError(
            "RESEARCH_PARAM_INVALID",
            f"segmentation 类型非法: {type(segmentation).__name__}",
            detail={"param": "segmentation",
                    "legal_range": '"auto" 或显式段列表'})
    if isinstance(forward_bars, bool) or not isinstance(forward_bars, int) \
            or forward_bars < 1 or forward_bars > n_df - 1:
        raise ResearchError(
            "RESEARCH_PARAM_INVALID", f"forward_bars 非法: {forward_bars!r}",
            detail={"param": "forward_bars", "legal_range": f"1..{n_df - 1}"})
    if condition is not None and (
            not isinstance(condition, dict)
            or set(condition) != {"subject", "action", "target"}):
        raise ResearchError(
            "RESEARCH_PARAM_INVALID",
            f"condition 须为 {{subject, action, target}} 三键结构: {condition!r}",
            detail={"param": "condition",
                    "legal_range": "与扫描候选/假设登记同形式的单条件（可选）"})
    if condition is not None and not isinstance(indicator, str):
        raise ResearchError(
            "RESEARCH_PARAM_INVALID",
            "携带 condition 时 indicator 必填（指标名——列名 subject 的"
            "归属指标，双件形制同 param_sweep）",
            detail={"param": "indicator",
                    "legal_range": "注册表指标名（list_indicators 查询）"})



def _ts_utc(v: Any) -> pd.Timestamp:
    ts = pd.Timestamp(v)
    if ts.tzinfo is None:
        ts = ts.tz_localize("UTC")
    return ts.tz_convert("UTC")


def regime_split(
    df: pd.DataFrame,
    *,
    segmentation: Any = "auto",
    indicator: Optional[str] = None,
    condition: Optional[Dict[str, Any]] = None,
    forward_bars: int = 24,
    data_ranges: List[Dict[str, str]] | None = None,
    timeframe: str = "1h",
    request_start: str | pd.Timestamp | None = None,
    hypothesis_id: str | None = None,
) -> AnalysisResult:
    """行情分段（状态序列直切）+ 可选逐段条件统计。

    Args:
        df: 含 warmup 前缀的 OHLCV+ADX/ATR DataFrame（路由经
            load_with_warmup 前扩装载；统计段以 request_start 切回）。
        segmentation: "auto" 或显式段列表 [{start, end, type?}]。
        indicator: condition 的归属指标名（condition 存在时必填）。
        condition: 可选中心条件（subject 列名须属于 df 列——由路由层
            以指标矩阵装载，双件形制同 param_sweep）。
        forward_bars: 逐段统计的前向持有期 N。
        request_start: 请求区间起点（warmup 只用不算——切段统计段）。

    Raises:
        ResearchError: 参数非法（附 legal_range）。
    """
    from core.features.indicators import _compute_indicator
    from core.features.registry import resolve_indicator_column

    _validate_segments_input(segmentation, condition, indicator, forward_bars, len(df))

    adx_col = resolve_indicator_column("ADX", {"period": 14})
    atr_col = resolve_indicator_column("ATR", {"period": 14})
    for c, s in _compute_indicator(df, "ADX", {"period": 14}).items():
        df[c] = s
    for c, s in _compute_indicator(df, "ATR", {"period": 14}).items():
        df[c] = s

    # 统计段切分（warmup 前缀只用不算——判读指标在全 df 收敛；
    # 置于指标计算后：布尔切片为副本，先切则不含新算指标列）
    if request_start is None:
        stat = df
    else:
        req = _ts_utc(request_start)
        stat = df[df.index >= req]
    if condition is not None:
        from core.features.registry import INDICATOR_REGISTRY
        if indicator not in INDICATOR_REGISTRY:
            raise ResearchError(
                "RESEARCH_PARAM_INVALID", f"indicator 未知: {indicator!r}",
                detail={"param": "indicator",
                        "legal_range": "注册表指标名（list_indicators 查询）"})
        from core.features.indicators import _DEFAULT_PARAMS
        for params in _DEFAULT_PARAMS.get(indicator, []):
            for c, s in _compute_indicator(df, indicator, dict(params)).items():
                if c not in df.columns:
                    df[c] = s
        if condition["subject"] not in df.columns:
            raise ResearchError(
                "RESEARCH_PARAM_INVALID",
                f"subject 列 {condition['subject']!r} 不属于 {indicator} 输出列",
                detail={"param": "condition.subject",
                        "legal_range": sorted(
                            c for c in df.columns
                            if indicator.lower() in c.lower())[:20]})

    # 切段（auto）或显式段补标（type 缺省按段内判读众数）
    if isinstance(segmentation, str):  # "auto"
        segments = build_segments(stat, adx_col, atr_col)
    else:
        from core.analysis.regime_segments import classify_series
        state = classify_series(stat, adx_col, atr_col)
        segments = []
        for seg in segmentation:
            sub = state[(state.index >= _ts_utc(seg["start"]))
                        & (state.index <= _ts_utc(seg["end"]))]
            if sub.empty:
                raise ResearchError(
                    "RESEARCH_PARAM_INVALID",
                    f"显式段区间无数据: {seg!r}",
                    detail={"param": "segmentation",
                            "legal_range": str((stat.index[0], stat.index[-1]))})
            seg_type = seg.get("type") or sub.mode().iloc[0]
            sl = stat[(stat.index >= _ts_utc(seg["start"]))
                      & (stat.index <= _ts_utc(seg["end"]))]
            segments.append({
                "start": sub.index[0], "end": sub.index[-1],
                "type": seg_type, "bars": int(len(sub)),
                "adx_mean": round(float(sl[adx_col].mean()), 2),
                "atr_pct_mean": round(
                    float((sl[atr_col] / sl["close"] * 100).mean()), 2),
                "atr_pct_pct_mean": None,
            })

    seg_payload = [{**s, "start": str(s["start"]), "end": str(s["end"])}
                   for s in segments]

    # 可选逐段统计（condition_stats 同口径；资格门 <30 旁路）
    by_segment: Optional[List[Dict[str, Any]]] = None
    if condition is not None:
        fwd_all = df["close"].pct_change(forward_bars).shift(-forward_bars)
        stat_mask_all = _mask_for(df, condition)
        by_segment = []
        for i, s in enumerate(segments):
            in_seg = pd.Series(False, index=df.index)
            in_seg[(df.index >= s["start"]) & (df.index <= s["end"])] = True
            sm = stat_mask_all & in_seg
            hit = fwd_all[sm].dropna()
            baseline = fwd_all[in_seg].dropna()
            baseline_mean = float(baseline.mean()) if len(baseline) else None
            p_value, n_valid = _p_value_for(
                fwd_all, sm.values, forward_bars, baseline_mean)
            if len(hit) < _MIN_SEGMENT_SAMPLES:
                state_v = "insufficient_sample"
                mean_v = p_v = None
            else:
                state_v = "scored"
                mean_v = float(hit.mean())
                p_v = p_value
            by_segment.append({
                "segment": i, "type": s["type"], "samples": int(len(hit)),
                "forward_return_mean": mean_v,
                "baseline_mean": baseline_mean, "p_value": p_v,
                "state": state_v,
            })

    payload = {
        "segmentation": "auto" if isinstance(segmentation, str) else "explicit",
        "segments": seg_payload,
        "condition_by_segment": by_segment,
        "gates": {**GATES, "min_segment_samples": _MIN_SEGMENT_SAMPLES},
        "method": {"regime": "adx_state_x_vol_override",
                   "split": "state_run_merge",
                   "stats": "t_test_vs_baseline"},
    }
    return build_result(
        template="regime_split",
        sample_size=int(len(stat)),
        confidence={"method": "state_split", "level": GATES["vol_pct"],
                    "segments": len(segments)},
        data_ranges=data_ranges,
        payload=payload,
        hypothesis_id=hypothesis_id,
    )
