"""反例画像模板（add-research-analysis-tools 0.4.1 R5）。

语义：条件规律的"反例驱动精化"通道——把条件的历史触发自动分为
符合组（观察窗口内任一时点实现方向预期——区间语义：触及即符合，
「触及目标后回落」的正常盈利样本不误判）与反例组，对两组在触发
时点的全指标特征分布做对比、按判别力（Cohen's d 效应量 A1）排序
输出差异清单——支撑从「大概率」精化到「高区分」。

边界（工具只呈现差异不生成条件）：输出 MUST NOT 自动生成新条件/
改写假设/给特征选择建议——特征选择由 AI 基于差异清单自主做出。
分布参数族与 distribution_compare._desc / condition_stats._series_stats
同键八键 {count,mean,std,min,q25,median,q75,max}（统计内核单一事实
源；入口不复用——分组形制不同 D6）。特征域=注册表 _DEFAULT_PARAMS
全部参数组全部输出列、排除 guard_only 与 compute_mode=="lazy"
（screen_patterns 同款筛选）；现算失败条目跳过并 skipped_features
披露（不静默缺失）。

方向判定比较序列=close 收盘价（A 级假设——与条件统计前向收益口径
同族；high/low 影线噪声不采用）。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd

from core.analysis.hypothesis_service import ResearchError
from core.analysis.result import AnalysisResult, build_result

# 方向封闭枚举（mixed 是执行概念不属研究主张——不开放）
_DIRECTIONS = ("long", "short")


def feature_matrix_names() -> List[str]:
    """特征矩阵指标名（注册表 _DEFAULT_PARAMS 全集减 guard_only/lazy
    ——screen_patterns "all" 同款筛选；装载 warmup 预算输入）。"""
    from core.features.registry import INDICATOR_REGISTRY

    return [name for name, d in INDICATOR_REGISTRY.items()
            if not d.guard_only and d.compute_mode != "lazy"]


def _feature_stats(s: pd.Series) -> Dict[str, Any]:
    """八键分布（空组：count=0 其余 None——键恒在，显式缺值语义）。"""
    if s.empty:
        return {"count": 0, "mean": None, "std": None, "min": None,
                "q25": None, "median": None, "q75": None, "max": None}
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


def _cohens_d(a: pd.Series, b: pd.Series) -> Optional[float]:
    """标准化效应量（合并方差 Cohen's d；样本不足或零方差 None）。"""
    if len(a) < 2 or len(b) < 2:
        return None
    va, vb = float(np.nanvar(a, ddof=1)), float(np.nanvar(b, ddof=1))
    na, nb = len(a), len(b)
    pooled = ((na - 1) * va + (nb - 1) * vb) / (na + nb - 2)
    if pooled <= 0:
        return None
    return float((np.nanmean(a) - np.nanmean(b)) / np.sqrt(pooled))


def counter_profile(
    df: pd.DataFrame,
    *,
    condition: Optional[Dict[str, Any]] = None,
    conditions: Optional[List[Dict[str, Any]]] = None,
    direction: str,
    data_ranges: list[dict[str, str]],
    forward_bars: int = 24,
    request_start: str | pd.Timestamp | None = None,
    min_samples: int = 30,
    hypothesis_id: str | None = None,
    mtf: "MtfEvalContext | None" = None,
) -> AnalysisResult:
    """反例画像：触发分组（区间语义）+ 全指标特征分布对比 + 判别力排序。

    Args:
        condition / conditions: 二选一（键组机制出生自带——两形制统一走
            引用并集前扩装载与 mask_for_all 求值，含长度 1）。
        direction: 方向预期（封闭枚举 long/short——判定比较序列 close）。
        forward_bars: 观察窗口（bar 数，与条件统计模板同口径缺省 24）。

    Raises:
        ResearchError: direction 枚举外（含 mixed）；forward_bars 非法；
            小样本拒绝（总触发数 < min_samples，经 build_result）。
    """
    from core.analysis.condition_kernel import mask_for_all, stat_window_mask
    from core.analysis.templates.param_guard import _require_int_range
    from core.features.indicators import _DEFAULT_PARAMS, _compute_indicator
    from core.features.registry import INDICATOR_REGISTRY

    if direction not in _DIRECTIONS:
        raise ResearchError(
            "RESEARCH_PARAM_INVALID",
            f"direction 封闭枚举 {list(_DIRECTIONS)}（实际 {direction!r}"
            "——mixed 是执行概念不属研究主张）",
            detail={"param": "direction", "value": direction,
                    "legal_range": list(_DIRECTIONS)})
    if (condition is None) == (conditions is None):
        raise ResearchError(
            "RESEARCH_PARAM_INVALID",
            "condition 与 conditions 须二选一（键组分派在路由层——"
            "直调方同样须遵守）",
            detail={"param": "condition|conditions",
                    "legal_form": "任选其一"})
    _require_int_range(forward_bars, name="forward_bars",
                       lo=1, hi=max(1, len(df) - 1))
    _require_int_range(min_samples, name="min_samples", lo=0)

    # 触发集：掩码求值（两形制统一 mask_for_all）∩ 统计段
    cond_list = conditions if conditions is not None else [condition]
    mask = mask_for_all(df, cond_list, mtf=mtf)
    in_stat = stat_window_mask(df, request_start)
    stat_mask = (mask & in_stat).fillna(False).astype(bool)
    n_triggers = int(stat_mask.sum())

    # 分组（区间语义）：符合=窗口内任一时点实现方向预期
    # （rolling max/min——触及即符合，终点口径会把「触及后回落」的
    # 正常盈利样本误判为反例）；尾部窗口不完整的触发不参与分组
    # （NaN 比较产 False 非 NaN——可判定性以 future 序列本身判）
    close = df["close"]
    if direction == "long":
        future = close.rolling(forward_bars).max().shift(-forward_bars)
        conform_mask = future > close
    else:
        future = close.rolling(forward_bars).min().shift(-forward_bars)
        conform_mask = future < close
    decidable = stat_mask & future.notna().values
    dropped_tail = n_triggers - int(decidable.sum())
    is_conform = decidable & conform_mask.fillna(False).astype(bool).values
    is_counter = decidable & ~conform_mask.fillna(False).astype(bool).values
    n_conform, n_counter = int(is_conform.sum()), int(is_counter.sum())
    n_grouped = n_conform + n_counter

    # 特征矩阵：全部参数组全部输出列（减 guard_only/lazy）触发时点值；
    # 现算失败条目跳过并披露
    work = df.copy()
    skipped_features: List[Dict[str, Any]] = []
    features: List[Dict[str, Any]] = []
    for name in feature_matrix_names():
        if name not in INDICATOR_REGISTRY:
            continue
        for params in _DEFAULT_PARAMS.get(name, []):
            try:
                cols = _compute_indicator(work, name, dict(params))
            except (KeyError, ValueError, AttributeError, TypeError) as exc:
                skipped_features.append(
                    {"indicator": name, "params": dict(params),
                     "error": str(exc)[:120]})
                continue
            for col, series in cols.items():
                a = series[is_conform].dropna()
                b = series[is_counter].dropna()
                features.append({
                    "feature": f"{name}:{col}",
                    "indicator": name,
                    "params": dict(params),
                    "conform": _feature_stats(a),
                    "counter": _feature_stats(b),
                    "cohens_d": _cohens_d(a, b),
                })
    # 判别力排序：|Cohen's d| 降序（None 沉底）
    features.sort(key=lambda f: (f["cohens_d"] is None,
                                 -abs(f["cohens_d"]) if f["cohens_d"] is not
                                 None else 0.0))

    # 反例占比 Wilson 区间（两组规模不确定性的诚实表达）+ 点估计披露
    #（counter_rate 与区间同源：n_grouped=0 时无点估计——键保留 None）
    counter_rate = (n_counter / n_grouped) if n_grouped else None
    confidence: Dict[str, Any] = {
        "method": "wilson_interval",
        "interval": (list(_wilson(n_counter, n_grouped))
                     if n_grouped else [0.0, 0.0]),
        "for": "counter_rate",
        "counter_rate": counter_rate,
        "n_grouped": n_grouped,
    }

    payload: Dict[str, Any] = {
        "direction": direction,
        "forward_bars": forward_bars,
        "groups": {
            "conform": {"count": n_conform},
            "counter": {"count": n_counter},
            # 反例组样本量低于 min_samples → insufficient 标注不拒
            #（与总触发小样本拒绝是两条通道：后者拒绝整个请求）
            "comparison_status": ("insufficient"
                                  if n_counter < min_samples else "ok"),
            "dropped_tail_triggers": dropped_tail,
        },
        "features": features,
    }
    if conditions is not None:
        payload["conditions"] = [dict(c) for c in conditions]
    else:
        payload["condition"] = dict(condition)
    if request_start is not None:
        from core.analysis.warmup_loader import to_utc
        req_ts = to_utc(pd.Timestamp(request_start))
        payload["warmup_bars"] = int((df.index < req_ts).sum())
    if skipped_features:
        payload["skipped_features"] = skipped_features

    return build_result(
        "counter_profile",
        sample_size=n_triggers,  # 总触发数（< min_samples 走既有小样本拒绝）
        confidence=confidence,
        data_ranges=data_ranges,
        payload=payload,
        min_samples=min_samples,
        hypothesis_id=hypothesis_id,
    )


def _wilson(successes: int, n: int, z: float = 1.96) -> list[float]:
    """Wilson 置信区间（委托 scoring 权威实现——condition_stats 同款）。"""
    from core.scoring.statistics import wilson_interval

    return list(wilson_interval(successes, n, z))
