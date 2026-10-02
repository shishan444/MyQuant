"""场景验证检测器的基类与数据结构。

服务对象：scene/ 域全部模块——scene_engine 与各检测器经
import 消费 SceneDetector/TriggerPoint/_param_number，
forward_stats 消费两个统计 dataclass；scene/__init__.py 包
出口 re-export。tests/evolution 场景特征网对四个 dataclass
直调锚。职责边界：纯结构定义与参数取值守卫，不含检测算法
与前向统计。
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

import pandas as pd


@dataclass
class TriggerPoint:
    """单次检出的场景出现点。"""
    id: int
    timestamp: str          # 触发 bar 的 ISO 时间串
    trigger_price: float    # 触发 bar 的 close 价
    bar_index: int          # 在 DataFrame 中的整数位置
    indicator_snapshot: Dict[str, float] = field(default_factory=dict)
    pattern_subtype: str = ""                               # 如 "double_top"
    pattern_metadata: Dict[str, Any] = field(default_factory=dict)  # key_points 等


@dataclass
class HorizonStats:
    """单触发点在单个 horizon 的前向指标。"""
    horizon: int            # 前向 bar 数
    close_pct: float        # (close_at_N - trigger) / trigger * 100
    max_gain_pct: float     # 相对触发价的最高 high 百分比
    max_loss_pct: float     # 相对触发价的最低 low 百分比
    bars_to_peak: int       # 最高 high 出现处的 bar 偏移
    bars_to_trough: int     # 最低 low 出现处的 bar 偏移
    is_partial: bool = False  # 可用 bar 不足 N 根


@dataclass
class AggregateStats:
    """单 horizon 全部触发点的聚合统计。"""
    horizon: int
    total_triggers: int
    win_rate: float             # close_pct > 0 的占比（%）
    avg_return_pct: float
    median_return_pct: float
    avg_max_gain_pct: float
    avg_max_loss_pct: float
    avg_bars_to_peak: float
    distribution: List[Dict[str, Any]] = field(default_factory=list)
    percentiles: Dict[str, float] = field(default_factory=dict)
    # Wilson 95% 置信区间（百分比域，与 win_rate 同口径；小样本胜率的
    # 可信度证据，消费方展示时随 win_rate 一并透出）
    win_rate_ci: List[float] = field(default_factory=lambda: [0.0, 0.0])


@dataclass
class SceneVerificationResult:
    """场景验证的完整输出。"""
    scene_type: str
    total_triggers: int
    statistics_by_horizon: List[AggregateStats] = field(default_factory=list)
    trigger_details: List[Dict[str, Any]] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)


class SceneDetector(ABC):
    """场景检测器的抽象基类。"""

    @property
    @abstractmethod
    def name(self) -> str:
        """人类可读的场景名。"""

    @property
    @abstractmethod
    def default_params(self) -> Dict[str, Any]:
        """本检测器的默认参数值。"""

    @abstractmethod
    def detect(self, df: pd.DataFrame, params: Dict[str, Any]) -> List[TriggerPoint]:
        """在指标增强后的 DataFrame 中检测场景出现点。

        Args:
            df: 含 OHLCV 与全部指标列的 DataFrame。
            params: 检测器参数（与默认参数合并）。

        Returns:
            TriggerPoint 实例列表，每次检出一条。
        """


def _param_number(params: Dict[str, Any], key: str, cast=float, min_value=None,
                  exclusive: bool = False):
    """detector 数值参数取值（F-2：坏值结构化拒绝而非裸 float()/int() 穿 500）。

    params 为 default_params 与请求合并后的 dict（key 必在）。坏值
    raise ResearchError 经 scene 路由兜底转 422。cast 保留原类型语义
    （int 对 "10.5" 依旧拒绝，不隐式截断）；非有限数拒绝——json.loads
    接受 NaN/Infinity 字面量，NaN 比较恒 False 漏过值域、inf 恒触发/
    静默无结果、经 int() 转换还 OverflowError 裸穿；min_value 提供时
    校验值域（period/window/spacing 类键 >=1，与 engine.
    _lookback_condition 的 window>=1 拒绝同款；正值语义键
    exclusive=True 走 > 下界，0 值恒触发/静默无结果同拒），不传保持
    现行为。
    """
    import math

    from core.analysis.hypothesis_service import ResearchError

    raw = params[key]
    try:
        val = cast(raw)
    except (TypeError, ValueError, OverflowError):
        raise ResearchError(
            "RESEARCH_PARAM_INVALID",
            f"params.{key} 须为数值: {raw!r}",
            detail={"param": f"params.{key}", "value": raw,
                    "legal_range": "数值"},
        ) from None
    if not math.isfinite(val):
        raise ResearchError(
            "RESEARCH_PARAM_INVALID",
            f"params.{key} 须为有限数: {raw!r}",
            detail={"param": f"params.{key}", "value": str(raw),
                    "legal_range": "有限数值"},
        )
    if min_value is not None:
        bound = f"> {min_value}" if exclusive else f">= {min_value}"
        if (exclusive and val <= min_value) or (not exclusive and val < min_value):
            raise ResearchError(
                "RESEARCH_PARAM_INVALID",
                f"params.{key} 须为 {bound}: {val!r}",
                detail={"param": f"params.{key}", "value": val,
                        "legal_range": bound},
            )
    return val
