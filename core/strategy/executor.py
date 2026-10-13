"""策略 DNA → 交易信号向量转换（回测/实盘/进化全策略执行链的信号
生产桥接模块）。

职责：把 StrategyDNA 的信号基因（指标条件 × 角色）求值为四类交易
信号（entries/exits/adds/reduces），并组合层内/跨层逻辑产出
SignalSet——vectorbt 回测引擎、实盘执行器与进化适应度评估共用的
唯一信号生产实现（batch_signal_sets 为进化种群提供基因级去重的
批量求值）。
边界：只做信号生产与组合，不做仓位/风控计算（risk_genes 在
回测引擎侧消费）；指标列来自 enhanced_df 既有列或按需现算，
数据装载归上游调用方。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import numpy as np
import pandas as pd

from core.strategy.dna import StrategyDNA, SignalRole
from core.features.registry import INDICATOR_REGISTRY, resolve_indicator_column

logger = logging.getLogger(__name__)


@dataclass
class SignalSet:
    """单个策略的完整交易信号集（四类信号 + MTF 诊断元数据）。"""

    entries: pd.Series     # 开仓信号
    exits: pd.Series       # 平仓信号
    adds: pd.Series        # 加仓信号
    reduces: pd.Series     # 减仓信号
    degraded_layers: int = 0    # 因数据缺失被跳过的 MTF 层数
    entry_direction: pd.Series | None = None  # 入场方向：+1 多 / -1 空（mixed 方向模式用）
    mtf_diagnostics: dict | None = None  # MTF 引擎诊断评分
    confidence: pd.Series | None = None  # MTF 逐 bar 置信度（产出与值域权威在 mtf_engine._compute_confidence）


def evaluate_condition(
    indicator_series: pd.Series,
    close_series: pd.Series,
    condition: dict,
    df: pd.DataFrame | None = None,
) -> pd.Series:
    """结构化条件 dict → 布尔 Series（单基因条件求值的唯一入口）。

    未知条件类型返回全 False（进化变异可产任意类型串，不炸链）。

    Args:
        indicator_series: 指标值序列（subject 侧）。
        close_series: 收盘价序列（price_above/price_below 类的比较基准）。
        condition: 含 "type" 与可选 "threshold" 的条件字典。
        df: 完整 enhanced DataFrame（cross_above_series / lookback_any/
            all / touch_bounce / role_reversal / wick_touch 类需要全列）。

    Returns:
        与输入同索引的布尔 Series。
    """
    cond_type = condition["type"]

    if cond_type == "eq":
        threshold = condition.get("threshold", 1)
        return indicator_series == threshold
    elif cond_type in ("lt", "gt", "le", "ge"):
        # fix-research-c6 存在性分派：threshold 非 None 走现状阈值比较
        # （旧 DNA 逐字节不变）；threshold 缺失且带 target_indicator →
        # 序列对照（逐元素比较，target 列经 _resolve_target_indicator_
        # series 现算）；两者皆无 → 全 False（消灭原裸下标 KeyError 在
        # 缺键时炸穿全链的潜伏隐患——现状因进化恒写 threshold 未触发）。
        # 并存（进化变异恒写逻辑可产）→ threshold 优先、target_* 忽略
        # ——退化行为确定不炸链。
        threshold = condition.get("threshold")
        if threshold is not None:
            op = {"lt": lambda s, t: s < t, "gt": lambda s, t: s > t,
                  "le": lambda s, t: s <= t, "ge": lambda s, t: s >= t}[cond_type]
            return op(indicator_series, threshold)
        if "target_indicator" in condition:
            target_series = _resolve_target_indicator_series(
                condition, df)
            if target_series is None:
                return pd.Series(False, index=indicator_series.index)
            return {"lt": lambda s, t: s < t, "gt": lambda s, t: s > t,
                    "le": lambda s, t: s <= t, "ge": lambda s, t: s >= t}[cond_type](
                indicator_series, target_series)
        return pd.Series(False, index=indicator_series.index)
    elif cond_type == "cross_above":
        threshold = condition.get("threshold")
        if threshold is not None:
            return (indicator_series.shift(1) < threshold) & (indicator_series >= threshold)
        else:
            return pd.Series(False, index=indicator_series.index)
    elif cond_type == "cross_below":
        threshold = condition.get("threshold")
        if threshold is not None:
            return (indicator_series.shift(1) > threshold) & (indicator_series <= threshold)
        else:
            return pd.Series(False, index=indicator_series.index)
    elif cond_type == "price_above":
        return close_series > indicator_series
    elif cond_type == "price_below":
        return close_series < indicator_series
    # ── 动态条件（序列对照类）──
    elif cond_type == "cross_above_series":
        target_series = _resolve_target_indicator_series(condition, df)
        if target_series is None:
            return pd.Series(False, index=indicator_series.index)
        return (indicator_series.shift(1) < target_series.shift(1)) & (indicator_series >= target_series)
    elif cond_type == "cross_below_series":
        target_series = _resolve_target_indicator_series(condition, df)
        if target_series is None:
            return pd.Series(False, index=indicator_series.index)
        return (indicator_series.shift(1) > target_series.shift(1)) & (indicator_series <= target_series)
    elif cond_type == "lookback_any":
        window = condition.get("window", 5)
        inner = condition.get("inner", {})
        inner_signal = _evaluate_inner_condition(indicator_series, close_series, inner, df)
        return (inner_signal.astype(float).rolling(window=window, min_periods=1).sum() > 0).fillna(False).astype(bool)
    elif cond_type == "lookback_all":
        window = condition.get("window", 5)
        inner = condition.get("inner", {})
        inner_signal = _evaluate_inner_condition(indicator_series, close_series, inner, df)
        return (inner_signal.astype(float).rolling(window=window, min_periods=window).sum() == window).fillna(False).astype(bool)
    # ── 支撑/阻力类条件 ──
    elif cond_type == "touch_bounce":
        return _eval_touch_bounce(indicator_series, close_series, df, condition)
    elif cond_type == "role_reversal":
        return _eval_role_reversal(indicator_series, close_series, df, condition)
    elif cond_type == "wick_touch":
        return _eval_wick_touch(indicator_series, close_series, df, condition)
    else:
        return pd.Series(False, index=indicator_series.index)


def _resolve_target_indicator_series(condition: dict, df: pd.DataFrame | None) -> pd.Series | None:
    """把 target_indicator + target_params 解析为 df 中的目标序列。

    序列对照类条件（cross_above_series 等）的 target 侧现算入口。"""
    if df is None:
        return None
    target_name = condition.get("target_indicator", "")
    target_params = condition.get("target_params", {})
    # fix-research-c6：target_field 透传 _SimpleGene 的 field_name 槽
    # （多输出指标如 MACD 的列名 target 解码后 field 落正确列——无
    # field 时 None=单输出指标缺省列，_get_indicator_column 既有语义）
    gene = _SimpleGene(target_name, target_params,
                       condition.get("target_field"))
    try:
        return _get_indicator_column(df, gene)
    except ValueError:
        return None


def _evaluate_inner_condition(
    indicator_series: pd.Series,
    close_series: pd.Series,
    inner: dict,
    df: pd.DataFrame | None,
) -> pd.Series:
    """回看窗类条件的内层条件求值（阈值比较直算，复杂形委托主入口）。"""
    inner_type = inner.get("type", "")
    if inner_type in ("lt", "gt", "le", "ge"):
        threshold = inner.get("threshold", 0)
        if inner_type == "lt":
            return indicator_series < threshold
        elif inner_type == "gt":
            return indicator_series > threshold
        elif inner_type == "le":
            return indicator_series <= threshold
        elif inner_type == "ge":
            return indicator_series >= threshold
    elif inner_type == "price_above":
        return close_series > indicator_series
    elif inner_type == "price_below":
        return close_series < indicator_series
    # 复杂内层条件委托 evaluate_condition 主入口（单点求值语义）
    return evaluate_condition(indicator_series, close_series, inner, df)


def _eval_touch_bounce(
    indicator_series: pd.Series,
    close_series: pd.Series,
    df: pd.DataFrame | None,
    condition: dict,
) -> pd.Series:
    """触碰反弹：价格触及指标线后向反方向反弹。

    direction="support"：最低价近线 + 收盘在线上 + 当前根收盘较前一根更高
    direction="resistance"：最高价近线 + 收盘在线下 + 当前根收盘较前一根更低
    """
    if df is None:
        return pd.Series(False, index=indicator_series.index)

    direction = condition.get("direction", "support")
    proximity_pct = condition.get("proximity_pct", 0.01)

    low = df["low"]
    high = df["high"]
    line = indicator_series

    # 近线判定只用当前及历史数据（无 shift(-1)——不引入未来数据）
    if direction == "support":
        proximity = (low - line).abs() <= line.abs() * proximity_pct
        above_line = close_series > line
        bounce_up = close_series > close_series.shift(1)  # 当前收盘 > 前一根收盘
        result = proximity & above_line & bounce_up
    else:
        proximity = (high - line).abs() <= line.abs() * proximity_pct
        below_line = close_series < line
        bounce_down = close_series < close_series.shift(1)  # 当前收盘 < 前一根收盘
        result = proximity & below_line & bounce_down

    return result.fillna(False)


def _eval_role_reversal(
    indicator_series: pd.Series,
    close_series: pd.Series,
    df: pd.DataFrame | None,
    condition: dict,
) -> pd.Series:
    """角色反转：指标线从支撑转阻力或从阻力转支撑。

    role="support"：近期自下向上穿越 + 当前位于线上
    role="resistance"：近期自上向下穿越 + 当前位于线下
    """
    if df is None:
        return pd.Series(False, index=indicator_series.index)

    role = condition.get("role", "resistance")
    lookback = condition.get("lookback", 10)

    line = indicator_series

    if role == "support":
        # 线此前为阻力（价格在线下），当前价格已上穿
        was_below = close_series.shift(lookback) < line.shift(lookback)
        now_above = close_series > line
        result = was_below & now_above
    else:
        # 线此前为支撑（价格在线上），当前价格已下破
        was_above = close_series.shift(lookback) > line.shift(lookback)
        now_below = close_series < line
        result = was_above & now_below

    return result.fillna(False)


def _eval_wick_touch(
    indicator_series: pd.Series,
    close_series: pd.Series,
    df: pd.DataFrame | None,
    condition: dict,
) -> pd.Series:
    """影线触碰：影线触及指标线但收盘价在线的另一侧。

    direction="above"：最高价近线 + 收盘在线下
    direction="below"：最低价近线 + 收盘在线上
    """
    if df is None:
        return pd.Series(False, index=indicator_series.index)

    direction = condition.get("direction", "above")
    proximity_pct = condition.get("proximity_pct", 0.01)

    low = df["low"]
    high = df["high"]
    line = indicator_series

    if direction == "above":
        wick_near = (high - line).abs() <= line.abs() * proximity_pct
        close_below = close_series < line
        result = wick_near & close_below
    else:
        wick_near = (low - line).abs() <= line.abs() * proximity_pct
        close_above = close_series > line
        result = wick_near & close_above

    return result.fillna(False)


class _SimpleGene:
    """最小 gene 形对象（_get_indicator_column 复用的鸭子类型适配）。"""
    __slots__ = ("indicator", "params", "field_name")

    def __init__(self, indicator: str, params: dict, field_name: str | None = None):
        self.indicator = indicator
        self.params = params
        self.field_name = field_name


def _empty_signal_set(df: pd.DataFrame) -> SignalSet:
    """返回与 DataFrame 索引对齐的全 False SignalSet（降级空信号）。"""
    return SignalSet(
        entries=pd.Series(False, index=df.index),
        exits=pd.Series(False, index=df.index),
        adds=pd.Series(False, index=df.index),
        reduces=pd.Series(False, index=df.index),
    )


def combine_signals(signal_list: list[pd.Series], logic: str) -> pd.Series:
    """多布尔 Series 的 AND/OR 组合（层内与跨层信号合并的共用件）。"""
    if not signal_list:
        return pd.Series(False, index=signal_list[0].index if signal_list else [])

    if logic == "AND":
        result = signal_list[0].copy()
        for s in signal_list[1:]:
            result = result & s
        return result
    elif logic == "OR":
        result = signal_list[0].copy()
        for s in signal_list[1:]:
            result = result | s
        return result
    else:
        return signal_list[0].copy()


_indicator_column_cache: dict = {}


def _get_indicator_column(df: pd.DataFrame, gene, _cache=None) -> pd.Series:
    """从 enhanced DataFrame 解析指标列（基因级列查找单点）。

    查找序：注册表精确列名 → 列名前缀匹配 → 按需现算（变异产生
    非默认参数时列未预计算）。结果按 (df 身份, 指标, 参数, 字段)
    缓存——同代重复查找零重算；缓存生命周期由调用方显式清理
    （clear_indicator_cache，dna_to_signal_set 入口调用）。
    """
    cache = _cache if _cache is not None else _indicator_column_cache
    cache_key = (id(df), gene.indicator,
                 tuple(sorted(gene.params.items())),
                 gene.field_name)
    if cache_key in cache:
        return cache[cache_key]

    indicator = gene.indicator
    params = gene.params

    # 经注册表解析列名（编码命名权威）
    reg = INDICATOR_REGISTRY.get(indicator)
    if reg is not None:
        col = resolve_indicator_column(
            indicator, params, gene.field_name or "", reg.naming
        )
    else:
        # 未知指标兜底：按列名前缀匹配
        matches = [c for c in df.columns if c.lower().startswith(indicator.lower())]
        if matches:
            col = matches[0]
        else:
            raise ValueError(f"Cannot find column for indicator {indicator}")

    if col in df.columns:
        result = df[col]
        cache[cache_key] = result
        return result

    # 兜底：列名子串匹配
    matches = [c for c in df.columns if col in c]
    if matches:
        result = df[matches[0]]
        cache[cache_key] = result
        return result

    # 按需现算：指标已注册但列未预计算（如变异产生非默认参数）——
    # 现算并写回 df 供后续查找复用
    if reg is not None:
        from core.features.indicators import _compute_indicator
        new_cols = _compute_indicator(df, indicator, params)
        if new_cols:
            # 一次整块写回（fix-executor-batch-health）：逐列 insert 在宽
            # enhanced_df（>100 列）上触发 pandas PerformanceWarning 且插入
            # 成本线性上升——攒齐 new_cols 整块赋值，列集合与值不变；
            # 极短数据下部分输出列可能为 None（原逐列写回同样容忍），
            # 以 None 列写入保持列集合不变
            df[list(new_cols.keys())] = pd.DataFrame(
                {name: (series if series is not None else None)
                 for name, series in new_cols.items()},
                index=df.index)
            # 现算列写回后重试查找
            if col in df.columns:
                result = df[col]
                cache[cache_key] = result
                return result
            # 列名不完全一致——按现算输出列名互含匹配
            for name in new_cols:
                if col in name or name in col:
                    result = df[name]
                    cache[cache_key] = result
                    return result

    raise ValueError(f"Column '{col}' not found in DataFrame. Available: {list(df.columns)}")


def clear_indicator_cache() -> None:
    """清空指标列缓存。

    每代求值入口调用——缓存以 id(df) 为键不自动失效，跨代/跨测试
    残留会因 Python id 复用命中陈旧键返回错误列。
    """
    _indicator_column_cache.clear()


def evaluate_layer(
    layer,
    df: pd.DataFrame,
) -> SignalSet:
    """在预计算 DataFrame 上求值单个 TimeframeLayer。

    返回含 entries/exits/adds/reduces 的 SignalSet（MTF 逐层求值
    单元——层内按基因角色分组后经 logic_genes 组合）。
    """
    close = df["close"]
    entry_triggers, entry_guards = [], []
    exit_triggers, exit_guards = [], []
    add_triggers, add_guards = [], []
    add_inhibitors = []  # 0.4.2 R3/DC-1：ADD_INHIBIT 抑制基因（任一成立不加仓）
    reduce_triggers, reduce_guards = [], []
    direction_genes = []

    for gene in layer.signal_genes:
        try:
            indicator_col = _get_indicator_column(df, gene)
            signal = evaluate_condition(indicator_col, close, gene.condition, df=df)
            signal = signal.fillna(False)

            if gene.role == SignalRole.ENTRY_TRIGGER:
                entry_triggers.append(signal)
            elif gene.role == SignalRole.ENTRY_GUARD:
                entry_guards.append(signal)
            elif gene.role == SignalRole.EXIT_TRIGGER:
                exit_triggers.append(signal)
            elif gene.role == SignalRole.EXIT_GUARD:
                exit_guards.append(signal)
            elif gene.role == SignalRole.ADD_TRIGGER:
                add_triggers.append(signal)
            elif gene.role == SignalRole.ADD_GUARD:
                add_guards.append(signal)
            elif gene.role == SignalRole.ADD_INHIBIT:
                add_inhibitors.append(signal)
            elif gene.role == SignalRole.REDUCE_TRIGGER:
                reduce_triggers.append(signal)
            elif gene.role == SignalRole.REDUCE_GUARD:
                reduce_guards.append(signal)
            elif gene.role == SignalRole.DIRECTION:
                gene_direction = pd.Series(
                    np.where(signal, 1.0, -1.0),
                    index=df.index,
                )
                direction_genes.append(gene_direction)
        except ValueError:
            continue

    all_entry = entry_triggers + entry_guards
    entries = combine_signals(all_entry, layer.logic_genes.entry_logic) if all_entry else pd.Series(False, index=df.index)

    all_exit = exit_triggers + exit_guards
    exits = combine_signals(all_exit, layer.logic_genes.exit_logic) if all_exit else pd.Series(False, index=df.index)

    logic = layer.logic_genes
    add_logic = getattr(logic, 'add_logic', 'AND') or 'AND'
    reduce_logic = getattr(logic, 'reduce_logic', 'AND') or 'AND'

    all_add = add_triggers + add_guards
    adds = combine_signals(all_add, add_logic) if all_add else pd.Series(False, index=df.index)

    # 抑制组合（DC-1）：adds=(trigger AND guard) AND NOT(inhibitor 任一
    # 成立)——事件语义取反≠布尔取反，独立角色承载（cross 类锚点）
    if add_inhibitors:
        inhibited = pd.concat(add_inhibitors, axis=1).any(axis=1)
        adds = adds & ~inhibited

    all_reduce = reduce_triggers + reduce_guards
    reduces = combine_signals(all_reduce, reduce_logic) if all_reduce else pd.Series(False, index=df.index)

    # 同 bar 开平互斥（冲突时保留 exit）
    both = entries & exits
    entries = entries & ~both

    # 由 DIRECTION 基因构建 entry_direction（存在时）
    entry_direction = direction_genes[0] if direction_genes else None

    return SignalSet(
        entries=entries,
        exits=exits,
        adds=adds,
        reduces=reduces,
        entry_direction=entry_direction,
    )


def resample_signals(
    high_tf_signals: pd.Series,
    target_index: pd.DatetimeIndex,
) -> pd.Series:
    """高周期信号投影到执行周期索引（0.2.14 asof_completed 收口）。

    高周期 bar T 的信号在 T+tf（收盘时刻）后才对基准可见——现行
    开盘即见 ffill 为前视窗口（bar T 开盘即见 T 收盘才知道的信号）。
    薄委托 mtf_alignment.project_completed_bool（四链同一权威实现）；
    tf 由信号索引差分中位数推断（infer_tf_delta——对缺口鲁棒，缺口
    上保守方向无前视）。收口 = 回测对齐实盘 ``_filter_forming_bar``
    的时间语义（实盘每时刻丢形成中 bar，shift(1) 与现行 ffill 在实盘
    时序下同值——实盘/模拟盘结果不变）。
    """
    from core.data.mtf_alignment import (
        infer_tf_delta, project_completed_bool,
    )

    if high_tf_signals.empty or len(target_index) == 0:
        return pd.Series(False, index=target_index)

    return project_completed_bool(
        high_tf_signals, target_index,
        infer_tf_delta(high_tf_signals.index))


def dna_to_signals(
    dna: StrategyDNA,
    enhanced_df: pd.DataFrame,
    dfs_by_timeframe: dict | None = None,
) -> tuple[pd.Series, pd.Series]:
    """StrategyDNA → 开平布尔 Series（向后兼容形）。

    返回 (entries, exits) 二元组；需 adds/reduces 的完整信号集
    用 dna_to_signal_set()。
    """
    sig_set = dna_to_signal_set(dna, enhanced_df, dfs_by_timeframe)
    return sig_set.entries, sig_set.exits


def _apply_entry_trigger_policy(dna: StrategyDNA,
                               entries: pd.Series) -> pd.Series:
    """入场信号事件化（trigger-policy 卡5 / D10 唯一实现）。

    带 entry_trigger_policy 的 DNA：entries 掩码经权威变换为事件位置
    回投（信号层密度=事件密度——与统计链同口径；空仓进场抑制照旧
    消费事件脉冲）。无字段策略原样返回（既有路径逐字节不变——零
    改写红线）。bars 上界求值侧校验（D12——请求区间数据同窗）。
    suppress_bars 非幂等（二次抑制窗 2N）——唯一挂点+assemble 显式
    跳过参数在结构上排除二次变换。
    """
    policy = dna.entry_trigger_policy
    if policy is None:
        return entries
    from core.analysis.event_policy import apply_policy_to_series, validate_policy
    # CARD-01（event-triggers 批次三）：策略通道全程无校验的求值点单点
    # 收口——归一（对归一形幂等；非法形在此 422 而非求值中途裸
    # TypeError/KeyError）；显式 {"mode": "none"} 归一 None=等价无策略
    # 原样返回（D4 归一形约定）
    policy = validate_policy(policy)
    if policy is None:
        return entries
    # CARD-03：bars 校验+变换+回投收敛 apply_policy_to_series 单点
    # （param 名保真策略链声明键 entry_trigger_policy）
    return apply_policy_to_series(
        entries, policy, param_name="entry_trigger_policy")


def _mixed_entry_direction(direction_genes, index: pd.Index) -> pd.Series:
    """mixed 模式 entry_direction 生成（dna_to_signal_set 与批量求值
    共享单源——review-042 CARD-13 双份收敛）。

    有 DIRECTION 基因取首个基因序列（bar 级方向信号）；无基因中性
    0.0（引擎方向分派 0 值不触发）——validate_dna 对 mixed 无
    DIRECTION 基因为 errors 拒绝，此处中性兜底只服务绕过校验的
    库内直调路径（不炸、不误触发）。
    """
    if direction_genes:
        return direction_genes[0]
    return pd.Series(0.0, index=index)


def dna_to_signal_set(
    dna: StrategyDNA,
    enhanced_df: pd.DataFrame,
    dfs_by_timeframe: dict | None = None,
    apply_trigger_policy: bool = True,
) -> SignalSet:
    """StrategyDNA → 完整 SignalSet（含 adds/reduces 的信号生产主入口）。

    apply_trigger_policy=False 供 assemble_signal_set 取掩码态 base
    （插件组合在完整合并掩码上组合后统一变换——防二次变换；
    trigger-policy 卡5）。
    """
    # 入口清空模块级指标列缓存：缓存以 id(df) 为键且不自动清理，
    # 不清则上次调用/测试的残留 Series 跨调用泄漏——Python id 复用
    # 会让新建 df 命中陈旧键返回错误指标列
    clear_indicator_cache()
    # MTF 模式（角色感知的跨层信号组合）
    if dna.is_mtf and dfs_by_timeframe is not None:
        # MTF 划界防御（trigger-policy D10/6.3）：带 policy DNA 与 MTF
        # 层间组合语义无定义——显式结构化拒绝，不静默直译（翻译产物
        # 恒单周期，该形态只能经进化变异产生；算子侧已跳过，此处为
        # 引擎防御兜底）。单周期兼容形制放行：from_json 的 layers
        # auto-wrap（单层=execution 周期、mtf_mode 缺省）在隔离验证等
        # 传递 dfs_by_timeframe 的链上等同单周期（is_mtf 仅表示 layers
        # 数据结构），不属划界拒绝域——事件化经下方 legacy 组合出口挂。
        _policy = dna.entry_trigger_policy
        _exec_tf = dna.execution_genes.timeframe
        # R6（event-triggers 批次三）：`dna.layers is None or` 左支在
        # is_mtf 分支内恒 False（is_mtf 要求 layers 非 None 且 >=1）——
        # 死分支删除后的等价简化
        _single_compat = (
            dna.mtf_mode is None
            and len(dna.layers) == 1
            and dna.layers[0].timeframe == _exec_tf)
        if _policy is not None and not _single_compat:
            from core.analysis.hypothesis_service import ResearchError
            raise ResearchError(
                "RESEARCH_NOT_EXPRESSIBLE",
                "多周期（MTF）形态暂不支持 entry_trigger_policy——层间"
                "组合语义未定义（翻译产物恒单周期；进化算子对带 policy"
                "个体跳过 mtf_mode 变异）",
                detail={"field": "entry_trigger_policy",
                        "mtf_mode": dna.mtf_mode,
                        "slow_path": "MTF 事件策略语义留独立版本决策"})
        # 新 MTF 引擎路径：mtf_mode 已设置时启用
        if dna.mtf_mode is not None:
            from core.strategy.mtf_engine import run_mtf_engine
            return run_mtf_engine(dna, dfs_by_timeframe, enhanced_df)
        trend_entries = []
        trend_exits = []
        exec_entries = []
        exec_exits = []
        exec_adds = []
        exec_reduces = []

        # legacy 通道：无角色定义时的全层信号直并（见下方 else 分支）
        all_layer_entries = []
        all_layer_exits = []
        all_layer_adds = []
        all_layer_reduces = []

        # mixed 方向策略的趋势方向序列收集
        trend_direction_series = []

        has_any_role = any(layer.role is not None for layer in dna.layers)

        degraded_count = 0
        for layer in dna.layers:
            layer_df = dfs_by_timeframe.get(layer.timeframe)
            if layer_df is None:
                logger.error(
                    "MTF layer timeframe %r data not found in dfs_by_timeframe, "
                    "skipping layer. Available: %s",
                    layer.timeframe, list(dfs_by_timeframe.keys()),
                )
                degraded_count += 1
                continue

            sig = evaluate_layer(layer, layer_df)
            layer_role = layer.role  # None 即缺省 "execution" 执行层
            exec_tf = dna.execution_genes.timeframe
            is_cross_tf = layer.timeframe != exec_tf

            if is_cross_tf:
                resampled_entries = resample_signals(sig.entries, enhanced_df.index)
                resampled_exits = resample_signals(sig.exits, enhanced_df.index)
                resampled_adds = resample_signals(sig.adds, enhanced_df.index)
                resampled_reduces = resample_signals(sig.reduces, enhanced_df.index)

                # 0.2.14：脉冲投影收口 pulse_completed（触发时间戳 +tf
                # 平移——落在收盘时刻所在基准 bar；_resample_pulse 退役，
                # 末 bar 硬编码 1 天窗口缺陷一并消除）
                from core.data.mtf_alignment import infer_tf_delta, pulse_completed
                _layer_delta = infer_tf_delta(layer_df.index)
                pulse_entries = pulse_completed(sig.entries, enhanced_df.index, _layer_delta)
                pulse_exits = pulse_completed(sig.exits, enhanced_df.index, _layer_delta)
                pulse_adds = pulse_completed(sig.adds, enhanced_df.index, _layer_delta)
                pulse_reduces = pulse_completed(sig.reduces, enhanced_df.index, _layer_delta)
            else:
                resampled_entries = sig.entries.reindex(enhanced_df.index, fill_value=False)
                resampled_exits = sig.exits.reindex(enhanced_df.index, fill_value=False)
                resampled_adds = sig.adds.reindex(enhanced_df.index, fill_value=False)
                resampled_reduces = sig.reduces.reindex(enhanced_df.index, fill_value=False)

                pulse_entries = resampled_entries
                pulse_exits = resampled_exits
                pulse_adds = resampled_adds
                pulse_reduces = resampled_reduces

            # 并入 legacy all_layer 清单（无角色组合的向后兼容通道）
            all_layer_entries.append(resampled_entries)
            all_layer_exits.append(resampled_exits)
            all_layer_adds.append(resampled_adds)
            all_layer_reduces.append(resampled_reduces)

            if layer_role in ("trend", "structure", "zone"):
                # 趋势/结构/区间层：状态信号（重采样后持续生效的形制）
                trend_entries.append(resampled_entries)
                trend_exits.append(resampled_exits)

                # 由趋势层的条件类型推断方向
                entry_triggers = [g for g in layer.signal_genes
                                  if g.role == SignalRole.ENTRY_TRIGGER]
                direction = pd.Series(1.0, index=enhanced_df.index)
                for gene in entry_triggers:
                    cond_type = gene.condition.get("type", "")
                    if cond_type == "price_above":
                        # price_above 为真（收盘 > 指标）→ 看多 → +1
                        direction = pd.Series(
                            np.where(resampled_entries, 1.0, -1.0),
                            index=enhanced_df.index,
                        )
                        break
                    elif cond_type == "price_below":
                        # price_below 为真（收盘 < 指标）→ 看空 → -1
                        direction = pd.Series(
                            np.where(resampled_entries, -1.0, 1.0),
                            index=enhanced_df.index,
                        )
                        break
                trend_direction_series.append(direction)
            else:
                # 执行层（缺省）：脉冲信号（不做 ffill——逐事件形制）
                exec_entries.append(pulse_entries)
                exec_exits.append(pulse_exits)
                exec_adds.append(pulse_adds)
                exec_reduces.append(pulse_reduces)

        # 跨层信号组合
        if has_any_role:
            # 角色感知组合：尊重 cross_layer_logic 设定
            logic = dna.cross_layer_logic or "AND"
            if trend_entries and exec_entries:
                trend_gate = combine_signals(trend_entries, "AND")
                exec_trigger = combine_signals(exec_entries, "OR")
                if logic == "OR":
                    combined_entries = trend_gate | exec_trigger
                else:
                    combined_entries = trend_gate & exec_trigger
            elif exec_entries:
                combined_entries = combine_signals(exec_entries, "OR")
            elif trend_entries:
                combined_entries = combine_signals(trend_entries, "AND")
            else:
                combined_entries = pd.Series(False, index=enhanced_df.index)

            combined_exits = combine_signals(exec_exits, "OR") if exec_exits else pd.Series(False, index=enhanced_df.index)
            if trend_exits:
                trend_exit_signal = combine_signals(trend_exits, "OR")
                combined_exits = combined_exits | trend_exit_signal
            combined_adds = combine_signals(exec_adds, "OR") if exec_adds else pd.Series(False, index=enhanced_df.index)
            combined_reduces = combine_signals(exec_reduces, "OR") if exec_reduces else pd.Series(False, index=enhanced_df.index)
        else:
            # legacy 行为：无角色定义，全部层按 cross_layer_logic 直并
            if not all_layer_entries:
                return SignalSet(
                    entries=pd.Series(False, index=enhanced_df.index),
                    exits=pd.Series(False, index=enhanced_df.index),
                    adds=pd.Series(False, index=enhanced_df.index),
                    reduces=pd.Series(False, index=enhanced_df.index),
                    degraded_layers=degraded_count,
                )
            combined_entries = combine_signals(all_layer_entries, dna.cross_layer_logic)
            combined_exits = combine_signals(all_layer_exits, dna.cross_layer_logic)
            combined_adds = combine_signals(all_layer_adds, dna.cross_layer_logic)
            combined_reduces = combine_signals(all_layer_reduces, dna.cross_layer_logic)

        # 事件化（trigger-policy 卡5）：单周期兼容形制（auto-wrap 单层）
        # 传递 dfs_by_timeframe 的链经此出口——与单周期路径同款挂点
        # （combine 后、entry∧exit 清洗前）；真 MTF 带 policy 已在上
        # 方划界拒绝，此处仅兼容形制触达。legacy 空集分支（无 entries）
        # 空集输入下变换恒等（无 True 即无事件），不另挂。
        if apply_trigger_policy:
            combined_entries = _apply_entry_trigger_policy(
                dna, combined_entries)

        both = combined_entries & combined_exits
        combined_entries = combined_entries & ~both

        # 由趋势层构建 entry_direction（mixed 方向支持）
        entry_direction = None
        if has_any_role and trend_direction_series:
            # 趋势方向组合：取首个趋势层的方向
            entry_direction = trend_direction_series[0]

        return SignalSet(
            entries=combined_entries,
            exits=combined_exits,
            adds=combined_adds,
            reduces=combined_reduces,
            degraded_layers=degraded_count,
            entry_direction=entry_direction,
        )

    # 单周期模式
    if dna.is_mtf and dfs_by_timeframe is None:
        raise ValueError(
            f"MTF DNA requires dfs_by_timeframe but got None. "
            f"Needed timeframes: {dna.timeframes}"
        )

    close = enhanced_df["close"]

    entry_triggers, entry_guards = [], []
    exit_triggers, exit_guards = [], []
    add_triggers, add_guards = [], []
    add_inhibitors = []  # 0.4.2 R3/DC-1：ADD_INHIBIT 抑制基因
    reduce_triggers, reduce_guards = [], []
    direction_genes = []

    for gene in dna.signal_genes:
        try:
            indicator_col = _get_indicator_column(enhanced_df, gene)
            signal = evaluate_condition(indicator_col, close, gene.condition, df=enhanced_df)
            signal = signal.fillna(False)

            if gene.role == SignalRole.ENTRY_TRIGGER:
                entry_triggers.append(signal)
            elif gene.role == SignalRole.ENTRY_GUARD:
                entry_guards.append(signal)
            elif gene.role == SignalRole.EXIT_TRIGGER:
                exit_triggers.append(signal)
            elif gene.role == SignalRole.EXIT_GUARD:
                exit_guards.append(signal)
            elif gene.role == SignalRole.ADD_TRIGGER:
                add_triggers.append(signal)
            elif gene.role == SignalRole.ADD_GUARD:
                add_guards.append(signal)
            elif gene.role == SignalRole.ADD_INHIBIT:
                add_inhibitors.append(signal)
            elif gene.role == SignalRole.REDUCE_TRIGGER:
                reduce_triggers.append(signal)
            elif gene.role == SignalRole.REDUCE_GUARD:
                reduce_guards.append(signal)
            elif gene.role == SignalRole.DIRECTION:
                # DIRECTION 基因：True → +1（多），False → -1（空）
                gene_direction = pd.Series(
                    np.where(signal, 1.0, -1.0),
                    index=enhanced_df.index,
                )
                direction_genes.append(gene_direction)
        except ValueError:
            continue

    all_entry = entry_triggers + entry_guards
    entries = combine_signals(all_entry, dna.logic_genes.entry_logic) if all_entry else pd.Series(False, index=enhanced_df.index)

    all_exit = exit_triggers + exit_guards
    exits = combine_signals(all_exit, dna.logic_genes.exit_logic) if all_exit else pd.Series(False, index=enhanced_df.index)

    add_logic = getattr(dna.logic_genes, 'add_logic', 'AND') or 'AND'
    reduce_logic = getattr(dna.logic_genes, 'reduce_logic', 'AND') or 'AND'

    all_add = add_triggers + add_guards
    adds = combine_signals(all_add, add_logic) if all_add else pd.Series(False, index=enhanced_df.index)

    # 抑制组合（DC-1）：adds=(trigger AND guard) AND NOT(inhibitor 任一
    # 成立)——独立角色承载布尔 NOT（不取反 action 映射，cross 类锚点）
    if add_inhibitors:
        inhibited = pd.concat(add_inhibitors, axis=1).any(axis=1)
        adds = adds & ~inhibited

    all_reduce = reduce_triggers + reduce_guards
    reduces = combine_signals(all_reduce, reduce_logic) if all_reduce else pd.Series(False, index=enhanced_df.index)

    # 事件化（trigger-policy 卡5）：when 全集 AND 合并掩码经权威变换
    # （combine 之后、entry∧exit 清洗之前——与验证链 when 掩码同口径；
    # 清洗后再段化会把被清洗连续段断裂产生伪段首）
    if apply_trigger_policy:
        entries = _apply_entry_trigger_policy(dna, entries)

    both = entries & exits
    entries = entries & ~both

    # 生成 entry_direction（mixed 模式走共享单源；单方向显式设定）
    entry_direction = None
    dna_direction = dna.risk_genes.direction
    if dna_direction == "mixed":
        entry_direction = _mixed_entry_direction(direction_genes, enhanced_df.index)
    else:
        # 单方向 DNA：显式设定方向
        entry_direction = pd.Series(
            1.0 if dna_direction == "long" else -1.0,
            index=enhanced_df.index,
        )

    return SignalSet(
        entries=entries,
        exits=exits,
        adds=adds,
        reduces=reduces,
        entry_direction=entry_direction,
    )


def _gene_signature(gene) -> tuple:
    """信号基因的可哈希签名（批量求值的基因级去重键）。"""
    return (
        gene.indicator,
        tuple(sorted(gene.params.items())),
        gene.role.value if hasattr(gene.role, "value") else str(gene.role),
        gene.field_name,
        tuple(sorted(gene.condition.items())) if gene.condition else (),
    )


def batch_signal_sets(
    individuals: list[StrategyDNA],
    enhanced_df: pd.DataFrame,
    dfs_by_timeframe: dict | None = None,
) -> list[SignalSet]:
    """种群批量求值 SignalSet（基因级去重——进化适应度评估的批量通道）。

    个体独立求值的替代：① 收集种群全部唯一基因签名；② 每个唯一
    基因条件只求值一次；③ 从缓存结果逐个体组装 SignalSet。稳态种群（第
    2 代起）结构性共享基因（精英保留/近亲繁殖）——去重的收益前提
    （非正确性前提：首代全唯一签名时行为等价逐个体独立求值），
    去重把重复求值压缩为组装查表。

    MTF 个体回退 dna_to_signal_set() 全路径——其信号计算含跨层
    合成，无法按基因独立拆分。

    Args:
        individuals: 待求值的 StrategyDNA 列表。
        enhanced_df: 含指标的 enhanced DataFrame。
        dfs_by_timeframe: 多周期数据字典（可选）。

    Returns:
        与 individuals 同序的 SignalSet 列表。
    """
    if not individuals:
        return []

    # 延迟 import（analysis→strategy 既有依赖方向为正向，此处按项目
    # 延迟先例保持加载序稳定）：MTF 回退的防御拒绝透传判型用
    from core.analysis.hypothesis_service import ResearchError

    # 第 1 步：求值全部唯一基因条件
    gene_results: dict[tuple, pd.Series] = {}
    skipped_genes = 0  # fix-executor-batch-health：签名非法跳过计数（可观测）

    for dna in individuals:
        # MTF 个体走 dna_to_signal_set 全路径（第 2 步统一处理）
        if dna.is_mtf and dfs_by_timeframe is not None:
            continue

        for gene in dna.signal_genes:
            # 签名构造/去重查询纳入保护：结构非法（条件含 dict 值等
            # 不可哈希形态）在 hash 时抛 TypeError——不入去重表 + 计数，
            # 不上炸整批（组装段 .get 默认全 False 天然回退）
            try:
                sig = _gene_signature(gene)
                if sig in gene_results:
                    continue
            except TypeError:
                skipped_genes += 1
                logger.warning(
                    "batch_signal_sets 跳过签名非法基因（不可哈希）: "
                    "indicator=%s condition=%r",
                    gene.indicator, gene.condition)
                continue

            try:
                indicator_col = _get_indicator_column(enhanced_df, gene)
                signal = evaluate_condition(
                    indicator_col, enhanced_df["close"], gene.condition,
                    df=enhanced_df,
                )
                gene_results[sig] = signal.fillna(False)
            except (ValueError, KeyError, TypeError):
                # TypeError 兜条件 target 为 None 等形态的求值失败
                # （numpy 数值 vs None 比较）——与既有白名单同走全 False
                # 降级，单基因失败不炸整批
                gene_results[sig] = pd.Series(False, index=enhanced_df.index)

    if skipped_genes:
        logger.warning(
            "batch_signal_sets 本批共跳过 %d 个签名非法基因", skipped_genes)

    # 第 2 步：逐个体组装 SignalSet
    results: list[SignalSet] = []
    for dna in individuals:
        # MTF：全路径
        if dna.is_mtf and dfs_by_timeframe is not None:
            try:
                results.append(dna_to_signal_set(dna, enhanced_df, dfs_by_timeframe))
            except ResearchError:
                # 防御拒绝透传不吞（trigger-policy D10/6.3）：带 policy
                # MTF DNA 的结构化拒绝不得被静默替换成「策略零信号」
                raise
            except Exception:
                results.append(_empty_signal_set(enhanced_df))
            continue

        # 单周期：从缓存基因结果组装
        entry_triggers, entry_guards = [], []
        exit_triggers, exit_guards = [], []
        add_triggers, add_guards = [], []
        add_inhibitors = []  # 0.4.2 R3/DC-1：ADD_INHIBIT 抑制基因
        reduce_triggers, reduce_guards = [], []
        direction_genes = []

        _default_off = pd.Series(False, index=enhanced_df.index)
        for gene in dna.signal_genes:
            # 签名非法（不可哈希——Step 1 已跳过未入表）在组装层同样
            # 降级全 False，不炸整批
            try:
                sig = _gene_signature(gene)
                signal = gene_results.get(sig, _default_off)
            except TypeError:
                signal = _default_off

            if gene.role == SignalRole.ENTRY_TRIGGER:
                entry_triggers.append(signal)
            elif gene.role == SignalRole.ENTRY_GUARD:
                entry_guards.append(signal)
            elif gene.role == SignalRole.EXIT_TRIGGER:
                exit_triggers.append(signal)
            elif gene.role == SignalRole.EXIT_GUARD:
                exit_guards.append(signal)
            elif gene.role == SignalRole.ADD_TRIGGER:
                add_triggers.append(signal)
            elif gene.role == SignalRole.ADD_GUARD:
                add_guards.append(signal)
            elif gene.role == SignalRole.ADD_INHIBIT:
                add_inhibitors.append(signal)
            elif gene.role == SignalRole.REDUCE_TRIGGER:
                reduce_triggers.append(signal)
            elif gene.role == SignalRole.REDUCE_GUARD:
                reduce_guards.append(signal)
            elif gene.role == SignalRole.DIRECTION:
                gene_direction = pd.Series(
                    np.where(signal, 1.0, -1.0),
                    index=enhanced_df.index,
                )
                direction_genes.append(gene_direction)

        all_entry = entry_triggers + entry_guards
        entries = combine_signals(all_entry, dna.logic_genes.entry_logic) if all_entry else pd.Series(False, index=enhanced_df.index)

        all_exit = exit_triggers + exit_guards
        exits = combine_signals(all_exit, dna.logic_genes.exit_logic) if all_exit else pd.Series(False, index=enhanced_df.index)

        add_logic = getattr(dna.logic_genes, "add_logic", "AND") or "AND"
        reduce_logic = getattr(dna.logic_genes, "reduce_logic", "AND") or "AND"

        all_add = add_triggers + add_guards
        adds = combine_signals(all_add, add_logic) if all_add else pd.Series(False, index=enhanced_df.index)

        # 抑制组合（DC-1，批量路径守护不拆任务——漏分支=静默丢弃抑制剂）：
        # adds=(trigger AND guard) AND NOT(inhibitor 任一成立)
        if add_inhibitors:
            inhibited = pd.concat(add_inhibitors, axis=1).any(axis=1)
            adds = adds & ~inhibited

        all_reduce = reduce_triggers + reduce_guards
        reduces = combine_signals(all_reduce, reduce_logic) if all_reduce else pd.Series(False, index=enhanced_df.index)

        # 事件化（trigger-policy 卡5）：缓存组装出口与 dna_to_signal_set
        # 同一单实现（verify/进化评估经 batch_run 消费由此覆盖）；挂点
        # 同款——combine 后、entry∧exit 清洗前
        entries = _apply_entry_trigger_policy(dna, entries)

        both = entries & exits
        entries = entries & ~both

        # 生成 entry_direction（mixed 模式走共享单源；单方向保持 None——
        # 批量路径既有形制，方向由 direction_vals 数组承载）
        entry_direction = None
        if dna.risk_genes.direction == "mixed":
            entry_direction = _mixed_entry_direction(direction_genes, enhanced_df.index)

        results.append(SignalSet(
            entries=entries,
            exits=exits,
            adds=adds,
            reduces=reduces,
            entry_direction=entry_direction,
        ))

    return results
