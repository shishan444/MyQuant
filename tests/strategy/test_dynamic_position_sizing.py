"""动态定仓两键测试（complete-research-tooling 任务 4）。

V4a 缺省逐字节/V4b pct 档等价容差/V4f 杠杆截断+计数/V4j 五点同步
对拍/V4k 域三例+组合校验/V4i 空头对称。
"""
from __future__ import annotations

import pytest

pytestmark = [pytest.mark.unit]

from tests.helpers.data_factory import make_enhanced_df, make_dna


class TestFivePointSync:
    """V4j 五点同步对拍：validator 常量/dna 字段/schemas 模型/登记键集/
    翻译键集。"""

    def test_v4j_five_points(self):
        from core.strategy.validator import (
            POSITION_MODES, RISK_FRACTION_MAX, RISK_FRACTION_MIN)
        assert POSITION_MODES == ("fixed", "risk_budget")
        assert (RISK_FRACTION_MIN, RISK_FRACTION_MAX) == (0.0, 0.5)
        from core.strategy.dna import RiskGenes
        rg = RiskGenes(position_mode="risk_budget", risk_fraction=0.1)
        d = rg.to_dict()
        assert d["position_mode"] == "risk_budget"
        assert d["risk_fraction"] == 0.1
        assert "position_mode" not in RiskGenes().to_dict(), \
            "缺省不写键（存量序列化逐位不变）"
        from api.schemas import RiskGenesModel
        assert "position_mode" in RiskGenesModel.model_fields
        assert "risk_fraction" in RiskGenesModel.model_fields
        from core.analysis.hypothesis_to_strategy import _D2_RISK_FIELDS
        assert {"position_mode", "risk_fraction"} <= set(_D2_RISK_FIELDS)
        from core.analysis.hypothesis_service import _STRATEGY_PARAM_KEYS
        assert {"position_mode", "risk_fraction"} <= set(
            _STRATEGY_PARAM_KEYS)


class TestDomainAndCombination:
    """V4k 域三例+mode 非法拒+risk_budget 组合校验（登记面）。"""

    def _register(self, tmp_path, db, extra):
        from core.analysis.hypothesis_service import (
            ResearchError, register_hypothesis)
        conds = {"when_conditions": [
            {"subject": {"indicator": "RSI", "params": {"period": 14}},
             "action": "lt", "target": 35}],
            "then_conditions": [
                {"subject": {"indicator": "RSI", "params": {"period": 30}},
                 "action": "gt", "target": 70}]}
        rng = [{"symbol": "BTCUSDT", "timeframe": "4h",
                "start": "2024-01-01T00:00:00Z",
                "end": "2024-02-01T00:00:00Z"}]
        return register_hypothesis(
            db, conditions=conds, statement="s", data_ranges=rng,
            strategy_params=extra)

    def test_v4k_risk_fraction_domain(self, tmp_path):
        from core.persistence.db_ext import init_db_ext
        from core.analysis.executor import ensure_research_tables
        from core.analysis.hypothesis_service import ResearchError
        db = tmp_path / "q.db"
        init_db_ext(db); ensure_research_tables(db)
        # 0 拒 / 0.5 放 / 0.51 拒（0.5 放行组合带齐 stop_loss+mode）
        for bad, field in (((0,), "risk_fraction"), ((0.51,), "risk_fraction")):
            with pytest.raises(ResearchError) as ei:
                self._register(tmp_path, db, {
                    "position_mode": "risk_budget", "risk_fraction": bad[0],
                    "stop_loss": 0.05})
            assert ei.value.detail["field"] == field
        # 0.5 放（risk_fraction 闭区间上界）
        out = self._register(tmp_path, db, {
            "position_mode": "risk_budget", "risk_fraction": 0.5,
            "stop_loss": 0.05})
        assert out["duplicated"] is False

    def test_v4k_mode_illegal_and_combination(self, tmp_path):
        from core.persistence.db_ext import init_db_ext
        from core.analysis.executor import ensure_research_tables
        from core.analysis.hypothesis_service import ResearchError
        db = tmp_path / "q.db"
        init_db_ext(db); ensure_research_tables(db)
        with pytest.raises(ResearchError, match="position_mode"):
            self._register(tmp_path, db, {"position_mode": "bogus"})
        # 组合：risk_budget 缺 risk_fraction 拒
        with pytest.raises(ResearchError, match="risk_fraction"):
            self._register(tmp_path, db, {
                "position_mode": "risk_budget", "stop_loss": 0.05})
        # 组合：risk_budget 缺 stop_loss 拒（公式分母）
        with pytest.raises(ResearchError, match="stop_loss"):
            self._register(tmp_path, db, {
                "position_mode": "risk_budget", "risk_fraction": 0.1})

    def test_validator_face_combination(self):
        """validate_dna 面同语义（直构 DNA 五键防线）。"""
        from core.strategy.validator import validate_dna
        dna = make_dna()
        dna.risk_genes.position_mode = "risk_budget"
        dna.risk_genes.risk_fraction = 0.1
        dna.risk_genes.stop_loss = 0.0  # 缺止损
        res = validate_dna(dna)
        assert not res.is_valid
        assert any("stop_loss" in e for e in res.errors)


class TestEngineBehavior:
    """V4a/V4b/V4f/V4i 引擎行为锚。"""

    def _run(self, dna, df):
        from core.backtest.engine import BacktestEngine
        from core.strategy.executor import (
            dna_to_signal_set, clear_indicator_cache)
        clear_indicator_cache()
        sig = dna_to_signal_set(dna, df)
        res, pf = BacktestEngine().run_with_portfolio(dna, df)
        return res, pf

    def test_v4a_default_byte_identical(self):
        """缺省 fixed：无声明路径与改造前等价（v8 锚同义——补行级双跑
        对照：None 与显式 "fixed" 同 metrics）。"""
        df = make_enhanced_df(n=300, seed=42)
        a = make_dna(entry_value=30, exit_value=70)
        res_a, _ = self._run(a, df)
        b = make_dna(entry_value=30, exit_value=70)
        b.risk_genes.position_mode = "fixed"
        res_b, _ = self._run(b, df)
        assert res_a.total_trades == res_b.total_trades
        assert res_a.metrics_dict["sharpe_ratio"] == \
            res_b.metrics_dict["sharpe_ratio"]
        # position_sizing 披露在场（纯增量）
        assert res_a.metrics_dict["position_sizing"]["mode"] == "fixed"

    def test_v4b_pct_equivalence_tolerance(self):
        """V4b：risk_budget+pct 与 fixed（size=rf/sl）逐笔 size 等价
        （容差断言——权益计价口径微差 0.5% 内；敞口一致性 V4e 同族）。"""
        df = make_enhanced_df(n=400, seed=42)
        rb = make_dna(entry_value=30, exit_value=70)
        rb.risk_genes.position_mode = "risk_budget"
        rb.risk_genes.risk_fraction = 0.04
        rb.risk_genes.stop_loss = 0.05
        rb.risk_genes.take_profit = None
        fx = make_dna(entry_value=30, exit_value=70)
        fx.risk_genes.position_size = 0.8  # = rf/sl
        fx.risk_genes.stop_loss = 0.05
        fx.risk_genes.take_profit = None
        res_rb, pf_rb = self._run(rb, df)
        res_fx, pf_fx = self._run(fx, df)
        assert res_rb.total_trades == res_fx.total_trades > 0
        tr_rb = pf_rb.trades.records_readable["Size"].values
        tr_fx = pf_fx.trades.records_readable["Size"].values
        rel = abs(tr_rb - tr_fx) / tr_fx
        assert (rel < 0.005).all(), \
            f"pct 档等价容差超限: {rel.max()}"

    def test_v4f_leverage_cap_and_count(self):
        """V4f：截断到 min(leverage, max_exposure)×权益 顶格+orders_
        capped 重放计数与截断事实一致。"""
        df = make_enhanced_df(n=400, seed=42)
        dna = make_dna(entry_value=30, exit_value=70)
        dna.risk_genes.position_mode = "risk_budget"
        dna.risk_genes.risk_fraction = 0.5
        dna.risk_genes.stop_loss = 0.05
        dna.risk_genes.take_profit = None
        dna.risk_genes.leverage = 2
        res, pf = self._run(dna, df)
        ps = res.metrics_dict["position_sizing"]
        assert ps["mode"] == "risk_budget"
        assert ps["leverage_cap"] == 1.0, \
            "cap=min(leverage, max_exposure 缺省 1.0)"
        assert ps["orders_capped"] == res.total_trades, \
            "rf=0.5×sl=0.05 名义 10×权益恒截断——每笔都应计数"
        tr = pf.trades.records_readable
        val = pf.value().values
        for k in range(len(tr)):
            pos = df.index.get_loc(tr["Entry Timestamp"].iloc[k])
            vn = val[pos - 1] if pos > 0 else 100000.0
            notional = tr["Size"].iloc[k] * tr["Avg Entry Price"].iloc[k]
            assert notional <= vn * 1.01, \
                f"第{k}笔名义超 cap（截断失效）: {notional} vs {vn}"

    def test_v4i_short_symmetric_same_formula(self):
        """V4i：空头对称同式（距离=|入场−止损|恒正——公式无方向分支；
        空头 risk_budget 跑通且敞口约束同款成立）。"""
        df = make_enhanced_df(n=400, seed=42)
        dna = make_dna(entry_value=70, exit_value=30, direction="short")
        dna.risk_genes.position_mode = "risk_budget"
        dna.risk_genes.risk_fraction = 0.1
        dna.risk_genes.stop_loss = 0.05
        dna.risk_genes.take_profit = None
        res, pf = self._run(dna, df)
        assert res.total_trades > 0, "空头 risk_budget 应有交易"
        ps = res.metrics_dict["position_sizing"]
        assert ps["mode"] == "risk_budget"
        tr = pf.trades.records_readable
        val = pf.value().values
        for k in range(len(tr)):
            pos = df.index.get_loc(tr["Entry Timestamp"].iloc[k])
            vn = val[pos - 1] if pos > 0 else 100000.0
            notional = abs(tr["Size"].iloc[k]) * tr["Avg Entry Price"].iloc[k]
            assert notional <= vn * 1.01, "空头敞口约束同款失效"
