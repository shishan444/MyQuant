"""成交方式测试（rebuild-paper-trading 任务 1.2）。

验证面：四类意图成交价断言（信号/时间类按执行 bar 开盘价、SL/TP 与
清算即时类按触发价）+滑点四象限（多头买/卖、空头买/卖）不利方向
偏移断言（对齐回测 vbt order slippage 形制）+费用=名义×费率。
"""
from __future__ import annotations

import pytest

pytestmark = [pytest.mark.unit]

from core.paper_trading.kernel.fills import MarketFill
from core.paper_trading.kernel.types import BarSignals, ClosedBar, Intent


def _bar(open_=100.0, high=101.0, low=99.0, close=100.5, seq=0) -> ClosedBar:
    return ClosedBar(seq=seq, open=open_, high=high, low=low, close=close)


class TestBasePriceByKind:
    """四类意图成交价断言。"""

    def setup_method(self):
        self.model = MarketFill(fee_rate=0.0, slippage_rate=0.0)

    def test_signal_kind_fills_at_exec_bar_open(self):
        """信号类（open/close/add/reduce）按执行 bar 开盘价。"""
        bar = _bar(open_=100.0)
        for kind in ("open", "close", "add", "reduce"):
            fill = self.model.fill(
                Intent(kind=kind, direction="long"), bar, 10.0)
            assert fill.price == pytest.approx(100.0), \
                f"{kind} 应按执行 bar 开盘价成交"

    def test_time_kind_fills_at_exec_bar_open(self):
        """时间类（exit_max_hold/exit_time）按执行 bar 开盘价。"""
        bar = _bar(open_=100.0)
        for kind in ("exit_max_hold", "exit_time"):
            fill = self.model.fill(
                Intent(kind=kind, direction="long"), bar, 10.0)
            assert fill.price == pytest.approx(100.0), \
                f"{kind} 应按执行 bar 开盘价成交"

    def test_sl_tp_fills_at_trigger_price(self):
        """SL/TP 按触发价成交（非执行 bar 开盘/收盘价）。"""
        bar = _bar(open_=100.0, high=103.0, low=95.0, close=96.0)
        fill_sl = self.model.fill(
            Intent(kind="exit_sl", direction="long",
                   trigger_price=95.5), bar, 10.0)
        assert fill_sl.price == pytest.approx(95.5)
        fill_tp = self.model.fill(
            Intent(kind="exit_tp", direction="long",
                   trigger_price=102.0), bar, 10.0)
        assert fill_tp.price == pytest.approx(102.0)

    def test_liquidation_fills_at_trigger_price(self):
        """清算按触发价（=p_liq）成交（spec：清算成交价=触发价）。"""
        bar = _bar(open_=100.0, low=90.3)
        fill = self.model.fill(
            Intent(kind="exit_liquidation", direction="long",
                   trigger_price=90.3614), bar, 10.0)
        assert fill.price == pytest.approx(90.3614)


class TestSlippageFourQuadrants:
    """滑点四象限：买入 ×(1+slip)（买贵）、卖出 ×(1−slip)（卖便宜）。"""

    SLIP = 0.0005

    def setup_method(self):
        self.model = MarketFill(fee_rate=0.0, slippage_rate=self.SLIP)

    def test_long_buy_slips_upward(self):
        """多头买入（开多）：价×(1+slip)。"""
        bar = _bar(open_=100.0)
        fill = self.model.fill(
            Intent(kind="open", direction="long"), bar, 10.0)
        assert fill.price == pytest.approx(100.0 * (1 + self.SLIP))

    def test_long_sell_slips_downward(self):
        """多头卖出（平多）：价×(1−slip)。"""
        bar = _bar(open_=100.0)
        fill = self.model.fill(
            Intent(kind="close", direction="long"), bar, 10.0)
        assert fill.price == pytest.approx(100.0 * (1 - self.SLIP))

    def test_short_sell_slips_downward(self):
        """空头卖出（开空）：价×(1−slip)。"""
        bar = _bar(open_=100.0)
        fill = self.model.fill(
            Intent(kind="open", direction="short"), bar, 10.0)
        assert fill.price == pytest.approx(100.0 * (1 - self.SLIP))

    def test_short_buy_slips_upward(self):
        """空头买入（平空回补）：价×(1+slip)。"""
        bar = _bar(open_=100.0)
        fill = self.model.fill(
            Intent(kind="close", direction="short"), bar, 10.0)
        assert fill.price == pytest.approx(100.0 * (1 + self.SLIP))

    def test_slippage_cost_is_adverse_offset_times_quantity(self):
        """滑点成本=不利偏移货币量（|偏移价|×数量）。"""
        bar = _bar(open_=100.0)
        fill = self.model.fill(
            Intent(kind="open", direction="long"), bar, 20.0)
        assert fill.slippage_cost == pytest.approx(
            100.0 * self.SLIP * 20.0)


class TestFee:
    """费用=成交名义×费率（滑点后价基）。"""

    def test_fee_is_notional_times_rate(self):
        model = MarketFill(fee_rate=0.001, slippage_rate=0.0)
        bar = _bar(open_=100.0)
        fill = model.fill(Intent(kind="open", direction="long"), bar, 10.0)
        assert fill.fee == pytest.approx(0.001 * 10.0 * 100.0)

    def test_fee_applies_to_slipped_price(self):
        """费用按滑点后成交价计（名义=数量×成交价）。"""
        model = MarketFill(fee_rate=0.001, slippage_rate=0.0005)
        bar = _bar(open_=100.0)
        fill = model.fill(Intent(kind="open", direction="long"), bar, 10.0)
        expected_price = 100.0 * 1.0005
        assert fill.fee == pytest.approx(0.001 * 10.0 * expected_price)
