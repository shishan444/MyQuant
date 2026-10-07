"""条件统计模板（add-ai-research-mcp 2.1；fix-condition-stats-forward-return
前向收益语义修复）。

语义：在给定数据上计算指标，统计"条件成立的 bar"占比与信号后的前向
收益分布——回答"这个条件历史上出现得多频繁、出现后 N 根收益如何"。
条件形制复用假设验证引擎的 {subject, action, target}（core/validation
/engine.py 同源语义），指标计算复用 core/features/indicators
._compute_indicator（任务书授权的私有复用——同一指标定义源，不造第二套）。

前向收益口径 close(t) → close(t+N)（向量化 close.pct_change(N).shift(-N)，
与统计侧相关扫描/模式挖掘/假设引擎同口径）。显著性为条件组前向收益均值
vs 全体 bar 前向收益均值（baseline）的单样本 t 检验，p 值仅在间隔
≥ forward_bars 的去重叠信号子集上计算（重叠窗口不虚增显著性）；
描述性统计用全样本，两组样本量分别声明。

sample_size 为有效前向信号数（信号总数 − 尾部不足 N 根的丢弃数，
丢弃数以 dropped_tail_signals 输出）；小样本拒绝阈值作用于该口径。

warmup「只用不算」：request_start 非 None 时，df 视为"warmup 前缀 +
统计段"（路由路径由 load_with_warmup 前扩装载）——指标在全 df（含
前缀）上计算以保证收敛，统计仅取 work.index >= request_start 的 bar，
前缀根数以 warmup_bars 声明。

当根收益（close.pct_change()）字段名为 same_bar_return_stats（仅诊断
用；旧字段名 return_stats_when_hit 已废弃，不再承载任何收益语义）。
"""
from __future__ import annotations

import math
from typing import Any, Dict

import numpy as np
import pandas as pd

from core.analysis.hypothesis_service import ResearchError
from core.analysis.templates.param_guard import _require_int_range
from core.analysis.result import AnalysisResult, build_result
from core.analysis.warmup_loader import to_utc


def _wilson_interval(successes: int, n: int, z: float = 1.96) -> list[float]:
    """Wilson 置信区间（委托 scoring 权威实现，返回 list 保持调用方兼容）。"""
    from core.scoring.statistics import wilson_interval

    return list(wilson_interval(successes, n, z))


def _series_stats(s: pd.Series) -> Dict[str, float]:
    if s.empty:
        return {}
    return {
        "count": int(len(s)),
        "mean": float(np.nanmean(s)),
        "std": float(np.nanstd(s, ddof=1)) if len(s) > 1 else 0.0,
        "min": float(np.nanmin(s)),
        "q25": float(np.nanquantile(s, 0.25)),
        "median": float(np.nanquantile(s, 0.50)),
        "q75": float(np.nanquantile(s, 0.75)),
        "max": float(np.nanmax(s)),
    }


def condition_stats(
    df: pd.DataFrame,
    *,
    indicator: str,
    params: Dict[str, Any],
    condition: Dict[str, Any],
    data_ranges: list[dict[str, str]],
    forward_bars: int = 24,
    request_start: str | pd.Timestamp | None = None,
    min_samples: int = 30,
    hypothesis_id: str | None = None,
    mtf: "MtfEvalContext | None" = None,
    trigger_policy: Dict[str, Any] | None = None,
) -> AnalysisResult:
    """条件频率与信号后前向收益统计。

    Args:
        df: 含 OHLCV（及衍生品列，可选）的 DataFrame，DatetimeIndex UTC。
            路由路径下为 load_with_warmup 前扩装载结果（含 warmup 前缀）。
        indicator: INDICATOR_REGISTRY 指标名（如 "EMA"）。
        params: 指标参数（如 {"period": 50}）。
        condition: {subject, action, target}；subject 可引用指标输出列
            （命名形如 ema_50）或基础列（close/volume/open_interest...）。
        forward_bars: 前向持有期 N（bar 数），前向收益 = close(t+N)/close(t)-1。
        request_start: 请求区间起点。None 时统计段=全 df（向后兼容）；
            非 None 时 df 视为含 warmup 前缀的装载结果，统计段取
            work.index >= request_start 的 bar（前缀只用不算）。

    Raises:
        ResearchError: 未知指标；forward_bars/min_samples 非法（附
            合法范围）；小样本拒绝（经 build_result——卡10 收编，原裸
            ValueError 落 500 误导 Agent 盲重试）；target 不可解析
            （卡3b）。subject 缺列不抛（0.2.13 契约：全 False 安全降级）。
    """
    from core.features.indicators import _compute_indicator
    from core.features.registry import INDICATOR_REGISTRY
    from core.validation.engine import _evaluate_single_condition

    from core.analysis.templates.column_guard import require_indicator
    require_indicator(indicator, INDICATOR_REGISTRY)
    # 参数前置守卫（坏 params 从模板内 KeyError→failed 任务变 422——
    # 复用读数通道的 D7 校验权威）
    from core.analysis.indicator_query import validate_params
    validate_params(indicator, params)

    # 持有期校验（TD-3.1 迁移 param_guard——输出形制与旧内联逐字节一致）
    _require_int_range(forward_bars, name="forward_bars",
                       lo=1, hi=len(df) - 1)
    # 小样本阈值校验（clear-analysis-tds CARD-01：裸透传补守卫——
    # 阈值类 lo=0 无上界，0=禁用小样本检查的既有合法语义，与
    # correlation_scan/distribution_compare 同族一致）
    _require_int_range(min_samples, name="min_samples", lo=0)

    # trigger-policy 卡4（5.2）：形制归一 + bars 上界求值侧校验（全 df
    # 含 warmup 前缀——与 forward_bars 同窗；D12 数据已加载）
    from core.analysis.event_policy import validate_policy
    policy_norm = validate_policy(trigger_policy)
    if (policy_norm is not None
            and policy_norm.get("mode") == "suppress_bars"
            and policy_norm["bars"] > len(df) - 1):
        raise ResearchError(
            "RESEARCH_PARAM_INVALID",
            f"trigger_policy.bars 超求值数据长度上界: {policy_norm['bars']}",
            detail={"param": "trigger_policy.bars",
                    "value": policy_norm["bars"],
                    "legal_range": f"1..{len(df) - 1}"})

    # 指标计算（不改输入 df；全 df——含 warmup 前缀，保证指标收敛）
    new_cols = _compute_indicator(df, indicator, dict(params))
    work = df.copy()
    for col, series in new_cols.items():
        work[col] = series

    # 0.2.13：求值走 condition_kernel._mask_for（两段式内聚——结构化
    # subject 先 encode 精确列名再求值，字符串精确命中；fillna 降级同款）
    # 0.2.14：mtf 上下文透传——高周期条件（timeframe 键）走掩码投影
    from core.analysis.condition_kernel import (
        MtfEvalContext, _mask_for, _p_value_for, stat_window_mask)
    mask = _mask_for(work, condition, mtf=mtf)

    # 统计段：request_start 之前的 warmup 前缀只用不算（共享掩码
    # 样板；req_ts 留供 warmup_bars 披露）
    in_stat = stat_window_mask(work, request_start)
    req_ts = to_utc(pd.Timestamp(request_start)) if request_start is not None else None

    stat_mask = mask & in_stat
    # trigger-policy 卡4：带策略时统计段内掩码经权威变换为事件位置
    # 回投事件掩码（变换域=统计段过滤后——与统计链 /api/validate 同
    # 域，specs 消费面同源锚；warmup 前缀不参与段结构判定，跨链事件
    # 序列一致的前提）。上面掩码直译语句保留——无策略路径逐字节不变。
    # 下游 fwd/ret/loc 布尔索引与 _p_value_for 全部天然切口径；
    # n_hit/sample_size/dropped_tail 随事件集。
    if policy_norm is not None:
        from core.analysis.event_policy import mask_to_events
        event_pos = mask_to_events(stat_mask.values, policy_norm)
        stat_mask = pd.Series(False, index=stat_mask.index)
        stat_mask.iloc[event_pos] = True
    n_total = int(in_stat.sum())  # 统计段 bar 数（hit_rate 分母）

    # 前向收益（全 df 计算；尾部不足 N 根为 NaN）
    fwd = work["close"].pct_change(forward_bars).shift(-forward_bars)
    hit_fwd = fwd[stat_mask]
    valid = hit_fwd.dropna()
    dropped = int(hit_fwd.isna().sum())  # 尾部不足 N 根的信号数
    n_hit = int(stat_mask.sum())  # 统计段命中数 == len(hit_fwd)

    # baseline：统计段全体 bar 的前向收益均值
    bas_fwd = fwd[in_stat].dropna()
    baseline_mean = float(bas_fwd.mean()) if len(bas_fwd) else None

    # 去重叠 t 检验（0.2.1 同口径——第四消费方收敛至 condition_kernel
    # _p_value_for 共享实现，消除双实现漂移面；返回 (p, n_overlapped)，
    # baseline None / 样本<2 / 零方差时 p 为 None）
    p_value, n_overlapped = _p_value_for(
        fwd, stat_mask.values, forward_bars, baseline_mean)
    forward_significance: Dict[str, Any] = {
        "method": "t_test_vs_baseline",
        "p_value": p_value,
        "for": "forward_return_mean_vs_baseline",
        "n_overlapped": int(n_overlapped),
        "n_total_signals": int(len(valid)),
        "baseline_mean": baseline_mean,
    }

    rate_interval = _wilson_interval(n_hit, n_total)
    confidence = {
        "method": "wilson_interval",
        "interval": rate_interval,
        "for": "hit_rate",
        "n_total": n_total,
        "forward_significance": forward_significance,
    }

    # 当根收益（仅诊断用；非前向收益语义）
    ret = work["close"].pct_change()
    payload = {
        "indicator": indicator,
        "params": dict(params),
        "condition": dict(condition),
        "forward_bars": forward_bars,
        "hit_rate": (n_hit / n_total) if n_total else 0.0,
        "hit_count": n_hit,
        f"forward_return_{forward_bars}b": _series_stats(valid),
        f"baseline_{forward_bars}b": {"mean": baseline_mean},
        "win_rate": float((valid > 0).mean()) if len(valid) else None,
        "same_bar_return_stats": _series_stats(ret[stat_mask]),
        "close_stats_when_hit": _series_stats(work.loc[stat_mask, "close"]),
        "dropped_tail_signals": dropped,
        "warmup_bars": (int((work.index < req_ts).sum())
                        if req_ts is not None else 0),
        "total_bars": int(len(work)),  # 全 df（含 warmup 前缀）
    }
    if policy_norm is not None:
        payload["trigger_policy"] = policy_norm   # 报告声明生效策略

    return build_result(
        "condition_stats",
        sample_size=int(len(valid)),  # 有效前向信号数（< min_samples 即小样本拒绝）
        confidence=confidence,
        data_ranges=data_ranges,
        payload=payload,
        min_samples=min_samples,
        hypothesis_id=hypothesis_id,
    )
