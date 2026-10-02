"""图表形态匹配引擎：极值优先搜索 + 事后校验。

核心算法移植自 BennyThadikaran/stock-pattern（368 stars）。与旧版
关键差异：搜索从最显著的峰（最高价）开始向外扩展，而非按序扫描
pivot——保证「最佳」峰候选总是最先被尝试。

服务对象：top_pattern.py 形态场景检测器（经 scene_engine 的
DETECTORS 注册表分派链）；tests/evolution 场景特征网对
match_patterns 及内部 helper 的直调锚。职责边界：pivot 序列 →
形态实例（PatternMatch），pivot 识别归 pivot.py，场景语义包装
归调用方。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd

from .pivot import PivotPoint


@dataclass
class PatternMatch:
    """一例检出的图表形态（类型 + 完成 bar + 关键点）。"""

    pattern_type: str                               # "double_top" | "head_shoulders_top" | "triple_top"
    completion_bar: int                             # 形态完成的 bar 下标
    key_points: List[Dict[str, Any]] = field(default_factory=list)


# ---------------------------------------------------------------------------
# 内部辅助函数
# ---------------------------------------------------------------------------

def _build_key_points(*pairs: tuple) -> List[Dict[str, Any]]:
    """从 (label, PivotPoint) 对构建 key_points 列表。"""
    return [
        {"label": label, "index": p.index, "price": p.price}
        for label, p in pairs
    ]


def _compute_avg_bar_length(bar_lengths: np.ndarray, start_idx: int, end_idx: int) -> float:
    """取给定下标区间内 bar 长度（high - low）的中位数。

    Args:
        bar_lengths: 预计算的 (high - low) numpy 数组。

    区间裁剪到数组边界后为空（start >= end）时返回 0.0。
    """
    start = max(0, start_idx)
    end = min(len(bar_lengths), end_idx + 1)
    if start >= end:
        return 0.0
    return float(np.median(bar_lengths[start:end]))


def _validate_pivot_anchor(point: PivotPoint, df: pd.DataFrame) -> bool:
    """校验 pivot 锚定：peak 价格须等于所在 bar 的 High，trough 须等于 Low。

    拒收锚错 OHLC 分量的 pivot——例如 peak 的价格取自 Low 而非
    High，说明该 pivot 定位错误。下标越界同样拒收。
    """
    if point.index < 0 or point.index >= len(df):
        return False
    row = df.iloc[point.index]
    if point.pivot_type == "peak":
        return abs(point.price - row["high"]) < 0.01
    return abs(point.price - row["low"]) < 0.01


def _check_level_intact(
    level_price: float,
    from_bar: int,
    df: pd.DataFrame,
    direction: str,
    avg_bar_length: float,
    check_bars: int = 0,
) -> bool:
    """检查关键价位自 from_bar 起是否未被有效击穿。

    Args:
        level_price: 关键价位。
        from_bar: 起检 bar 下标。
        df: 含 high/low 列的 DataFrame。
        direction: "below" 指价位不得向下破位，
                   "above" 指价位不得向上破位。
        avg_bar_length: 破位容差（须超出 1x avgBarLength 才计入）。
        check_bars: 检查的 bar 数，0 表示检查全部后续 bar。

    Returns:
        价位仍然完好（未被有效击穿）返回 True。
    """
    if from_bar + 1 >= len(df):
        return True
    end = len(df) if check_bars == 0 else min(from_bar + 1 + check_bars, len(df))
    subsequent = df.iloc[from_bar + 1:end]
    if direction == "below":
        lowest = subsequent["low"].min()
        return lowest > level_price - avg_bar_length
    # direction == "above"
    highest = subsequent["high"].max()
    return highest < level_price + avg_bar_length


def _get_atr(atr_series: pd.Series, bar_index: int) -> Optional[float]:
    """安全取指定 bar 下标处的 ATR 值（越界或 NaN 返回 None）。"""
    if atr_series is not None and 0 <= bar_index < len(atr_series):
        val = atr_series.iloc[bar_index]
        if not np.isnan(val):
            return float(val)
    return None


# ---------------------------------------------------------------------------
# 形态探测器：极值优先搜索策略
# ---------------------------------------------------------------------------

# 形态首尾点之间的最大 bar 距离。
# 防止把相距过远的峰拼成形态（噪声），同时限制搜索空间。
_MAX_PATTERN_SPAN = 200


def _find_double_tops(
    pivots: List[PivotPoint],
    df: pd.DataFrame,
    bar_lengths: np.ndarray,
    atr_series: Optional[pd.Series],
) -> List[PatternMatch]:
    """极值优先搜索全部双顶形态。

    算法（源自 stock-pattern）：
    1. 峰按价格降序排列（最显著者优先）。
    2. 对每个峰 A，取 A 之后（span 限内）最高的峰 C。
    3. 取 A 与 C 之间最低的谷 B。
    4. 施加几何约束 + 事后校验。
    5. 失败则跳到下一个 A。
    """
    peaks = [p for p in pivots if p.pivot_type == "peak"]
    if len(peaks) < 2:
        return []

    peaks_by_price = sorted(peaks, key=lambda p: p.price, reverse=True)

    # 峰按下标预排序，加速区间查询
    peaks_by_idx = sorted(peaks, key=lambda p: p.index)

    results: List[PatternMatch] = []
    used_indices: set = set()

    for a in peaks_by_price:
        if a.index in used_indices:
            continue

        # 找 A 之后 _MAX_PATTERN_SPAN bar 内的峰，按价格降序
        c_candidates = sorted(
            [p for p in peaks_by_idx
             if a.index < p.index <= a.index + _MAX_PATTERN_SPAN
             and p.index not in used_indices],
            key=lambda p: p.price,
            reverse=True,
        )
        if not c_candidates:
            continue

        found = False
        for c in c_candidates:
            # 取 A 与 C 之间最低的谷
            b_candidates = [
                p for p in pivots
                if p.pivot_type == "trough"
                and a.index < p.index < c.index
            ]
            if not b_candidates:
                continue
            b = min(b_candidates, key=lambda p: p.price)

            # --- 几何约束 ---
            abl = _compute_avg_bar_length(bar_lengths, a.index, c.index)
            if abl <= 0:
                continue

            # 两峰接近：价差 <= 0.5 * avgBarLength
            if abs(a.price - c.price) > abl * 0.5:
                continue

            # 谷须低于两峰
            if b.price >= min(a.price, c.price):
                continue

            # ATR 高度约束（8x 上限，适配 crypto 波动率）
            atr_val = _get_atr(atr_series, c.index)
            height = c.price - b.price
            if atr_val and atr_val > 0:
                if height > atr_val * 8 or height < atr_val * 0.5:
                    continue

            # 量能确认（放宽口径：仅当第 2 峰量能超第 1 峰 1.5x 才拒收
            # —— 避免 crypto 量能噪声误杀）
            if a.volume > 0 and c.volume > 0:
                if c.volume >= a.volume * 1.5:
                    continue

            # --- 事后校验 ---
            if not _validate_pivot_anchor(a, df):
                continue
            if not _validate_pivot_anchor(b, df):
                continue
            if not _validate_pivot_anchor(c, df):
                continue

            # C 位完好：只查其后 10 根 bar（短期确认）
            if not _check_level_intact(c.price, c.index, df, "above", abl, check_bars=10):
                continue

            # 形态确认
            used_indices.update([a.index, b.index, c.index])
            results.append(PatternMatch(
                pattern_type="double_top",
                completion_bar=c.index,
                key_points=_build_key_points(
                    ("peak_1", a), ("trough_1", b), ("peak_2", c),
                ),
            ))
            found = True
            break

    results.sort(key=lambda m: m.completion_bar)
    return results


def _find_head_shoulders_tops(
    pivots: List[PivotPoint],
    df: pd.DataFrame,
    bar_lengths: np.ndarray,
    atr_series: Optional[pd.Series],
) -> List[PatternMatch]:
    """极值优先搜索全部头肩顶形态。

    算法（源自 stock-pattern）：
    1. 峰按价格降序排列（头候选）。
    2. 对每个头 C，向前搜左肩 A（C 前最高峰）。
    3. 向后搜右肩 E（C 后最高峰）。
    4. 取腋点 B（A-C 间最低谷）与 D（C-E 间最低谷）。
    5. 施加几何约束 + 事后校验。
    """
    peaks = [p for p in pivots if p.pivot_type == "peak"]
    if len(peaks) < 3:
        return []

    peaks_by_price = sorted(peaks, key=lambda p: p.price, reverse=True)

    results: List[PatternMatch] = []
    used_indices: set = set()

    for head in peaks_by_price:
        if head.index in used_indices:
            continue

        # 左肩：头之前（span 限内）的最高峰
        left_candidates = [
            p for p in peaks
            if head.index - _MAX_PATTERN_SPAN <= p.index < head.index
            and p.index not in used_indices
        ]
        if not left_candidates:
            continue
        left_shoulder = max(left_candidates, key=lambda p: p.price)

        # 右肩：头之后（span 限内）的最高峰
        right_candidates = [
            p for p in peaks
            if head.index < p.index <= head.index + _MAX_PATTERN_SPAN
            and p.index not in used_indices
        ]
        if not right_candidates:
            continue
        right_shoulder = max(right_candidates, key=lambda p: p.price)

        # 左腋点：左肩与头之间的最低谷
        armpit_left_cands = [
            p for p in pivots
            if p.pivot_type == "trough"
            and left_shoulder.index < p.index < head.index
        ]
        if not armpit_left_cands:
            continue
        armpit_left = min(armpit_left_cands, key=lambda p: p.price)

        # 右腋点：头与右肩之间的最低谷
        armpit_right_cands = [
            p for p in pivots
            if p.pivot_type == "trough"
            and head.index < p.index < right_shoulder.index
        ]
        if not armpit_right_cands:
            continue
        armpit_right = min(armpit_right_cands, key=lambda p: p.price)

        # --- 几何约束 ---
        abl = _compute_avg_bar_length(bar_lengths, armpit_left.index, armpit_right.index)
        if abl <= 0:
            continue

        # 头须为最高点
        if head.price <= left_shoulder.price or head.price <= right_shoulder.price:
            continue

        # 两腋点均须低于两肩
        if max(armpit_left.price, armpit_right.price) >= min(left_shoulder.price, right_shoulder.price):
            continue

        # 两肩大致等高
        if abs(left_shoulder.price - right_shoulder.price) > abl * 0.5:
            continue

        # 颈线（两腋点）大致水平
        if abs(armpit_left.price - armpit_right.price) >= abl:
            continue

        # 头肩结构须显著：头高出肩线 >= 0.6 * avgBarLength
        avg_shoulder = (left_shoulder.price + right_shoulder.price) / 2.0
        if head.price - avg_shoulder < abl * 0.6:
            continue

        # ATR 高度约束（8x 上限，适配 crypto 波动率）
        atr_val = _get_atr(atr_series, right_shoulder.index)
        if atr_val and atr_val > 0:
            avg_neckline = (armpit_left.price + armpit_right.price) / 2.0
            h = head.price - avg_neckline
            if h > atr_val * 8 or h < atr_val * 0.5:
                continue

        # --- 事后校验 ---
        all_points = [left_shoulder, armpit_left, head, armpit_right, right_shoulder]
        if not all(_validate_pivot_anchor(p, df) for p in all_points):
            continue

        # 颈线完好：只查其后 10 根 bar（短期确认）
        neckline_price = min(armpit_left.price, armpit_right.price)
        if not _check_level_intact(neckline_price, right_shoulder.index, df, "below", abl, check_bars=10):
            continue

        used_indices.update([p.index for p in all_points])
        results.append(PatternMatch(
            pattern_type="head_shoulders_top",
            completion_bar=right_shoulder.index,
            key_points=_build_key_points(
                ("left_shoulder", left_shoulder), ("neckline_1", armpit_left),
                ("head", head), ("neckline_2", armpit_right),
                ("right_shoulder", right_shoulder),
            ),
        ))

    results.sort(key=lambda m: m.completion_bar)
    return results


def _find_triple_tops(
    pivots: List[PivotPoint],
    df: pd.DataFrame,
    bar_lengths: np.ndarray,
    atr_series: Optional[pd.Series],
) -> List[PatternMatch]:
    """极值优先搜索全部三重顶形态。

    算法：
    1. 峰按价格降序排列。
    2. 对每个峰 P1，取时间上在其后且价格接近的 P2、P3 两峰。
    3. 取每对峰之间的谷。
    4. 施加几何约束 + 事后校验。
    """
    peaks = [p for p in pivots if p.pivot_type == "peak"]
    if len(peaks) < 3:
        return []

    peaks_by_price = sorted(peaks, key=lambda p: p.price, reverse=True)

    results: List[PatternMatch] = []
    used_indices: set = set()

    for p1 in peaks_by_price:
        if p1.index in used_indices:
            continue

        # 找 P1 之后（span 限内）的全部峰，按价格降序
        later_peaks = sorted(
            [p for p in peaks
             if p1.index < p.index <= p1.index + _MAX_PATTERN_SPAN
             and p.index not in used_indices],
            key=lambda p: p.price,
            reverse=True,
        )
        if len(later_peaks) < 2:
            continue

        # 从后续峰中取 P2、P3 组合逐一尝试
        found = False
        for p2 in later_peaks:
            if found:
                break
            for p3 in later_peaks:
                if p3.index <= p2.index:
                    continue

                # P1 与 P2 之间的谷
                t1_cands = [
                    p for p in pivots
                    if p.pivot_type == "trough"
                    and p1.index < p.index < p2.index
                ]
                if not t1_cands:
                    continue
                t1 = min(t1_cands, key=lambda p: p.price)

                # P2 与 P3 之间的谷
                t2_cands = [
                    p for p in pivots
                    if p.pivot_type == "trough"
                    and p2.index < p.index < p3.index
                ]
                if not t2_cands:
                    continue
                t2 = min(t2_cands, key=lambda p: p.price)

                # --- 几何约束 ---
                abl = _compute_avg_bar_length(bar_lengths, p1.index, p3.index)
                if abl <= 0:
                    continue

                # 三峰价格接近
                threshold = abl * 0.5
                if abs(p1.price - p2.price) > threshold:
                    continue
                if abs(p2.price - p3.price) > threshold:
                    continue
                if abs(p1.price - p3.price) > threshold:
                    continue

                # 谷须低于峰
                avg_peak = (p1.price + p2.price + p3.price) / 3.0
                if t1.price >= avg_peak or t2.price >= avg_peak:
                    continue

                # ATR 高度约束（8x 上限，适配 crypto 波动率）
                atr_val = _get_atr(atr_series, p3.index)
                avg_trough = (t1.price + t2.price) / 2.0
                height = avg_peak - avg_trough
                if atr_val and atr_val > 0:
                    if height > atr_val * 8 or height < atr_val * 0.5:
                        continue

                # 量能：放宽的递减口径（p3 不得超 p1 的 1.5x）
                if p1.volume > 0 and p3.volume > 0:
                    if p3.volume >= p1.volume * 1.5:
                        continue

                # --- 事后校验 ---
                all_pts = [p1, t1, p2, t2, p3]
                if not all(_validate_pivot_anchor(pt, df) for pt in all_pts):
                    continue

                # P3 位完好：只查其后 10 根 bar
                if not _check_level_intact(p3.price, p3.index, df, "above", abl, check_bars=10):
                    continue

                used_indices.update([pt.index for pt in all_pts])
                results.append(PatternMatch(
                    pattern_type="triple_top",
                    completion_bar=p3.index,
                    key_points=_build_key_points(
                        ("peak_1", p1), ("trough_1", t1),
                        ("peak_2", p2), ("trough_2", t2),
                        ("peak_3", p3),
                    ),
                ))
                found = True
                break

    results.sort(key=lambda m: m.completion_bar)
    return results


# ---------------------------------------------------------------------------
# 公共 API
# ---------------------------------------------------------------------------

def match_patterns(
    pivots: List[PivotPoint],
    df: Optional[pd.DataFrame] = None,
    atr_series: Optional[pd.Series] = None,
) -> List[PatternMatch]:
    """在 pivot 序列上以极值优先搜索图表形态。

    各形态类型由独立探测器分别搜索；结果按 key point 下标去重。

    Args:
        pivots: 按时间序排列的 pivot 点。
        df: 含 high/low 列的 DataFrame（avgBarLength 与事后校验用）。
        atr_series: ATR 值序列（高度约束用）。

    Returns:
        PatternMatch 实例列表，按 completion_bar 排序；pivot 少于
        3 个或 df 为 None 时返回空列表。
    """
    if len(pivots) < 3:
        return []

    if df is None:
        return []

    # bar 长度预计算为 numpy 数组，加速中位数计算
    bar_lengths = (df["high"].values.astype(np.float64) - df["low"].values.astype(np.float64))

    all_results: List[PatternMatch] = []

    # 探测顺序：头肩顶最优先（最特异），其次三重顶，最后双顶
    all_results.extend(_find_head_shoulders_tops(pivots, df, bar_lengths, atr_series))
    all_results.extend(_find_triple_tops(pivots, df, bar_lengths, atr_series))
    all_results.extend(_find_double_tops(pivots, df, bar_lengths, atr_series))

    # 按 key point 的 bar 下标去重（同 bar 组合 = 同一形态）
    seen: set = set()
    deduped: List[PatternMatch] = []
    for m in sorted(all_results, key=lambda m: m.completion_bar):
        bars = tuple(kp["index"] for kp in m.key_points)
        if bars not in seen:
            seen.add(bars)
            deduped.append(m)

    return deduped
