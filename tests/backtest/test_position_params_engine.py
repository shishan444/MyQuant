"""initial_fraction 引擎消费测试（add-position-sizing-params 2.1）。

族 4a/4d：装配分派单点（effective_first_size 第十位）+声明时首笔
保证金数值锚（权益 10 万×0.2→2 万，对照不声明×position_size=0.3
→3 万）+双入口一致锚与绝对值锚（run vs batch_run 单列种群——漏接
任一入口即静默分叉）+risk_budget 档直构防御（忽略 initial_fraction）。

回归锚承载说明：不声明路径「与改造前逐字节一致」由既有
tests/strategy/test_v8_byte_anchor.py 字节锚（基线=改造前 main 实拍
sha）承载——本文件补显式无键与缺省 None 的等价对照（ENG-9 形制）。
"""
from __future__ import annotations

import pandas as pd
import pytest

pytestmark = [pytest.mark.integration]

from core.backtest.engine import BacktestEngine, _per_dna_risk_params
from core.strategy.dna import RiskGenes
from core.strategy.executor import (
    dna_to_signal_set, clear_indicator_cache)
from tests.helpers.data_factory import make_dna, make_enhanced_df

# 首仓名义观测容差：vbt 百分比仓基数=决策时点权益（首笔=init_cash
# 10 万），成交价含滑点 0.05% → 名义相对差 ≈0.1%（沿 V4b 0.5% 先例）
_NOTIONAL_TOL = 0.005


def _run_pair(dna, df):
    """run 单路径与 batch_run 单列种群各跑一遍（双入口对拍底座）。"""
    clear_indicator_cache()
    res, pf = BacktestEngine().run_with_portfolio(dna, df)
    results = BacktestEngine().batch_run([dna], enhanced_df=df)
    return res, pf, results[0]


class TestAssemblyDispatch:
    """装配分派单点：第十位 effective_first_size（物化分派——两构建
    入口的唯一取值源）。"""

    def test_p9_dispatch_declared_wins(self):
        """声明时=initial_fraction、否则=position_size。"""
        df = make_enhanced_df(30)
        assert _per_dna_risk_params(
            RiskGenes(position_size=0.3, initial_fraction=0.2), df)[9] \
            == 0.2
        assert _per_dna_risk_params(
            RiskGenes(position_size=0.3), df)[9] == 0.3

    def test_p9_no_second_judgment_source(self):
        """直构 risk_budget+initial_fraction 时装配仍物化声明值（无
        position_mode 第二判断源）——「忽略」由消费端 risk_budget 分支
        不读 size_pct 的结构承担（行为面见 TestRiskBudgetIgnores）。"""
        df = make_enhanced_df(30)
        rg = RiskGenes(position_mode="risk_budget", risk_fraction=0.04,
                       stop_loss=0.05, initial_fraction=0.2)
        assert _per_dna_risk_params(rg, df)[9] == 0.2


class TestFirstEntrySizing:
    """族 4a/4d：首笔保证金数值锚+双入口一致锚+绝对值锚。"""

    def _first_notional(self, trades_df) -> tuple[float, float, float]:
        row = trades_df.iloc[0]
        size = abs(float(row["Size"]))
        price = float(row["Avg Entry Price"])
        return size, price, size * price

    def test_declared_first_margin_numeric_anchor(self):
        """specs 数值锚：权益 10 万×initial_fraction=0.2→首笔保证金
        （首仓名义）≈2 万。"""
        df = make_enhanced_df(n=400, seed=42)
        dna = make_dna(entry_value=30, exit_value=70)
        dna.risk_genes.position_size = 0.3
        dna.risk_genes.initial_fraction = 0.2
        res, pf, _ = _run_pair(dna, df)
        assert res.total_trades > 0
        _, _, notional = self._first_notional(pf.trades.records_readable)
        assert abs(notional - 100000 * 0.2) <= 100000 * 0.2 * _NOTIONAL_TOL, \
            f"声明 initial_fraction=0.2 首仓名义应≈2 万，实际 {notional}"

    def test_undeclared_uses_position_size_control(self):
        """对照：不声明×position_size=0.3→首仓名义≈3 万（与 2 万数值
        可区分——替换生效的决定性差异）。"""
        df = make_enhanced_df(n=400, seed=42)
        dna = make_dna(entry_value=30, exit_value=70)
        dna.risk_genes.position_size = 0.3
        res, pf, _ = _run_pair(dna, df)
        _, _, notional = self._first_notional(pf.trades.records_readable)
        assert abs(notional - 100000 * 0.3) <= 100000 * 0.3 * _NOTIONAL_TOL, \
            f"不声明首仓名义应≈3 万（position_size 路径），实际 {notional}"

    def test_two_entry_consistency_anchor(self):
        """一致锚：同一声明 DNA 经 run 与 batch_run（单列种群）首笔
        成交价与数量两入口一致——漏改任一入口（装配/批量接线）即红，
        不依赖理论值锚定。"""
        df = make_enhanced_df(n=400, seed=42)
        dna = make_dna(entry_value=30, exit_value=70)
        dna.risk_genes.position_size = 0.3
        dna.risk_genes.initial_fraction = 0.2
        _, pf, batch_res = _run_pair(dna, df)
        s_size, s_price, _ = self._first_notional(
            pf.trades.records_readable)
        b_size, b_price, _ = self._first_notional(batch_res.trades_df)
        assert s_size == b_size, \
            f"首笔数量两入口分叉: run={s_size} batch={b_size}"
        assert s_price == b_price, \
            f"首笔成交价两入口分叉: run={s_price} batch={b_price}"

    def test_two_entry_absolute_value_anchor(self):
        """绝对值锚：两路径首笔保证金均=权益×initial_fraction——补
        「两入口一致地漏接」时一致锚恒绿的盲区（两入口同错时数值锚红）。"""
        df = make_enhanced_df(n=400, seed=42)
        dna = make_dna(entry_value=30, exit_value=70)
        dna.risk_genes.position_size = 0.3
        dna.risk_genes.initial_fraction = 0.2
        _, pf, batch_res = _run_pair(dna, df)
        _, _, s_notional = self._first_notional(
            pf.trades.records_readable)
        _, _, b_notional = self._first_notional(batch_res.trades_df)
        for label, notional in (("run", s_notional), ("batch", b_notional)):
            assert abs(notional - 100000 * 0.2) \
                <= 100000 * 0.2 * _NOTIONAL_TOL, \
                f"{label} 入口首仓名义应≈2 万，实际 {notional}"

    def test_undeclared_explicit_no_key_equivalence(self):
        """缺省 None 与显式无新键 DNA 输出全同（物化=position_size
        精确等价——ENG-9 形制；「与改造前逐字节一致」由 v8 字节锚承载）。"""
        df = make_enhanced_df(n=400, seed=42)
        a = make_dna(entry_value=30, exit_value=70)
        a.risk_genes.position_size = 0.3
        a.risk_genes.initial_fraction = None
        b = make_dna(entry_value=30, exit_value=70)
        b.risk_genes.position_size = 0.3
        res_a, pf_a, _ = _run_pair(a, df)
        res_b, pf_b, _ = _run_pair(b, df)
        pd.testing.assert_series_equal(res_a.equity_curve, res_b.equity_curve)
        assert res_a.total_trades == res_b.total_trades
        assert res_a.metrics_dict == res_b.metrics_dict
        assert pf_a.trades.records_readable["Size"].tolist() == \
            pf_b.trades.records_readable["Size"].tolist()


class TestRiskBudgetIgnores:
    """risk_budget 档不读 initial_fraction（互斥由装载校验承担；直构
    DNA 防御=忽略——消费端 risk_budget 分支不读 size_pct）。"""

    def test_direct_built_dna_ignored(self):
        """直构 risk_budget+initial_fraction（装载校验会拒的组合）直跑
        引擎：首笔走风险预算公式（qty×stop_dist≈eq×rf 恒等式，V4e
        形制），首仓名义≠eq×initial_fraction（忽略的数值面）。"""
        df = make_enhanced_df(n=400, seed=42)
        dna = make_dna(entry_value=30, exit_value=70)
        dna.risk_genes.position_mode = "risk_budget"
        dna.risk_genes.risk_fraction = 0.04
        dna.risk_genes.stop_loss = 0.05
        dna.risk_genes.take_profit = None
        dna.risk_genes.initial_fraction = 0.2  # 直构（validate_dna 拒）
        clear_indicator_cache()
        res, pf = BacktestEngine().run_with_portfolio(dna, df)
        assert res.total_trades > 0
        tr = pf.trades.records_readable
        row = tr.iloc[0]
        size = abs(float(row["Size"]))
        price = float(row["Avg Entry Price"])
        # 风险预算恒等式：qty×(price×sl)≈eq×rf（首笔 eq=10 万）
        budget = size * price * 0.05
        assert abs(budget - 100000 * 0.04) <= 100000 * 0.04 * 0.01, \
            f"首笔应走风险预算公式，恒等式破坏: {budget}"
        # 忽略面：首仓名义与 eq×initial_fraction=2 万显著不同
        notional = size * price
        assert abs(notional - 100000 * 0.2) > 5000, \
            f"risk_budget 档不应消费 initial_fraction，名义 {notional}"


class TestPositioningAssemblyParity:
    """R6 引擎侧扩面（build-position-registry 4.6）：切换后
    _per_dna_risk_params 装配三元组（p[7]/p[8]/p[9]）与
    core.positioning.expand_for_backtest 同参产出逐位一致——装配
    路径与适配器同源证明；并与 design §11 基准表（改造前实拍，
    tester 2026-10-18 独立复证）字面对照——design 锚失效即红。

    四组代表 DNA 与 R6 基准表一一对应：③fixed+rf 声明=全档物化
    关键组（fixed 档也物化 rf——按档位省略即红）。
    """

    @pytest.mark.parametrize(
        ("rg_builder", "baseline"),
        [
            pytest.param(
                lambda: RiskGenes(position_size=0.3),
                (0.0, 0.0, 0.3), id="①fixed 缺省"),
            pytest.param(
                lambda: RiskGenes(position_size=0.3,
                                  initial_fraction=0.2),
                (0.0, 0.0, 0.2), id="②声明 initial_fraction"),
            pytest.param(
                lambda: RiskGenes(position_size=0.3,
                                  risk_fraction=0.01),
                (0.0, 0.01, 0.3), id="③fixed+rf 声明"),
            pytest.param(
                lambda: RiskGenes(position_mode="risk_budget",
                                  position_size=0.3, risk_fraction=0.01),
                (1.0, 0.01, 0.3), id="④risk_budget"),
        ])
    def test_engine_triple_equals_adapter_and_baseline(
            self, rg_builder, baseline):
        """引擎装配 p[7]/p[8]/p[9]==expand_for_backtest 三元组（同源
        证明）==design §11 基准表字面值（行为零变化锚）。"""
        from core.positioning import expand_for_backtest

        rg = rg_builder()
        df = make_enhanced_df(30)
        p = _per_dna_risk_params(rg, df)
        exp = expand_for_backtest(rg.position_mode, rg.position_size,
                                  rg.initial_fraction, rg.risk_fraction)
        engine_triple = (p[7], p[8], p[9])
        assert engine_triple == (
            exp.position_mode_code, exp.risk_fraction,
            exp.effective_first_size), "引擎装配与适配器分叉"
        assert engine_triple == baseline, (
            f"装配三元组 {engine_triple} 偏离 design 基准 {baseline}")
