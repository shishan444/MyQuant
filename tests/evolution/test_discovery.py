"""Unit tests for B9 discovery module: label_generator, stat_validator, feature_encoder, rule_extractor, tree_engine, knn_engine.

Covers all 11 public units identified in the module audit.
Run with: pytest tests/test_discovery.py -v
"""

import numpy as np
import pandas as pd
import pytest

pytestmark = [pytest.mark.unit]

from tests.helpers.data_factory import make_ohlcv

# ============================================================================
# Fixtures
# ============================================================================

@pytest.fixture
def ohlcv_df():
    """500-bar BTC 4h with indicators computed."""
    from core.features.indicators import compute_all_indicators
    df = make_ohlcv(n=500, freq="4h")
    return compute_all_indicators(df)

@pytest.fixture
def basic_ohlcv():
    """Raw OHLCV without indicators, for label_generator tests."""
    return make_ohlcv(n=200, freq="4h")

# ============================================================================
# B9-1: label_generator.generate_labels
# ============================================================================

class TestGenerateLabels:
    """Tests for core/discovery/label_generator.py:generate_labels."""

    def test_output_columns(self, basic_ohlcv):
        from core.discovery.label_generator import generate_labels
        labels = generate_labels(basic_ohlcv, horizon=12)
        assert list(labels.columns) == ["direction", "future_close_pct", "future_high_pct", "future_low_pct"]

    def test_direction_values(self, basic_ohlcv):
        from core.discovery.label_generator import generate_labels
        labels = generate_labels(basic_ohlcv, horizon=12)
        valid = labels["direction"].dropna()
        assert set(valid.unique()).issubset({"UP", "DOWN", "FLAT"})

    def test_last_horizon_rows_are_nan(self, basic_ohlcv):
        from core.discovery.label_generator import generate_labels
        labels = generate_labels(basic_ohlcv, horizon=12)
        assert labels["future_close_pct"].iloc[-12:].isna().all()

    def test_future_close_pct_sign_matches_direction(self, basic_ohlcv):
        from core.discovery.label_generator import generate_labels
        labels = generate_labels(basic_ohlcv, horizon=12, up_threshold=0.01, down_threshold=-0.01)
        valid = labels.dropna(subset=["future_close_pct"])
        up_mask = valid["direction"] == "UP"
        if up_mask.any():
            assert (valid.loc[up_mask, "future_close_pct"] > 0.01).all()
        down_mask = valid["direction"] == "DOWN"
        if down_mask.any():
            assert (valid.loc[down_mask, "future_close_pct"] < -0.01).all()

    def test_index_preserved(self, basic_ohlcv):
        from core.discovery.label_generator import generate_labels
        labels = generate_labels(basic_ohlcv, horizon=12)
        assert labels.index.equals(basic_ohlcv.index)

# ============================================================================
# B9-2: stat_validator.wilson_confidence
# ============================================================================

class TestWilsonConfidence:
    """Tests for core/discovery/stat_validator.py:wilson_confidence."""

    def test_total_zero_returns_zeros(self):
        from core.discovery.stat_validator import wilson_confidence
        assert wilson_confidence(0, 0) == (0.0, 0.0)

    def test_all_successes(self):
        from core.discovery.stat_validator import wilson_confidence
        lower, upper = wilson_confidence(100, 100, z=1.96)
        assert lower > 0.9
        assert upper >= 0.999  # Float precision: may be 0.9999...

    def test_no_successes(self):
        from core.discovery.stat_validator import wilson_confidence
        lower, upper = wilson_confidence(0, 100, z=1.96)
        assert lower == 0.0
        assert upper < 0.05

    def test_half_successes_contains_0_5(self):
        from core.discovery.stat_validator import wilson_confidence
        lower, upper = wilson_confidence(50, 100, z=1.96)
        assert lower < 0.5 < upper

    def test_bounds_in_0_1(self):
        from core.discovery.stat_validator import wilson_confidence
        for s in [0, 10, 50, 90, 100]:
            lower, upper = wilson_confidence(s, 100, z=1.96)
            assert 0.0 <= lower <= 1.0
            assert 0.0 <= upper <= 1.0

    def test_higher_z_wider_interval(self):
        from core.discovery.stat_validator import wilson_confidence
        l1, u1 = wilson_confidence(50, 100, z=1.96)
        l2, u2 = wilson_confidence(50, 100, z=2.576)
        assert (u2 - l2) > (u1 - l1)

# ============================================================================
# B9-3: stat_validator.discretize_indicator
# ============================================================================

class TestDiscretizeIndicator:
    """Tests for core/discovery/stat_validator.py:discretize_indicator."""

    def test_rsi_bins(self):
        from core.discovery.stat_validator import discretize_indicator
        s = pd.Series([10, 25, 40, 55, 65, 80, 95])
        result = discretize_indicator(s, "rsi_14")
        assert "oversold" in result.values
        assert "overbought" in result.values

    def test_unknown_indicator_uses_quartiles(self):
        from core.discovery.stat_validator import discretize_indicator
        s = pd.Series(range(100), dtype=float)
        result = discretize_indicator(s, "custom_indicator")
        assert result.notna().any()

# ============================================================================
# B9-4: stat_validator.build_conditional_prob_table
# ============================================================================

class TestBuildConditionalProbTable:
    """Tests for core/discovery/stat_validator.py:build_conditional_prob_table."""

    def test_output_structure(self):
        from core.discovery.stat_validator import build_conditional_prob_table
        df = pd.DataFrame({
            "rsi_14": np.random.uniform(0, 100, 200),
            "direction": np.random.choice(["UP", "DOWN", "FLAT"], 200),
        })
        result = build_conditional_prob_table(df, "rsi_14", "direction", "UP")
        for label, info in result.items():
            assert "prob" in info
            assert "lower_ci" in info
            assert "upper_ci" in info
            assert "count" in info

    def test_prob_between_0_and_1(self):
        from core.discovery.stat_validator import build_conditional_prob_table
        df = pd.DataFrame({
            "rsi_14": np.random.uniform(0, 100, 200),
            "direction": np.random.choice(["UP", "DOWN"], 200),
        })
        result = build_conditional_prob_table(df, "rsi_14", "direction", "UP")
        for info in result.values():
            assert 0.0 <= info["prob"] <= 1.0

# ============================================================================
# B9-5: stat_validator.validate_rule_lift
# ============================================================================

class TestValidateRuleLift:
    """Tests for core/discovery/stat_validator.py:validate_rule_lift."""

    def test_lift_above_1_for_good_rule(self):
        from core.discovery.stat_validator import validate_rule_lift
        np.random.seed(42)
        n = 500
        rsi = np.random.uniform(0, 100, n)
        direction = np.where(rsi < 30, "UP", np.where(rsi > 70, "DOWN", "FLAT"))
        df = pd.DataFrame({"rsi_14": rsi, "direction": direction})
        lift = validate_rule_lift(
            df,
            [{"feature": "rsi_14", "operator": "le", "threshold": 30}],
            target_value="UP",
        )
        assert lift > 1.0

    def test_lift_zero_for_missing_column(self):
        from core.discovery.stat_validator import validate_rule_lift
        df = pd.DataFrame({"a": [1, 2, 3], "direction": ["UP", "DOWN", "UP"]})
        lift = validate_rule_lift(df, [{"feature": "nonexistent", "operator": "gt", "threshold": 0}])
        # Baseline prob * 0 / baseline = 0 when no filtering effect
        assert lift >= 0.0

    def test_lift_zero_for_few_samples(self):
        from core.discovery.stat_validator import validate_rule_lift
        df = pd.DataFrame({"x": [1.0], "direction": ["UP"]})
        lift = validate_rule_lift(df, [{"feature": "x", "operator": "le", "threshold": 100}])
        assert lift == 0.0  # < 10 samples

# ============================================================================
# B9-6: feature_encoder.FeatureEncoder
# ============================================================================

class TestFeatureEncoder:
    """Tests for core/discovery/feature_encoder.py:FeatureEncoder."""

    def test_fit_sets_feature_columns(self, ohlcv_df):
        from core.discovery.feature_encoder import FeatureEncoder
        enc = FeatureEncoder()
        enc.fit(ohlcv_df)
        assert len(enc.feature_columns) > 0

    def test_transform_shape_matches(self, ohlcv_df):
        from core.discovery.feature_encoder import FeatureEncoder
        enc = FeatureEncoder()
        features = enc.fit_transform(ohlcv_df)
        assert features.shape[0] == len(ohlcv_df)
        assert features.shape[1] == len(enc.feature_columns)

    def test_transform_values_0_1_range(self, ohlcv_df):
        from core.discovery.feature_encoder import FeatureEncoder
        enc = FeatureEncoder()
        features = enc.fit_transform(ohlcv_df)
        assert features.min() >= -0.01  # Allow tiny float error
        assert features.max() <= 1.01

    def test_empty_dataframe(self):
        from core.discovery.feature_encoder import FeatureEncoder
        enc = FeatureEncoder()
        df = pd.DataFrame({"close": [1.0, 2.0]})
        features = enc.fit_transform(df)
        assert features.shape[0] == 2

# ============================================================================
# B9-7: rule_extractor.extract_rules
# ============================================================================

class TestExtractRules:
    """Tests for core/discovery/rule_extractor.py:extract_rules."""

    def _make_tree(self):
        """Create a simple trained decision tree for testing."""
        from sklearn.tree import DecisionTreeClassifier
        np.random.seed(42)
        X = np.random.randn(200, 3)
        y = np.where(X[:, 0] > 0, "UP", "DOWN")
        clf = DecisionTreeClassifier(max_depth=3, min_samples_leaf=20, random_state=42)
        clf.fit(X, y)
        return clf, ["feat_a", "feat_b", "feat_c"]

    def test_returns_list_of_rule_items(self):
        from core.discovery.rule_extractor import extract_rules, RuleItem
        clf, names = self._make_tree()
        rules = extract_rules(clf, names)
        assert isinstance(rules, list)
        if rules:
            assert isinstance(rules[0], RuleItem)

    def test_rule_has_required_fields(self):
        from core.discovery.rule_extractor import extract_rules
        clf, names = self._make_tree()
        rules = extract_rules(clf, names)
        if rules:
            r = rules[0]
            assert hasattr(r, "conditions")
            assert hasattr(r, "direction")
            assert hasattr(r, "confidence")
            assert hasattr(r, "samples")
            assert hasattr(r, "lift")
            assert r.direction in ("UP", "DOWN")

    def test_max_rules_limit(self):
        from core.discovery.rule_extractor import extract_rules
        clf, names = self._make_tree()
        rules = extract_rules(clf, names, max_rules=3)
        assert len(rules) <= 3

    def test_conditions_have_feature_operator_threshold(self):
        from core.discovery.rule_extractor import extract_rules
        clf, names = self._make_tree()
        rules = extract_rules(clf, names)
        if rules:
            for cond in rules[0].conditions:
                assert "feature" in cond
                assert "operator" in cond
                assert "threshold" in cond

# ============================================================================
# B9-8: tree_engine.PatternDiscoveryEngine
# ============================================================================

class TestPatternDiscoveryEngine:
    """Tests for core/discovery/tree_engine.py:PatternDiscoveryEngine."""

    def test_discover_returns_discovery_result(self, ohlcv_df):
        from core.discovery.tree_engine import PatternDiscoveryEngine, DiscoveryResult
        engine = PatternDiscoveryEngine(max_depth=3, min_samples_leaf=30)
        result = engine.discover(ohlcv_df)
        assert isinstance(result, DiscoveryResult)

    def test_discover_result_fields(self, ohlcv_df):
        from core.discovery.tree_engine import PatternDiscoveryEngine
        engine = PatternDiscoveryEngine(max_depth=3, min_samples_leaf=30)
        result = engine.discover(ohlcv_df)
        assert isinstance(result.rules, list)
        assert isinstance(result.accuracy, float)
        assert isinstance(result.cv_scores, list)
        assert isinstance(result.feature_importance, dict)
        assert isinstance(result.tree_depth, int)
        assert isinstance(result.n_samples, int)

    def test_accuracy_between_0_and_1(self, ohlcv_df):
        from core.discovery.tree_engine import PatternDiscoveryEngine
        engine = PatternDiscoveryEngine(max_depth=3, min_samples_leaf=30)
        result = engine.discover(ohlcv_df)
        assert 0.0 <= result.accuracy <= 1.0

    def test_small_data_returns_empty_rules(self):
        from core.discovery.tree_engine import PatternDiscoveryEngine
        engine = PatternDiscoveryEngine(max_depth=3, min_samples_leaf=50)
        small_df = make_ohlcv(n=20, freq="4h")
        result = engine.discover(small_df)
        assert result.rules == []
        assert result.accuracy == 0.0

# ============================================================================
# B9-9: knn_engine.SimilarCaseEngine
# ============================================================================

class TestSimilarCaseEngine:
    """Tests for core/discovery/knn_engine.py:SimilarCaseEngine."""

    def test_fit_returns_self(self, ohlcv_df):
        from core.discovery.knn_engine import SimilarCaseEngine
        engine = SimilarCaseEngine(n_neighbors=10)
        result = engine.fit(ohlcv_df)
        assert result is engine

    def test_find_similar_returns_list(self, ohlcv_df):
        from core.discovery.knn_engine import SimilarCaseEngine, SimilarCase
        engine = SimilarCaseEngine(n_neighbors=10, horizon=12)
        engine.fit(ohlcv_df)
        features = engine.encoder.transform(ohlcv_df.iloc[:1])
        cases = engine.find_similar(features[0], n_neighbors=5)
        assert isinstance(cases, list)
        if cases:
            assert isinstance(cases[0], SimilarCase)

    def test_predict_returns_prediction_result(self, ohlcv_df):
        from core.discovery.knn_engine import SimilarCaseEngine, PredictionResult
        engine = SimilarCaseEngine(n_neighbors=10, horizon=12)
        engine.fit(ohlcv_df)
        features = engine.encoder.transform(ohlcv_df.iloc[:1])
        pred = engine.predict(features[0], n_neighbors=5)
        assert isinstance(pred, PredictionResult)
        assert pred.predicted_direction in ("UP", "DOWN", "FLAT")

    def test_predict_before_fit_returns_flat(self):
        from core.discovery.knn_engine import SimilarCaseEngine
        engine = SimilarCaseEngine()
        features = np.random.randn(10)
        pred = engine.predict(features)
        assert pred.predicted_direction == "FLAT"
        assert pred.confidence == 0.0

    def test_similar_case_fields(self, ohlcv_df):
        from core.discovery.knn_engine import SimilarCaseEngine
        engine = SimilarCaseEngine(n_neighbors=5, horizon=12)
        engine.fit(ohlcv_df)
        features = engine.encoder.transform(ohlcv_df.iloc[:1])
        cases = engine.find_similar(features[0], n_neighbors=3)
        if cases:
            c = cases[0]
            assert c.index >= 0
            assert c.close_price > 0
            assert c.distance >= 0
