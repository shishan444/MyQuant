"""WHEN/THEN 假设验证引擎。

服务对象：/api/validate 端点（前端验证页经此调 validate_hypothesis）、
rule_engine（进出场配对共用 _evaluate_conditions）、condition_kernel
与 analysis/templates/condition_stats（_evaluate_single_condition 为
二者的兜底求值路径）、tests/analysis 对比基准链。职责边界：WHEN
触发识别 → THEN 前瞻窗口核验 → 统计聚合（分布/分位/集中度/信号
频率/极值）；条件求值原语委托 condition_kernel.mask_for，本模块
不自持掩码语义。
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional
import logging
import numpy as np
import pandas as pd
from typing import TYPE_CHECKING

from core.features.indicators import compute_all_indicators

if TYPE_CHECKING:
    from core.analysis.condition_kernel import MtfEvalContext
from core.data.storage import load_parquet

logger = logging.getLogger(__name__)


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
    from core.analysis.hypothesis_service import ResearchError
    from core.data.mtf_loader import safe_symbol, find_parquet
    from core.validation.mtf import load_mtf_data, merge_to_base, get_timeframe_minutes

    if data_dir is None:
        data_dir = str(Path(__file__).resolve().parent.parent.parent / "data" / "market")

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
        try:
            mtf_data = load_mtf_data(pair, list(referenced_tfs), start, end, data_dir)
            if mtf_data:
                # 各高周期 DataFrame 分别算指标
                mtf_with_indicators = {}
                for tf, tf_df in mtf_data.items():
                    mtf_with_indicators[tf] = compute_all_indicators(tf_df)

                base_tf_minutes = get_timeframe_minutes(timeframe)
                tf_minutes_map = {tf: get_timeframe_minutes(tf) for tf in mtf_with_indicators}
                enhanced_df = merge_to_base(
                    enhanced_df, mtf_with_indicators,
                    base_tf_minutes, tf_minutes_map,
                )
        except Exception as exc:
            # 异常原文只进日志不回传（live_trading dna 预检同款取舍：
            # 异常文本形态不可控，warnings 面向客户端固定分类文案）
            logger.warning(
                "MTF 数据装载失败，回退单周期求值: %r", exc, exc_info=exc)
            warnings.append(
                "MTF data loading failed, falling back to single timeframe")

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


def _evaluate_single_condition(df: pd.DataFrame, cond: Dict, warnings: List[str] | None = None) -> pd.Series:
    """单条件求值（subject × action × target → 布尔掩码）。"""
    subject = cond.get("subject", "")
    action = cond.get("action", "")
    target = cond.get("target", "")

    # 解析 subject 序列
    subject_series = _resolve_subject(df, subject)
    if subject_series is None:
        if warnings is not None:
            warnings.append(f"Subject '{subject}' not found in data columns")
        return pd.Series(False, index=df.index)

    # 解析 target（数值或列序列）
    target_val = _resolve_target(df, target)

    if target_val is None:
        if warnings is not None:
            warnings.append(f"Target '{target}' could not be resolved to a numeric value or data column")
        return pd.Series(False, index=df.index)

    # 动作分派
    action_map = {
        "touch": lambda s, t: _touch_condition(s, t),
        "cross_above": lambda s, t: _cross_above(s, t),
        "cross_below": lambda s, t: _cross_below(s, t),
        "breakout": lambda s, t: _cross_above(s, t),
        "breakdown": lambda s, t: _cross_below(s, t),
        "gt": lambda s, t: s > t,
        "lt": lambda s, t: s < t,
        "ge": lambda s, t: s >= t,
        "le": lambda s, t: s <= t,
        # 价格对指标列比较（fix-research-c2）：比较主体固定 close，语义
        # 与回测面 executor.py evaluate_condition 先例逐字对齐（close >
        # indicator_series）；target 为占位值被忽略（统计面占位契约——
        # 与 screen_patterns 等价改写注释 price_above ≡ close gt X 一致）
        "price_above": lambda s, t: df["close"] > s,
        "price_below": lambda s, t: df["close"] < s,
        # 三键补实现（用户裁决 2026-10-13 能力完整方向——声明域与执行
        # 域一致，消灭对拍①豁免集）：eq/lookback_all/wick_touch 语义与
        # 回测面 executor evaluate_condition 逐字对齐
        "eq": lambda s, t: s == t,
        "lookback_all": lambda s, t: _lookback_condition(df, cond, s, "all"),
        "wick_touch": lambda s, t: _wick_touch_condition(df, cond, s),
        "spike": lambda s, t: _spike_condition(s, df, t),
        "shrink": lambda s, t: _shrink_condition(s, df, t),
        "divergence_top": lambda s, t: _resolve_pattern(df, "divergence_top", s.name if hasattr(s, "name") else subject),
        "divergence_bottom": lambda s, t: _resolve_pattern(df, "divergence_bottom", s.name if hasattr(s, "name") else subject),
        "consecutive_up": lambda s, t: _resolve_pattern(df, "consecutive_up", s.name if hasattr(s, "name") else subject, t),
        "consecutive_down": lambda s, t: _resolve_pattern(df, "consecutive_down", s.name if hasattr(s, "name") else subject, t),
        # 动态上下文动作（Phase 2）
        "cross_above_series": lambda s, t: _cross_above_series_condition(df, cond, s),
        "cross_below_series": lambda s, t: _cross_below_series_condition(df, cond, s),
        "lookback_any": lambda s, t: _lookback_condition(df, cond, s, "any"),
        # R4 时序动作（fix-mtf-execution-consistency）：事件后 N 根内
        # 不含当根——与执行链同口径（shift(1) 后开窗，T①-D 对拍锁）
        "within_bars_after": lambda s, t: _within_bars_after_condition(df, cond, s),
        # 支撑/阻力动作（Phase 4）
        "touch_bounce": lambda s, t: _resolve_snr_pattern(df, "touch_bounce", s.name if hasattr(s, "name") else subject, cond),
        "role_reversal": lambda s, t: _resolve_snr_pattern(df, "role_reversal", s.name if hasattr(s, "name") else subject, cond),
    }

    fn = action_map.get(action)
    if fn is None:
        # 未识别动作结构化拒绝（fix-research-c2）：原「warnings + 全
        # False 掩码」静默降级——warnings 在同步调用结果里不可见，
        # 研究者误读为「该条件从未发生」（缺陷三 fr_cfbcab214136）。
        # legal_values 以 action_map 键集为单一事实源（运行时取，
        # 不写字面清单——与 signal_assembly H5 先例同形制）；经
        # ResearchError 冒泡为 422（mask_for/_mask_base 与
        # rule_engine 的 _evaluate_conditions 共用本函数自动获得）。
        from core.analysis.errors import ResearchError  # Z1：轻量定义点（errors.py 零依赖可安全模块级 import——与 param_guard 新代码先例一致；同文件 4 处历史遗留旧面导入留待全域迁移专项）
        raise ResearchError(
            "RESEARCH_PARAM_INVALID",
            f"未识别的比较动作: {action!r}",
            detail={"param": "condition.action",
                    "value": action,
                    "legal_values": sorted(action_map),
                    "hint": "action 须为动作分派表键集之一"})
    return fn(subject_series, target_val)


def _wick_touch_condition(df, cond, indicator_series):
    """影线触碰（统计面——与回测面 executor._eval_wick_touch 语义对齐）：
    direction="above"：最高价近线 + 收盘在线下；"below" 反向。
    proximity_pct 缺省 0.01；附加键经 cond 透传（cross_above_series
    同款通道）。"""
    direction = cond.get("direction", "above")
    proximity_pct = cond.get("proximity_pct", 0.01)
    line = indicator_series
    if direction == "above":
        wick_near = (df["high"] - line).abs() <= line.abs() * proximity_pct
        return wick_near & (df["close"] < line)
    wick_near = (df["low"] - line).abs() <= line.abs() * proximity_pct
    return wick_near & (df["close"] > line)


def _resolve_subject(df: pd.DataFrame, subject: str) -> Optional[pd.Series]:
    """把 subject 名解析为 DataFrame 列（未命中返回 None，消费方
    全 False 降级）。"""
    subject_map = {
        "close": "close", "price": "close",
        "open": "open", "high": "high", "low": "low",
        "volume": "volume",
        "kdj": "stoch_k",
    }

    if subject in subject_map:
        col = subject_map[subject]
        return df[col] if col in df.columns else None

    # 0.2.13：模糊子串兜底退役（结构化引用+校验前置后失联即工程错误）；
    # 注册表前缀名（Lab 老通道公开形制）做确定性归一——前缀精确匹配
    # output_fields 后经 resolve 还原默认参数列（精确翻译，非模糊猜测）；
    # 其余未命中返回 None（消费方全 False 安全降级契约不变）
    if subject in df.columns:
        return df[subject]
    from core.features.registry import INDICATOR_REGISTRY
    for name, defn in INDICATOR_REGISTRY.items():
        if subject in defn.output_fields:
            from core.analysis.registry_meta import default_params_for
            from core.features.registry import resolve_indicator_column
            col = resolve_indicator_column(
                name, default_params_for(name), defn.output_fields[0])
            if col in df.columns:
                return df[col]
            return None
    return None


def _resolve_target(df: pd.DataFrame, target):
    """把 target 解析为数值或 DataFrame 列序列。

    解析顺序：
    1. 已是数值直接转 float。
    2. 字符串尝试 float 解析。
    3. 跨周期形制 "tf:indicator"（如 "4h:ema_20" → 查 "ema_20_4h" 列）。
    4. DataFrame 精确列名匹配。
    """
    if isinstance(target, (int, float)):
        return float(target)

    if not isinstance(target, str) or not target:
        return None

    # 数值解析
    try:
        return float(target)
    except (ValueError, TypeError):
        pass

    # 跨周期引用："4h:ema_20" → 查 "ema_20_4h" 列
    if ":" in target:
        parts = target.split(":", 1)
        if len(parts) == 2:
            tf_suffix, indicator = parts[0], parts[1]
            # 带周期后缀的精确列名（0.3.2 卡3c：后缀模糊兜底删除——
            # 非精确命中的列名错配产出「合法」假证据，比静默 0 更高危）
            suffixed = f"{indicator}_{tf_suffix}"
            if suffixed in df.columns:
                return df[suffixed]

    # 精确列名（卡3c：归一化子串模糊兜底删除，解析链收敛为
    # 数值/跨周期精确/精确列名/None——无任何归一化路径）
    if target in df.columns:
        return df[target]

    return None


def _resolve_pattern(
    df: pd.DataFrame,
    pattern: str,
    subject_col: str,
    target=None,
) -> pd.Series:
    """形态类动作求值（委托 patterns 模块）。"""
    from core.validation.patterns import (
        detect_divergence_top,
        detect_divergence_bottom,
        detect_consecutive_up,
        detect_consecutive_down,
    )

    # 实际列名解析（卡3c：模糊兜底删除——列缺失统一 False 降级，
    # 与 subject/target 解析的精确语义一致）
    if subject_col not in df.columns:
        return pd.Series(False, index=df.index)

    if pattern == "divergence_top":
        return detect_divergence_top(df, subject_col)
    elif pattern == "divergence_bottom":
        return detect_divergence_bottom(df, subject_col)
    elif pattern == "consecutive_up":
        count = 3
        if target is not None:
            try:
                count = int(float(target))
            except (ValueError, TypeError):
                pass
        return detect_consecutive_up(df, subject_col, count)
    elif pattern == "consecutive_down":
        count = 3
        if target is not None:
            try:
                count = int(float(target))
            except (ValueError, TypeError):
                pass
        return detect_consecutive_down(df, subject_col, count)

    return pd.Series(False, index=df.index)


def _cross_above_series_condition(
    df: pd.DataFrame, cond: Dict, subject_series: pd.Series,
) -> pd.Series:
    """上穿另一指标序列。"""
    target_series = _resolve_subject(df, cond.get("target", ""))
    if target_series is None:
        return pd.Series(False, index=df.index)
    return (subject_series.shift(1) < target_series.shift(1)) & (subject_series >= target_series)


def _cross_below_series_condition(
    df: pd.DataFrame, cond: Dict, subject_series: pd.Series,
) -> pd.Series:
    """下穿另一指标序列。"""
    target_series = _resolve_subject(df, cond.get("target", ""))
    if target_series is None:
        return pd.Series(False, index=df.index)
    return (subject_series.shift(1) > target_series.shift(1)) & (subject_series <= target_series)


# 0.3.7 TD-11：lookback 族内层动作合法集（与 _lookback_condition 求值
# 分支同源防漂移——枚举对拍锚守护；缺省 price_above 是既有契约）
_LOOKBACK_INNER_ACTIONS = frozenset(
    {"gt", "lt", "ge", "le", "price_above"})


def _lookback_inner_mask(
    df: pd.DataFrame, cond: Dict, subject_series: pd.Series,
) -> pd.Series:
    """内层动作掩码（inner_action×target 兼任阈值对 subject 列求值）。

    lookback 族与 within_bars_after 共用单源（fix-mtf-execution-
    consistency R4 提取——同族内层机制一处实现）。TD-11 非法内层动作
    结构化拒绝含内（within_bars_after 不入 _LOOKBACK_INNER_ACTIONS
    白名单——嵌套窗口递归在此拒绝，spec「MUST NOT 进入内层动作
    白名单」由不含即达）。
    """
    from core.analysis.hypothesis_service import ResearchError

    inner_action = cond.get("inner_action", "price_above")
    if inner_action not in _LOOKBACK_INNER_ACTIONS:
        # 0.3.7 TD-11：非法内层动作结构化拒绝——原静默走 else 分支
        # （price_above 缺省）使意图错位且无提示；缺省本身仍是既有契约
        raise ResearchError(
            "RESEARCH_PARAM_INVALID",
            f"condition.inner_action 未知: {inner_action!r}",
            detail={"param": "condition.inner_action",
                    "value": inner_action,
                    "legal_values": sorted(_LOOKBACK_INNER_ACTIONS),
                    "hint": "inner_action 须为合法动作集之一"})

    if inner_action in ("gt", "lt", "ge", "le"):
        # 0.3.7 TD-11：target 坏值结构化拒绝（原坏串静默归 0 变成
        # threshold=0 的失真比较）——复用 _cond_number（window 键
        # F-2 先例同码族），缺省 0 保留
        threshold = _cond_number(cond, "target", 0)
        if inner_action == "gt":
            return subject_series > threshold
        elif inner_action == "lt":
            return subject_series < threshold
        elif inner_action == "ge":
            return subject_series >= threshold
        else:
            return subject_series <= threshold
    # 默认内层动作：price_above
    return df["close"] > subject_series


def _lookback_condition(
    df: pd.DataFrame, cond: Dict, subject_series: pd.Series, mode: str,
) -> pd.Series:
    """前瞻核验：window bar 内内层条件是否达成（any/all 两模式）。"""
    # F-2：坏值/零/负窗口结构化拒绝——裸穿 rolling(window=0) 直接 500
    # （校验拆至 _cond_window，与 THEN 截窗共用同源）
    window = _cond_window(cond)
    inner_signal = _lookback_inner_mask(df, cond, subject_series)

    if mode == "any":
        return inner_signal.rolling(window=window, min_periods=1).apply(any, raw=False).fillna(False).astype(bool)
    else:
        return inner_signal.rolling(window=window, min_periods=1).apply(all, raw=False).fillna(False).astype(bool)


def _within_bars_after_condition(
    df: pd.DataFrame, cond: Dict, subject_series: pd.Series,
) -> pd.Series:
    """事件后 N 根内（不含当根）——R4 时序动作（fix-mtf-execution-
    consistency）。

    求值形制与执行链（executor.evaluate_condition within_bars_after
    分支）同一当根排除口径：内层掩码 shift(1) 后 rolling(window,
    min_periods=1).sum()>0——双链对拍 T①-D 行为锁（分析链漏 shift(1)
    则当根事件判别场景必红）。
    """
    window = _cond_window(cond)
    inner_signal = _lookback_inner_mask(df, cond, subject_series)
    return (inner_signal.astype(float).shift(1).rolling(
        window=window, min_periods=1).sum() > 0).fillna(False).astype(bool)


def _cond_number(cond: Dict, key: str, default, cast=float):
    """动作数值键取值（F-2：坏值结构化拒绝而非裸 float()/int() 穿 500）。

    弱类型入口（run_analysis 的 condition 为任意 dict）下 SNR 动作的
    proximity_pct 等键可为不可解析值——ResearchError 经 Lab/分析路由
    既有的 research_error_to_http 兜底转 422。cast 保留原类型语义
    （lookback 的 int 对 "10.5" 依旧拒绝，不隐式截断）。
    """
    from core.analysis.hypothesis_service import ResearchError

    raw = cond.get(key, default)
    try:
        return cast(raw)
    except (TypeError, ValueError):
        raise ResearchError(
            "RESEARCH_PARAM_INVALID",
            f"condition.{key} 须为数值: {raw!r}",
            detail={"param": f"condition.{key}", "value": raw,
                    "legal_range": "数值"}) from None


def _cond_window(cond: Dict) -> int:
    """条件 window 键取值校验（F-2 同码族：坏值/零/负结构化拒绝）。

    lookback 前瞻与 THEN 逐条件截窗共用（原 lookback 内联校验拆出）：
    THEN 侧 window=0 曾静默截空跳过条件（iloc[:0] → continue）使
    兜底块以坏 target 判定，负值从尾部截窗——均为失真路径，不静默。
    """
    from core.analysis.hypothesis_service import ResearchError

    window = cond.get("window", 5)
    try:
        window = int(window)
    except (TypeError, ValueError, OverflowError):
        # OverflowError：window=inf 经 int() 转换裸穿（与 _param_number 同款收口）
        raise ResearchError(
            "RESEARCH_PARAM_INVALID",
            f"condition.window 须为整数: {window!r}",
            detail={"param": "condition.window", "value": str(window),
                    "legal_range": ">=1 整数"},
        ) from None
    if window < 1:
        raise ResearchError(
            "RESEARCH_PARAM_INVALID",
            f"condition.window 须为 >=1: {window!r}",
            detail={"param": "condition.window", "value": window,
                    "legal_range": ">=1 整数"},
        )
    return window


def _resolve_snr_pattern(
    df: pd.DataFrame,
    pattern: str,
    subject_col: str,
    cond: Dict,
) -> pd.Series:
    """支撑/阻力形态动作求值（委托 patterns 模块）。"""
    from core.validation.patterns import detect_touch_bounce, detect_role_reversal

    # 实际列名解析（卡3c：模糊兜底删除——列缺失统一 False 降级，
    # 与 _resolve_pattern/subject/target 的精确解析语义一致）
    if subject_col not in df.columns:
        return pd.Series(False, index=df.index)

    if pattern == "touch_bounce":
        direction = cond.get("direction", "support")
        proximity_pct = _cond_number(cond, "proximity_pct", 0.01)
        bounce_pct = _cond_number(cond, "bounce_pct", 0.005)
        return detect_touch_bounce(df, subject_col, direction, proximity_pct, bounce_pct)
    elif pattern == "role_reversal":
        role = cond.get("role", "resistance")
        lookback = _cond_number(cond, "lookback", 10, cast=int)
        return detect_role_reversal(df, subject_col, role, lookback)

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


def _touch_condition(series: pd.Series, target) -> pd.Series:
    """触及判定（0.5% 容差或序列标准差 10% 取宽者）。"""
    tolerance = abs(target) * 0.005 if isinstance(target, (int, float)) else 0
    return abs(series - target) <= max(tolerance, series.std() * 0.1)


def _cross_above(series: pd.Series, target) -> pd.Series:
    return (series.shift(1) < target) & (series >= target)


def _cross_below(series: pd.Series, target) -> pd.Series:
    return (series.shift(1) > target) & (series <= target)


def _spike_condition(series: pd.Series, df: pd.DataFrame, target) -> pd.Series:
    """放量：当前值 > N × 滚动均值（N 默认 2.0）。"""
    try:
        multiplier = float(target) if isinstance(target, (int, float, str)) else 2.0
    except (ValueError, TypeError):
        multiplier = 2.0
    rolling_avg = series.rolling(window=20, min_periods=1).mean()
    return series >= rolling_avg * multiplier


def _shrink_condition(series: pd.Series, df: pd.DataFrame, target) -> pd.Series:
    """缩量：当前值 < N × 滚动均值（N 默认 0.5）。"""
    try:
        multiplier = float(target) if isinstance(target, (int, float, str)) else 0.5
    except (ValueError, TypeError):
        multiplier = 0.5
    rolling_avg = series.rolling(window=20, min_periods=1).mean()
    return series <= rolling_avg * multiplier


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
