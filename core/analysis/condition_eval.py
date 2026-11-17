"""单条件求值内核（retire-legacy-subsystems D-1 自 core/validation/engine.py
提取——纯搬家，签名/分派键集/pandas 语义逐字节不变）。

服务对象：condition_kernel（mask_for 兜底求值路径两处直调）与
analysis/templates/condition_stats（condition/conditions 两形制共用）、
api/routes/validate 与 rule_engine（经 engine 残骸 re-export 过渡，随
core/validation/ 壳退役改直调/退役）。职责边界：subject × action ×
target 单条件 → 布尔掩码；多条件 logic 组合与 WHEN/THEN 编排不在
本模块（随旧验证壳退役）。
"""
from __future__ import annotations

from typing import Dict, List, Optional

import pandas as pd

# 0.3.7 TD-11：lookback 族内层动作合法集（与 _lookback_condition 求值
# 分支同源防漂移——枚举对拍锚守护；缺省 price_above 是既有契约）。
# fix-mtf-review-consistency CARD-04：定义点收敛至 condition_kernel
# 单源（LOOKBACK_INNER_ACTIONS），此处按原名引用
from core.analysis.condition_kernel import (
    LOOKBACK_INNER_ACTIONS as _LOOKBACK_INNER_ACTIONS,
)


# 统计面动作键集（indicator-and-action-repair B6 卡 D 4.2）：与
# _evaluate_single_condition 内 action_map 分派表同一键集的模块级
# 事实源——求值函数内对齐断言防两处漂移
STATISTICS_ACTION_KEYS: frozenset = frozenset({
    "touch", "cross_above", "cross_below", "breakout", "breakdown",
    "gt", "lt", "ge", "le",
    "price_above", "price_below",
    "eq", "lookback_all", "wick_touch",
    "spike", "shrink",
    "divergence_top", "divergence_bottom",
    "consecutive_up", "consecutive_down",
    "cross_above_series", "cross_below_series",
    "lookback_any", "within_bars_after",
    "touch_bounce", "role_reversal",
})


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

    # 分派表与模块级键集对齐（B6 卡 D——两处漂移即红；assert 零开销）
    assert frozenset(action_map) == STATISTICS_ACTION_KEYS, \
        "action_map 与 STATISTICS_ACTION_KEYS 键集漂移（须同步维护）"

    fn = action_map.get(action)
    if fn is None:
        # 未识别动作结构化拒绝（fix-research-c2）：原「warnings + 全
        # False 掩码」静默降级——warnings 在同步调用结果里不可见，
        # 研究者误读为「该条件从未发生」（缺陷三 fr_cfbcab214136）。
        # legal_values 以 action_map 键集为单一事实源（运行时取，
        # 不写字面清单——与 signal_assembly H5 先例同形制）；经
        # ResearchError 冒泡为 422（mask_for/_mask_base 与
        # rule_engine 的 _evaluate_conditions 共用本函数自动获得）。
        from core.analysis.errors import ResearchError  # Z1：轻量定义点（errors.py 零依赖可安全模块级 import——与 param_guard 新代码先例一致）
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
    from core.analysis.patterns import (
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
    from core.analysis.errors import ResearchError

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
    proximity_pct 等键可为不可解析值——ResearchError 冒泡应用层
    全局兜底（research_error_to_http 单源）转 422。cast 保留原类型语义
    （lookback 的 int 对 "10.5" 依旧拒绝，不隐式截断）。
    """
    from core.analysis.errors import ResearchError

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
    from core.analysis.errors import ResearchError

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
    from core.analysis.patterns import detect_touch_bounce, detect_role_reversal

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
