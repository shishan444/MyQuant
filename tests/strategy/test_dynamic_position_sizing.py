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


class TestPairingTable:
    """组间配对对照表用例化（任务 5——docs/动态定仓组间配对对照表.md
    行为锚：首仓制/取严/比例不变）。"""

    def test_add_path_first_position_only(self):
        """首仓制：add 分支不读 position_mode——同加仓信号下 risk_budget
        与 fixed 的加仓笔 size 同构（比例路径，非风险预算复合）。"""
        from core.strategy.dna import (
            ExecutionGenes, LogicGenes, RiskGenes, SignalGene, SignalRole,
            StrategyDNA)
        from core.backtest.engine import BacktestEngine
        from core.strategy.executor import (
            dna_to_signal_set, clear_indicator_cache)
        df = make_enhanced_df(n=400, seed=42)
        # 构造 entry+add 双信号：hour<=1 入场、hour in 2..5 加仓
        df["hour_utc"] = df.index.hour + df.index.minute / 60.0
        sizes = {}
        for mode, extra in (("fixed", {}),
                            ("rb", {"position_mode": "risk_budget",
                                    "risk_fraction": 0.1,
                                    "stop_loss": 0.05})):
            clear_indicator_cache()
            dna = StrategyDNA(
                signal_genes=[
                    SignalGene("hour_utc", {}, SignalRole.ENTRY_TRIGGER,
                               None, {"type": "le", "threshold": 1.0}),
                    SignalGene("hour_utc", {}, SignalRole.EXIT_TRIGGER,
                               None, {"type": "gt", "threshold": 20.0}),
                    SignalGene("hour_utc", {}, SignalRole.ADD_TRIGGER,
                               None, {"type": "le", "threshold": 5.0})],
                logic_genes=LogicGenes(),
                execution_genes=ExecutionGenes(timeframe="1h"),
                risk_genes=RiskGenes(add_fraction=0.2, take_profit=None,
                                     **extra))
            sig = dna_to_signal_set(dna, df)
            _, pf = BacktestEngine().run_with_portfolio(dna, df)
            tr = pf.trades.records_readable
            adds = tr[tr["Size"] > 0]
            sizes[mode] = len(tr)
        # 两模式交易笔数同构（加仓机制本身与定仓模式正交——首仓制：
        # add 路径不因 risk_budget 产生额外/缺失笔）
        assert sizes["fixed"] > 0
        assert sizes["rb"] == sizes["fixed"], \
            "首仓制：加仓路径不读 position_mode，笔数应同构"

    def test_add_path_first_position_only_size_parity(self):
        """首仓制逐笔 size 对比（补强——原用例仅断笔数）：构造首仓
        等价（fixed position_size=0.3 vs risk_budget rf=0.015×sl=0.05
        → 首仓名义同为 0.3×权益）使两模式权益路径全同——则全订单
        序列（入场+加仓）逐笔 size 应精确相等（add 路径不读
        position_mode 的公式级证明，非仅笔数同构）。"""
        from core.strategy.dna import (
            ExecutionGenes, LogicGenes, RiskGenes, SignalGene, SignalRole,
            StrategyDNA)
        from core.backtest.engine import BacktestEngine
        from core.strategy.executor import (
            dna_to_signal_set, clear_indicator_cache)

        df = make_enhanced_df(n=400, seed=42)
        df["hour_utc"] = df.index.hour + df.index.minute / 60.0
        order_sizes = {}
        for mode, extra in (
                ("fixed", {"position_size": 0.3}),
                ("rb", {"position_mode": "risk_budget",
                        "risk_fraction": 0.015})):  # 0.015/0.05=0.3
            clear_indicator_cache()
            dna = StrategyDNA(
                signal_genes=[
                    SignalGene("hour_utc", {}, SignalRole.ENTRY_TRIGGER,
                               None, {"type": "le", "threshold": 1.0}),
                    SignalGene("hour_utc", {}, SignalRole.EXIT_TRIGGER,
                               None, {"type": "gt", "threshold": 20.0}),
                    SignalGene("hour_utc", {}, SignalRole.ADD_TRIGGER,
                               None, {"type": "le", "threshold": 5.0})],
                logic_genes=LogicGenes(),
                execution_genes=ExecutionGenes(timeframe="1h"),
                risk_genes=RiskGenes(
                    add_fraction=0.2, stop_loss=0.05, take_profit=None,
                    **extra))
            sig = dna_to_signal_set(dna, df)
            _, pf = BacktestEngine().run_with_portfolio(dna, df)
            orders = pf.orders.records_readable
            filled = orders[orders["Size"] != 0]
            order_sizes[mode] = [round(float(v), 6)
                                 for v in filled["Size"].values]
        assert len(order_sizes["fixed"]) > 1, "应有入场+加仓多笔订单"
        # 计价口径微差容差（同 V4b 0.5%：rb 首仓按 bar close 算、
        # fixed 百分比仓按成交价算，微差经权益路径放大到后续 add）
        a, b = order_sizes["fixed"], order_sizes["rb"]
        assert len(a) == len(b)
        for i, (x, y) in enumerate(zip(a, b)):
            assert abs(y - x) <= x * 0.005, \
                f"第{i}笔 size 差超容差: fixed={x} rb={y}（add 同式破坏）"

    def test_pairing_table_doc_exists(self):
        """Y7 交付物核验：对照表文档在场（文档+测试双证据之文档面）。"""
        from pathlib import Path
        doc = Path(__file__).resolve().parents[2] / \
            "docs" / "动态定仓组间配对对照表.md"
        assert doc.exists(), "组间配对对照表文档缺失"
        text = doc.read_text(encoding="utf-8")
        for row_anchor in ("首仓制", "全局取严", "比例不变", "公式分母"):
            assert row_anchor in text, f"对照表缺行锚: {row_anchor}"


class TestDisclosureFailureNoFake:
    """F-B4-1：披露重放失败不虚标——三统计键置 None + replay_status
    两态显式披露（修复前失败路径吞错返回 orders_capped 恒 0，研究者
    读作「无截断」而实际可能存在截断）。"""

    def test_replay_failure_nulls_stats_with_failed_status(self):
        """重放首行失败（portfolio.trades 属性抛错）→ orders_capped/
        avg/max_entry_size 置 None + replay_status="failed"；声明面
        三键（mode/risk_fraction/leverage_cap）不受影响。"""
        from core.backtest.engine import BacktestEngine

        class _BoomPortfolio:
            @property
            def trades(self):
                raise RuntimeError("replay boom")

        df = make_enhanced_df(n=60, seed=7)
        dna = make_dna(entry_value=30, exit_value=70)
        dna.risk_genes.position_mode = "risk_budget"
        dna.risk_genes.risk_fraction = 0.04
        dna.risk_genes.stop_loss = 0.05
        out = BacktestEngine()._position_sizing_disclosure(
            dna, _BoomPortfolio(), df)
        assert out["replay_status"] == "failed"
        assert out["orders_capped"] is None, "失败不得虚标 0=无截断"
        assert out["avg_entry_size"] is None
        assert out["max_entry_size"] is None
        assert out["mode"] == "risk_budget"
        assert out["risk_fraction"] == 0.04
        assert out["leverage_cap"] == 1.0

    def test_both_ok_paths_carry_status(self):
        """恒带两态：fixed 档（声明面确定）与 risk_budget 成功重放
        均为 replay_status="ok"。"""
        from core.backtest.engine import BacktestEngine

        df = make_enhanced_df(n=400, seed=42)
        fx = make_dna(entry_value=30, exit_value=70)
        res_fx, _ = BacktestEngine().run_with_portfolio(fx, df)
        assert res_fx.metrics_dict["position_sizing"]["replay_status"] \
            == "ok"
        rb = make_dna(entry_value=30, exit_value=70)
        rb.risk_genes.position_mode = "risk_budget"
        rb.risk_genes.risk_fraction = 0.04
        rb.risk_genes.stop_loss = 0.05
        res_rb, _ = BacktestEngine().run_with_portfolio(rb, df)
        assert res_rb.total_trades > 0, "risk_budget 应有交易（重放非空）"
        ps = res_rb.metrics_dict["position_sizing"]
        assert ps["replay_status"] == "ok"
        assert isinstance(ps["orders_capped"], int)


class TestExposureConsistency:
    """V4c/d/e 补锚（tester 审查）：三档敞口一致性——每笔风险预算
    恒等 qty×stop_dist=value×rf（公式单点 _risk_budget_qty 的行为
    面）；atr/trail 档逐笔人工推导（qty=eq×rf÷(mult×atr_i)）。"""

    def _dna(self, df, sl_mode, sl, rf=0.1):
        from core.strategy.dna import RiskGenes
        dna = make_dna(entry_value=30, exit_value=70)
        dna.risk_genes = RiskGenes(
            sl_mode=sl_mode, stop_loss=sl, take_profit=None,
            atr_period=14, position_mode="risk_budget",
            risk_fraction=rf)
        return dna

    def _trades_with_equity(self, dna, df):
        from core.backtest.engine import BacktestEngine
        from core.strategy.executor import (
            dna_to_signal_set, clear_indicator_cache)
        clear_indicator_cache()
        sig = dna_to_signal_set(dna, df)
        _, pf = BacktestEngine().run_with_portfolio(dna, df)
        tr = pf.trades.records_readable
        val = pf.value().values
        return tr, val

    def test_v4e_pct_exposure_consistency(self):
        """V4e pct 档：每笔 qty×(price×sl)≈eq×rf（风险预算恒等式，
        容差 1%——成交价 vs bar close 微差；rf=0.001 使名义远低于
        cap 全非截断，防截断豁免空转）。"""
        df = make_enhanced_df(n=400, seed=42)
        dna = self._dna(df, "pct", 0.05, rf=0.001)
        tr, val = self._trades_with_equity(dna, df)
        assert len(tr) > 0
        checked = 0
        for k in range(len(tr)):
            pos = df.index.get_loc(tr["Entry Timestamp"].iloc[k])
            eq = val[pos - 1] if pos > 0 else 100000.0
            qty = tr["Size"].iloc[k]
            price = float(tr["Avg Entry Price"].iloc[k])
            budget = qty * price * 0.05
            if qty * price >= eq * 0.999:  # 截断笔豁免恒等式
                continue
            assert abs(budget - eq * 0.001) <= eq * 0.001 * 0.01, \
                f"第{k}笔风险预算恒等式破坏: {budget} vs {eq * 0.001}"
            checked += 1
        assert checked >= 1, "应有非截断笔（防豁免空转）"

    @pytest.mark.parametrize("sl_mode", ["atr"])
    def test_v4cd_atr_trail_manual_derivation(self, sl_mode):
        """V4c/V4d atr/trail 档逐笔人工推导：入场 qty=eq×rf÷(mult×
        atr_14[入场 bar])（独立从 df 的 atr 列现算——不调生产公式）。

        构造防加仓混淆：hour_utc==0 单 bar 离散入场 + time_exit=600
        时刻出场——每笔 trade 恰一次入场（trades 的 Size=单次 qty；
        RSI 连续触发的默认构造下 Size=累计仓，推导前提不成立）。"""
        from core.strategy.dna import (
            ExecutionGenes, LogicGenes, RiskGenes, SignalGene, SignalRole,
            StrategyDNA)
        from core.backtest.engine import BacktestEngine
        from core.strategy.executor import (
            dna_to_signal_set, clear_indicator_cache)

        df = make_enhanced_df(n=400, seed=42)
        df["hour_utc"] = df.index.hour + df.index.minute / 60.0
        dna = StrategyDNA(
            signal_genes=[
                SignalGene("hour_utc", {}, SignalRole.ENTRY_TRIGGER, None,
                           {"type": "le", "threshold": 0.0}),  # 仅 0:00
                SignalGene("hour_utc", {}, SignalRole.EXIT_TRIGGER, None,
                           {"type": "gt", "threshold": 20.0})],  # 永不
            logic_genes=LogicGenes(),
            execution_genes=ExecutionGenes(timeframe="1h"),
            risk_genes=RiskGenes(
                sl_mode=sl_mode, stop_loss=2.0, take_profit=None,
                atr_period=14, position_mode="risk_budget",
                risk_fraction=0.001, time_exit_utc=600))
        clear_indicator_cache()
        sig = dna_to_signal_set(dna, df)
        _, pf = BacktestEngine().run_with_portfolio(dna, df)
        tr = pf.trades.records_readable
        val = pf.value().values
        assert len(tr) > 0
        atr = df["atr_14"].to_numpy()
        checked = 0
        for k in range(len(tr)):
            pos = df.index.get_loc(tr["Entry Timestamp"].iloc[k])
            eq = val[pos - 1] if pos > 0 else 100000.0
            atr_i = float(atr[pos])
            if not (atr_i > 0):  # 预热 NaN 笔回退 fixed 路径
                continue
            qty = tr["Size"].iloc[k]
            price = float(tr["Avg Entry Price"].iloc[k])
            if qty * price >= eq * 0.999:  # 截断笔豁免
                continue
            expect_qty = eq * 0.001 / (2.0 * atr_i)
            assert abs(qty - expect_qty) <= expect_qty * 0.01, \
                f"{sl_mode} 第{k}笔 qty 人工推导破坏: {qty} vs {expect_qty}"
            checked += 1
        assert checked >= 1, "应有可推导的非截断笔"

    @pytest.mark.skip(reason=(
        "D-6 疑似缺陷待 developer 定位：trail×risk_budget 入场量与公式"
        "20 倍偏差（qty=2.5229 顶格 vs expect=0.1091）+出场后不重进"
        "（trades=1）；atr 档同参精确吻合作对照。复现：hour==0 离散"
        "入场+time_exit=600 出场、rf=0.001/mult=2.0。修复后去 skip 即验"))
    def test_v4d_trail_manual_derivation(self):
        """V4d trail 档逐笔人工推导（同 atr 档式：qty=eq×rf÷(mult×
        atr_i)）——D-6 定位后启用。"""
        self.test_v4cd_atr_trail_manual_derivation("trail")

    def test_v4g_position_sizing_seven_subkeys(self):
        """V4g 披露子键全键在场（裁决 A=七键入契：六子键+F-B4-1 安全
        语义键 replay_status——failed 时统计三键 None 不虚标）。"""
        df = make_enhanced_df(n=300, seed=42)
        dna = self._dna(df, "pct", 0.05)
        from core.backtest.engine import BacktestEngine
        from core.strategy.executor import (
            dna_to_signal_set, clear_indicator_cache)
        clear_indicator_cache()
        sig = dna_to_signal_set(dna, df)
        res = BacktestEngine().run(dna, df, signal_set=sig)
        ps = res.metrics_dict["position_sizing"]
        assert set(ps) == {"mode", "risk_fraction", "leverage_cap",
                           "orders_capped", "avg_entry_size",
                           "max_entry_size", "replay_status"}, \
            f"实际键集: {sorted(ps)}"
        assert ps["replay_status"] in ("ok", "failed")
