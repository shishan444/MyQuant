"""Decision tree rule discovery engine.

Trains a DecisionTreeClassifier on indicator features to discover
indicator-state-to-price-direction mappings, then extracts
interpretable trading rules from the tree structure.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd
from sklearn.model_selection import TimeSeriesSplit
from sklearn.tree import DecisionTreeClassifier

from core.discovery.feature_encoder import FeatureEncoder
from core.discovery.label_generator import generate_labels
from core.discovery.rule_extractor import extract_rules, RuleItem


@dataclass
class DiscoveryResult:
    """Result from pattern discovery."""
    rules: List[RuleItem]
    accuracy: float
    cv_scores: List[float]
    feature_importance: Dict[str, float]
    tree_depth: int
    n_samples: int


class PatternDiscoveryEngine:
    """Discover trading rules from historical data using decision trees."""

    def __init__(self, max_depth: int = 5, horizon: int = 12,
                 min_samples_leaf: int = 50):
        self.max_depth = max_depth
        self.horizon = horizon
        self.min_samples_leaf = min_samples_leaf
        self.encoder = FeatureEncoder()

    def discover(self, df: pd.DataFrame, target: str = "direction") -> DiscoveryResult:
        """Run pattern discovery pipeline.

        Args:
            df: Enhanced DataFrame with OHLCV + indicator columns.
            target: Target column name from label generator.

        Returns:
            DiscoveryResult with extracted rules and metrics.
        """
        # 1. Generate labels
        labels = generate_labels(df, horizon=self.horizon)

        # 2. Encode features（时序防前视：scaler 仅前段 fit，外推 transform）
        fit_end = max(int(len(df) * 0.5), 2)
        self.encoder.fit(df.iloc[:fit_end])
        features = self.encoder.transform(df)

        # 3. Filter valid rows (no NaN in labels)
        valid_mask = labels[target].notna() & ~np.isnan(features).all(axis=1)
        # Remove last horizon rows (no future data)
        valid_mask[-self.horizon:] = False

        X = features[valid_mask.values]
        y = labels.loc[valid_mask, target].values

        if len(X) < self.min_samples_leaf * 3:
            return DiscoveryResult(
                rules=[], accuracy=0.0, cv_scores=[],
                feature_importance={}, tree_depth=0, n_samples=len(X),
            )

        # 4. TimeSeriesSplit cross validation
        cv = TimeSeriesSplit(n_splits=3)
        cv_scores = []

        for train_idx, val_idx in cv.split(X):
            X_train, X_val = X[train_idx], X[val_idx]
            y_train, y_val = y[train_idx], y[val_idx]

            # Filter to only UP/DOWN for binary-like classification
            mask = np.isin(y_train, ["UP", "DOWN"])
            if mask.sum() < self.min_samples_leaf:
                continue

            clf = DecisionTreeClassifier(
                max_depth=self.max_depth,
                min_samples_leaf=self.min_samples_leaf,
                random_state=42,
            )
            clf.fit(X_train[mask], y_train[mask])
            score = clf.score(X_val, y_val)
            cv_scores.append(score)

        # 5. Train final model on all data
        mask = np.isin(y, ["UP", "DOWN"])
        if mask.sum() < self.min_samples_leaf:
            return DiscoveryResult(
                rules=[], accuracy=0.0, cv_scores=cv_scores,
                feature_importance={}, tree_depth=0, n_samples=len(X),
            )

        clf = DecisionTreeClassifier(
            max_depth=self.max_depth,
            min_samples_leaf=self.min_samples_leaf,
            random_state=42,
        )
        clf.fit(X[mask], y[mask])

        accuracy = np.mean(cv_scores) if cv_scores else 0.0

        # 6. Extract rules from tree
        feature_names = self.encoder.feature_columns
        rules = extract_rules(clf, feature_names, max_rules=10)

        # 7. Feature importance
        importance = {}
        if feature_names:
            for name, imp in zip(feature_names, clf.feature_importances_):
                if imp > 0:
                    importance[name] = round(float(imp), 4)

        return DiscoveryResult(
            rules=rules,
            accuracy=round(accuracy, 4),
            cv_scores=[round(s, 4) for s in cv_scores],
            feature_importance=importance,
            tree_depth=clf.get_depth(),
            n_samples=int(mask.sum()),
        )
