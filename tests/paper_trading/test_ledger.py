"""记账器测试（rebuild-paper-trading 任务 1.4）。

族 1 清算数值锚（spec 主锚 9.6386%+边界两侧价格格点+第二锚 33.07%
+空头对称手算锚+lev=1 不判+清算成交价=p_liq+档位边界+inspect 无
maintenance_threshold 消费）；定仓分派（fixed 逐字节/risk_budget
恒等式+cap 截断+残缺组合 (0,0) 回退+ATR NaN 回退+atr 档距离锚+
权益浮动）；状态机（add 并仓重锚/reduce 比例释放/close 结算）；
族 2 kernel 锚（apply_funding 金额式，rate 正负两向）。
"""
from __future__ import annotations

import inspect

import pytest

pytestmark = [pytest.mark.unit]

from core.market_model import maintenance_margin_rate
from core.paper_trading.kernel.fills import MarketFill
from core.paper_trading.kernel.ledger import (
    Ledger,
    funding_amount,
    liquidation_price,
)
from core.paper_trading.kernel.types import Intent, RiskParams

_ZERO_COST = MarketFill(fee_rate=0.0, slippage_rate=0.0)


def make_params(**overrides) -> RiskParams:
    """物化参数构造（缺省=pct 多头无杠杆基线；数值锚用 fee/slip=0）。"""
    base = dict(
        stop_loss=0.05, take_profit=None, position_size=1.0, leverage=1,
        direction="long", sl_mode="pct", atr_period=14)
    base.update(overrides)
    return RiskParams(**base)


def _bar(price: float, seq: int = 0, atr=None):
    from core.paper_trading.kernel.types import ClosedBar
    return ClosedBar(
        seq=seq, open=price, high=price, low=price, close=price, atr=atr)


def _open(ledger: Ledger, price: float, side: str = "long", seq: int = 0,
          entry_atr=None):
    return ledger.execute(
        Intent(kind="open", direction=side), _bar(price, seq, entry_atr),
        _ZERO_COST, entry_atr=entry_atr)


class TestLiquidationAnchors:
    """族 1 清算数值锚（market_model 仓位口径两式）。"""

    @staticmethod
    def _long_context(position_size=1.0):
        """fee=0/slip=0、initial_cash=5_000、lev=10、entry=1.0 开仓后：
        qty=5_000×10、margin=5_000、balance=0（主锚链路）。"""
        params = make_params(position_size=position_size, leverage=10)
        ledger = Ledger(5000.0, params)
        fill = _open(ledger, 1.0)
        assert fill.fee == 0.0
        return ledger, ledger.position

    def test_main_anchor_tier_boundary_inclusive(self):
        """主锚：名义 50_000 恰落第一档（边界含，mmr=0.004）→
        p_liq=45_000/49_800=0.9036144，反向 9.6386%。"""
        ledger, pos = self._long_context()
        assert pos.quantity == pytest.approx(50_000.0)
        assert pos.margin == pytest.approx(5_000.0)
        assert ledger.balance == pytest.approx(0.0)
        p_liq = liquidation_price(
            "long", pos.entry_price, pos.quantity, pos.margin,
            ledger.balance, mark_price=1.0)
        assert p_liq == pytest.approx(0.9036144, rel=1e-6)
        assert (1.0 - p_liq) == pytest.approx(0.096386, rel=1e-4)

    def test_main_anchor_boundary_grid(self):
        """边界两侧价格格点：p=0.904（反向 9.6%）不触发、
        p=0.903（反向 9.7%）触发。"""
        from core.paper_trading.kernel.intents import SignalInterpreter
        from core.paper_trading.kernel.types import BarSignals, ClosedBar
        ledger, pos = self._long_context()
        account = ledger.snapshot(mark_price=1.0)

        def _intents_at(low: float):
            bar = ClosedBar(
                seq=5, open=low, high=low, low=low, close=low)
            return SignalInterpreter().interpret(
                bar, BarSignals(), account, ledger.params)

        not_hit = _intents_at(0.904)
        assert not_hit == [] or not_hit[0].kind != "exit_liquidation"
        hit = _intents_at(0.903)
        assert hit[0].kind == "exit_liquidation"
        assert hit[0].trigger_price == pytest.approx(0.9036144, rel=1e-6)

    def test_second_anchor_small_position_depth(self):
        """第二锚：ps=0.3（margin=1_500、名义 15_000、balance=3_500）→
        p_liq=10_000/14_940=0.669347，反向 33.07%。"""
        ledger, pos = self._long_context(position_size=0.3)
        assert pos.quantity == pytest.approx(15_000.0)
        assert pos.margin == pytest.approx(1_500.0)
        assert ledger.balance == pytest.approx(3_500.0)
        p_liq = liquidation_price(
            "long", pos.entry_price, pos.quantity, pos.margin,
            ledger.balance, mark_price=1.0)
        assert p_liq == pytest.approx(10_000.0 / 14_940.0, rel=1e-6)
        assert (1.0 - p_liq) == pytest.approx(0.3307, rel=1e-3)

    def test_short_symmetric_anchor(self):
        """空头对称式手算锚：p_liq=(balance+margin+qty×entry)/(qty×
        (1+mmr))=(0+5_000+50_000)/50_200=1.0956175。"""
        params = make_params(position_size=1.0, leverage=10)
        ledger = Ledger(5000.0, params)
        _open(ledger, 1.0, side="short")
        pos = ledger.position
        p_liq = liquidation_price(
            "short", pos.entry_price, pos.quantity, pos.margin,
            ledger.balance, mark_price=1.0)
        assert p_liq == pytest.approx(55_000.0 / 50_200.0, rel=1e-6)
        assert p_liq > 1.0  # 空头升破触发

    def test_liquidation_fill_price_is_p_liq(self):
        """清算成交价=p_liq+kind=exit_liquidation（经成交唯一通道）。"""
        from core.paper_trading.kernel.intents import SignalInterpreter
        from core.paper_trading.kernel.types import BarSignals, ClosedBar
        ledger, _ = self._long_context()
        account = ledger.snapshot(mark_price=1.0)
        bar = ClosedBar(seq=5, open=0.903, high=0.903, low=0.903,
                        close=0.903)
        intents = SignalInterpreter().interpret(
            bar, BarSignals(), account, ledger.params)
        fill = ledger.execute(intents[0], bar, _ZERO_COST)
        assert fill.intent.kind == "exit_liquidation"
        assert fill.price == pytest.approx(0.9036144, rel=1e-6)
        assert ledger.position is None

    def test_mmr_tier_boundary_selection(self):
        """BinanceMmrTiers 档位边界 50_000/50_001 选档（边界含）。"""
        assert maintenance_margin_rate(50_000) == 0.004
        assert maintenance_margin_rate(50_001) == 0.005

    def test_ledger_has_no_maintenance_threshold_consumption(self):
        """inspect 守护：ledger 源码无 maintenance_threshold 消费
        （沿 tests/backtest/test_liquidation_threshold.py 先例——该
        函数为回测无杠杆曲线的等效换算形制，仓位口径禁用）。"""
        import core.paper_trading.kernel.ledger as ledger_mod
        src = inspect.getsource(ledger_mod)
        assert "maintenance_threshold" not in src


class TestPositionSizing:
    """open 定仓分派（14 参数消费：fixed/risk_budget 两档）。"""

    def test_fixed_margin_is_equity_times_position_size(self):
        """fixed 档逐字节不变：margin=equity×position_size（权益口径）。"""
        params = make_params(position_size=0.25, leverage=4)
        ledger = Ledger(10_000.0, params)
        fill = _open(ledger, 100.0)
        assert fill.quantity == pytest.approx(
            10_000.0 * 0.25 * 4 / 100.0)  # qty=margin×lev/price
        assert ledger.position.margin == pytest.approx(10_000.0 * 0.25)
        assert ledger.balance == pytest.approx(10_000.0 - 2_500.0)

    def test_risk_budget_identity_untruncated(self):
        """未截断恒等式：qty×stop_dist=equity×risk_fraction
        （手算锚：10 万×0.01=1_000；sl=2%、价 100 → qty=500）。"""
        params = make_params(
            position_mode="risk_budget", risk_fraction=0.01,
            stop_loss=0.02)
        ledger = Ledger(100_000.0, params)
        fill = _open(ledger, 100.0)
        assert fill.quantity == pytest.approx(500.0)
        assert fill.capped is False
        stop_dist = 100.0 * 0.02
        assert fill.quantity * stop_dist == pytest.approx(
            100_000.0 * 0.01)

    def test_risk_budget_cap_truncation_with_marker(self):
        """cap=equity×min(leverage,max_exposure) 截断+capped 标记。

        rf=0.5、sl=1%、价 100：名义 5_000_000 → cap=100_000×min(10,1)
        =100_000 → qty=1_000、capped=true。
        """
        params = make_params(
            position_mode="risk_budget", risk_fraction=0.5,
            stop_loss=0.01, leverage=10, max_exposure=1.0)
        ledger = Ledger(100_000.0, params)
        fill = _open(ledger, 100.0)
        assert fill.quantity == pytest.approx(1_000.0)
        assert fill.capped is True
        assert fill.quantity * 100.0 == pytest.approx(100_000.0)

    def test_risk_budget_atr_distance_anchor(self):
        """atr 档距离=sl_mult×该 bar ATR：rf=0.02、sl=2.0、ATR=2.5
        → dist=5 → qty=100_000×0.02/5=400。"""
        params = make_params(
            position_mode="risk_budget", risk_fraction=0.02,
            stop_loss=2.0, sl_mode="atr")
        ledger = Ledger(100_000.0, params)
        fill = _open(ledger, 100.0, entry_atr=2.5)
        assert fill.quantity == pytest.approx(400.0)

    def test_residual_combination_falls_back_fixed_without_raise(self):
        """直构残缺组合（绕过装载校验）走公式单点防御 (0,0)→fixed
        定仓且不抛异常（与回测引擎兜底一致；不另设抛异常分支）。"""
        for overrides in (
                dict(position_mode="risk_budget", risk_fraction=0.0,
                     stop_loss=0.05),  # 缺 risk_fraction（物化 0.0）
                dict(position_mode="risk_budget", risk_fraction=0.01,
                     stop_loss=0.0),  # stop_loss≤0
        ):
            params = make_params(position_size=0.3, **overrides)
            ledger = Ledger(10_000.0, params)
            fill = _open(ledger, 100.0)
            assert fill.quantity == pytest.approx(
                10_000.0 * 0.3 / 100.0), f"残缺组合 {overrides} 应回退 fixed"

    def test_entry_bar_atr_nan_falls_back_fixed(self):
        """运行中入场 bar ATR NaN=该次定仓回退 fixed（对齐回测引擎
        engine.py:786-795 NaN 防御——受控等价覆盖此路径）。"""
        params = make_params(
            position_mode="risk_budget", risk_fraction=0.01,
            stop_loss=2.0, sl_mode="atr", position_size=0.3)
        ledger = Ledger(10_000.0, params)
        fill = _open(ledger, 100.0, entry_atr=float("nan"))
        assert fill.quantity == pytest.approx(10_000.0 * 0.3 / 100.0)

    def test_budget_constant_amount_floats_with_equity(self):
        """权益变动后第二笔 qty 随权益浮动（预算恒定金额浮动语义）。"""
        params = make_params(
            position_mode="risk_budget", risk_fraction=0.01,
            stop_loss=0.02)
        fill_a = _open(Ledger(100_000.0, params), 100.0)
        fill_b = _open(Ledger(200_000.0, params), 100.0)
        assert fill_a.quantity == pytest.approx(500.0)
        assert fill_b.quantity == pytest.approx(1_000.0)
        # 两笔的风险预算金额恒等：qty×stop_dist 都=equity×rf
        assert fill_a.quantity * 2.0 == pytest.approx(1_000.0)
        assert fill_b.quantity * 2.0 == pytest.approx(2_000.0)


class TestStateMachine:
    """add 并仓重锚/reduce 按比例释放/close 结算。"""

    @staticmethod
    def _ledger_with_position():
        """10_000 资金、ps=0.1、lev=10 价 100 开仓：qty=100、
        margin=1_000、balance=9_000。"""
        params = make_params(position_size=0.1, leverage=10)
        ledger = Ledger(10_000.0, params)
        _open(ledger, 100.0)
        return ledger

    def test_add_reanchors_entry_and_accumulates(self):
        """加仓：add_margin=add_fraction×当时总权益（逐仓口径含划出
        的保证金与浮盈）；entry 加权重锚、margin/quantity 累加。"""
        params = make_params(position_size=0.1, leverage=10,
                             add_fraction=0.5)
        ledger = Ledger(10_000.0, params)
        _open(ledger, 100.0)  # margin=1_000、qty=100、balance=9_000
        fill = ledger.execute(
            Intent(kind="add", direction="long"), _bar(110.0, seq=1),
            _ZERO_COST)
        # 加仓时总权益=balance 9_000+margin 1_000+浮盈 100×(110−100)
        # =11_000 → add_margin=11_000×0.5=5_500
        assert fill.quantity == pytest.approx(5_500.0 * 10 / 110.0)
        pos = ledger.position
        assert pos.margin == pytest.approx(1_000.0 + 5_500.0)
        assert pos.entry_price == pytest.approx(
            (100.0 * 100.0 + 110.0 * (5_500.0 * 10 / 110.0))
            / (100.0 + 5_500.0 * 10 / 110.0))
        assert ledger.balance == pytest.approx(9_000.0 - 5_500.0)

    def test_add_keeps_trail_peak_continuous(self):
        """加仓不重置 trail 峰值（持仓期语义）。"""
        params = make_params(position_size=0.1, leverage=10,
                             add_fraction=0.1, sl_mode="trail")
        ledger = Ledger(10_000.0, params)
        _open(ledger, 100.0)
        ledger.update_trail_peak(_bar(120.0, seq=1))  # 峰值 120
        ledger.execute(
            Intent(kind="add", direction="long"), _bar(110.0, seq=2),
            _ZERO_COST)
        assert ledger.position.trail_peak == pytest.approx(120.0)

    def test_reduce_releases_margin_proportionally(self):
        """减仓：减量=quantity×exit_fraction；按比例释放保证金+
        部分已实现盈亏。"""
        ledger = self._ledger_with_position()  # qty=100、margin=1_000
        fill = ledger.execute(
            Intent(kind="reduce", direction="long"), _bar(120.0, seq=1),
            _ZERO_COST)
        assert fill.quantity == pytest.approx(50.0)  # 缺省 0.5
        assert fill.realized_pnl == pytest.approx(50.0 * 20.0)
        pos = ledger.position
        assert pos.quantity == pytest.approx(50.0)
        assert pos.margin == pytest.approx(500.0)
        # balance=9_000+释放 500+pnl 1_000
        assert ledger.balance == pytest.approx(10_500.0)

    def test_reduce_does_not_reset_hold_count(self):
        """减仓不重置持有计数（entry_bar_seq 不动）。"""
        ledger = self._ledger_with_position()
        ledger.execute(
            Intent(kind="reduce", direction="long"), _bar(100.0, seq=3),
            _ZERO_COST)
        snap = ledger.snapshot(mark_price=100.0, bar_seq=3)
        assert snap.bars_held == 4  # 入场 seq=0 计第 1 根

    def test_close_settles_pnl(self):
        """全平：pnl=方向×quantity×(price−entry)；balance+=margin+
        pnl−fee。"""
        ledger = self._ledger_with_position()
        fill = ledger.execute(
            Intent(kind="close", direction="long"), _bar(130.0, seq=1),
            _ZERO_COST)
        assert fill.realized_pnl == pytest.approx(100.0 * 30.0)
        assert ledger.position is None
        assert ledger.balance == pytest.approx(
            9_000.0 + 1_000.0 + 3_000.0)
        assert ledger.closed_trades == 1

    def test_close_short_direction_sign(self):
        """空头全平：pnl=−quantity×(price−entry)。"""
        params = make_params(position_size=0.1, leverage=10)
        ledger = Ledger(10_000.0, params)
        _open(ledger, 100.0, side="short")
        fill = ledger.execute(
            Intent(kind="close", direction="short"), _bar(90.0, seq=1),
            _ZERO_COST)
        assert fill.realized_pnl == pytest.approx(100.0 * 10.0)
        assert ledger.balance == pytest.approx(11_000.0)

    def test_fee_charged_on_notional(self):
        """开仓费=fee×quantity×price（名义计费）。"""
        model = MarketFill(fee_rate=0.001, slippage_rate=0.0)
        params = make_params(position_size=0.1, leverage=10)
        ledger = Ledger(10_000.0, params)
        fill = ledger.execute(
            Intent(kind="open", direction="long"), _bar(100.0), model)
        assert fill.fee == pytest.approx(0.001 * fill.quantity * 100.0)
        assert ledger.balance == pytest.approx(
            10_000.0 - 1_000.0 - fill.fee)


class TestSingleSourceGuard:
    """定仓公式单源守护（spec R3：MUST NOT 在新域复制公式——inspect
    静态锚；行为守护（受控等价两侧等值+恒等式手算锚）之外的防回潮层）。
    """

    def test_risk_budget_qty_single_source_consumption(self):
        """单源三断言：import 在场+消费点在场+params.risk_fraction
        唯一出现于调用实参（内联公式复制必产生第二处即红）。"""
        import core.paper_trading.kernel.ledger as ledger_mod

        src = inspect.getsource(ledger_mod)
        assert "from core.backtest.engine import risk_budget_qty" in src
        assert "risk_budget_qty(" in src, "消费点须在场"
        # params.risk_fraction 唯一出现=risk_budget_qty 调用实参行
        # （docstring 为无前缀文字不计）——本地复制公式必再出现
        assert src.count("params.risk_fraction") == 1, (
            "risk_fraction 参与处须唯一（单源调用实参）——出现第二处"
            "即疑定仓公式体复制")


class TestFunding:
    """族 2 kernel 锚：apply_funding 金额=quantity×结算价×rate×方向符号。"""

    @staticmethod
    def _ledger(side="long"):
        params = make_params(position_size=0.1, leverage=10)
        ledger = Ledger(10_000.0, params)
        _open(ledger, 100.0, side=side)  # qty=100、balance=9_000
        return ledger

    def test_long_pays_positive_rate(self):
        ledger = self._ledger("long")
        amount = ledger.apply_funding(rate=0.0001, settle_price=110.0)
        assert amount == pytest.approx(100.0 * 110.0 * 0.0001)
        assert ledger.balance == pytest.approx(9_000.0 - 1.1)
        assert ledger.total_funding == pytest.approx(1.1)

    def test_short_receives_positive_rate(self):
        ledger = self._ledger("short")
        amount = ledger.apply_funding(rate=0.0001, settle_price=110.0)
        assert amount == pytest.approx(-1.1)
        assert ledger.balance == pytest.approx(9_000.0 + 1.1)

    def test_long_receives_negative_rate(self):
        """rate<0 反向：多头收。"""
        ledger = self._ledger("long")
        amount = ledger.apply_funding(rate=-0.0001, settle_price=110.0)
        assert amount == pytest.approx(-1.1)
        assert ledger.balance == pytest.approx(9_000.0 + 1.1)

    def test_funding_charged_regardless_of_leverage(self):
        """资金费与杠杆无关（对质六裁决）：leverage=1 持仓同样扣。"""
        params = make_params(position_size=1.0, leverage=1)
        ledger = Ledger(10_000.0, params)
        _open(ledger, 100.0)  # qty=100、balance=0
        amount = ledger.apply_funding(rate=0.0001, settle_price=100.0)
        assert amount == pytest.approx(1.0)
        assert ledger.balance == pytest.approx(-1.0)

    def test_no_position_zero_funding(self):
        ledger = Ledger(10_000.0, make_params())
        assert ledger.apply_funding(rate=0.0001, settle_price=100.0) == 0.0
        assert ledger.balance == 10_000.0


class TestFundingAmountSingleSource:
    """B1 收敛锚：funding_amount 纯函数单点（正常结算与快照重算
    （补旧账/终态回补）共用——runtime 侧改接后的引用面）。"""

    @pytest.mark.parametrize("signed_qty,rate,expected", [
        (100.0, 0.0001, 1.1),    # 多头 rate>0：付
        (-100.0, 0.0001, -1.1),  # 空头 rate>0：收
        (100.0, -0.0001, -1.1),  # 多头 rate<0：收
        (-100.0, -0.0001, 1.1),  # 空头 rate<0：付
    ])
    def test_signed_quantity_and_rate_signs(self, signed_qty, rate,
                                            expected):
        assert funding_amount(signed_qty, 110.0, rate) \
            == pytest.approx(expected)

    def test_apply_funding_consistent_with_single_point(self):
        """apply_funding 与纯函数单点一致（同参数同值）。"""
        ledger = TestFunding._ledger("long")
        amount = ledger.apply_funding(rate=0.0001, settle_price=110.0)
        assert amount == pytest.approx(funding_amount(100.0, 110.0, 0.0001))


class TestStopTakeDisclosureAnchors:
    """A2 回归锚：atr 模式 TP 披露值与判定权威同式（ATR 倍数域）。

    此前 _initial_stop 的 is_tp 恒走 pct 域，atr 模式 TP 披露
    =entry×(1+tp) 与实际判定线 entry+tp×ATR 不一致（纯披露面缺陷，
    review A2 修正）。
    """

    def test_atr_mode_tp_disclosure_uses_atr_multiple(self):
        """atr 模式：sl=100−2×2.5=95、tp=100+4×2.5=110（倍数域）。"""
        params = make_params(sl_mode="atr", stop_loss=2.0,
                             take_profit=4.0)
        ledger = Ledger(10_000.0, params)
        _open(ledger, 100.0, entry_atr=2.5)
        pos = ledger.position
        assert pos.sl_price == pytest.approx(95.0)
        assert pos.tp_price == pytest.approx(110.0)

    def test_pct_mode_tp_disclosure_uses_ratio(self):
        """pct 模式对照：tp=100×1.10=110（比例域锚入场价）。"""
        params = make_params(sl_mode="pct", stop_loss=0.05,
                             take_profit=0.10)
        ledger = Ledger(10_000.0, params)
        _open(ledger, 100.0)
        pos = ledger.position
        assert pos.sl_price == pytest.approx(95.0)
        assert pos.tp_price == pytest.approx(110.0)


class TestIntentPreconditionGuards:
    """意图前置条件守卫与全平计数（闸门盲区消解）。"""

    def test_open_with_existing_position_returns_none(self):
        """持仓中 open 意图 → None（重复开仓防御——不加仓不炸）。"""
        params = make_params(position_size=0.1, leverage=10)
        ledger = Ledger(10_000.0, params)
        _open(ledger, 100.0)
        fill = ledger.execute(
            Intent(kind="open", direction="long"), _bar(105.0, seq=1),
            _ZERO_COST)
        assert fill is None
        assert ledger.position.quantity == pytest.approx(100.0)

    def test_reduce_without_position_returns_none(self):
        """无持仓 reduce 意图 → None（无仓可减防御）。"""
        ledger = Ledger(10_000.0, make_params())
        fill = ledger.execute(
            Intent(kind="reduce", direction="long"), _bar(100.0),
            _ZERO_COST)
        assert fill is None

    def test_full_reduce_closes_position_and_counts_trade(self):
        """exit_fraction=1.0 全量减仓 → 清仓+计一次全平交易。"""
        params = make_params(position_size=0.1, leverage=10,
                             exit_fraction=1.0)
        ledger = Ledger(10_000.0, params)
        _open(ledger, 100.0)  # qty=100
        fill = ledger.execute(
            Intent(kind="reduce", direction="long"), _bar(120.0, seq=1),
            _ZERO_COST)
        assert fill is not None
        assert fill.quantity == pytest.approx(100.0)
        assert ledger.position is None  # quantity<=0 → 清仓
        assert ledger.closed_trades == 1


class TestFundingPaidLifecycle:
    """持仓级资金费归集生命周期（add-position-sizing-params 族 4）。

    funding_paid=当前持仓判定输入（金额防线的资金费分量）；
    total_funding=账户累计披露——两口径并存。
    """

    def test_open_initializes_zero(self):
        """开仓归零：新 Position.funding_paid=0.0。"""
        params = make_params(position_size=0.1, leverage=10)
        ledger = Ledger(10_000.0, params)
        _open(ledger, 100.0)
        assert ledger.position.funding_paid == 0.0

    def test_settlement_accumulates_signed(self):
        """结算累加带符号：付出为正（加深浮亏）、收到为负（抵减）。"""
        params = make_params(position_size=0.1, leverage=10)
        ledger = Ledger(10_000.0, params)
        _open(ledger, 100.0)  # qty=100
        paid = ledger.apply_funding(rate=0.0001, settle_price=100.0)
        assert paid == pytest.approx(1.0)
        assert ledger.position.funding_paid == pytest.approx(1.0)
        received = ledger.apply_funding(rate=-0.0001, settle_price=100.0)
        assert received == pytest.approx(-1.0)
        assert ledger.position.funding_paid == pytest.approx(0.0)

    def test_accumulated_funding_flows_into_snapshot(self):
        """快照透传：snapshot().position.funding_paid 与内部态同值
        （解释器判定输入链路）。"""
        params = make_params(position_size=0.1, leverage=10)
        ledger = Ledger(10_000.0, params)
        _open(ledger, 100.0)
        ledger.apply_funding(rate=0.0001, settle_price=100.0)
        ledger.apply_funding(rate=0.0001, settle_price=110.0)  # +1.1
        snap = ledger.snapshot(mark_price=100.0)
        assert snap.position.funding_paid == pytest.approx(2.1)

    def test_add_keeps_accumulation_continuous(self):
        """并仓延续：加仓不重置 funding_paid（同一 Position 对象——
        Q7 并仓语义：合并持仓的总浮亏含并仓后累计资金费）。"""
        params = make_params(position_size=0.1, leverage=10,
                             add_fraction=0.1)
        ledger = Ledger(10_000.0, params)
        _open(ledger, 100.0)
        ledger.apply_funding(rate=0.0001, settle_price=100.0)  # 1.0
        pos_before = ledger.position
        ledger.execute(
            Intent(kind="add", direction="long"), _bar(110.0, seq=1),
            _ZERO_COST)
        assert ledger.position is pos_before  # 同一对象延续
        assert ledger.position.funding_paid == pytest.approx(1.0)
        ledger.apply_funding(rate=0.0001, settle_price=110.0)
        assert ledger.position.funding_paid > 1.0  # 并仓后继续累计

    def test_close_discards_and_total_funding_persists(self):
        """全平丢弃持仓级口径、账户级 total_funding 并存保留。"""
        params = make_params(position_size=0.1, leverage=10)
        ledger = Ledger(10_000.0, params)
        _open(ledger, 100.0)
        ledger.apply_funding(rate=0.0001, settle_price=100.0)  # 1.0
        ledger.execute(
            Intent(kind="close", direction="long"), _bar(100.0, seq=1),
            _ZERO_COST)
        assert ledger.position is None  # 持仓级口径随对象丢弃
        assert ledger.total_funding == pytest.approx(1.0)  # 账户级保留

    def test_reentry_resumes_from_zero(self):
        """全平→再入场：新持仓 funding_paid 从零起（旧段不残留），
        账户级 total_funding 跨段连续累计。"""
        params = make_params(position_size=0.1, leverage=10)
        ledger = Ledger(10_000.0, params)
        _open(ledger, 100.0)
        ledger.apply_funding(rate=0.0001, settle_price=100.0)  # 1.0
        ledger.execute(
            Intent(kind="close", direction="long"), _bar(100.0, seq=1),
            _ZERO_COST)
        _open(ledger, 100.0, seq=2)
        assert ledger.position.funding_paid == 0.0
        assert ledger.total_funding == pytest.approx(1.0)

    def test_no_position_settlement_no_accumulation(self):
        """空仓结算零归集（apply_funding 空仓分支 0.0）。"""
        ledger = Ledger(10_000.0, make_params())
        assert ledger.apply_funding(rate=0.0001, settle_price=100.0) == 0.0
        assert ledger.total_funding == 0.0

    @pytest.mark.parametrize("rate", [0.0001, -0.0001])
    def test_apply_and_record_dual_path_same_effect(self, rate):
        """双路径同式断言（对质①裁决测试锁）：apply_funding 与
        funding_amount+record_funding 两路径对 funding_paid/balance/
        total_funding 三键同值——funding_paid 累加单源在 record_funding，
        executor 与 apply_funding 皆为消费方无第二写点。"""
        def _fresh_ledger():
            params = make_params(position_size=0.1, leverage=10)
            ledger = Ledger(10_000.0, params)
            _open(ledger, 100.0)  # qty=100
            return ledger

        via_apply = _fresh_ledger()
        amount = via_apply.apply_funding(rate=rate, settle_price=110.0)

        via_record = _fresh_ledger()
        via_record.record_funding(
            funding_amount(100.0, 110.0, rate))

        assert amount == pytest.approx(funding_amount(100.0, 110.0, rate))
        assert via_apply.position.funding_paid \
            == pytest.approx(via_record.position.funding_paid)
        assert via_apply.balance == pytest.approx(via_record.balance)
        assert via_apply.total_funding == pytest.approx(
            via_record.total_funding)

    def test_record_funding_without_position_only_two_keys(self):
        """无持仓经 record_funding 落账：只动 balance/total_funding 两键
        （funding_paid 无归集载体——空仓语义）。"""
        ledger = Ledger(10_000.0, make_params())
        ledger.record_funding(1.5)
        assert ledger.position is None
        assert ledger.balance == pytest.approx(10_000.0 - 1.5)
        assert ledger.total_funding == pytest.approx(1.5)


class TestInitialFractionSizing:
    """首笔定仓分派（add-position-sizing-params 族 4）。

    数值锚（specs）：权益 10 万、声明 initial_fraction=0.2 → 首笔
    margin=2 万；对照不声明×position_size=0.3=3 万。分派式与引擎
    装配 effective_first_size 同式（materialize 对照断言锁）。
    """

    def test_declared_initial_fraction_sizes_first_entry(self):
        """声明 0.2：首笔 margin=100_000×0.2=20_000（position_size
        0.3 被替换不消费）。"""
        params = make_params(
            initial_fraction=0.2, position_size=0.3, leverage=1)
        ledger = Ledger(100_000.0, params)
        fill = _open(ledger, 100.0)
        assert ledger.position.margin == pytest.approx(20_000.0)
        assert fill.quantity == pytest.approx(200.0)

    def test_undeclared_uses_position_size_baseline(self):
        """不声明对照：margin=100_000×0.3=30_000（存量路径逐位不变）。"""
        params = make_params(position_size=0.3, leverage=1)
        ledger = Ledger(100_000.0, params)
        fill = _open(ledger, 100.0)
        assert ledger.position.margin == pytest.approx(30_000.0)
        assert fill.quantity == pytest.approx(300.0)

    def test_add_behavior_identical_when_first_entry_equal(self):
        """加仓行为与不声明时逐位一致：首仓经两路径等价（声明
        initial_fraction=0.2 vs 不声明 position_size=0.2 殊途同归）
        后，同序列加仓成交/margin/balance 逐位一致——initial_fraction
        消费点仅首笔，加仓走 add_fraction 既有公式零变化。"""
        declared = Ledger(
            100_000.0, make_params(
                initial_fraction=0.2, position_size=0.3, leverage=1,
                add_fraction=0.1))
        undeclared = Ledger(
            100_000.0, make_params(
                position_size=0.2, leverage=1, add_fraction=0.1))
        for ledger in (declared, undeclared):
            _open(ledger, 100.0)
            assert ledger.position.quantity == pytest.approx(200.0)
        fill_a = declared.execute(
            Intent(kind="add", direction="long"), _bar(110.0, seq=1),
            _ZERO_COST)
        fill_b = undeclared.execute(
            Intent(kind="add", direction="long"), _bar(110.0, seq=1),
            _ZERO_COST)
        assert fill_a.quantity == fill_b.quantity
        assert declared.position.quantity \
            == pytest.approx(undeclared.position.quantity)
        assert declared.position.margin \
            == pytest.approx(undeclared.position.margin)
        assert declared.balance == pytest.approx(undeclared.balance)

    def test_risk_budget_ignores_initial_fraction(self):
        """risk_budget 档不读 initial_fraction：直构共存（绕过装载
        互斥）走 risk_budget 定仓（与不声明同值——防御=忽略）。"""
        with_if = make_params(
            position_mode="risk_budget", risk_fraction=0.01,
            stop_loss=0.02, initial_fraction=0.2, position_size=0.3)
        without_if = make_params(
            position_mode="risk_budget", risk_fraction=0.01,
            stop_loss=0.02, position_size=0.3)
        fill_a = _open(Ledger(100_000.0, with_if), 100.0)
        fill_b = _open(Ledger(100_000.0, without_if), 100.0)
        assert fill_a.quantity == pytest.approx(500.0)  # 100_000×0.01/2
        assert fill_a.quantity == fill_b.quantity
