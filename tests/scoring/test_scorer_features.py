"""scorer 特征测试网（现状记录语义）。

锚定 compute_fitness/score_strategy 的分支细节：objective 回退、
qualified 判据、satisfaction 各维度 ratio 手算值、legacy 总分映射、
缺失维度排除与运行时限量覆盖。期望值独立手算。
"""
import pytest

pytestmark = [pytest.mark.unit]

from core.scoring.scorer import RequirementsConfig, compute_fitness, score_strategy


def _metrics(**overrides):
    base = {
        "annual_return": 0.20, "max_drawdown": -0.25,
        "win_rate": 0.45, "total_trades": 15, "profit_factor": 1.5,
        "sharpe_ratio": 1.8, "calmar_ratio": 2.5,
    }
    base.update(overrides)
    return base


class TestObjectiveFallback:
    """core 层直调的宽容回退（分层保留，非冗余）。

    API 边界已由 schema Literal 收紧做 422 拒绝（见
    test_schema_contract.py）；本层回退是 core 公共入口对内部直调
    的防线——两层各司其职，勿以一层简化另一层。
    """

    def test_invalid_objective_falls_back_to_sharpe(self):
        req = RequirementsConfig(objective="bogus")
        result = compute_fitness(_metrics(), requirements=req)
        assert result["objective_name"] == "sharpe"
        assert result["fitness"] == pytest.approx(1.8)

    def test_zero_trades_bypasses_objective_validation(self):
        # 早退发生在 objective 校验之前：非法名原样透传（现状特征）
        req = RequirementsConfig(objective="bogus")
        result = compute_fitness({"total_trades": 0}, requirements=req)
        assert result["objective_name"] == "bogus"
        assert result["fitness"] == 0.0


class TestQualifiedGate:
    def test_zero_fitness_with_all_constraints_met_not_qualified(self):
        # qualified = 无约束失败 且 fitness>0：sharpe=0 两条件只满足前者
        result = compute_fitness(_metrics(sharpe_ratio=0.0))
        assert result["constraint_failures"] == []
        assert result["fitness"] == 0.0
        assert result["qualified"] is False

    def test_profit_factor_constraint_failure_recorded(self):
        # pf < min_profit_factor → 失败清单记录且 fitness 归零（四类硬门之一）
        req = RequirementsConfig(min_profit_factor=2.0)
        result = compute_fitness(_metrics(profit_factor=1.5), requirements=req)
        assert "profit_factor" in result["constraint_failures"]
        assert result["fitness"] == 0.0
        assert result["qualified"] is False


class TestSatisfactionRatios:
    """各维度 ratio 的手算锚定（默认 req：min_annual_return=0 → min_ar=0.15）。"""

    def test_return_ratio_positive(self):
        result = compute_fitness(_metrics(annual_return=0.30))
        dim = result["satisfaction"]["annual_return"]
        assert dim["required"] == pytest.approx(0.15)  # min_ar 回退值
        assert dim["ratio"] == pytest.approx(2.0)      # 0.30/0.15
        assert dim["met"] is True

    def test_return_ratio_negative_half_credit(self):
        # 负收益：max(0, 1 + ar/min_ar) * 0.5 → (1-0.5)*0.5 = 0.25
        result = compute_fitness(_metrics(annual_return=-0.075))
        assert result["satisfaction"]["annual_return"]["ratio"] == pytest.approx(0.25)

    def test_return_ratio_deep_negative_clamped(self):
        # ar=-0.30：1-2=-1 → max(0,·)=0 → 0.0
        result = compute_fitness(_metrics(annual_return=-0.30))
        assert result["satisfaction"]["annual_return"]["ratio"] == 0.0

    def test_drawdown_ratio_inverted(self):
        # 反比维度：ratio = req.max_drawdown / actual = 0.30/0.10 = 3.0
        result = compute_fitness(_metrics(max_drawdown=-0.10))
        dim = result["satisfaction"]["max_drawdown"]
        assert dim["ratio"] == pytest.approx(3.0)
        assert dim["inverted"] is True

    def test_drawdown_zero_ratio_is_one(self):
        # actual_dd=0 走 else 分支 → 1.0
        result = compute_fitness(_metrics(max_drawdown=0.0))
        assert result["satisfaction"]["max_drawdown"]["ratio"] == 1.0

    def test_win_rate_missing_semantics(self):
        # win_rate=None → missing 标记结构（ratio 不参与评分维度）
        result = compute_fitness(_metrics(win_rate=None))
        assert result["satisfaction"]["win_rate"] == {
            "actual": None, "required": 0.0, "ratio": None,
            "met": False, "is_percent": True, "inverted": False,
            "missing": True,
        }

    def test_win_rate_zero_with_zero_requirement(self):
        # min_win_rate=0 且 win_rate=0：ratio=0.0（actual>0 才给 1.0）
        result = compute_fitness(_metrics(win_rate=0.0))
        dim = result["satisfaction"]["win_rate"]
        assert dim["ratio"] == 0.0
        assert dim["met"] is True  # 0>=0 成立：met 与 ratio 现状不一致

    def test_win_rate_positive_with_zero_requirement(self):
        result = compute_fitness(_metrics(win_rate=0.4))
        assert result["satisfaction"]["win_rate"]["ratio"] == 1.0

    def test_positive_min_win_rate_ratio_divides(self):
        # min_win_rate>0 → ratio = actual/required 除法分支（0.45/0.3=1.5）
        req = RequirementsConfig(min_win_rate=0.3)
        result = compute_fitness(_metrics(win_rate=0.45), requirements=req)
        dim = result["satisfaction"]["win_rate"]
        assert dim["required"] == pytest.approx(0.3)
        assert dim["ratio"] == pytest.approx(1.5)
        assert dim["met"] is True

    def test_trades_ratio(self):
        req = RequirementsConfig(min_total_trades=10)
        result = compute_fitness(_metrics(total_trades=5), requirements=req)
        assert result["satisfaction"]["total_trades"]["ratio"] == pytest.approx(0.5)

    def test_profit_factor_ratio_at_boundary(self):
        # 1.2/1.2=1.0 且 >= 判定 met（边界含等号）
        req = RequirementsConfig(min_profit_factor=1.2)
        result = compute_fitness(_metrics(profit_factor=1.2), requirements=req)
        dim = result["satisfaction"]["profit_factor"]
        assert dim["ratio"] == 1.0
        assert dim["met"] is True


class TestLegacyScoreMapping:
    """score_strategy 的 fitness→total_score 映射（现状倍率与封顶）。"""

    def test_explorer_sharpe_times_twenty(self):
        metrics = _metrics(sharpe_ratio=2.0)
        result = score_strategy(metrics, "explorer")
        assert result["fitness"] == pytest.approx(2.0)
        assert result["total_score"] == 40.0

    def test_explorer_sharpe_caps_at_hundred(self):
        result = score_strategy(_metrics(sharpe_ratio=6.0), "explorer")
        assert result["fitness"] == pytest.approx(6.0)
        assert result["total_score"] == 100.0

    def test_max_return_annual_return_times_ten(self):
        metrics = _metrics(annual_return=6.0)
        result = score_strategy(metrics, "max_return")
        assert result["total_score"] == 60.0

    def test_max_return_caps_at_hundred(self):
        result = score_strategy(_metrics(annual_return=12.0), "max_return")
        assert result["total_score"] == 100.0


class TestDimensionScoresExclusion:
    def test_missing_win_rate_excluded(self):
        # ratio=None 的维度不进 dimension_scores；有值维度 = ratio*100
        metrics = _metrics(win_rate=None, annual_return=0.30)
        result = score_strategy(metrics, "explorer")
        assert "win_rate" not in result["dimension_scores"]
        assert result["dimension_scores"]["annual_return"] == pytest.approx(200.0)

    def test_dimension_score_not_clamped(self):
        # ratio 2.0 → 200：dimension_scores 不做 0-100 钳制（与 total_score 不同）
        result = score_strategy(_metrics(annual_return=0.30), "explorer")
        assert result["dimension_scores"]["annual_return"] == pytest.approx(200.0)


class TestRuntimeLimitOverrides:
    def test_explorer_negative_drawdown_limit_abs_applied(self):
        # 负限量取 abs → 0.2 硬约束；dd=0.3 超限 → fitness=0
        metrics = _metrics(max_drawdown=-0.30)
        result = score_strategy(metrics, "explorer", max_drawdown_limit=-0.2)
        assert result["fitness"] == 0.0
        assert result["total_score"] == 0.0

    def test_optimizer_runtime_return_limit_overrides_default(self):
        # 默认 0.10 被运行时 0.5 覆盖：ar=0.3 → hard fail annual_return
        metrics = _metrics(annual_return=0.30, max_drawdown=-0.10)
        result = score_strategy(metrics, "optimizer", min_annual_return_limit=0.5)
        assert result["total_score"] == 0.0
        assert result.get("hard_constraint_failed") == "annual_return"


class TestLiquidatedPassthrough:
    def test_score_strategy_liquidated_flag(self):
        result = score_strategy(_metrics(), "explorer", liquidated=True)
        assert result["liquidated"] is True
        assert result["fitness"] == 0.0
        assert result["total_score"] == 0.0
