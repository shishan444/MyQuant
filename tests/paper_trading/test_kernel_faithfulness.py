"""执行内核忠实性串联测试（rebuild-paper-trading 任务 1.5）。

intents×fills×ledger 三件串联（ChainHarness=八步序消费侧的最小执行
器）。验证面：design §8.3 忠实性清单逐条一用例（首笔定仓/减仓比例/
加仓权益比例/敞口拒/max_hold 不重置/time_exit 必平/清算解式/SL/TP
触发价成交/费用口径/零门槛/七段判定序/八步序 seq 编号）+risk_budget
四断言（恒等式/cap 截断标记/非法 DNA 装载拒绝/ATR NaN 回退）+
inspect 新域源码无判定层私设门槛字面量。
"""
from __future__ import annotations

import inspect
from pathlib import Path

import pytest

pytestmark = [pytest.mark.unit]

from core.paper_trading.kernel.fills import MarketFill
from core.paper_trading.kernel.intents import SignalInterpreter
from core.paper_trading.kernel.ledger import Ledger
from core.paper_trading.kernel.types import (
    BarSignals,
    ClosedBar,
    ExecutionEvent,
    Intent,
    RiskParams,
    materialize,
)

import pandas as pd

from tests.helpers.data_factory import make_dna


def make_params(**overrides) -> RiskParams:
    base = dict(
        stop_loss=0.05, take_profit=None, position_size=1.0, leverage=1,
        direction="long", sl_mode="pct", atr_period=14)
    base.update(overrides)
    return RiskParams(**base)


def make_bar(seq, open_, high, low, close, timestamp=None, atr=None):
    return ClosedBar(
        seq=seq, open=open_, high=high, low=low, close=close,
        timestamp=timestamp, tf_minutes=240.0, atr=atr)


class ChainHarness:
    """串联最小执行器（八步序消费侧模拟——组 3 执行器的 kernel 切片）。

    每 bar 闭合按八步序消费：即时类判定（清算/SL/TP，快照=本根执行
    前，八步序第 1/2 步）当根按触发价成交 → 消费上根 pending（出场类
    第 3 步先于入场族第 4 步，按本根开盘价成交）→ 信号评估（第 7 步，
    快照=本根成交后——次根开仓与当根出场信号的 shift(1) 衔接不漏单）
    产非即时类挂 pending（次根执行）→ trail 峰值落账。
    """

    def __init__(self, params: RiskParams, initial_cash=10_000.0,
                 fee=0.0, slip=0.0):
        self.ledger = Ledger(initial_cash, params)
        self.fill_model = MarketFill(fee, slip)
        self.interpreter = SignalInterpreter()
        self.pending: list[Intent] = []
        self.events: list[ExecutionEvent] = []

    def on_bar_closed(self, bar: ClosedBar,
                      signals: BarSignals | None = None) -> None:
        signals = signals or BarSignals()
        # 八步序 1/2：即时类判定（pre 快照）+当根成交
        pre = self.interpreter.interpret(
            bar, signals, self.ledger.snapshot(bar.close, bar.seq),
            self.ledger.params)
        for intent in pre:
            if intent.kind == "exit_liquidation":
                self._execute(intent, bar, seq=1)
            elif intent.kind in ("exit_sl", "exit_tp"):
                self._execute(intent, bar, seq=2)
        # 八步序 3/4：pending 消费（出场先于入场族）
        exit_kinds = ("exit_max_hold", "exit_time", "close")
        for intent in [i for i in self.pending if i.kind in exit_kinds]:
            self._execute(intent, bar, seq=3)
        for intent in [i for i in self.pending if i.kind not in exit_kinds]:
            self._execute(intent, bar, seq=4)
        self.pending = []
        # 八步序第 7 步：信号评估（post 快照）挂 pending（次根执行）
        post = self.interpreter.interpret(
            bar, signals, self.ledger.snapshot(bar.close, bar.seq),
            self.ledger.params)
        self.pending.extend(
            i for i in post
            if i.kind not in ("exit_liquidation", "exit_sl", "exit_tp"))
        # trail 峰值落账（持仓期逐 bar）
        self.ledger.update_trail_peak(bar)

    def _execute(self, intent, bar, seq):
        fill = self.ledger.execute(intent, bar, self.fill_model)
        if fill is not None:
            self.events.append(ExecutionEvent(
                fill=fill,
                bar_time=str(bar.timestamp if bar.timestamp is not None
                             else bar.seq),
                seq=seq))

    @property
    def fills(self):
        return [e.fill for e in self.events]


class TestFaithfulnessChecklist:
    """design §8.3 忠实性清单逐条一用例。"""

    def test_first_entry_margin_is_position_size_times_equity(self):
        """首笔建仓动用=position_size×权益（权益口径）。"""
        h = ChainHarness(make_params(position_size=0.3, leverage=1),
                         initial_cash=10_000.0)
        h.on_bar_closed(make_bar(0, 100, 101, 99, 100),
                        BarSignals(entry=True))
        h.on_bar_closed(make_bar(1, 100, 101, 99, 100))
        assert h.ledger.position is not None
        assert h.ledger.position.margin == pytest.approx(10_000.0 * 0.3)

    def test_reduce_declared_30_is_30_percent(self):
        """声明减仓 30% 即实际减仓 30%。"""
        params = materialize(_risk_genes(exit_fraction=0.3))
        h = ChainHarness(params, initial_cash=10_000.0)
        h.on_bar_closed(make_bar(0, 100, 101, 99, 100),
                        BarSignals(entry=True))
        h.on_bar_closed(make_bar(1, 100, 101, 99, 100))  # 开仓 qty=100
        qty_before = h.ledger.position.quantity
        h.on_bar_closed(make_bar(2, 100, 101, 99, 100),
                        BarSignals(reduce=True))
        h.on_bar_closed(make_bar(3, 100, 101, 99, 100))
        assert h.ledger.position.quantity == pytest.approx(qty_before * 0.7)

    def test_add_margin_is_add_fraction_times_equity(self):
        """加仓动用=add_fraction×当时总权益（逐仓口径：开仓后
        balance 9_000+margin 1_000+浮盈 0=10_000 → add_margin=2_000）。"""
        params = make_params(position_size=0.1, leverage=1,
                             add_fraction=0.2)
        h = ChainHarness(params, initial_cash=10_000.0)
        h.on_bar_closed(make_bar(0, 100, 101, 99, 100),
                        BarSignals(entry=True))
        h.on_bar_closed(make_bar(1, 100, 101, 99, 100))  # margin=1_000
        assert h.ledger.equity(100.0) == pytest.approx(10_000.0)
        h.on_bar_closed(make_bar(2, 100, 101, 99, 100),
                        BarSignals(add=True))
        h.on_bar_closed(make_bar(3, 100, 101, 99, 100))
        assert h.ledger.position.margin == pytest.approx(
            1_000.0 + 10_000.0 * 0.2)

    def test_exposure_cap_rejects_add_this_bar_only(self):
        """敞口超限本 bar 拒加仓（不炸不降级），敞口回落后放行。

        总权益口径（含划出保证金）：开仓 margin=5_000/balance=5_000/
        equity=10_000；add 名义=10_000×0.6=6_000、当前名义 5_000 →
        11_000>10_000×1.0 → 拒。
        """
        params = make_params(position_size=0.5, leverage=1,
                             add_fraction=0.6, max_exposure=1.0)
        h = ChainHarness(params, initial_cash=10_000.0)
        h.on_bar_closed(make_bar(0, 100, 101, 99, 100),
                        BarSignals(entry=True))
        h.on_bar_closed(make_bar(1, 100, 101, 99, 100))  # 名义 5_000
        h.on_bar_closed(make_bar(2, 100, 101, 99, 100),
                        BarSignals(add=True))
        h.on_bar_closed(make_bar(3, 100, 101, 99, 100))
        qty_after_reject = h.ledger.position.quantity
        assert qty_after_reject == pytest.approx(50.0), "超限 bar 不加仓"
        # max_exposure 放宽后同信号放行（下根重新评估）
        h.ledger.params = make_params(
            position_size=0.5, leverage=1, add_fraction=0.6,
            max_exposure=2.0)
        h.ledger.position.quantity = qty_after_reject  # 保持仓位
        h.on_bar_closed(make_bar(4, 100, 101, 99, 100),
                        BarSignals(add=True))
        h.on_bar_closed(make_bar(5, 100, 101, 99, 100))
        assert h.ledger.position.quantity > qty_after_reject

    def test_max_hold_forces_exit_and_reduce_does_not_reset(self):
        """max_hold 到期必平且减仓不重置计数（入场 bar 计第 1 根）。"""
        params = make_params(position_size=0.3, leverage=1, max_hold=4)
        h = ChainHarness(params, initial_cash=10_000.0)
        h.on_bar_closed(make_bar(0, 100, 101, 99, 100),
                        BarSignals(entry=True))
        h.on_bar_closed(make_bar(1, 100, 101, 99, 100))  # bar1 开仓（第1根）
        h.on_bar_closed(make_bar(2, 100, 101, 99, 100),
                        BarSignals(reduce=True))
        h.on_bar_closed(make_bar(3, 100, 101, 99, 100))  # bar3 减仓执行
        assert h.ledger.position is not None, "减仓非全平（0.5 缺省）"
        # bar3 interpret 计数=3−1+1=3<4；bar4 interpret 计数=4>=4 → 出场
        h.on_bar_closed(make_bar(4, 100, 101, 99, 100))
        h.on_bar_closed(make_bar(5, 100, 101, 99, 100))  # bar5 执行平仓
        assert h.ledger.position is None
        kinds = [(f.intent.kind, e.seq) for e in h.events for f in [e.fill]]
        assert "exit_max_hold" in [k for k, _ in kinds]

    def test_time_exit_forces_close(self):
        """time_exit_utc 时刻必平（区间覆盖 t_exit 的判定 bar 次根
        开盘执行；开仓 bar 区间即覆盖 → 持仓 1 根后必平——与回测
        引擎同 bar 判定等价）。"""
        params = make_params(position_size=0.3, leverage=1,
                             time_exit_utc=600)  # 10:00 UTC
        h = ChainHarness(params, initial_cash=10_000.0)
        h.on_bar_closed(make_bar(0, 100, 101, 99, 100,
                                 timestamp=pd.Timestamp(
                                     "2026-08-30 04:00:00+00:00")),
                        BarSignals(entry=True))
        # bar1（08:00 开盘）区间 [08:00,12:00) 覆盖 10:00：开仓与
        # 时间出场判定同 bar（出场意图 post 快照产出）
        h.on_bar_closed(make_bar(1, 100, 101, 99, 100,
                                 timestamp=pd.Timestamp(
                                     "2026-08-30 08:00:00+00:00")))
        assert h.ledger.position is not None, "开仓当根不即时平"
        # bar2（12:00 开盘，区间不含 10:00）：消费 exit_time 意图平仓
        h.on_bar_closed(make_bar(2, 100, 101, 99, 100,
                                 timestamp=pd.Timestamp(
                                     "2026-08-30 12:00:00+00:00")))
        assert h.ledger.position is None
        exit_events = [e for e in h.events
                       if e.fill.intent.kind == "exit_time"]
        assert exit_events, "time_exit 必平"
        assert exit_events[0].seq == 3  # 八步序第 3 步

    def test_liquidation_trigger_price_matches_market_model(self):
        """清算触发价=market_model 分层解式（串联全链路）。"""
        params = make_params(position_size=1.0, leverage=10)
        h = ChainHarness(params, initial_cash=5_000.0)
        h.on_bar_closed(make_bar(0, 1.0, 1.0, 1.0, 1.0),
                        BarSignals(entry=True))
        h.on_bar_closed(make_bar(1, 1.0, 1.0, 1.0, 1.0))  # qty=50_000
        # bar2 跌破 p_liq=0.9036144（low=0.90）
        h.on_bar_closed(make_bar(2, 0.905, 0.905, 0.90, 0.90))
        liq = [e for e in h.events if e.fill.intent.kind == "exit_liquidation"]
        assert liq, "跌破触发价应清算"
        assert liq[0].fill.price == pytest.approx(0.9036144, rel=1e-6)
        assert liq[0].seq == 1  # 八步序第 1 步
        assert h.ledger.position is None

    def test_sl_touch_fills_at_trigger_price(self):
        """SL 按 high/low 触碰+触发价成交（非开盘/收盘价）。"""
        params = make_params(position_size=0.3, leverage=1, stop_loss=0.05)
        h = ChainHarness(params, initial_cash=10_000.0)
        h.on_bar_closed(make_bar(0, 100, 101, 99, 100),
                        BarSignals(entry=True))
        h.on_bar_closed(make_bar(1, 100, 101, 99, 100))  # entry=100
        # bar2：open=97、low=94.5 触碰 95 → 触发价成交（非 open 97）
        h.on_bar_closed(make_bar(2, 97.0, 97.5, 94.5, 96.0))
        sl = [e for e in h.events if e.fill.intent.kind == "exit_sl"]
        assert sl[0].fill.price == pytest.approx(95.0)
        assert sl[0].seq == 2  # 八步序第 2 步

    def test_tp_touch_fills_at_trigger_price(self):
        params = make_params(position_size=0.3, leverage=1, stop_loss=0.05,
                             take_profit=0.10)
        h = ChainHarness(params, initial_cash=10_000.0)
        h.on_bar_closed(make_bar(0, 100, 101, 99, 100),
                        BarSignals(entry=True))
        h.on_bar_closed(make_bar(1, 100, 101, 99, 100))
        h.on_bar_closed(make_bar(2, 105.0, 110.5, 104.0, 108.0))
        tp = [e for e in h.events if e.fill.intent.kind == "exit_tp"]
        assert tp[0].fill.price == pytest.approx(110.0)

    def test_fee_is_notional_times_rate(self):
        """费用=名义×费率（串联：开仓成交按成交价名义计费）。"""
        params = make_params(position_size=0.3, leverage=1)
        h = ChainHarness(params, initial_cash=10_000.0, fee=0.001)
        h.on_bar_closed(make_bar(0, 100, 101, 99, 100),
                        BarSignals(entry=True))
        h.on_bar_closed(make_bar(1, 100, 101, 99, 100))
        open_fill = [f for f in h.fills if f.intent.kind == "open"][0]
        assert open_fill.fee == pytest.approx(
            0.001 * open_fill.quantity * open_fill.price)

    def test_zero_threshold_exit_one_bar_underwater(self):
        """零门槛场景：持仓 1 根+浮亏未覆盖手续费+exit 信号→照常平仓
        （无 min_hold/min_profit 类判定层私设）。"""
        params = make_params(position_size=0.3, leverage=1, stop_loss=0.05)
        h = ChainHarness(params, initial_cash=10_000.0, fee=0.001)
        h.on_bar_closed(make_bar(0, 100, 101, 99, 100),
                        BarSignals(entry=True))
        # bar1 开仓（entry≈100）；bar1 同时给 exit 信号→挂 pending
        h.on_bar_closed(make_bar(1, 99.9, 99.95, 99.8, 99.85),
                        BarSignals(exit=True))
        h.on_bar_closed(make_bar(2, 99.8, 99.9, 99.7, 99.8))
        assert h.ledger.position is None, "持仓 1 根浮亏照常平仓"
        assert h.ledger.closed_trades == 1

    def test_judgment_order_serialized(self):
        """七段判定序串联锚：清算优先 SL、SL 优先 TP、止损优先时间
        出场（三场景各自断言产出的唯一意图 kind）。"""
        # 场景 1：清算优先 SL（lever=10 仓位，同 bar 双触发）
        params = make_params(position_size=1.0, leverage=10,
                             stop_loss=0.05)
        h = ChainHarness(params, initial_cash=5_000.0)
        h.on_bar_closed(make_bar(0, 1.0, 1.0, 1.0, 1.0),
                        BarSignals(entry=True))
        h.on_bar_closed(make_bar(1, 1.0, 1.0, 1.0, 1.0))
        h.on_bar_closed(make_bar(2, 0.905, 0.905, 0.88, 0.88))
        kinds = [e.fill.intent.kind for e in h.events]
        assert kinds[-1] == "exit_liquidation"

        # 场景 2：SL 优先 TP（同 bar 双触碰）
        params = make_params(position_size=0.3, leverage=1,
                             stop_loss=0.05, take_profit=0.10)
        h2 = ChainHarness(params, initial_cash=10_000.0)
        h2.on_bar_closed(make_bar(0, 100, 101, 99, 100),
                         BarSignals(entry=True))
        h2.on_bar_closed(make_bar(1, 100, 101, 99, 100))
        h2.on_bar_closed(make_bar(2, 100, 115.0, 90.0, 100.0))
        kinds2 = [e.fill.intent.kind for e in h2.events]
        assert kinds2[-1] == "exit_sl"

        # 场景 3：止损优先时间出场（max_hold 到期同 bar SL 触碰）
        params3 = make_params(position_size=0.3, leverage=1,
                              stop_loss=0.05, max_hold=2)
        h3 = ChainHarness(params3, initial_cash=10_000.0)
        h3.on_bar_closed(make_bar(0, 100, 101, 99, 100),
                         BarSignals(entry=True))
        h3.on_bar_closed(make_bar(1, 100, 101, 99, 100))
        # bar2 interpret：bars_held=2>=2（max_hold）与 SL 触碰并存
        h3.on_bar_closed(make_bar(2, 100, 101, 94.0, 95.0))
        kinds3 = [e.fill.intent.kind for e in h3.events]
        assert kinds3[-1] == "exit_sl"

    def test_seq_audit_numbering_follows_eight_step_order(self):
        """八步序 seq 审计编号：清算=1、SL/TP=2、时间/信号出场=3、
        入场族=4（步骤位即 bar 内序号）。"""
        params = make_params(position_size=0.3, leverage=1,
                             stop_loss=0.05)
        h = ChainHarness(params, initial_cash=10_000.0)
        h.on_bar_closed(make_bar(0, 100, 101, 99, 100),
                        BarSignals(entry=True))
        h.on_bar_closed(make_bar(1, 100, 101, 99, 100))  # open seq=4
        assert h.events[0].seq == 4
        h.on_bar_closed(make_bar(2, 100, 101, 99, 100),
                        BarSignals(exit=True))
        h.on_bar_closed(make_bar(3, 100, 101, 99, 100))  # close seq=3
        assert h.events[1].seq == 3
        # SL 场景 seq=2
        h.on_bar_closed(make_bar(4, 100, 101, 99, 100),
                        BarSignals(entry=True))
        h.on_bar_closed(make_bar(5, 100, 101, 99, 100))
        h.on_bar_closed(make_bar(6, 100, 101, 94.0, 95.0))  # SL seq=2
        last = [e for e in h.events if e.fill.intent.kind == "exit_sl"]
        assert last[0].seq == 2


def _risk_genes(**overrides):
    from core.strategy.dna import RiskGenes
    return RiskGenes(**overrides)


class TestRiskBudgetFaithfulness:
    """risk_budget 四断言（+距离分派与权益浮动补强）。"""

    def test_identity_untruncated_chain(self):
        """未截断恒等式：qty×stop_dist=权益×risk_fraction（串联链路，
        手算锚 100_000×0.01=1_000）。"""
        params = make_params(position_mode="risk_budget",
                             risk_fraction=0.01, stop_loss=0.02)
        h = ChainHarness(params, initial_cash=100_000.0)
        h.on_bar_closed(make_bar(0, 100, 101, 99, 100),
                        BarSignals(entry=True))
        h.on_bar_closed(make_bar(1, 100, 101, 99, 100))
        fill = h.fills[0]
        assert fill.quantity * (100.0 * 0.02) == pytest.approx(1_000.0)
        assert fill.capped is False

    def test_cap_truncation_with_capped_marker_chain(self):
        """cap 截断+capped 标记（串联链路）。"""
        params = make_params(position_mode="risk_budget",
                             risk_fraction=0.5, stop_loss=0.01,
                             leverage=10, max_exposure=1.0)
        h = ChainHarness(params, initial_cash=100_000.0)
        h.on_bar_closed(make_bar(0, 100, 101, 99, 100),
                        BarSignals(entry=True))
        h.on_bar_closed(make_bar(1, 100, 101, 99, 100))
        fill = h.fills[0]
        assert fill.quantity == pytest.approx(1_000.0)
        assert fill.capped is True

    def test_illegal_dna_combination_rejected_by_loader(self):
        """非法 DNA 组合装载拒绝：risk_budget 缺 risk_fraction 或
        stop_loss≤0 → validate_dna 拒绝（正常路径无回退——error 不进
        running 的判定面；执行器消费见组 3）。"""
        from core.strategy.validator import validate_dna
        base = make_dna(entry_value=30, exit_value=70)
        # 缺 risk_fraction（None）
        dna = make_dna(entry_value=30, exit_value=70)
        dna.risk_genes.position_mode = "risk_budget"
        dna.risk_genes.risk_fraction = None
        dna.risk_genes.stop_loss = 0.05
        result = validate_dna(dna)
        assert not result.is_valid
        assert any("risk_fraction" in e for e in result.errors)
        # stop_loss≤0
        dna2 = make_dna(entry_value=30, exit_value=70)
        dna2.risk_genes.position_mode = "risk_budget"
        dna2.risk_genes.risk_fraction = 0.05
        dna2.risk_genes.stop_loss = 0.0
        result2 = validate_dna(dna2)
        assert not result2.is_valid
        assert any("stop_loss" in e for e in result2.errors)
        assert base is not None  # 对照组合法基线存在

    def test_entry_atr_nan_falls_back_fixed_chain(self):
        """入场 bar ATR NaN=该次定仓回退 fixed（串联链路，等价覆盖
        回测引擎 NaN 防御路径）。"""
        params = make_params(position_mode="risk_budget",
                             risk_fraction=0.01, stop_loss=2.0,
                             sl_mode="atr", position_size=0.3)
        h = ChainHarness(params, initial_cash=10_000.0)
        # entry 信号 bar 0（ATR NaN）；执行 bar 1（ATR NaN——定仓读
        # 执行 bar 的当根 ATR）
        h.on_bar_closed(make_bar(0, 100, 101, 99, 100, atr=float("nan")),
                        BarSignals(entry=True))
        h.on_bar_closed(make_bar(1, 100, 101, 99, 100, atr=float("nan")))
        fill = h.fills[0]
        assert fill.quantity == pytest.approx(
            10_000.0 * 0.3 / 100.0), "NaN 回退 fixed 定仓"

    def test_trail_distance_identity_engine_semantics(self):
        """trail 档定仓距离恒等式（引擎 D-6 修复后语义：距离=价×sl
        比例域——实施偏差记录见汇报）：qty×(price×sl)=equity×rf。"""
        params = make_params(position_mode="risk_budget",
                             risk_fraction=0.01, stop_loss=0.02,
                             sl_mode="trail")
        h = ChainHarness(params, initial_cash=100_000.0)
        h.on_bar_closed(make_bar(0, 100, 101, 99, 100),
                        BarSignals(entry=True))
        h.on_bar_closed(make_bar(1, 100, 101, 99, 100))
        fill = h.fills[0]
        assert fill.quantity == pytest.approx(500.0)
        assert fill.quantity * (100.0 * 0.02) == pytest.approx(1_000.0)

    def test_equity_float_second_entry_after_close(self):
        """权益变动后第二笔 qty 随权益浮动（平仓再开串联链路：
        首笔 500、盈利后权益 101_000 → 第二笔 505）。"""
        params = make_params(position_mode="risk_budget",
                             risk_fraction=0.01, stop_loss=0.02)
        h = ChainHarness(params, initial_cash=100_000.0)
        h.on_bar_closed(make_bar(0, 100, 101, 99, 100),
                        BarSignals(entry=True))
        h.on_bar_closed(make_bar(1, 100, 101, 99, 100))  # qty=500
        h.on_bar_closed(make_bar(2, 100, 101, 99, 100),
                        BarSignals(exit=True))
        h.on_bar_closed(make_bar(3, 105, 106, 104, 105))  # close@105
        assert h.fills[1].realized_pnl == pytest.approx(
            500.0 * 5.0), "首笔盈利 2_500"
        h.on_bar_closed(make_bar(4, 105, 106, 104, 105),
                        BarSignals(entry=True))
        h.on_bar_closed(make_bar(5, 105, 106, 104, 105))
        second = h.fills[2]
        # 权益=100_000−fee(0)+2_500=102_500；qty=102_500×0.01/(105×0.02)
        assert second.quantity == pytest.approx(102_500.0 * 0.01 / 2.1)


class TestNoPrivateThresholds:
    """inspect 新域源码无 min_hold/min_profit 类门槛字面量
    （防判定层私设回潮——考场无观点的静态守护）。"""

    def test_no_min_hold_min_profit_literals_in_kernel(self):
        kernel_dir = Path(
            __file__).resolve().parents[2] / "core" / "paper_trading"
        offenders = []
        for py in sorted(kernel_dir.rglob("*.py")):
            src = py.read_text(encoding="utf-8")
            for literal in ("min_hold", "min_profit", "min_bars_hold",
                            "breakeven"):
                if literal in src:
                    offenders.append(f"{py.name}:{literal}")
        assert offenders == [], f"发现门槛字面量: {offenders}"
