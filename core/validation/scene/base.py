"""Base classes and data structures for scene verification detectors."""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

import pandas as pd


@dataclass
class TriggerPoint:
    """A single detected scene occurrence."""
    id: int
    timestamp: str          # ISO string of the trigger bar
    trigger_price: float    # close price at trigger bar
    bar_index: int          # integer position in DataFrame
    indicator_snapshot: Dict[str, float] = field(default_factory=dict)
    pattern_subtype: str = ""                               # e.g. "double_top"
    pattern_metadata: Dict[str, Any] = field(default_factory=dict)  # key_points etc.


@dataclass
class HorizonStats:
    """Forward-looking metrics for a single trigger at one horizon."""
    horizon: int            # number of bars forward
    close_pct: float        # (close_at_N - trigger) / trigger * 100
    max_gain_pct: float     # highest high relative to trigger %
    max_loss_pct: float     # lowest low relative to trigger %
    bars_to_peak: int       # bar offset where max high occurred
    bars_to_trough: int     # bar offset where min low occurred
    is_partial: bool = False  # fewer than N bars available


@dataclass
class AggregateStats:
    """Aggregated statistics across all triggers for one horizon."""
    horizon: int
    total_triggers: int
    win_rate: float             # % where close_pct > 0
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
    """Complete scene verification output."""
    scene_type: str
    total_triggers: int
    statistics_by_horizon: List[AggregateStats] = field(default_factory=list)
    trigger_details: List[Dict[str, Any]] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)


class SceneDetector(ABC):
    """Abstract base class for scene detectors."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Human-readable scene name."""

    @property
    @abstractmethod
    def default_params(self) -> Dict[str, Any]:
        """Default parameter values for this detector."""

    @abstractmethod
    def detect(self, df: pd.DataFrame, params: Dict[str, Any]) -> List[TriggerPoint]:
        """Detect scene occurrences in the indicator-enhanced DataFrame.

        Args:
            df: DataFrame with OHLCV + all indicator columns.
            params: Detector-specific parameters (merged with defaults).

        Returns:
            List of TriggerPoint instances, one per detected occurrence.
        """
