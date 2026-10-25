"""MTF（Multi-TimeFrame，多周期）共振引擎——多维分数门控体系。

职能：为显式多层 StrategyDNA 提供跨层求值与决策门控——层内信号
求值加上下文提取（方向/价位/动量）、跨层合成三分数（direction/
confluence/momentum）、按 mtf_mode 过滤执行层信号；取代第一代
布尔 AND/OR 跨层组合逻辑。

服务对象：executor.dna_to_signal_set 的 MTF 分支是唯一生产消费点
（显式层 DNA 经 is_mtf 路由进入；显式多层策略回测与进化多层个体
共用该单点，一次修复全线受益）。研究链对本引擎无直接入口
（proposal 非目标：分数共振研究链可达性无需求牵引，挂起）。

职责边界：只做跨层合成与门控；层内基因信号求值委托
evaluate_layer（R1 合流单点——带周期标注基因走轴内求值+投影）；
数值上下文的周期投影委托 mtf_alignment.project_completed 权威
实现。三层求值管线：
  1. 层求值：evaluate_layer_with_context——信号集 + 上下文提取
  2. 跨层合成：synthesize_cross_layer——方向分/共振分/动量分
  3. 决策门控：apply_decision_gate——按分数与 mtf_mode 过滤

核心概念（口径依据）：
  - s%（proximity percentage，邻近百分比）= ATR/close ×
    proximity_mult——价格区间的相对半宽
  - 价格区间（price zone）：价位水平 P 附近的
    [P×(1-s%), P×(1+s%)]
  - 共振（confluence）：跨层价格区间交集宽度的归一化得分
  - 方向（direction）：由 structure 层导出，周期冲突时最高周期
    优先（highest TF wins）
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional, Tuple

import numpy as np
import pandas as pd

from core.strategy.dna import StrategyDNA, SignalRole, SignalGene, derive_role
from core.strategy.executor import (
    SignalSet, evaluate_layer, _get_indicator_column, resample_signals,
    evaluate_condition, combine_signals,
)
from core.features.registry import INDICATOR_REGISTRY


# ---------------------------------------------------------------------------
# L2：核心算法（区间代数与分数计算——纯函数无状态）
# ---------------------------------------------------------------------------

def compute_s_pct(atr: float, close: float, proximity_mult: float) -> float:
    """邻近百分比 s% = (ATR / close) × proximity_mult。

    close<=0（坏价/未收敛）返回 0.0——零宽度区间使价格共振自然
    失效，非异常路径。
    """
    if close <= 0:
        return 0.0
    return (atr / close) * proximity_mult


def build_price_zone(price: float, s_pct: float) -> Tuple[float, float]:
    """价位水平 P 的价格区间 [P×(1-s%), P×(1+s%)]。"""
    return (price * (1 - s_pct), price * (1 + s_pct))


def merge_intervals(intervals: List[Tuple[float, float]]) -> List[Tuple[float, float]]:
    """重叠区间合并为排序并集（共振度量的预处理——区间代数基元）。"""
    if not intervals:
        return []
    sorted_ivs = sorted(intervals, key=lambda x: x[0])
    merged = [sorted_ivs[0]]
    for start, end in sorted_ivs[1:]:
        if start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return merged


def intersect_intervals(
    a: List[Tuple[float, float]],
    b: List[Tuple[float, float]],
) -> List[Tuple[float, float]]:
    """两个已排序区间集的交集（双指针线性扫描；空输入得空集）。"""
    result = []
    i, j = 0, 0
    while i < len(a) and j < len(b):
        lo = max(a[i][0], b[j][0])
        hi = min(a[i][1], b[j][1])
        if lo < hi:
            result.append((lo, hi))
        if a[i][1] < b[j][1]:
            i += 1
        else:
            j += 1
    return result


def compute_confluence_score(
    layer_zones: List[List[Tuple[float, float]]],
    current_price: float,
    max_zone_width: float,
) -> float:
    """跨层价格区间交集的共振分（逐 bar 标量版，合成层循环调用）。

    得分 = (交集总宽 / max_zone_width) × price_proximity_factor：
    交集归一化宽度 × 现价邻近因子。现价在交集内因子=1.0；交集外
    按距离线性衰减（超过 max_zone_width 直接 0——远离共振区不
    计分）。非空区间组 <2 层（无跨层可言）或交集为空返回 0.0——
    缺语义而非异常。
    """
    # 过滤空区间组（该层本 bar 无价位水平）
    non_empty = [z for z in layer_zones if z]
    if len(non_empty) < 2:
        return 0.0

    # 渐进交集：逐层求交，任一步为空即无共振
    overlap = non_empty[0]
    for zones in non_empty[1:]:
        overlap = intersect_intervals(overlap, zones)
        if not overlap:
            return 0.0

    total_width = sum(hi - lo for lo, hi in overlap)
    if total_width <= 0:
        return 0.0

    # 现价与交集的关系决定邻近因子
    price_in_overlap = any(lo <= current_price <= hi for lo, hi in overlap)
    if not price_in_overlap:
        # 交集外：按到交集边缘的最小距离线性衰减
        min_dist = min(abs(current_price - lo) for lo, hi in overlap)
        if min_dist > max_zone_width:
            return 0.0
        proximity_factor = max(0.0, 1.0 - min_dist / max_zone_width)
    else:
        proximity_factor = 1.0

    raw_score = total_width / max_zone_width
    # 上限 1.0
    score = min(1.0, raw_score) * proximity_factor
    return round(score, 6)


def compute_proximity_score(
    price_levels: List[pd.Series],
    current_price: pd.Series,
    s_pct: float,
) -> pd.Series:
    """单层降级的邻近得分（Type B gap B——仅一层有价位水平时的回退）。

    向量化实现：(N, n_levels) 价位矩阵经 numpy broadcasting 逐 bar
    求到各价位水平的相对距离，取最小值折算得分。price_levels 空或
    s_pct<=0 返回全 0 序列（无价位可依）。
    """
    if not price_levels or s_pct <= 0:
        return pd.Series(0.0, index=current_price.index)

    n = len(current_price)
    price_vals = current_price.values

    # (n, n_levels) 价位矩阵
    level_arrays = [lv.values[:n] for lv in price_levels]
    level_matrix = np.column_stack(level_arrays)

    # broadcasting：(n, 1) 对 (n, n_levels)
    price_broadcast = price_vals[:, np.newaxis]
    safe_price = np.where(price_broadcast > 0, price_broadcast, 1.0)
    rel_dists = np.abs(price_broadcast - level_matrix) / safe_price

    # NaN 价位视为无穷远（该水平本 bar 不可用）
    rel_dists = np.where(np.isnan(level_matrix), np.inf, rel_dists)

    # 每 bar 最小距离
    min_rel_dist = np.nanmin(rel_dists, axis=1)

    # 命中 s% 邻域得 1 - min_dist/s_pct，域外 0
    scores = np.where(
        (min_rel_dist <= s_pct) & (price_vals > 0),
        np.maximum(0.0, 1.0 - min_rel_dist / s_pct),
        0.0,
    )

    return pd.Series(scores, index=current_price.index)


def resolve_direction_conflict(
    layers_with_direction: List[Tuple[str, pd.Series]],
) -> pd.Series:
    """structure 层间的方向冲突裁决：最高周期优先（'3d' > '1d' > '4h'）。

    空输入返回标量 0.0 序列（中性——调用方以默认方向兜底）。
    """
    if not layers_with_direction:
        return pd.Series(0.0)

    def _tf_hours(tf_str: str) -> float:
        """周期串折算小时数（排序比较用）。"""
        tf = tf_str.strip().lower()
        if tf.endswith("d"):
            return float(tf[:-1]) * 24
        if tf.endswith("h"):
            return float(tf[:-1])
        if tf.endswith("m"):
            return float(tf[:-1]) / 60
        return 0.0

    # 按周期降序取最高层
    sorted_layers = sorted(layers_with_direction, key=lambda x: _tf_hours(x[0]), reverse=True)
    return sorted_layers[0][1]


# ---------------------------------------------------------------------------
# L3：层求值器 + 上下文提取
# ---------------------------------------------------------------------------

@dataclass
class LayerResult:
    """单层求值结果：信号集 + 可选上下文（未提取的字段为 None/空）。"""
    signal_set: SignalSet
    direction: Optional[pd.Series] = None
    price_levels: List[pd.Series] = field(default_factory=list)
    momentum: Optional[pd.Series] = None
    strength: Optional[pd.Series] = None
    volatility: Optional[pd.Series] = None


def _get_all_columns(
    df: pd.DataFrame,
    indicator_name: str,
    params: dict,
) -> List[Tuple[str, pd.Series]]:
    """从 DataFrame 提取指标全部输出列（输出字段清单以注册表为权威）。

    逐列经 _get_indicator_column 解析（复用基因形制入口）；单列
    解析失败（ValueError/KeyError——列不在 df）跳过不并入，未注册
    指标返回空表。
    """
    reg = INDICATOR_REGISTRY.get(indicator_name)
    if reg is None:
        return []

    results = []
    for out_field in reg.output_fields:
        # 伪基因对象走 _get_indicator_column 既有解析链
        gene = _SimpleGene(indicator_name, params, out_field)
        try:
            col = _get_indicator_column(df, gene)
            results.append((out_field, col))
        except (ValueError, KeyError):
            continue
    return results


class _SimpleGene:
    """伪基因最小对象（__slots__ 三属性）——复用 _get_indicator_column。"""
    __slots__ = ("indicator", "params", "field_name")

    def __init__(self, indicator: str, params: dict, field_name: str | None = None):
        self.indicator = indicator
        self.params = params
        self.field_name = field_name


# ---------------------------------------------------------------------------
# 声明式上下文映射：指标类别 → 可提取上下文类型（单源口径）
# ---------------------------------------------------------------------------

_CONTEXT_SCHEMA: dict[str, set[str]] = {
    "trend": {"direction", "price_levels"},
    "volatility": {"price_levels"},
    "momentum": {"momentum"},
    "volume": {"momentum"},
    "trend_strength": {"momentum"},
    "derivatives": {"momentum"},
    "ml": {"momentum"},
}


def extract_context(
    df: pd.DataFrame,
    gene: SignalGene,
    indicator_category: str,
) -> dict:
    """从单基因求值提取上下文（方向/价位/动量——非交易信号）。

    按 _CONTEXT_SCHEMA 声明式类别映射决定提取面；动量经 rolling
    min-max 逐序列归一到 [-1, +1]。指标列缺失（ValueError/KeyError）
    或类别不在映射内返回空 dict（上下文缺失不阻断层求值——ctx 是
    门控输入的增强面非必需面）。

    返回 dict 三键（按类别可选）：
      - direction：+1/-1 序列（trend 类 × price_above/price_below）
      - price_levels：价位水平序列清单（价格形输出类）
      - momentum：归一动量序列（momentum 类）
    """
    ctx: dict = {}
    schema = _CONTEXT_SCHEMA.get(indicator_category, set())
    if not schema:
        return ctx

    close = df["close"]

    # 指标列缺失即无上下文可提取
    try:
        indicator_col = _get_indicator_column(df, gene)
    except (ValueError, KeyError):
        return ctx

    cond_type = gene.condition.get("type", "")

    # 方向提取：trend 类 × price 条件（close 与指标列的相对位置）
    if "direction" in schema and cond_type in ("price_above", "price_below"):
        if cond_type == "price_above":
            ctx["direction"] = pd.Series(
                np.where(close > indicator_col, 1.0, -1.0),
                index=df.index,
            )
        elif cond_type == "price_below":
            ctx["direction"] = pd.Series(
                np.where(close < indicator_col, -1.0, 1.0),
                index=df.index,
            )

    # 价位提取：价格形输出类（bandwidth/percent 等非价位列排除）
    if "price_levels" in schema:
        all_cols = _get_all_columns(df, gene.indicator, gene.params)
        price_cols = []
        for name, series in all_cols:
            if name in ("bandwidth", "percent"):
                continue
            price_cols.append(series)
        if price_cols:
            ctx["price_levels"] = price_cols

    # 动量提取：rolling min-max 逐序列归一到 [-1, +1]
    if "momentum" in schema:
        lookback = 100
        rolling_min = indicator_col.rolling(lookback, min_periods=1).min()
        rolling_max = indicator_col.rolling(lookback, min_periods=1).max()
        range_val = (rolling_max - rolling_min).replace(0, 1)
        normalized = (2 * (indicator_col - rolling_min) / range_val - 1).clip(-1, 1)
        ctx["momentum"] = normalized

    return ctx


def resample_values(
    series: pd.Series,
    target_index: pd.DatetimeIndex,
) -> pd.Series:
    """数值上下文投影（0.2.14 asof_completed 收口——第四投影形态）。

    direction/price_levels/momentum 上下文同受纪律约束：高周期 bar T
    的数值在 T+tf（收盘）后可见（现行开盘即见为前视窗口）。薄委托
    mtf_alignment.project_completed（四链同一权威实现）；不做 bool
    转换，首段 NaN 语义与既有形制一致。
    """
    from core.data.mtf_alignment import infer_tf_delta, project_completed

    if series.empty or len(target_index) == 0:
        return pd.Series(np.nan, index=target_index)

    return project_completed(
        series, target_index, infer_tf_delta(series.index))


def evaluate_layer_with_context(
    layer,
    df: pd.DataFrame,
    exec_df_index: pd.DatetimeIndex | None = None,
    dfs_by_timeframe: dict | None = None,
) -> LayerResult:
    """单层求值 + 上下文提取（层轴 df 求值；ctx 是门控增强面）。

    structure/zone 层额外提取方向/价位/动量并投影到执行轴（合成
    分数须与执行层逐 bar 对齐）；execution 层只产出基础信号集。
    role 缺省按 "execution" 解释（与 derive_role 兜底同语义）。

    dfs_by_timeframe 透传（fix-mtf-execution-consistency R1）：层内带
    基因级周期标注的基因经 evaluate_layer 合流单点需要装载集；ctx
    提取（extract_context）保持层轴不扩（R1-M1 负向回归锚锁定）。
    """
    # 基础信号求值（合流单点）
    sig = evaluate_layer(layer, df, dfs_by_timeframe)

    role = layer.role
    if role is None:
        role = "execution"

    direction = None
    price_levels = []
    momentum = None
    strength = None
    volatility = None

    # 逐基因提取上下文
    for gene in layer.signal_genes:
        reg = INDICATOR_REGISTRY.get(gene.indicator)
        category = reg.category if reg else "unknown"

        ctx = extract_context(df, gene, category)

        if "direction" in ctx:
            raw_dir = ctx["direction"]
            if exec_df_index is not None and role in ("structure", "zone"):
                direction = resample_values(raw_dir, exec_df_index)
            else:
                direction = raw_dir
        if "price_levels" in ctx:
            # structure/zone 层价位投影到执行轴（跨层对齐前提）
            if exec_df_index is not None and role in ("structure", "zone"):
                for pl in ctx["price_levels"]:
                    resampled = resample_values(pl, exec_df_index)
                    price_levels.append(resampled)
            else:
                price_levels.extend(ctx["price_levels"])
        if "momentum" in ctx:
            if exec_df_index is not None and role in ("structure", "zone"):
                momentum = resample_values(ctx["momentum"], exec_df_index)
            else:
                momentum = ctx["momentum"]

    return LayerResult(
        signal_set=sig,
        direction=direction,
        price_levels=price_levels,
        momentum=momentum,
        strength=strength,
        volatility=volatility,
    )


# ---------------------------------------------------------------------------
# L4：跨层合成 + 决策门控
# ---------------------------------------------------------------------------

@dataclass
class MTFSynthesis:
    """跨层合成三分数+强度乘数（决策门控输入；全部为逐 bar 序列）。"""
    direction_score: pd.Series      # +1（多头）/ -1（空头）/ 0（中性）
    confluence_score: pd.Series     # 0.0 ~ 1.0
    momentum_score: pd.Series       # 0.0 ~ 1.0
    strength_multiplier: pd.Series  # 0.0 ~ 1.0+


def synthesize_cross_layer(
    layer_results: List[Tuple[str, LayerResult]],
    exec_index: pd.DatetimeIndex,
    exec_close: pd.Series,
    exec_atr: pd.Series,
    proximity_mult: float,
    dna: StrategyDNA,
) -> MTFSynthesis:
    """跨层合成三分数（执行轴逐 bar；全部层求值结果的总装配）。

    步骤：
    1. 方向分——structure 层收集并冲突裁决；无 structure 层时按
       DNA 风险方向兜底（long→+1 / short→-1 / 其他→0）
    2. 共振分——structure/zone 层价格区间交集；单层回退邻近得分；
       无价位水平时回退动量方向一致性（C1 fix）
    3. 动量分——全层动量均值归一到 0~1（缺动量上下文取中性 0.5）
    """
    n = len(exec_index)

    # 缺省分数（各分支按证据覆写）
    direction_score = pd.Series(0.0, index=exec_index)
    confluence_score = pd.Series(0.0, index=exec_index)
    momentum_score = pd.Series(0.0, index=exec_index)
    strength_multiplier = pd.Series(1.0, index=exec_index)

    # 按类型收集上下文
    structure_directions = []
    all_price_zones = []  # [(tf, [zones_per_bar])]
    zone_momentums = []

    for tf, lr in layer_results:
        role = None
        # 由周期反查层角色
        for layer in (dna.layers or []):
            if layer.timeframe == tf:
                role = layer.role
                break
        if role is None:
            role = derive_role(tf)

        if lr.direction is not None:
            structure_directions.append((tf, lr.direction))

    # 1. 方向：structure 层裁决或 DNA 风险方向兜底
    if structure_directions:
        direction_score = resolve_direction_conflict(structure_directions)
    else:
        # 无 structure 层——继承 DNA 声明方向
        if dna.risk_genes.direction == "long":
            direction_score = pd.Series(1.0, index=exec_index)
        elif dna.risk_genes.direction == "short":
            direction_score = pd.Series(-1.0, index=exec_index)
        else:
            direction_score = pd.Series(0.0, index=exec_index)

    # 2. 共振：非执行层价位水平的逐 bar 计算
    non_exec_price_levels = []
    for tf, lr in layer_results:
        role = None
        for layer in (dna.layers or []):
            if layer.timeframe == tf:
                role = layer.role
                break
        if role is None:
            role = derive_role(tf)
        if role in ("structure", "zone") and lr.price_levels:
            non_exec_price_levels.append((tf, lr.price_levels))

    if len(non_exec_price_levels) >= 2:
        # 逐 bar 共振计算（numpy 数组预提取，避免逐 bar .iloc 开销）
        scores = np.zeros(n)
        exec_atr_vals = exec_atr.values
        exec_close_vals = exec_close.values
        # 价位数组预提取
        level_arrays_by_tf = []
        for tf, levels in non_exec_price_levels:
            level_arrays_by_tf.append([lv.values for lv in levels])

        for bar_idx in range(n):
            s_pct = compute_s_pct(exec_atr_vals[bar_idx], exec_close_vals[bar_idx], proximity_mult)
            layer_zones = []
            for level_vals_list in level_arrays_by_tf:
                zones = []
                for lv in level_vals_list:
                    if bar_idx < len(lv) and not np.isnan(lv[bar_idx]):
                        zone = build_price_zone(lv[bar_idx], s_pct)
                        zones.append(zone)
                if zones:
                    layer_zones.append(zones)
            scores[bar_idx] = compute_confluence_score(
                layer_zones, exec_close_vals[bar_idx],
                max_zone_width=exec_close_vals[bar_idx] * s_pct * 2 if s_pct > 0 else 1.0,
            )
        confluence_score = pd.Series(scores, index=exec_index)
    elif len(non_exec_price_levels) == 1:
        # 单非执行层：邻近得分回退
        tf, levels = non_exec_price_levels[0]
        s_pct_avg = (exec_atr / exec_close * proximity_mult).mean()
        if s_pct_avg > 0 and levels:
            confluence_score = compute_proximity_score(levels, exec_close, s_pct_avg)

    # 3. 动量：全层均值归一（0~1，中性 0.5）
    momentum_values = []
    non_exec_momenta = []  # 仅 structure/zone 层动量（共振回退用）
    for tf, lr in layer_results:
        role = None
        for layer in (dna.layers or []):
            if layer.timeframe == tf:
                role = layer.role
                break
        if role is None:
            role = derive_role(tf)
        if lr.momentum is not None:
            momentum_values.append(lr.momentum)
            if role in ("structure", "zone"):
                non_exec_momenta.append(lr.momentum)
    if momentum_values:
        # 合并动量归一到 0~1（除以全序列绝对最大值的线性归一）
        combined = pd.concat(momentum_values, axis=1).mean(axis=1)
        abs_max = combined.abs().max()
        if abs_max > 0:
            momentum_score = (combined / abs_max * 0.5 + 0.5).clip(0, 1)
        else:
            momentum_score = pd.Series(0.5, index=exec_index)
    else:
        momentum_score = pd.Series(0.5, index=exec_index)

    # 2b. 动量共振回退（C1 fix）：价格共振因 structure/zone 层缺
    # 价位水平而全 0 时，以动量方向一致性作共振分。
    if confluence_score.eq(0.0).all() and len(non_exec_momenta) >= 2:
        mom_matrix = np.column_stack([
            m.values[:n] for m in non_exec_momenta
        ])
        valid_mask = ~np.isnan(mom_matrix)
        valid_counts = valid_mask.sum(axis=1).astype(float)

        pos_counts = np.where(valid_mask, mom_matrix > 0, False).sum(axis=1).astype(float)
        neg_counts = np.where(valid_mask, mom_matrix < 0, False).sum(axis=1).astype(float)
        majority = np.maximum(pos_counts, neg_counts)

        agreement = np.where(valid_counts >= 2, majority / valid_counts, 0.0)

        # 量纲：全一致=1.0，勉强过半=0.3
        momentum_confs = np.where(
            (agreement > 0.5) & (valid_counts >= 2),
            0.3 + 0.7 * (agreement - 0.5) / 0.5,
            0.0,
        )
        confluence_score = pd.Series(momentum_confs, index=exec_index)
    elif confluence_score.eq(0.0).all() and len(non_exec_momenta) == 1:
        # 单非执行层仅有动量：动量强度作共振代理（向量化）
        mom_series = non_exec_momenta[0].values[:n]
        mom_abs_max = np.nanmax(np.abs(mom_series))
        if mom_abs_max > 0:
            normalized = np.abs(mom_series) / mom_abs_max
            direction = dna.risk_genes.direction
            if direction == "long":
                direction_mask = mom_series > 0
                scale = 0.5
            elif direction == "short":
                direction_mask = mom_series < 0
                scale = 0.5
            else:
                direction_mask = mom_series != 0
                scale = 0.3
            not_nan = ~np.isnan(mom_series)
            single_mom_confs = np.where(
                direction_mask & not_nan,
                normalized * scale,
                0.0,
            )
            confluence_score = pd.Series(single_mom_confs, index=exec_index)

    return MTFSynthesis(
        direction_score=direction_score,
        confluence_score=confluence_score,
        momentum_score=momentum_score,
        strength_multiplier=strength_multiplier,
    )


def _compute_confidence(synthesis: MTFSynthesis) -> pd.Series:
    """合成分数的混合置信度（SignalSet.confidence 载荷）。

    公式：confidence = confluence×0.5 + momentum×0.3 + |direction|×0.2，
    截断到 [0.1, 1.0]（下限 0.1——置信度不归零，诊断面保留可比性）。
    """
    import numpy as np

    confidence = (
        synthesis.confluence_score * 0.5
        + synthesis.momentum_score * 0.3
        + synthesis.direction_score.abs() * 0.2
    )
    return confidence.clip(0.1, 1.0)


def apply_decision_gate(
    exec_signal_set: SignalSet,
    synthesis: MTFSynthesis,
    dna: StrategyDNA,
) -> SignalSet:
    """MTF 决策门控：按合成分数过滤执行层信号。

    过滤面（口径）：
      - entries：时序信号 ∧ 方向匹配 ∧ 共振 ≥ threshold
      - adds：共振 ≥ threshold×0.8（放宽档）
      - exits/reduces：不过滤——离场与减仓属风险管理，不因门控阻塞

    mtf_mode 控制（None=向后兼容不门控，仅携带诊断分数）：
      "direction" / "confluence" / "direction+confluence" 分别启用
      对应过滤组合。诊断载荷（mtf_diagnostics+confidence）无论是否
      门控均产出。
    """
    mtf_mode = dna.mtf_mode
    threshold = dna.confluence_threshold

    entries = exec_signal_set.entries.copy()
    exits = exec_signal_set.exits.copy()
    adds = exec_signal_set.adds.copy()
    reduces = exec_signal_set.reduces.copy()

    if mtf_mode is None:
        # 不门控（向后兼容）
        return SignalSet(
            entries=entries,
            exits=exits,
            adds=adds,
            reduces=reduces,
            degraded_layers=exec_signal_set.degraded_layers,
            entry_direction=exec_signal_set.entry_direction,
            mtf_diagnostics={
                "direction_score": synthesis.direction_score,
                "confluence_score": synthesis.confluence_score,
                "momentum_score": synthesis.momentum_score,
                "strength_multiplier": synthesis.strength_multiplier,
                "mtf_mode": None,
            },
            confidence=_compute_confidence(synthesis),
        )

    # 方向门
    direction_pass = pd.Series(True, index=entries.index)
    if mtf_mode in ("direction", "direction+confluence"):
        if dna.risk_genes.direction == "long":
            direction_pass = synthesis.direction_score > 0
        elif dna.risk_genes.direction == "short":
            direction_pass = synthesis.direction_score < 0
        else:
            # mixed：方向非零即可（中性拒绝）
            direction_pass = synthesis.direction_score != 0

    # 共振门
    confluence_pass = pd.Series(True, index=entries.index)
    if mtf_mode in ("confluence", "direction+confluence"):
        confluence_pass = synthesis.confluence_score >= threshold

    # 双门控入场
    entries = entries & direction_pass & confluence_pass

    # 加仓信号走放宽共振（threshold 的 80%）
    add_confluence_pass = pd.Series(True, index=adds.index)
    if mtf_mode in ("confluence", "direction+confluence"):
        add_confluence_pass = synthesis.confluence_score >= threshold * 0.8
    adds = adds & add_confluence_pass

    # 离场与减仓不过滤

    return SignalSet(
        entries=entries,
        exits=exits,
        adds=adds,
        reduces=reduces,
        degraded_layers=exec_signal_set.degraded_layers,
        entry_direction=exec_signal_set.entry_direction,
        mtf_diagnostics={
            "direction_score": synthesis.direction_score,
            "confluence_score": synthesis.confluence_score,
            "momentum_score": synthesis.momentum_score,
            "strength_multiplier": synthesis.strength_multiplier,
            "mtf_mode": mtf_mode,
        },
        confidence=_compute_confidence(synthesis),
    )


def run_mtf_engine(
    dna: StrategyDNA,
    dfs_by_timeframe: dict,
    enhanced_df: pd.DataFrame,
) -> SignalSet:
    """MTF 引擎主入口（executor MTF 分支委托点——见模块 docstring）。

    管线：逐层求值+上下文提取（层轴数据缺失计 degraded 降级跳过）
    → 执行层信号合并 → 跨层合成 → 决策门控。entry_direction 由
    合成方向分承载（engine 仅 mixed 消费）。执行周期帧缺装载时以
    enhanced_df 兜底（执行轴数据恒在场的装载前提）。
    """
    exec_tf = dna.execution_genes.timeframe
    # 执行周期帧为合成基准轴
    exec_df = dfs_by_timeframe.get(exec_tf, enhanced_df)

    # 逐层求值+上下文提取
    layer_results: List[Tuple[str, LayerResult]] = []
    degraded_count = 0

    for layer in (dna.layers or []):
        layer_df = dfs_by_timeframe.get(layer.timeframe)
        if layer_df is None:
            degraded_count += 1
            continue

        role = layer.role
        if role is None:
            role = derive_role(layer.timeframe)

        # 非执行层传执行轴索引供投影（逐 bar 对齐前提）
        exec_index = exec_df.index if layer.timeframe != exec_tf else None
        lr = evaluate_layer_with_context(
            layer, layer_df, exec_df.index, dfs_by_timeframe)
        layer_results.append((layer.timeframe, lr))

    # 执行层信号合并
    exec_signal_set = _build_exec_signal_set(
        layer_results, dna, exec_df,
    )

    # 跨层合成
    exec_close = exec_df["close"]
    exec_atr = _get_exec_atr(exec_df)

    synthesis = synthesize_cross_layer(
        layer_results, exec_df.index, exec_close, exec_atr,
        dna.proximity_mult, dna,
    )

    # 决策门控
    result = apply_decision_gate(exec_signal_set, synthesis, dna)
    result.degraded_layers = degraded_count

    # 入场方向=合成方向分
    result.entry_direction = synthesis.direction_score

    return result


def _build_exec_signal_set(
    layer_results: List[Tuple[str, LayerResult]],
    dna: StrategyDNA,
    exec_df: pd.DataFrame,
) -> SignalSet:
    """全层结果合并为执行轴信号集（先投影后组合）。

    非执行层信号经 resample_signals 投影到执行轴（状态信号
    ffill 语义），执行层直接 reindex 对齐。组合口径：entries 跨层
    AND（严格共振入场）、exits/adds/reduces 跨层 OR（任一层可触发）
    ；入场与离场同 bar 并存时入场让位（防自相矛盾信号）。
    """
    all_entries = []
    all_exits = []
    all_adds = []
    all_reduces = []

    for tf, lr in layer_results:
        role = None
        for layer in (dna.layers or []):
            if layer.timeframe == tf:
                role = layer.role
                break
        if role is None:
            role = derive_role(tf)

        # 各层信号投影/对齐到执行轴
        sig = lr.signal_set
        if tf != dna.execution_genes.timeframe:
            entries = resample_signals(sig.entries, exec_df.index)
            exits = resample_signals(sig.exits, exec_df.index)
            adds = resample_signals(sig.adds, exec_df.index)
            reduces = resample_signals(sig.reduces, exec_df.index)
        else:
            entries = sig.entries.reindex(exec_df.index, fill_value=False)
            exits = sig.exits.reindex(exec_df.index, fill_value=False)
            adds = sig.adds.reindex(exec_df.index, fill_value=False)
            reduces = sig.reduces.reindex(exec_df.index, fill_value=False)

        all_entries.append(entries)
        all_exits.append(exits)
        all_adds.append(adds)
        all_reduces.append(reduces)

    if not all_entries:
        return SignalSet(
            entries=pd.Series(False, index=exec_df.index),
            exits=pd.Series(False, index=exec_df.index),
            adds=pd.Series(False, index=exec_df.index),
            reduces=pd.Series(False, index=exec_df.index),
        )

    # 组合：入场跨层 AND，离场/加仓/减仓跨层 OR
    combined_entries = combine_signals(all_entries, "AND")
    combined_exits = combine_signals(all_exits, "OR")
    combined_adds = combine_signals(all_adds, "OR")
    combined_reduces = combine_signals(all_reduces, "OR")

    # 入场与离场同 bar 并存时入场让位
    both = combined_entries & combined_exits
    combined_entries = combined_entries & ~both

    return SignalSet(
        entries=combined_entries,
        exits=combined_exits,
        adds=combined_adds,
        reduces=combined_reduces,
    )


def _get_exec_atr(exec_df: pd.DataFrame) -> pd.Series:
    """执行轴 ATR 序列（s% 邻近口径的波动输入）。

    三级回退：现成 atr_* 列 → high/low/close 现算 TR 的 14 均线
    （缺价兜底 close×2%）→ close×2%（近似波动，保管线可跑）。
    """
    # 现成 ATR 列优先
    for col in exec_df.columns:
        if col.startswith("atr_"):
            return exec_df[col]
    # 现算简单 ATR 形波动
    if all(c in exec_df.columns for c in ("high", "low", "close")):
        tr = pd.concat([
            exec_df["high"] - exec_df["low"],
            (exec_df["high"] - exec_df["close"].shift(1)).abs(),
            (exec_df["low"] - exec_df["close"].shift(1)).abs(),
        ], axis=1).max(axis=1)
        return tr.rolling(14, min_periods=1).mean().fillna(exec_df["close"] * 0.02)
    # 终级兜底
    return exec_df["close"] * 0.02
