"""WHEN/THEN 假设验证引擎（求值内核已提取至 core/analysis/condition_eval
——retire-legacy-subsystems D-1；本模块为编排残骸+过渡 re-export，随
core/validation/ 壳退役）。

服务对象：/api/validate 端点（前端验证页经此调 validate_hypothesis）、
rule_engine（进出场配对共用 _evaluate_conditions）、tests/analysis
对比基准链。职责边界：WHEN 触发识别 → THEN 前瞻窗口核验 → 统计聚合
（分布/分位/集中度/信号频率/极值）；单条件求值原语（_evaluate_
single_condition 族）委托 condition_eval，条件掩码语义委托
condition_kernel.mask_for。
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional
import logging
import numpy as np
import pandas as pd
from typing import TYPE_CHECKING

from core.features.indicators import compute_all_indicators
# 求值内核 re-export（D-1 提取——原模块内定义迁移 condition_eval，本
# 模块自身消费 _cond_number/_cond_window；其余符号为 rule_engine 与
# 存量测试的过渡引用面，随 core/validation/ 壳退役收敛直调）
from core.analysis.condition_eval import (  # noqa: F401
    STATISTICS_ACTION_KEYS,
    _evaluate_single_condition,
    _wick_touch_condition,
    _resolve_subject,
    _resolve_target,
    _resolve_pattern,
    _cross_above_series_condition,
    _cross_below_series_condition,
    _lookback_inner_mask,
    _lookback_condition,
    _within_bars_after_condition,
    _cond_number,
    _cond_window,
    _resolve_snr_pattern,
    _touch_condition,
    _cross_above,
    _cross_below,
    _spike_condition,
    _shrink_condition,
)

if TYPE_CHECKING:
    from core.analysis.condition_kernel import MtfEvalContext
from core.data.storage import load_parquet

logger = logging.getLogger(__name__)

# 0.3.7 TD-11 / CARD-04：LOOKBACK_INNER_ACTIONS 定义点在
# condition_kernel 单源，此处按原名 re-export——既有测试 import 零改动
from core.analysis.condition_kernel import (
    LOOKBACK_INNER_ACTIONS as _LOOKBACK_INNER_ACTIONS,  # noqa: F401
)


@dataclass
class TriggerRecord:
    """单次触发事件（时间/价格/THEN 达成/触发时指标值快照）。"""
    id: int
    time: str
    trigger_price: float
    change_pct: float
    matched: bool
    indicator_values: Dict[str, float] = field(default_factory=dict)


@dataclass
class DistributionBucket:
    """涨幅分布直方图单桶（达成/未达成计数）。"""
    range_start: float
    range_end: float
    match_count: int
    mismatch_count: int
    total_count: int


@dataclass
class ValidationResult:
    """完整验证输出（命中率 + 触发明细 + 五组统计）。"""
    match_rate: float
    total_count: int
    match_count: int
    mismatch_count: int
    triggers: List[TriggerRecord] = field(default_factory=list)
    distribution: List[DistributionBucket] = field(default_factory=list)
    percentiles: Dict[str, float] = field(default_factory=dict)
    concentration: Dict[str, List[float]] = field(default_factory=dict)
    signal_frequency: Dict[str, float] = field(default_factory=dict)
    extremes: List[Dict[str, Any]] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)


def validate_hypothesis(
    pair: str,
    timeframe: str,
    start: str,
    end: str,
    when_conditions: List[Dict[str, Any]],
    then_conditions: List[Dict[str, Any]],
    indicator_params: Optional[Dict[str, Any]] = None,
    then_window: int = 8,
    data_dir: Optional[str] = None,
    base_timeframe: Optional[str] = None,
    mtf: Optional["MtfEvalContext"] = None,
    trigger_policy: Optional[Dict[str, Any]] = None,
) -> ValidationResult:
    """对历史数据验证 WHEN→THEN 假设。

    Args:
        pair: 交易对如 "BTCUSDT"
        timeframe: K 线周期如 "4h"
        start: 起始日 "2024-01-01"
        end: 截止日 "2024-12-31"
        when_conditions: WHEN 条件 dict 列表
        then_conditions: THEN 条件 dict 列表
        indicator_params: 可选指标参数覆盖
        then_window: THEN 核验的前瞻 bar 数
        data_dir: 可选行情数据目录
        base_timeframe: MTF 验证的基准周期（可选）

    Returns:
        ValidationResult 全量统计
    """
    from pathlib import Path
    from core.analysis.errors import ResearchError
    from core.data.mtf_loader import safe_symbol, find_parquet
    from core.validation.mtf import enrich_with_mtf

    if data_dir is None:
        from core.data.fapi.rest import default_data_dir

        data_dir = str(default_data_dir())

    # 生效基准周期（base_timeframe 优先）
    effective_base = base_timeframe or timeframe

    # 数据装载（B4.1 共享路径发现：safe_symbol 归一 + 别名兜底；
    # 未知 timeframe 结构化拒绝而非静默空结果）
    try:
        parquet_path = find_parquet(Path(data_dir), safe_symbol(pair), timeframe)
    except ValueError as exc:
        raise ResearchError(
            "RESEARCH_PARAM_INVALID",
            f"timeframe 未知: {timeframe!r}",
            detail={"param": "timeframe", "value": timeframe,
                    "legal_range": "TF_SECONDS 已知周期"},
        ) from exc

    if parquet_path is None:
        return ValidationResult(match_rate=0, total_count=0, match_count=0, mismatch_count=0)

    df = load_parquet(parquet_path)
    if df is None or len(df) == 0:
        return ValidationResult(match_rate=0, total_count=0, match_count=0, mismatch_count=0)

    # 日期区间过滤
    if start:
        df = df[df.index >= start]
    if end:
        df = df[df.index <= end]

    if len(df) == 0:
        return ValidationResult(match_rate=0, total_count=0, match_count=0, mismatch_count=0)

    # 基准周期指标计算
    enhanced_df = compute_all_indicators(df)

    # MTF：收集条件引用的高周期并合并其指标列
    all_conditions = when_conditions + then_conditions
    referenced_tfs = set()
    for cond in all_conditions:
        cond_tf = cond.get("timeframe", "")
        if cond_tf and cond_tf != effective_base:
            referenced_tfs.add(cond_tf)

    # 求值期收集警告
    warnings: List[str] = []

    if referenced_tfs:
        # R-3（fix-silent-degradation）：装载-合并段与 rule_engine 共享
        # 单点 enrich_with_mtf（原逐字重复段收口；数据类异常回退单周期
        # 求值+固定文案 warning、非数据类上抛显性失败——取舍见其实现）
        enhanced_df, _w = enrich_with_mtf(
            enhanced_df, pair, timeframe, sorted(referenced_tfs), start, end, data_dir)
        warnings.extend(_w)

    # Evaluate WHEN conditions（mtf 上下文透传——Lab 链高周期条件掩码投影）
    when_mask = _evaluate_conditions(enhanced_df, when_conditions, warnings, mtf=mtf)

    # 触发点定位
    trigger_indices = enhanced_df.index[when_mask].tolist()

    # trigger-policy 卡3：带策略时掩码经权威变换为事件位置（/api/validate
    # 统计链单点分支——无策略路径上面原语句逐字不动；归一形由调用侧
    # validate_policy 产出）。bars 上界为求值侧值域校验（D12——登记侧
    # 仅形制，此处数据已加载，与 forward_bars 同窗同语义；CARD-03 收敛
    # require_bars_within 单点，param 名保真统计链声明键）。
    if trigger_policy is not None:
        from core.analysis.event_policy import mask_to_events, require_bars_within
        require_bars_within(trigger_policy, len(df), "trigger_policy")
        event_pos = mask_to_events(when_mask.values, trigger_policy)
        trigger_indices = enhanced_df.index[event_pos].tolist()

    if not trigger_indices:
        return ValidationResult(match_rate=0, total_count=0, match_count=0, mismatch_count=0, warnings=warnings)

    # THEN 窗口取全条件最大值
    max_then_window = then_window
    for cond in then_conditions:
        cond_window = cond.get("window")
        if cond_window is not None:
            max_then_window = max(max_then_window, cond_window)

    # 逐触发点核验 THEN 条件
    triggers = []
    changes = []
    for i, trigger_idx in enumerate(trigger_indices):
        trigger_loc = enhanced_df.index.get_loc(trigger_idx)
        trigger_price = enhanced_df.loc[trigger_idx, "close"]

        # THEN 前瞻窗口（取全条件最大窗口切窗）
        end_loc = min(trigger_loc + max_then_window + 1, len(enhanced_df))
        window_df = enhanced_df.iloc[trigger_loc + 1:end_loc]

        if len(window_df) == 0:
            continue

        # THEN 核验（各条件在自窗内判定）
        then_matched = _check_then_conditions(window_df, then_conditions, trigger_price, trigger_loc, max_then_window)

        # 窗末涨跌幅
        final_price = window_df.iloc[-1]["close"]
        change_pct = ((final_price - trigger_price) / trigger_price) * 100

        # 记录触发时刻的指标值快照
        trigger_row = enhanced_df.loc[trigger_idx]
        ind_values = {}
        for col in enhanced_df.columns:
            if col not in ["open", "high", "low", "close", "volume"]:
                try:
                    val = float(trigger_row[col])
                    if not np.isnan(val):
                        ind_values[col] = round(val, 4)
                except (ValueError, TypeError):
                    pass

        triggers.append(TriggerRecord(
            id=i + 1,
            time=str(trigger_idx),
            trigger_price=trigger_price,
            change_pct=round(change_pct, 2),
            matched=then_matched,
            indicator_values=ind_values,
        ))
        changes.append(change_pct)

    if not triggers:
        return ValidationResult(match_rate=0, total_count=0, match_count=0, mismatch_count=0, warnings=warnings)

    match_count = sum(1 for t in triggers if t.matched)
    total_count = len(triggers)
    match_rate = round(match_count / total_count * 100, 1) if total_count > 0 else 0

    # 涨幅分布
    distribution = _build_distribution(changes, [t.matched for t in triggers])

    # 分位数
    percentiles = {}
    if changes:
        arr = np.array(changes)
        percentiles = {
            "p10": round(float(np.percentile(arr, 10)), 2),
            "p25": round(float(np.percentile(arr, 25)), 2),
            "p50": round(float(np.percentile(arr, 50)), 2),
            "p75": round(float(np.percentile(arr, 75)), 2),
            "p90": round(float(np.percentile(arr, 90)), 2),
            "min": round(float(np.min(arr)), 2),
            "max": round(float(np.max(arr)), 2),
        }

    # 集中度区间
    concentration = {}
    if changes:
        arr = np.array(changes)
        concentration = {
            "p50_range": [round(float(np.percentile(arr, 25)), 2), round(float(np.percentile(arr, 75)), 2)],
            "p90_range": [round(float(np.percentile(arr, 5)), 2), round(float(np.percentile(arr, 95)), 2)],
        }

    # 信号频率（月/周口径）
    signal_frequency = {}
    if total_count > 0 and len(df) > 0:
        days_covered = (df.index[-1] - df.index[0]).days
        if days_covered > 0:
            months = days_covered / 30.44
            signal_frequency = {
                "per_month": round(total_count / months, 1),
                "per_week": round(total_count / (days_covered / 7), 1),
                "total_months": round(months, 0),
            }

    # 极值（最差触发）
    extremes = []
    if changes:
        sorted_triggers = sorted(triggers, key=lambda t: t.change_pct)
        worst = sorted_triggers[0]
        extremes.append({
            "change_pct": worst.change_pct,
            "time": worst.time,
            "is_match": worst.matched,
        })

    return ValidationResult(
        match_rate=match_rate,
        total_count=total_count,
        match_count=match_count,
        mismatch_count=total_count - match_count,
        triggers=triggers,
        distribution=distribution,
        percentiles=percentiles,
        concentration=concentration,
        signal_frequency=signal_frequency,
        extremes=extremes,
        warnings=warnings,
    )


def _evaluate_conditions(
    df: pd.DataFrame, conditions: List[Dict],
    warnings: List[str] | None = None,
    mtf: "MtfEvalContext | None" = None,
) -> pd.Series:
    """逐条件 logic 组合求值：engine（WHEN/THEN）与 rule_engine（进出场配对）共用实现。

    每个条件自身的 ``logic`` 字段决定它与累积结果的组合方式（AND/OR），
    首条件无累积对象其 logic 忽略，IF 与 AND 在布尔级等价。
    """
    if not conditions:
        return pd.Series(False, index=df.index)

    result: Optional[pd.Series] = None
    from core.analysis.condition_kernel import mask_for

    for i, cond in enumerate(conditions):
        mask = mask_for(df, cond, mtf=mtf)   # 0.2.14 三分派（mtf 透传）
        # 逐条件语义：每个条件自身的 logic 与累积结果组合；
        # 首条件无累积对象，其 logic 忽略；IF 与 AND 布尔级等价
        logic = cond.get("logic", "AND") if i > 0 else None

        if result is None:
            result = mask
        elif logic == "OR":
            result = result | mask
        else:
            result = result & mask

    if result is not None:
        return result.fillna(False)

    return pd.Series(False, index=df.index)


def _check_then_conditions(
    window_df: pd.DataFrame,
    conditions: List[Dict],
    trigger_price: float,
    trigger_loc: int = 0,
    max_window: int = 8,
) -> bool:
    """前瞻窗口内 THEN 条件是否达成。

    各条件可带自身 ``window`` 字段——存在时 window_df 先截到该条件
    的窗口再判定。
    """
    for cond in conditions:
        action = cond.get("action", "")

        # 逐条件窗口：条件自带更小窗口时截窗判定（window 坏值/零/负
        # 经 _cond_window 同构拒绝——iloc[:0] 静默截空跳过条件使兜底块
        # 以坏 target 判定、iloc[:负] 尾截均为失真路径，Q7 收口）
        cond_window = cond.get("window")
        if cond_window is None:
            cond_df = window_df
        else:
            cond_window = _cond_window(cond)
            cond_df = window_df.iloc[:cond_window] if cond_window < max_window else window_df

        if len(cond_df) == 0:
            continue

        # 0.3.7 TD-11：THEN 侧 target 坏值同规收口（原静默归 0——
        # WHEN/THEN 两通道同构，无静默侧）
        threshold = _cond_number(cond, "target", 0)

        if action in ("drop", "lt", "le"):
            min_price = cond_df["close"].min()
            change = ((min_price - trigger_price) / trigger_price) * 100
            if action == "drop" and change <= threshold:
                return True
            if action == "lt" and change < threshold:
                return True
            if action == "le" and change <= threshold:
                return True
        elif action in ("rise", "gt", "ge"):
            max_price = cond_df["close"].max()
            change = ((max_price - trigger_price) / trigger_price) * 100
            if action == "rise" and change >= threshold:
                return True
            if action == "gt" and change > threshold:
                return True
            if action == "ge" and change >= threshold:
                return True

    # 兜底：任一条件按 target 阈值核验价格变动（TD-11 同构：坏 target
    # 结构化拒绝——循环体空窗 continue 曾使坏值绕过 _cond_number 落到
    # 此处静默归 0 判定，Q7 收口后两通道同一拒绝语义）
    if conditions:
        cond = conditions[0]
        action = cond.get("action", "drop")
        threshold = _cond_number(cond, "target", 0)

        if action in ("drop",):
            change = ((window_df["close"].min() - trigger_price) / trigger_price) * 100
            return bool(change <= threshold)
        elif action in ("rise",):
            change = ((window_df["close"].max() - trigger_price) / trigger_price) * 100
            return bool(change >= threshold)

    return False


def _build_distribution(changes: List[float], matches: List[bool]) -> List[DistributionBucket]:
    """涨幅百分比的直方图分桶（末桶闭区间，其余左闭右开）。"""
    if not changes:
        return []

    arr = np.array(changes)
    min_val = float(np.min(arr))
    max_val = float(np.max(arr))

    # 分桶（5-10 桶，按样本量取）
    num_buckets = min(10, max(5, len(changes) // 5))
    bucket_width = (max_val - min_val) / num_buckets if max_val != min_val else 2

    buckets = []
    for i in range(num_buckets):
        start = min_val + i * bucket_width
        end = start + bucket_width

        mask = (arr >= start) & (arr < end) if i < num_buckets - 1 else (arr >= start) & (arr <= end)
        indices = np.where(mask)[0]

        match_count = sum(1 for idx in indices if matches[idx])
        total = len(indices)

        buckets.append(DistributionBucket(
            range_start=round(start, 1),
            range_end=round(end, 1),
            match_count=match_count,
            mismatch_count=total - match_count,
            total_count=total,
        ))

    return buckets
