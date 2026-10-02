"""冠军追踪：分数 + 指标的原子快照更新（Hall-of-Fame 模式）。

服务对象：api/runner.py 进化任务线程（runner 的 Hall-of-Fame
原子快照——每代更新后写任务记录与 champion 落库）。职责边界：
只管历史最优的判定与持有，不做评估（分数由评估层给出）。
"""
from __future__ import annotations

import copy
import json
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Dict, Optional


@dataclass
class ChampionRecord:
    """某时刻历史最优个体的不可变快照（score/metrics/代数/时间戳）。"""
    score: float
    metrics: Dict[str, Any] = field(default_factory=dict)
    dimension_scores: Dict[str, float] = field(default_factory=dict)
    generation: int = 0
    timestamp: float = 0.0


class ChampionTracker:
    """跨代追踪历史最优个体。

    分数与指标恒作为一个原子单元更新——消除 best_score 与
    champion_metrics 可能来自不同个体的失步缺陷。
    """

    def __init__(self) -> None:
        self._champion: Optional[ChampionRecord] = None
        self._lock = threading.Lock()

    def update(
        self,
        score: float,
        metrics: Optional[Dict[str, Any]] = None,
        dimension_scores: Optional[Dict[str, float]] = None,
        generation: int = 0,
    ) -> bool:
        """候选严格更优时原子更新冠军。

        Returns:
            True 更新成功；False 未更新（分数非正或不优于现任）。
        """
        if score <= 0:
            return False

        record = ChampionRecord(
            score=score,
            metrics=copy.deepcopy(metrics) if metrics else {},
            dimension_scores=copy.deepcopy(dimension_scores) if dimension_scores else {},
            generation=generation,
            timestamp=time.time(),
        )

        with self._lock:
            if self._champion is None or score > self._champion.score:
                self._champion = record
                return True
            return False

    def get_champion(self) -> Optional[ChampionRecord]:
        """返回当前冠军快照（深拷贝，线程安全）。"""
        with self._lock:
            if self._champion is None:
                return None
            return copy.deepcopy(self._champion)

    @property
    def best_score(self) -> float:
        """当前最优分；无冠军时 -1.0。"""
        with self._lock:
            return self._champion.score if self._champion else -1.0
