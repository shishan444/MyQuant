"""Rule extractor: extracts interpretable trading rules from decision tree structure.

Traverses each leaf node, extracts the full path from root to leaf,
and converts each path into a set of indicator conditions.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional

import numpy as np
from sklearn.tree import DecisionTreeClassifier


from core.discovery.stat_validator import wilson_confidence


def rule_sort_key(r: "RuleItem") -> float:
    """B2.3: 规则排序键 = Wilson 置信下界（95%）。

    旧键（confidence × samples 点估计乘积）在乘积相近时按噪声排序——
    小样本高纯度规则会被大样本点估计险胜反超。Wilson 下界对样本量
    收缩，同点估计下样本多者排前。confidence 字段（点估计）保留展示
    语义，排序不修改 RuleItem。
    """
    if r.samples <= 0:
        return 0.0
    successes = min(int(round(r.confidence * r.samples)), r.samples)
    return wilson_confidence(successes, r.samples)[0]


@dataclass
class RuleItem:
    """A single trading rule extracted from the decision tree."""
    conditions: List[dict]  # List of {feature, operator, threshold}
    direction: str          # "UP" or "DOWN"
    confidence: float       # Purity of the leaf node
    samples: int            # Number of samples in leaf
    lift: float             # Lift over baseline


def extract_rules(
    clf: DecisionTreeClassifier,
    feature_names: List[str],
    max_rules: int = 10,
) -> List[RuleItem]:
    """Extract top rules from a trained decision tree.

    Args:
        clf: Fitted DecisionTreeClassifier.
        feature_names: Names of features used in training.
        max_rules: Maximum number of rules to return.

    Returns:
        List of RuleItem sorted by confidence * samples.
    """
    tree = clf.tree_
    children_left = tree.children_left
    children_right = tree.children_right
    features = tree.feature
    thresholds = tree.threshold
    values = tree.value
    n_node_samples = tree.n_node_samples

    rules = []
    n_classes = values.shape[2]

    def _traverse(node: int, path: List[dict]):
        """Recursively traverse the tree, collecting paths to leaves."""
        if children_left[node] == -1:  # Leaf node
            # Determine the predicted class
            node_values = values[node][0]
            total = node_values.sum()
            if total == 0:
                return

            class_idx = int(np.argmax(node_values))
            class_names = ["DOWN", "FLAT", "UP"] if n_classes >= 3 else ["DOWN", "UP"]
            direction = class_names[min(class_idx, len(class_names) - 1)]
            confidence = float(node_values[class_idx] / total)

            # Compute lift (vs baseline)
            baseline = 1.0 / n_classes
            lift = confidence / baseline if baseline > 0 else 1.0

            # Only include rules with lift > 1.2
            if lift > 1.2 and direction in ("UP", "DOWN"):
                rules.append(RuleItem(
                    conditions=list(path),
                    direction=direction,
                    confidence=round(confidence, 4),
                    samples=int(n_node_samples[node]),
                    lift=round(lift, 4),
                ))
            return

        # Get feature name
        feat_idx = features[node]
        if feat_idx < 0 or feat_idx >= len(feature_names):
            return
        feat_name = feature_names[feat_idx]
        threshold = float(thresholds[node])

        # Left child: feature <= threshold
        path_left = path + [{"feature": feat_name, "operator": "le", "threshold": round(threshold, 4)}]
        _traverse(children_left[node], path_left)

        # Right child: feature > threshold
        path_right = path + [{"feature": feat_name, "operator": "gt", "threshold": round(threshold, 4)}]
        _traverse(children_right[node], path_right)

    _traverse(0, [])

    # B2.3: Sort by Wilson lower bound (descending) — 点估计保留在 confidence 字段展示
    rules.sort(key=rule_sort_key, reverse=True)

    return rules[:max_rules]
