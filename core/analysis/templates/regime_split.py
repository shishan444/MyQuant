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

from core.analysis.condition_kernel import MtfEvalContext, mask_for, p_value_for, stat_window_mask
from core.analysis.warmup_loader import to_utc
from core.analysis.hypothesis_service import ResearchError
from core.analysis.templates.param_guard import _require_int_range
from core.analysis.regime_segments import GATES, build_segments
from core.analysis.result import AnalysisResult, build_result

_MIN_SEGMENT_SAMPLES = 30  # 逐段统计资格门（与 param_sweep 同口径）


def _validate_segments_input(
    segmentation: Any, condition: Any, indicator: Any,
    forward_bars: int, n_df: int,
    conditions: Any = None,
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
    # TD-3.1 迁移 param_guard（输出形制与旧内联逐字节一致）
    _require_int_range(forward_bars, name="forward_bars",
                       lo=1, hi=n_df - 1)
    if condition is not None and (
            not isinstance(condition, dict)
            or not {"subject", "action", "target"} <= set(condition)):
        # 0.2.14：三键必备、额外键允许（可选 timeframe 键=高周期中心
        # 条件经掩码投影求值；动作额外键随条件全量透传——对齐 param_sweep）
        raise ResearchError(
            "RESEARCH_PARAM_INVALID",
            f"condition 须含 subject/action/target 三键: {condition!r}",
            detail={"param": "condition",
                    "legal_range": "与扫描候选/假设登记同形式的单条件"
                                   "（MAY 携带 timeframe 键）"})
    if condition is not None and not isinstance(indicator, str):
        raise ResearchError(
            "RESEARCH_PARAM_INVALID",
            "携带 condition 时 indicator 必填（指标名——列名 subject 的"
            "归属指标，双件形制同 param_sweep）",
            detail={"param": "indicator",
                    "legal_range": "注册表指标名（list_indicators 查询）"})
    # 变更三 7.1：conditions 组合形制——条数与逐条三键校验复用共享
    # 单点（condition_stats 同源；同传互斥由路由层键组规则覆盖）
    if conditions is not None:
        from core.analysis.condition_kernel import validate_conditions_array
        validate_conditions_array(conditions)



def _ts_utc(v: Any) -> pd.Timestamp:
    """时间参数归一薄壳：to_utc + 任意值构造 pd.Timestamp（五处调用
    传 str/datetime 混合形）。"""
    return to_utc(pd.Timestamp(v))


def regime_split(
    df: pd.DataFrame,
    *,
    segmentation: Any = "auto",
    indicator: Optional[str] = None,
    condition: Optional[Dict[str, Any]] = None,
    conditions: Optional[List[Dict[str, Any]]] = None,
    forward_bars: int = 24,
    data_ranges: List[Dict[str, str]] | None = None,
    timeframe: str = "1h",
    request_start: str | pd.Timestamp | None = None,
    hypothesis_id: str | None = None,
    mtf: "MtfEvalContext | None" = None,
) -> AnalysisResult:
    """行情分段（状态序列直切）+ 可选逐段条件统计。

    Args:
        df: 含 warmup 前缀的 OHLCV+ADX/ATR DataFrame（路由经
            load_with_warmup 前扩装载；统计段以 request_start 切回）。
        segmentation: "auto" 或显式段列表 [{start, end, type?}]。
        indicator: condition 的归属指标名（condition 存在时必填）。
        condition: 可选中心条件（subject 列名须属于 df 列——由路由层
            以指标矩阵装载，双件形制同 param_sweep）。
        conditions: 可选组合条件数组（变更三 7.x——1..5 条 AND 语义，
            形制与 condition_stats 一致：结构化/基础列/插件列 subject
            与列名 target 均可；路由层经 _load_refs 前扩装载+插件列
            enrich 已入 df，求值走 mask_for_all 共享内核）。
        forward_bars: 逐段统计的前向持有期 N。
        request_start: 请求区间起点（warmup 只用不算——切段统计段）。

    Raises:
        ResearchError: 参数非法（附 legal_range）。
    """
    from core.features.indicators import _compute_indicator
    from core.features.registry import resolve_indicator_column

    _validate_segments_input(segmentation, condition, indicator,
                             forward_bars, len(df), conditions=conditions)

    # 入口副本：判读/条件指标列不写入调用方 df（模板纯计算契约，
    # 同族三模板 copy 形制——condition_stats/screen_patterns/param_sweep）
    df = df.copy()
    adx_col = resolve_indicator_column("ADX", {"period": 14})
    atr_col = resolve_indicator_column("ATR", {"period": 14})
    for c, s in _compute_indicator(df, "ADX", {"period": 14}).items():
        df[c] = s
    for c, s in _compute_indicator(df, "ATR", {"period": 14}).items():
        df[c] = s

    # 统计段切分（warmup 前缀只用不算——判读指标在副本全量收敛；
    # 置于指标计算后：布尔切片为副本，先切则不含新算指标列）
    stat = df[stat_window_mask(df, request_start)]
    if conditions is not None:
        # 变更三 7.2：组合形式不预装载归属指标、不做 subject 归属校验
        # ——列解析统一权威在 mask_for（结构化自算/_ensure_target_column
        # 现算/插件列经路由层 enrich 已入 df），不在模板层复制第二套
        pass
    elif condition is not None:
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
        # 0.2.13：结构化 subject 经 encode 归一精确列名（同 param_sweep）
        _subject_col = condition["subject"]
        if isinstance(_subject_col, dict):
            from core.analysis.column_ref import resolve_column_ref
            _subject_col = resolve_column_ref(_subject_col).encoded_name()
            condition = dict(condition, subject=_subject_col)
        if _subject_col not in df.columns:
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
    # 逐段统计（condition_stats 同口径；资格门 <30 旁路）——掩码来源
    # 前置分叉（变更三 7.2 + review 发现 2 重构：两形制共享单一循环体，
    # 口径同构由结构保证而非复制粘贴维持——键集/资格门/统计量单点定义）
    stat_mask_all = None
    if conditions is not None:
        from core.analysis.condition_kernel import mask_for_all
        stat_mask_all = mask_for_all(df, conditions, mtf=mtf)
    elif condition is not None:
        stat_mask_all = mask_for(df, condition, mtf=mtf)

    by_segment: Optional[List[Dict[str, Any]]] = None
    if stat_mask_all is not None:
        fwd_all = df["close"].pct_change(forward_bars).shift(-forward_bars)
        by_segment = []
        for i, s in enumerate(segments):
            in_seg = pd.Series(False, index=df.index)
            in_seg[(df.index >= s["start"]) & (df.index <= s["end"])] = True
            sm = stat_mask_all & in_seg
            hit = fwd_all[sm].dropna()
            baseline = fwd_all[in_seg].dropna()
            baseline_mean = float(baseline.mean()) if len(baseline) else None
            p_value, n_valid = p_value_for(
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
