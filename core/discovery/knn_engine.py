"""KNN similar case matching engine.

Finds historically similar market conditions using K-Nearest Neighbors
on indicator feature vectors, then analyzes the subsequent price movements
of those similar cases.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional

import numpy as np
import pandas as pd
from sklearn.neighbors import NearestNeighbors

from core.discovery.feature_encoder import FeatureEncoder


@dataclass
class SimilarCase:
    """A single historically similar case.

    三项未来统计字段（0.3.7 B8 方向 a）：案例落入尾部无未来窗口区时
    为 None（无数据 ≠ 零收益）——当前 KNN 邻居池在 fit 时已排除尾部
    NaN 行（valid_mask），此 None 分支为防御面（防未来重构撒谎），
    类型面显式声明使 None 语义对消费者可见。
    """
    index: int
    timestamp: str
    close_price: float
    future_return_pct: float | None
    future_high_pct: float | None
    future_low_pct: float | None
    distance: float


@dataclass
class PredictionResult:
    """Price range prediction from KNN."""
    predicted_direction: str  # "UP" / "DOWN" / "FLAT"
    avg_return: float
    median_return: float
    positive_pct: float       # % of neighbors that went up
    price_range_low: float    # 25th percentile return
    price_range_high: float   # 75th percentile return
    confidence: float
    accuracy: float
    similar_cases: List[SimilarCase]


class SimilarCaseEngine:
    """Find similar historical market conditions using KNN."""

    def __init__(self, n_neighbors: int = 50, horizon: int = 12):
        self.n_neighbors = n_neighbors
        self.horizon = horizon
        self.encoder = FeatureEncoder()
        self.nn: Optional[NearestNeighbors] = None
        self._df: Optional[pd.DataFrame] = None
        self._features: Optional[np.ndarray] = None
        self._future_returns: Optional[np.ndarray] = None
        self._future_highs: Optional[np.ndarray] = None
        self._future_lows: Optional[np.ndarray] = None

    def fit(self, df: pd.DataFrame) -> "SimilarCaseEngine":
        """Build the KNN index from historical data.

        Args:
            df: Enhanced DataFrame with OHLCV + indicator columns.
        """
        self._df = df.copy()
        # 时序防前视：scaler 仅用前段 fit（min/max 不含未来数据），
        # 后段以外推方式 transform
        fit_end = max(int(len(df) * 0.5), 2)
        self.encoder.fit(df.iloc[:fit_end])
        self._features = self.encoder.transform(df)

        close = df["close"].values
        high = df["high"].values
        low = df["low"].values

        # Pre-compute future returns
        n = len(df)
        self._future_returns = np.full(n, np.nan)
        self._future_highs = np.full(n, np.nan)
        self._future_lows = np.full(n, np.nan)

        for i in range(n - self.horizon):
            future_close = close[i + self.horizon]
            self._future_returns[i] = (future_close - close[i]) / close[i]
            self._future_highs[i] = (high[i + 1: i + 1 + self.horizon].max() - close[i]) / close[i]
            self._future_lows[i] = (low[i + 1: i + 1 + self.horizon].min() - close[i]) / close[i]

        # Fit KNN (exclude last horizon rows and NaN rows)
        valid_mask = ~np.isnan(self._future_returns) & ~np.isnan(self._features).all(axis=1)
        valid_features = self._features[valid_mask]

        if len(valid_features) > self.n_neighbors:
            self.nn = NearestNeighbors(
                n_neighbors=min(self.n_neighbors, len(valid_features)),
                metric="euclidean",
            )
            self.nn.fit(valid_features)
            self._valid_indices = np.where(valid_mask)[0]

        return self

    def find_similar(self, current_features: np.ndarray,
                     n_neighbors: int = 50) -> List[SimilarCase]:
        """Find the most similar historical cases.

        Args:
            current_features: Feature vector for the current market state.
            n_neighbors: Number of neighbors to return.

        Returns:
            List of SimilarCase objects.
        """
        if self.nn is None or self._df is None:
            return []

        k = min(n_neighbors, self.nn.n_neighbors)
        distances, indices = self.nn.kneighbors(
            current_features.reshape(1, -1), n_neighbors=k,
        )

        cases = []
        for dist, idx in zip(distances[0], indices[0]):
            real_idx = self._valid_indices[idx]
            ts = str(self._df.index[real_idx]) if hasattr(self._df.index, 'strftime') else str(real_idx)
            # 0.3.7 B8：尾部 NaN → None（无数据显式化，原置 0 冒充
            # 零收益）。当前邻居池已排除 NaN 行，本分支为防御面
            # （Valid_mask 重构时的诚实降级，不再撒谎）
            cases.append(SimilarCase(
                index=int(real_idx),
                timestamp=ts,
                close_price=float(self._df["close"].iloc[real_idx]),
                future_return_pct=(float(self._future_returns[real_idx])
                                   if not np.isnan(self._future_returns[real_idx])
                                   else None),
                future_high_pct=(float(self._future_highs[real_idx])
                                 if not np.isnan(self._future_highs[real_idx])
                                 else None),
                future_low_pct=(float(self._future_lows[real_idx])
                                if not np.isnan(self._future_lows[real_idx])
                                else None),
                distance=float(dist),
            ))

        return cases

    def predict(self, current_features: np.ndarray,
                n_neighbors: int = 50) -> PredictionResult:
        """Predict price range from KNN neighbors.

        Args:
            current_features: Feature vector for the current market state.
            n_neighbors: Number of neighbors to analyze.

        Returns:
            PredictionResult with predicted direction and confidence.
        """
        cases = self.find_similar(current_features, n_neighbors)

        if not cases:
            return PredictionResult(
                predicted_direction="FLAT",
                avg_return=0.0, median_return=0.0,
                positive_pct=0.5, price_range_low=0.0,
                price_range_high=0.0, confidence=0.0,
                accuracy=0.0, similar_cases=[],
            )

        # 0.3.7 B8 适配：float | None 语义下过滤 None（防御面——当前
        # 邻居池不产出 None；过滤后统计口径与非尾部队列一致）
        returns = np.array([c.future_return_pct for c in cases
                            if c.future_return_pct is not None])
        positive_pct = float((returns > 0).sum() / len(returns))

        # Direction based on majority
        if positive_pct > 0.6:
            direction = "UP"
        elif positive_pct < 0.4:
            direction = "DOWN"
        else:
            direction = "FLAT"

        # Confidence: how strong is the majority
        confidence = round(abs(positive_pct - 0.5) * 2, 4)

        # Accuracy estimation based on direction consistency
        correct = sum(1 for r in returns
                      if (direction == "UP" and r > 0) or
                      (direction == "DOWN" and r < 0) or
                      direction == "FLAT")
        accuracy = round(correct / len(returns), 4) if len(returns) > 0 else 0.0

        return PredictionResult(
            predicted_direction=direction,
            avg_return=round(float(np.mean(returns)), 4),
            median_return=round(float(np.median(returns)), 4),
            positive_pct=round(positive_pct, 4),
            price_range_low=round(float(np.percentile(returns, 25)), 4),
            price_range_high=round(float(np.percentile(returns, 75)), 4),
            confidence=confidence,
            accuracy=accuracy,
            similar_cases=cases[:20],  # Limit to 20 for response size
        )
