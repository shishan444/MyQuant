"""场景验证编排器：装载数据 → 分派检测器 → 计算前向统计。

服务对象：api/routes/scene.py 的场景验证端点（run_scene_
verification 直调）与 tests/evolution 场景特征网；DETECTORS
注册表是 scene/ 六检测器的统一装配点，scene/__init__.py 的
包出口 re-export 本模块入口。职责边界：只做编排与分派——
检测逻辑归各 detector，前向统计归 forward_stats，指标计算归
core.features.indicators，数据装载归 core.data.mtf_loader。
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd

from core.data.storage import load_parquet
from core.features.indicators import compute_all_indicators

from .base import SceneDetector, SceneVerificationResult, TriggerPoint
from .forward_stats import aggregate_by_horizon, compute_forward_stats
from .top_pattern import TopPatternDetector
from .volume_spike import VolumeSpikeDetector
from .mean_reversion import MeanReversionDetector
from .volume_breakout import VolumeBreakoutDetector
from .support_resistance import SupportResistanceDetector
from .cross_timeframe import CrossTimeframeDetector

logger = logging.getLogger("scene_engine")

# ---------------------------------------------------------------------------
# 检测器注册表
# ---------------------------------------------------------------------------

DETECTORS: Dict[str, SceneDetector] = {
    "top_pattern": TopPatternDetector(),
    "volume_spike": VolumeSpikeDetector(),
    "mean_reversion": MeanReversionDetector(),
    "volume_breakout": VolumeBreakoutDetector(),
    "support_resistance": SupportResistanceDetector(),
    "cross_timeframe": CrossTimeframeDetector(),
}

SCENE_TYPES = list(DETECTORS.keys())

# 场景展示元数据（前端用作标签/图标）
SCENE_META: Dict[str, Dict[str, str]] = {
    # -- top_pattern 的子模式（parent 字段标示代理归属）--
    "double_top":         {"label": "双顶",   "description": "M形态，两峰相近",
                           "parent": "top_pattern", "group": "顶部形态"},
    "head_shoulders_top": {"label": "头肩顶", "description": "左肩-头-右肩反转",
                           "parent": "top_pattern", "group": "顶部形态"},
    "triple_top":         {"label": "三重顶", "description": "三峰相近的顶部反转",
                           "parent": "top_pattern", "group": "顶部形态"},
    # -- 独立场景类型 --
    "volume_spike":       {"label": "成交量异动", "description": "成交量突破N倍均量",
                           "group": "独立场景"},
    "mean_reversion":     {"label": "均值回归",   "description": "价格偏离均线后回归",
                           "group": "独立场景"},
    "volume_breakout":    {"label": "放量突破",   "description": "放量+价格突破关键位",
                           "group": "独立场景"},
    "support_resistance": {"label": "支撑阻力",   "description": "价格触及支撑/阻力位",
                           "group": "独立场景"},
    "cross_timeframe":    {"label": "跨周期信号", "description": "高周期信号+低周期入场",
                           "group": "独立场景"},
}

# 子模式 scene_type → 实际检测器键的映射
SUB_PATTERN_PARENT: Dict[str, str] = {
    k: v["parent"] for k, v in SCENE_META.items() if "parent" in v
}


# ---------------------------------------------------------------------------
# 主入口
# ---------------------------------------------------------------------------

def run_scene_verification(
    symbol: str,
    timeframe: str,
    scene_type: str,
    params: Optional[Dict[str, Any]] = None,
    horizons: Optional[List[int]] = None,
    data_start: Optional[str] = None,
    data_end: Optional[str] = None,
    data_dir: Optional[str] = None,
) -> SceneVerificationResult:
    """执行场景验证：检测形态出现点，计算前向统计。

    Args:
        symbol: 交易对，如 "BTCUSDT"。
        timeframe: K 线周期，如 "4h"。
        scene_type: SCENE_TYPES 之一，或 top_pattern 的子模式名。
        params: 检测器参数（与各检测器默认参数合并）。
        horizons: 前向统计的 bar 数列表。默认 [6, 12, 24, 48]。
        data_start: 可选的起始日期过滤。
        data_end: 可选的结束日期过滤。
        data_dir: 可选的行情数据目录路径。

    Returns:
        SceneVerificationResult（触发点明细 + 分 horizon 聚合统计）。
    """
    warnings: List[str] = []

    # 子模式归并到父检测器；非子模式则直接使用
    detector_key = SUB_PATTERN_PARENT.get(scene_type, scene_type)
    sub_filter = scene_type if scene_type in SUB_PATTERN_PARENT else None

    # 校验 scene_type
    if detector_key not in DETECTORS and scene_type not in SCENE_META:
        return SceneVerificationResult(
            scene_type=scene_type, total_triggers=0,
            warnings=[f"Unknown scene_type '{scene_type}'. Valid: {list(SCENE_META.keys())}"],
        )

    if horizons is None:
        horizons = [6, 12, 24, 48]
    if not horizons:
        # 空列表无统计意义（forward_stats 的 max(horizons) 直接 ValueError 穿 500）
        from core.analysis.hypothesis_service import ResearchError

        raise ResearchError(
            "RESEARCH_PARAM_INVALID",
            f"horizons 不能为空: {horizons!r}",
            detail={"param": "horizons", "value": horizons,
                    "legal_range": "非空正整数列表"},
        )
    if params is None:
        params = {}

    # 解析数据目录
    if data_dir is None:
        data_dir = str(Path(__file__).resolve().parent.parent.parent.parent / "data" / "market")

    # 数据装载（B4.1 共享路径发现：safe_symbol 归一 + 别名兜底；
    # 未知 timeframe 结构化拒绝而非静默空结果）
    from core.analysis.hypothesis_service import ResearchError
    from core.data.mtf_loader import safe_symbol, find_parquet

    try:
        parquet_path = find_parquet(Path(data_dir), safe_symbol(symbol), timeframe)
    except ValueError as exc:
        raise ResearchError(
            "RESEARCH_PARAM_INVALID",
            f"timeframe 未知: {timeframe!r}",
            detail={"param": "timeframe", "value": timeframe,
                    "legal_range": "TF_SECONDS 已知周期"},
        ) from exc

    if parquet_path is None:
        return SceneVerificationResult(
            scene_type=scene_type, total_triggers=0,
            warnings=[f"No data found for {symbol}_{timeframe}"],
        )

    df = load_parquet(parquet_path)
    if df is None or len(df) == 0:
        return SceneVerificationResult(
            scene_type=scene_type, total_triggers=0,
            warnings=[f"Empty dataset for {symbol}_{timeframe}"],
        )

    # 按日期区间过滤
    if data_start:
        df = df[df.index >= data_start]
    if data_end:
        df = df[df.index <= data_end]

    if len(df) == 0:
        return SceneVerificationResult(
            scene_type=scene_type, total_triggers=0,
            warnings=[f"No data in range {data_start} ~ {data_end}"],
        )

    # 计算指标
    enhanced_df = compute_all_indicators(df)

    # MTF：cross_timeframe 时装载并合并高周期数据
    if detector_key == "cross_timeframe":
        higher_tf = params.get("higher_tf", "1d")
        try:
            from core.validation.mtf import load_mtf_data, merge_to_base, get_timeframe_minutes
            mtf_data = load_mtf_data(
                symbol, [higher_tf],
                data_start or "", data_end or "",
                data_dir,
            )
            if mtf_data and higher_tf in mtf_data:
                mtf_with_indicators = {higher_tf: compute_all_indicators(mtf_data[higher_tf])}
                base_minutes = get_timeframe_minutes(timeframe)
                tf_minutes_map = {higher_tf: get_timeframe_minutes(higher_tf)}
                enhanced_df = merge_to_base(
                    enhanced_df, mtf_with_indicators,
                    base_minutes, tf_minutes_map,
                )
        except Exception as exc:
            warnings.append(f"MTF loading failed: {exc}")
            return SceneVerificationResult(
                scene_type=scene_type, total_triggers=0,
                warnings=warnings,
            )

    # 运行检测器
    detector = DETECTORS[detector_key]
    merged_params = {**detector.default_params, **params}
    trigger_points = detector.detect(enhanced_df, merged_params)

    # 按子模式过滤（指定子模式时）
    if sub_filter:
        trigger_points = [tp for tp in trigger_points if tp.pattern_subtype == sub_filter]

    if not trigger_points:
        return SceneVerificationResult(
            scene_type=scene_type, total_triggers=0,
            warnings=["No scene occurrences detected in the data."],
        )

    # 逐触发点计算前向统计
    all_horizon_stats: Dict[int, List] = {h: [] for h in horizons}
    trigger_details: List[Dict[str, Any]] = []

    for tp in trigger_points:
        fwd = compute_forward_stats(enhanced_df, tp.bar_index, tp.trigger_price, horizons)

        detail: Dict[str, Any] = {
            "id": tp.id,
            "timestamp": tp.timestamp,
            "trigger_price": tp.trigger_price,
            "indicator_snapshot": tp.indicator_snapshot,
            "pattern_subtype": tp.pattern_subtype,
            "pattern_metadata": tp.pattern_metadata,
            "forward_stats": {
                str(h): {
                    "close_pct": s.close_pct,
                    "max_gain_pct": s.max_gain_pct,
                    "max_loss_pct": s.max_loss_pct,
                    "bars_to_peak": s.bars_to_peak,
                    "bars_to_trough": s.bars_to_trough,
                    "is_partial": s.is_partial,
                }
                for h, s in fwd.items()
            },
        }
        trigger_details.append(detail)

        for h, stats in fwd.items():
            if h in all_horizon_stats:
                all_horizon_stats[h].append(stats)

    # 聚合
    aggregated = aggregate_by_horizon(all_horizon_stats)

    return SceneVerificationResult(
        scene_type=scene_type,
        total_triggers=len(trigger_points),
        statistics_by_horizon=aggregated,
        trigger_details=trigger_details,
        warnings=warnings,
    )
