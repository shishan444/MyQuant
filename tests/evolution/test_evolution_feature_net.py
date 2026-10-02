"""core/evolution/ 特征测试网（修复期临时安全网）——diversity + evaluation。

背景：下游审查战（fix/mcp-research-downstream-review）建网补薄，实测
tests/evolution/ 基线下 diversity 66% / evaluation 65%：
compute_phenotype_diversity 与 maintain_diversity 主体、build_requirements
的 requirements_json 路径、evaluate_dna 的按需加载与零分兜底从未执行。

语义约定：现状记录特征锚——断言值按当前实现行为钉住，不臆断应然
行为；实现行为变化时此网报警即为生效。

Run with: venv/bin/python -m pytest tests/evolution/test_evolution_feature_net.py -v
"""

import json
from pathlib import Path

import pytest

pytestmark = [pytest.mark.unit]

from tests.helpers.data_factory import make_dna, make_enhanced_df


# ===========================================================================
# 1. diversity：表型距离与种群多样性（主体未执行）
# ===========================================================================

class TestPhenotypeDistanceFeature:
    """diversity.py 特征锚：signal/equity 距离与回退链。"""

    @staticmethod
    def _diag(raw_metrics=None, dimension_scores=None):
        d = {}
        if raw_metrics is not None:
            d["raw_metrics"] = raw_metrics
        if dimension_scores is not None:
            d["dimension_scores"] = dimension_scores
        return d

    def test_signal_distance_without_diagnostics_falls_back_to_genotype(self):
        """现状记录：任一方无 _eval_diagnostics 时回退基因型距离。"""
        from core.evolution.diversity import signal_distance, genotype_distance
        a = make_dna(entry_value=30)
        b = make_dna(entry_value=50)
        assert signal_distance(a, b) == genotype_distance(a, b)

    def test_signal_distance_without_raw_metrics_falls_back(self):
        from core.evolution.diversity import signal_distance, genotype_distance
        a = make_dna(entry_value=30)
        b = make_dna(entry_value=50)
        a._eval_diagnostics = self._diag()  # 空 dict → not raw_a
        b._eval_diagnostics = self._diag(raw_metrics={"total_trades": 5})
        assert signal_distance(a, b) == genotype_distance(a, b)

    def test_signal_distance_identical_metrics_is_zero(self):
        from core.evolution.diversity import signal_distance
        a = make_dna()
        b = make_dna()
        a._eval_diagnostics = self._diag(raw_metrics={
            "total_trades": 10, "win_rate": 0.5})
        b._eval_diagnostics = self._diag(raw_metrics={
            "total_trades": 10, "win_rate": 0.5})
        assert signal_distance(a, b) == pytest.approx(0.0, abs=1e-9)

    def test_signal_distance_different_trades_positive(self):
        from core.evolution.diversity import signal_distance
        a = make_dna()
        b = make_dna()
        a._eval_diagnostics = self._diag(raw_metrics={
            "total_trades": 10, "win_rate": 0.3})
        b._eval_diagnostics = self._diag(raw_metrics={
            "total_trades": 30, "win_rate": 0.6})
        assert 0.0 < signal_distance(a, b) <= 1.0

    def test_equity_distance_without_diagnostics_falls_back_to_signal(self):
        """现状记录：无诊断时 equity 距离回退 signal 距离（再回退基因型）。"""
        from core.evolution.diversity import equity_distance, signal_distance
        a = make_dna(entry_value=30)
        b = make_dna(entry_value=50)
        assert equity_distance(a, b) == signal_distance(a, b)

    def test_equity_distance_uses_dimension_scores(self):
        from core.evolution.diversity import equity_distance
        a = make_dna()
        b = make_dna()
        a._eval_diagnostics = self._diag(dimension_scores={"sharpe": 80.0})
        b._eval_diagnostics = self._diag(dimension_scores={"sharpe": 30.0})
        # |80-30|/100 / 1 维 = 0.5
        assert equity_distance(a, b) == pytest.approx(0.5)

    def test_equity_distance_without_dims_falls_back(self):
        from core.evolution.diversity import equity_distance, signal_distance
        a = make_dna()
        b = make_dna()
        a._eval_diagnostics = self._diag(raw_metrics={"total_trades": 5})
        b._eval_diagnostics = self._diag(raw_metrics={"total_trades": 9})
        # 双方有诊断但无 dimension_scores → 回退 signal 距离
        assert equity_distance(a, b) == signal_distance(a, b)


class TestPopulationDiversityFeature:
    """diversity.py 特征锚：compute_phenotype_diversity / maintain_diversity。"""

    def test_phenotype_diversity_single_individual_all_zero(self):
        from core.evolution.diversity import compute_phenotype_diversity
        result = compute_phenotype_diversity([make_dna()])
        assert result == {"genotype": 0.0, "signal": 0.0, "score": 0.0}

    def test_phenotype_diversity_two_unique(self):
        """现状记录：唯一基因型 → genotype=1.0，signal/score 按诊断计算。

        注意：entry_value 不进 gene_signature，签名唯一性用 indicator 区分。
        """
        from core.evolution.diversity import compute_phenotype_diversity
        a = make_dna(indicator="RSI")
        b = make_dna(indicator="MACD")
        a._eval_diagnostics = {"raw_metrics": {"total_trades": 10, "win_rate": 0.3}}
        b._eval_diagnostics = {"raw_metrics": {"total_trades": 30, "win_rate": 0.6}}
        result = compute_phenotype_diversity([a, b])
        assert result["genotype"] == 1.0
        assert 0.0 < result["signal"] <= 1.0
        assert isinstance(result["score"], float)

    def test_maintain_diversity_returns_population_when_diverse(self):
        """现状记录：多样性达标时原样返回（同一列表对象）。"""
        from core.evolution.diversity import check_and_maintain_diversity
        pop = [make_dna(indicator=i) for i in ("RSI", "MACD", "EMA", "SMA")]
        out = check_and_maintain_diversity(pop, threshold=0.5)
        assert out is pop

    def test_maintain_diversity_replaces_duplicates_when_collapsed(self):
        """现状记录：多样性低于阈值且最常见签名占比>30% → 重复个体被
        换成新随机 DNA（首个保留）。替换体由 create_random_dna 随机生成，
        断言不锁随机结果，只锚确定性结构。"""
        from core.evolution.diversity import check_and_maintain_diversity
        dup = make_dna(indicator="RSI")
        pop = [dup, dup, dup, make_dna(indicator="MACD")]
        out = check_and_maintain_diversity(pop, threshold=0.99)
        assert len(out) == 4
        sigs = [ind.gene_signature for ind in out]
        # 首个 dup 保留在首位；后两个被替换（签名种类数增加，
        # 三个随机体同时撞原签名的概率可忽略）
        assert sigs[0] == dup.gene_signature
        assert len(set(sigs)) >= 2


# ===========================================================================
# 2. evaluation：build_requirements / evaluate_dna / evaluate_population
# ===========================================================================

class TestBuildRequirementsFeature:
    """evaluation.py 特征锚：requirements_json 优先与解析失败显式报错。"""

    def test_requirements_json_string_parsed(self):
        from core.evolution.evaluation import build_requirements
        req = build_requirements({"requirements_json": json.dumps({
            "objective": "calmar", "min_win_rate": 0.4,
        })})
        assert req.objective == "calmar"
        assert req.min_win_rate == 0.4

    def test_requirements_json_dict_passthrough(self):
        from core.evolution.evaluation import build_requirements
        req = build_requirements({"requirements_json": {"objective": "sortino"}})
        assert req.objective == "sortino"

    def test_requirements_json_defaults_applied(self):
        from core.evolution.evaluation import build_requirements
        req = build_requirements({"requirements_json": {}})
        assert req.objective == "sharpe"
        assert req.max_drawdown == 0.30
        assert req.min_total_trades == 10

    def test_requirements_json_invalid_raises(self):
        """现状记录（B1.1）：解析失败显式 ValueError，不静默回退。"""
        from core.evolution.evaluation import build_requirements
        with pytest.raises(ValueError, match="requirements_json 解析失败"):
            build_requirements({"requirements_json": "{not-json"})

    def test_legacy_columns_fallback(self):
        from core.evolution.evaluation import build_requirements
        req = build_requirements({
            "min_annual_return": 0.15, "max_drawdown_limit": 0.25,
        })
        assert req.objective == "sharpe"
        assert req.min_annual_return == 0.15
        assert req.max_drawdown == 0.25

    def test_legacy_columns_defaults(self):
        from core.evolution.evaluation import build_requirements
        req = build_requirements({})
        assert req.min_annual_return == 0.0
        assert req.max_drawdown == 0.30


class TestEvaluateDnaFeature:
    """evaluation.py 特征锚：evaluate_dna 降级与主路径。"""

    @staticmethod
    def _task_row(**extra):
        row = {
            "symbol": "TESTUSDT",
            "timeframe": "4h",
            "task_id": "t1",
        }
        row.update(extra)
        return row

    def test_data_not_loadable_returns_zero(self, tmp_path):
        """现状记录：按需加载失败 → score 0 + used_real_data False。"""
        from core.evolution.evaluation import evaluate_dna
        dna = make_dna(symbol="NOEXIST")
        result = evaluate_dna(dna, self._task_row(symbol="NOEXIST"), Path(tmp_path))
        assert result["score"] == 0.0
        assert result["used_real_data"] is False
        assert result["fallback"] is True

    def test_no_entry_signal_zero_not_fallback(self, tmp_path):
        """现状记录：无入场信号 → score 0 但 fallback=False、qualified=False。"""
        from core.evolution.evaluation import evaluate_dna
        # entry_value=999 永不触发 RSI<999 之外的…… RSI 上限 100，
        # 用 entry lt -1（永假）
        dna = make_dna(entry_condition="lt", entry_value=-1)
        df = make_enhanced_df(n=200)
        result = evaluate_dna(
            dna, self._task_row(), Path(tmp_path), enhanced_df=df,
        )
        assert result["score"] == 0.0
        assert result["qualified"] is False
        assert result["fallback"] is False

    def test_main_path_scores_and_overrides(self, tmp_path):
        """主路径：真回测 → fitness 结构 + leverage/direction 强制覆写。"""
        from core.evolution.evaluation import evaluate_dna
        dna = make_dna(leverage=1, direction="long")
        df = make_enhanced_df(n=300)
        result = evaluate_dna(
            dna, self._task_row(), Path(tmp_path),
            leverage=3, direction="short", enhanced_df=df,
        )
        assert result["fallback"] is False
        assert result["used_real_data"] is True
        assert result["data_bars"] == 300
        assert "fitness" in result and "qualified" in result
        assert "raw_metrics" in result
        # 强制覆写现状：调用即改写 individual 的风险基因
        assert dna.risk_genes.leverage == 3
        assert dna.risk_genes.direction == "short"

    def test_on_demand_load_with_tf_pool(self, tmp_path):
        """现状记录：无预载 df 时按需装载；timeframe_pool 多周期一并装载。"""
        from core.data.storage import save_parquet
        from tests.helpers.data_factory import make_ohlcv
        save_parquet(make_ohlcv(n=200, freq="4h"), tmp_path / "TESTUSDT_4h.parquet")
        save_parquet(make_ohlcv(n=60, freq="1d"), tmp_path / "TESTUSDT_1d.parquet")
        from core.evolution.evaluation import evaluate_dna
        dna = make_dna()
        result = evaluate_dna(
            dna,
            self._task_row(timeframe_pool=json.dumps(["4h", "1d"])),
            Path(tmp_path),
        )
        assert result["used_real_data"] is True
        assert result["data_bars"] == 200


class TestEvaluatePopulationFeature:
    """evaluation.py 特征锚：批量评估与回退。"""

    @staticmethod
    def _task_row():
        return {"symbol": "TESTUSDT", "timeframe": "4h", "task_id": "t1"}

    def test_without_data_falls_back_to_individual(self, tmp_path):
        """现状记录：enhanced_df=None → 逐个 evaluate_dna（无数据→全 0）。"""
        from core.evolution.evaluation import evaluate_population
        pop = [make_dna(symbol="NOEXIST") for _ in range(3)]
        scores = evaluate_population(pop, self._task_row(), Path(tmp_path))
        assert scores == [0.0, 0.0, 0.0]

    def test_batch_path_scores_in_order(self, tmp_path):
        from core.evolution.evaluation import evaluate_population
        pop = [make_dna(entry_value=v) for v in (25, 35)]
        df = make_enhanced_df(n=300)
        scores = evaluate_population(pop, self._task_row(), Path(tmp_path), enhanced_df=df)
        assert len(scores) == 2
        assert all(s >= 0.0 for s in scores)
        # 批量路径把诊断挂到 individual（消费方依赖 _eval_diagnostics）
        assert all(hasattr(ind, "_eval_diagnostics") for ind in pop)
        assert pop[0]._eval_diagnostics["used_real_data"] is True
