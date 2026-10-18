"""信号解释器测试（rebuild-paper-trading 任务 1.3）。

验证面：映射规则 a-f 参数化（direction 过滤/反手两步/max_exposure
超限跳过不炸/mixed 符号 0/NaN 不开仓）+防线判定 g-i（pct/atr/trail
三模式触发价手算锚、trail 峰值含当根、max_hold 计数、time_exit
区间覆盖）+ATR 单 bar NaN/≤0 守卫（该 bar 无 SL/TP 意图且不回退
pct 距离）+判定序（清算优先 SL、SL 优先 TP、止损优先时间出场）+
金额防线（add-position-sizing-params：达线/未达线零副作用/资金费
分量/空头双向/归因序——SL 归止损、时间/信号出场归金额防线）。
"""
from __future__ import annotations

import math

import pytest

pytestmark = [pytest.mark.unit]

from core.paper_trading.kernel.account import (
    AccountSnapshot,
    PositionSnapshot,
)
from core.paper_trading.kernel.intents import SignalInterpreter
from core.paper_trading.kernel.types import BarSignals, ClosedBar, RiskParams


def make_params(**overrides) -> RiskParams:
    """物化参数构造（缺省=pct 多头无杠杆无时间出场的基线形态）。"""
    base = dict(
        stop_loss=0.05, take_profit=None, position_size=1.0, leverage=1,
        direction="long", sl_mode="pct", atr_period=14)
    base.update(overrides)
    return RiskParams(**base)


def make_position(side="long", entry=100.0, qty=10.0, margin=1000.0,
                  trail_peak=100.0, entry_bar_seq=0,
                  funding_paid=0.0) -> PositionSnapshot:
    return PositionSnapshot(
        side=side, entry_price=entry, quantity=qty, margin=margin,
        open_cost=qty * entry, sl_price=None, tp_price=None,
        trail_peak=trail_peak, entry_bar_seq=entry_bar_seq,
        funding_paid=funding_paid)


def make_account(position=None, balance=5000.0, mark=100.0,
                 bar_seq=0) -> AccountSnapshot:
    if position is None:
        return AccountSnapshot(
            equity=balance, balance=balance, unrealized_pnl=0.0,
            position=None, bars_held=0, position_notional=0.0)
    sign = 1.0 if position.side == "long" else -1.0
    upnl = sign * position.quantity * (mark - position.entry_price)
    # 逐仓总权益=balance+margin+未实现盈亏（与 Ledger.snapshot 同式）
    return AccountSnapshot(
        equity=balance + position.margin + upnl, balance=balance,
        unrealized_pnl=upnl, position=position,
        bars_held=bar_seq - position.entry_bar_seq + 1,
        position_notional=position.quantity * mark)


def make_bar(seq=0, open_=100.0, high=101.0, low=99.0, close=100.5,
             timestamp=None, tf_minutes=240.0, atr=None) -> ClosedBar:
    return ClosedBar(
        seq=seq, open=open_, high=high, low=low, close=close,
        timestamp=timestamp, tf_minutes=tf_minutes, atr=atr)


def interpret(bar, signals, account, params):
    return SignalInterpreter().interpret(bar, signals, account, params)


def _risk_genes_with(**overrides):
    """RiskGenes 构造（物化链路测试用——沿 dataclass 缺省域覆写）。"""
    from core.strategy.dna import RiskGenes
    return RiskGenes(**overrides)


class TestSignalMappingRules:
    """映射规则 a-f。"""

    # ── a. entry ∧ 无仓 → open ──

    def test_a_entry_no_position_opens_long(self):
        intents = interpret(
            make_bar(), BarSignals(entry=True, entry_direction=0.0),
            make_account(), make_params(direction="long"))
        assert [(i.kind, i.direction) for i in intents] == [("open", "long")]

    def test_a_direction_filter_conflict_no_open(self):
        """非 mixed 声明是过滤器：信号方向与 DNA 冲突不产生 open。"""
        intents = interpret(
            make_bar(), BarSignals(entry=True, entry_direction=-1.0),
            make_account(), make_params(direction="long"))
        assert intents == []
        intents = interpret(
            make_bar(), BarSignals(entry=True, entry_direction=1.0),
            make_account(), make_params(direction="short"))
        assert intents == []

    @pytest.mark.parametrize("sign,expected", [
        (1.0, ("open", "long")), (-1.0, ("open", "short"))])
    def test_a_mixed_direction_by_signal_sign(self, sign, expected):
        intents = interpret(
            make_bar(), BarSignals(entry=True, entry_direction=sign),
            make_account(), make_params(direction="mixed"))
        assert [(i.kind, i.direction) for i in intents] == [expected]

    @pytest.mark.parametrize("sign", [0.0, float("nan")])
    def test_a_mixed_zero_or_nan_sign_no_open(self, sign):
        """mixed 符号 0/NaN 不开仓。"""
        intents = interpret(
            make_bar(), BarSignals(entry=True, entry_direction=sign),
            make_account(), make_params(direction="mixed"))
        assert intents == []

    # ── b. exit ∧ 有仓 → close（reason=signal） ──

    def test_b_exit_signal_closes_all(self):
        pos = make_position(side="long")
        intents = interpret(
            make_bar(), BarSignals(exit=True),
            make_account(position=pos), make_params())
        assert len(intents) == 1
        assert intents[0].kind == "close"
        assert intents[0].reason == "signal"
        assert intents[0].direction == "long"

    # ── c. reduce ∧ 有仓 → reduce（比例=exit_fraction） ──

    @pytest.mark.parametrize("declared,expected", [
        (None, 0.5), (0.3, 0.3), (0.7, 0.7)])
    def test_c_reduce_fraction(self, declared, expected):
        """经 materialize 物化（None→缺省 0.5 的真实链路）。"""
        from core.paper_trading.kernel.types import materialize
        params = materialize(
            _risk_genes_with(exit_fraction=declared))
        pos = make_position(qty=100.0)
        intents = interpret(
            make_bar(), BarSignals(reduce=True),
            make_account(position=pos), params)
        assert intents[0].kind == "reduce"
        assert intents[0].size_fraction == expected

    # ── d. add ∧ 有仓同向 → add（敞口超限跳过不炸） ──

    def test_d_add_same_direction(self):
        pos = make_position(qty=40.0)  # 名义 40×100=4_000、margin=1_000
        intents = interpret(
            make_bar(), BarSignals(add=True, entry_direction=0.0),
            make_account(position=pos, balance=5000.0),
            make_params(add_fraction=0.1, max_exposure=2.0))
        # 总权益=5_000+1_000=6_000 → 加仓名义=6_000×0.1×1=600；
        # 4_000+600<=6_000×2 → 放行
        assert [(i.kind, i.size_fraction) for i in intents] \
            == [("add", 0.1)]

    def test_d_add_exposure_cap_skips_without_error(self):
        """敞口超限本 bar 跳过（不炸不降级）——空头持仓同判。

        总权益=5_000+margin 1_000=6_000；名义 5_500+600=6_100>
        6_000×1.0 → 拒。
        """
        pos = make_position(side="short", qty=55.0)
        intents = interpret(
            make_bar(), BarSignals(add=True, entry_direction=0.0),
            make_account(position=pos, balance=5000.0, mark=100.0),
            make_params(add_fraction=0.1, max_exposure=1.0))
        assert intents == []

    # ── e. entry ∧ 有仓且反向 → close（reason=reverse，两步反手） ──

    def test_e_reverse_entry_closes_only(self):
        """反向 entry 本根只平不立开（两步反手）。"""
        pos = make_position(side="long")
        intents = interpret(
            make_bar(), BarSignals(entry=True, entry_direction=-1.0),
            make_account(position=pos), make_params(direction="mixed"))
        assert len(intents) == 1
        assert intents[0].kind == "close"
        assert intents[0].reason == "reverse"

    def test_e_reverse_with_direction_dna(self):
        """持仓 long、DNA short 的 entry=反向 → close(reverse)。"""
        pos = make_position(side="long")
        intents = interpret(
            make_bar(), BarSignals(entry=True, entry_direction=0.0),
            make_account(position=pos), make_params(direction="short"))
        assert intents[0].kind == "close"
        assert intents[0].reason == "reverse"

    def test_e_same_direction_entry_with_position_ignored(self):
        """有仓同向 entry=无动作（不加仓——add 是独立信号）。"""
        pos = make_position(side="long")
        intents = interpret(
            make_bar(), BarSignals(entry=True, entry_direction=0.0),
            make_account(position=pos), make_params(direction="long"))
        assert intents == []

    # ── f. 语境不符忽略 ──

    def test_f_add_without_position_ignored(self):
        intents = interpret(
            make_bar(), BarSignals(add=True),
            make_account(), make_params())
        assert intents == []

    def test_f_add_reverse_direction_ignored(self):
        """add 信号但方向与持仓相反（无 entry 语境）→ 忽略。"""
        pos = make_position(side="long")
        intents = interpret(
            make_bar(), BarSignals(add=True, entry_direction=-1.0),
            make_account(position=pos), make_params(direction="mixed"))
        assert intents == []

    def test_f_reduce_exit_without_position_ignored(self):
        intents = interpret(
            make_bar(), BarSignals(reduce=True, exit=True),
            make_account(), make_params())
        assert intents == []


class TestStopTakeLevels:
    """g. 三模式触发价手算锚+触碰判定+SL 优先 TP。"""

    def test_g_pct_long_hand_anchor(self):
        """pct 多头：sl=entry×(1−sl)=95、tp=entry×(1+tp)=110。"""
        pos = make_position(entry=100.0)
        params = make_params(stop_loss=0.05, take_profit=0.10)
        # bar low=94.5 触碰 SL（非 low=95 恰触——含边界另测）
        intents = interpret(
            make_bar(low=94.5), BarSignals(), make_account(position=pos),
            params)
        assert intents[0].kind == "exit_sl"
        assert intents[0].trigger_price == pytest.approx(95.0)

    def test_g_pct_long_tp_hand_anchor(self):
        pos = make_position(entry=100.0)
        params = make_params(stop_loss=0.05, take_profit=0.10)
        intents = interpret(
            make_bar(low=99.0, high=110.5), BarSignals(),
            make_account(position=pos), params)
        assert intents[0].kind == "exit_tp"
        assert intents[0].trigger_price == pytest.approx(110.0)

    def test_g_pct_short_hand_anchor(self):
        """pct 空头：sl=entry×(1+sl)=105、tp=entry×(1−tp)=90。"""
        pos = make_position(side="short", entry=100.0)
        params = make_params(stop_loss=0.05, take_profit=0.10)
        intents = interpret(
            make_bar(high=105.5, low=99.0), BarSignals(),
            make_account(position=pos), params)
        assert intents[0].kind == "exit_sl"
        assert intents[0].trigger_price == pytest.approx(105.0)
        intents = interpret(
            make_bar(high=101.0, low=89.5), BarSignals(),
            make_account(position=pos), params)
        assert intents[0].kind == "exit_tp"
        assert intents[0].trigger_price == pytest.approx(90.0)

    def test_g_atr_long_hand_anchor(self):
        """atr 多头：sl=entry−sl_mult×ATR=100−2×3=94；tp=100+4×3=112。"""
        pos = make_position(entry=100.0)
        params = make_params(
            sl_mode="atr", stop_loss=2.0, take_profit=4.0)
        intents = interpret(
            make_bar(low=93.5, atr=3.0), BarSignals(),
            make_account(position=pos), params)
        assert intents[0].kind == "exit_sl"
        assert intents[0].trigger_price == pytest.approx(94.0)
        intents = interpret(
            make_bar(low=99.0, high=112.5, atr=3.0), BarSignals(),
            make_account(position=pos), params)
        assert intents[0].kind == "exit_tp"
        assert intents[0].trigger_price == pytest.approx(112.0)

    def test_g_trail_long_peak_includes_current_bar(self):
        """trail 多头：峰值含当根=max(快照 105, 当根 high 108)=108，
        sl=108×(1−0.05)=102.6；bar low=100.5 触发。"""
        pos = make_position(entry=100.0, trail_peak=105.0)
        params = make_params(sl_mode="trail", stop_loss=0.05)
        intents = interpret(
            make_bar(high=108.0, low=100.5), BarSignals(),
            make_account(position=pos), params)
        assert intents[0].kind == "exit_sl"
        assert intents[0].trigger_price == pytest.approx(102.6)

    def test_g_trail_short_peak_takes_low(self):
        """trail 空头：峰值=min(快照 95, 当根 low 92)=92，
        sl=92×(1+0.05)=96.6；bar high=97 触发。"""
        pos = make_position(side="short", entry=100.0, trail_peak=95.0)
        params = make_params(sl_mode="trail", stop_loss=0.05)
        intents = interpret(
            make_bar(high=97.0, low=92.0), BarSignals(),
            make_account(position=pos), params)
        assert intents[0].kind == "exit_sl"
        assert intents[0].trigger_price == pytest.approx(96.6)

    def test_g_sl_priority_over_tp_same_bar(self):
        """SL 与 TP 同 bar 并存 SL 优先（沿旧链 account.py:271 语义）。"""
        pos = make_position(entry=100.0)
        params = make_params(stop_loss=0.05, take_profit=0.10)
        intents = interpret(
            make_bar(low=90.0, high=115.0), BarSignals(),
            make_account(position=pos), params)
        assert [(i.kind, i.reason) for i in intents] == [("exit_sl", "sl")]

    def test_g_touch_boundary_is_inclusive(self):
        """触碰判定含边界（low 恰等于止损价触发）。"""
        pos = make_position(entry=100.0)
        params = make_params(stop_loss=0.05)
        intents = interpret(
            make_bar(low=95.0), BarSignals(),
            make_account(position=pos), params)
        assert intents[0].kind == "exit_sl"


class TestAtrGuard:
    """ATR 单 bar 无效守卫：该 bar 无 SL/TP 意图且不回退 pct。"""

    @pytest.mark.parametrize("bad_atr", [
        None, float("nan"), 0.0, -1.0])
    def test_invalid_atr_no_stop_no_pct_fallback(self, bad_atr):
        """atr 模式 ATR 无效（None/NaN/≤0）→ 无 SL/TP 意图。

        sl=0.05 若被按 pct 误回退会算出 95——bar low=94 触碰，断言
        无意图即证明不回退（守卫语义）。
        """
        pos = make_position(entry=100.0)
        params = make_params(sl_mode="atr", stop_loss=0.05)
        intents = interpret(
            make_bar(low=94.0, atr=bad_atr), BarSignals(),
            make_account(position=pos), params)
        assert intents == []

    def test_invalid_atr_no_tp_either(self):
        """ATR 无效时 TP 同样无意图（atr 模式 TP 也依赖 ATR）。"""
        pos = make_position(entry=100.0)
        params = make_params(sl_mode="atr", stop_loss=2.0, take_profit=4.0)
        intents = interpret(
            make_bar(high=120.0, atr=float("nan")), BarSignals(),
            make_account(position=pos), params)
        assert intents == []

    def test_next_bar_atr_recovers_defense(self):
        """次根 ATR 恢复有效后防线恢复。"""
        pos = make_position(entry=100.0)
        params = make_params(sl_mode="atr", stop_loss=2.0)
        intents = interpret(
            make_bar(low=80.0, atr=3.0), BarSignals(),
            make_account(position=pos), params)
        assert intents[0].kind == "exit_sl"
        assert intents[0].trigger_price == pytest.approx(94.0)

    def test_pct_mode_unaffected_by_atr_column(self):
        """pct 模式不读 ATR（列缺失零影响）。"""
        pos = make_position(entry=100.0)
        params = make_params(stop_loss=0.05)
        intents = interpret(
            make_bar(low=94.0), BarSignals(),
            make_account(position=pos), params)
        assert intents[0].kind == "exit_sl"


class TestTimeExits:
    """h. max_hold 计数 / i. time_exit 区间覆盖。"""

    def test_h_max_hold_triggers_at_n_bars(self):
        """入场 bar 计第 1 根：bars_held=5、max_hold=5 触发。"""
        pos = make_position(entry_bar_seq=0)
        params = make_params(max_hold=5)
        account = make_account(position=pos, bar_seq=4)  # 4−0+1=5
        intents = interpret(
            make_bar(seq=4), BarSignals(), account, params)
        assert intents[0].kind == "exit_max_hold"
        assert intents[0].reason == "max_hold"

    def test_h_max_hold_not_yet_due(self):
        pos = make_position(entry_bar_seq=0)
        params = make_params(max_hold=6)
        account = make_account(position=pos, bar_seq=4)  # 5<6
        intents = interpret(
            make_bar(seq=4), BarSignals(), account, params)
        assert intents == []

    def test_h_disabled_by_default_zero(self):
        """max_hold=0（缺省物化）禁用。"""
        pos = make_position(entry_bar_seq=0)
        account = make_account(position=pos, bar_seq=100)
        intents = interpret(
            make_bar(seq=100), BarSignals(), account, make_params())
        assert intents == []

    def test_i_time_exit_inside_bar_window(self):
        """bar 开盘 08:00 UTC、4h：t_exit=600（10:00）落区间 → 触发。"""
        import pandas as pd
        ts = pd.Timestamp("2026-08-30 08:00:00+00:00")
        pos = make_position()
        params = make_params(time_exit_utc=600)
        intents = interpret(
            make_bar(timestamp=ts, tf_minutes=240.0), BarSignals(),
            make_account(position=pos), params)
        assert intents[0].kind == "exit_time"
        assert intents[0].reason == "time_exit"

    def test_i_time_exit_window_right_edge_belongs_to_next_bar(self):
        """区间右端时刻（12:00）归下根 bar——左闭右开。"""
        import pandas as pd
        ts = pd.Timestamp("2026-08-30 08:00:00+00:00")
        pos = make_position()
        params = make_params(time_exit_utc=720)
        intents = interpret(
            make_bar(timestamp=ts, tf_minutes=240.0), BarSignals(),
            make_account(position=pos), params)
        assert intents == []

    def test_i_time_exit_at_open_minute_triggers(self):
        """t_exit 恰为 bar 开盘分钟（08:00=480）→ r=0<tf 触发。"""
        import pandas as pd
        ts = pd.Timestamp("2026-08-30 08:00:00+00:00")
        pos = make_position()
        params = make_params(time_exit_utc=480)
        intents = interpret(
            make_bar(timestamp=ts, tf_minutes=240.0), BarSignals(),
            make_account(position=pos), params)
        assert intents[0].kind == "exit_time"

    def test_i_time_exit_disabled_by_sentinel(self):
        """−1 哨兵禁用（0 是 0:00 合法值——哨兵与 0 的分界锚）。"""
        import pandas as pd
        ts = pd.Timestamp("2026-08-30 00:00:00+00:00")
        pos = make_position()
        intents = interpret(
            make_bar(timestamp=ts), BarSignals(),
            make_account(position=pos), make_params(time_exit_utc=-1))
        assert intents == []
        intents = interpret(
            make_bar(timestamp=ts), BarSignals(),
            make_account(position=pos), make_params(time_exit_utc=0))
        assert intents[0].kind == "exit_time"


class TestJudgmentOrder:
    """判定序：清算优先 SL、SL 优先 TP、止损优先时间出场。"""

    @staticmethod
    def _liquidatable_context():
        """fee=0/slip=0、5000 权益、ps=1.0、lev=10、entry=1.0 的多头
        （名义 50_000、margin 5_000、balance 0 → p_liq=0.9036144）。"""
        pos = make_position(
            side="long", entry=1.0, qty=50000.0, margin=5000.0)
        params = make_params(
            stop_loss=0.05, leverage=10)
        return pos, params

    def test_liquidation_priority_over_sl(self):
        """同 bar 清算与 SL 双触发 → 清算优先。"""
        pos, params = self._liquidatable_context()
        account = make_account(position=pos, balance=0.0, mark=1.0)
        # low=0.90 同时低于 p_liq(0.9036) 与 pct 止损线(0.95)
        intents = interpret(
            make_bar(open_=1.0, high=1.0, low=0.90, close=0.90),
            BarSignals(), account, params)
        assert [(i.kind, i.reason) for i in intents] \
            == [("exit_liquidation", "liquidation")]
        assert intents[0].trigger_price == pytest.approx(0.9036144)

    def test_leverage_one_never_liquidates(self):
        """杠杆=1 不判清算（无杠杆无清算）。"""
        pos = make_position(side="long", entry=1.0, qty=5000.0,
                            margin=5000.0)
        params = make_params(stop_loss=0.05, leverage=1)
        account = make_account(position=pos, balance=0.0, mark=1.0)
        intents = interpret(
            make_bar(open_=1.0, high=1.0, low=0.10, close=0.10),
            BarSignals(), account, params)
        # 低至 0.10 也无清算意图（pct SL 0.95 已触发——非清算）
        assert intents[0].kind == "exit_sl"

    def test_sl_priority_over_time_exit(self):
        """同 bar SL 触碰与 max_hold 到期 → 止损优先（时间出场不触发）。"""
        pos = make_position(entry=100.0, entry_bar_seq=0)
        params = make_params(stop_loss=0.05, max_hold=5)
        account = make_account(position=pos, bar_seq=4)
        intents = interpret(
            make_bar(seq=4, low=94.0), BarSignals(), account, params)
        assert intents[0].kind == "exit_sl"

    def test_time_exit_before_signal_exit_not_needed_both_closed(self):
        """时间出场先于信号出场（段内序：出场意图 reason 区分）。"""
        import pandas as pd
        ts = pd.Timestamp("2026-08-30 08:00:00+00:00")
        pos = make_position(entry=100.0)
        params = make_params(time_exit_utc=480)
        intents = interpret(
            make_bar(timestamp=ts), BarSignals(exit=True),
            make_account(position=pos), params)
        assert intents[0].kind == "exit_time"

    def test_reduce_priority_over_add_same_bar(self):
        """判定序「减仓→加仓」：同 bar 双信号 reduce 优先——加仓段
        该 bar 短路（spec 七段固定序；与 max_exposure 超限跳过同形制：
        不炸不降级，次根信号仍亮则次根执行）。"""
        pos = make_position(qty=100.0)
        intents = interpret(
            make_bar(), BarSignals(reduce=True, add=True),
            make_account(position=pos), make_params())
        assert [i.kind for i in intents] == ["reduce"]

    def test_time_exit_priority_over_max_hold_same_bar(self):
        """时间出场段内内序：time_exit_utc 先于 max_hold（对齐引擎
        内序——executor 八步序第 3 步注释声明；同 bar 双到期取
        time_exit，出场 reason 可区分两机制）。"""
        import pandas as pd
        ts = pd.Timestamp("2026-08-30 08:00:00+00:00")
        pos = make_position(entry_bar_seq=0)
        params = make_params(time_exit_utc=480, max_hold=5)
        # bar_seq=4 → bars_held=5=max_hold 到期；480=开盘分钟同触
        intents = interpret(
            make_bar(timestamp=ts, seq=4), BarSignals(),
            make_account(position=pos, bar_seq=4), params)
        assert [i.kind for i in intents] == ["exit_time"]


class TestMaxPositionLoss:
    """金额防线（add-position-sizing-params 段 3——收盘价浮亏口径）。

    手算锚：entry=100、qty=10 → mark=90 时 upnl=−100、loss=100；
    判定式 loss=−unrealized_pnl+funding_paid（资金费带符号：付出为
    正=加深浮亏）；≥阈值触发（含等）。
    """

    def test_reaches_threshold_triggers(self):
        """达线触发：loss=100 ≥ max_position_loss=100 → exit_max_loss。"""
        pos = make_position(entry=100.0, qty=10.0)
        params = make_params(max_position_loss=100.0)
        intents = interpret(
            make_bar(close=90.0), BarSignals(),
            make_account(position=pos, mark=90.0), params)
        assert [(i.kind, i.direction, i.reason) for i in intents] \
            == [("exit_max_loss", "long", "max_loss")]

    def test_exactly_at_threshold_is_inclusive(self):
        """恰好达线（==）触发（≥ 含等）；近线一侧不触发。

        恰等锚：mark=94 → loss=60、阈值 60 == 含等触发；近线锚：
        mark=94.001 → loss≈59.99 < 59.995 不触发。
        """
        pos = make_position(entry=100.0, qty=10.0)
        exact = interpret(
            make_bar(close=94.0), BarSignals(),
            make_account(position=pos, mark=94.0),
            make_params(max_position_loss=60.0))
        assert [i.kind for i in exact] == ["exit_max_loss"]
        near = interpret(
            make_bar(close=94.001), BarSignals(),
            make_account(position=pos, mark=94.001),
            make_params(max_position_loss=59.995))
        assert near == []

    def test_below_threshold_behaves_as_undeclared(self):
        """未达线零副作用：近线未达与未声明（0=禁用）输出一致。"""
        pos = make_position(entry=100.0, qty=10.0)
        near = interpret(
            make_bar(close=90.0), BarSignals(),
            make_account(position=pos, mark=90.0),
            make_params(max_position_loss=100.01))
        undeclared = interpret(
            make_bar(close=90.0), BarSignals(),
            make_account(position=pos, mark=90.0),
            make_params(max_position_loss=0.0))
        assert near == []
        assert near == undeclared

    def test_disabled_by_default_zero_never_triggers(self):
        """未声明（物化 0.0=禁用哨兵）：深亏也不触发（存量行为不变）。"""
        pos = make_position(entry=100.0, qty=10.0)
        intents = interpret(
            make_bar(close=50.0), BarSignals(),
            make_account(position=pos, mark=50.0), make_params())
        assert intents == []

    def test_funding_paid_component_pushes_to_threshold(self):
        """资金费分量进判定：价格浮亏 60+资金费 40=100 达线触发；
        funding_paid=0 对照不触发（价格分量单独未达线）。"""
        pos_with = make_position(entry=100.0, qty=10.0, funding_paid=40.0)
        params = make_params(max_position_loss=100.0)
        hit = interpret(
            make_bar(close=94.0), BarSignals(),
            make_account(position=pos_with, mark=94.0), params)
        assert [i.kind for i in hit] == ["exit_max_loss"]
        pos_without = make_position(entry=100.0, qty=10.0)
        miss = interpret(
            make_bar(close=94.0), BarSignals(),
            make_account(position=pos_without, mark=94.0), params)
        assert miss == []

    def test_negative_funding_received_offsets_loss(self):
        """资金费收入（负值）抵减浮亏：价格浮亏 100−收入 40=60 不达线。"""
        pos = make_position(entry=100.0, qty=10.0, funding_paid=-40.0)
        params = make_params(max_position_loss=100.0)
        intents = interpret(
            make_bar(close=90.0), BarSignals(),
            make_account(position=pos, mark=90.0), params)
        assert intents == []

    def test_short_side_direction_sign(self):
        """空头双向（公式带符号天然成立）：entry=100 价升 110 →
        upnl=−10×(110−100)=−100 达线触发、direction="short"。"""
        pos = make_position(side="short", entry=100.0, qty=10.0)
        params = make_params(max_position_loss=100.0)
        intents = interpret(
            make_bar(close=110.0), BarSignals(),
            make_account(position=pos, mark=110.0), params)
        assert [(i.kind, i.direction) for i in intents] \
            == [("exit_max_loss", "short")]

    def test_short_profit_side_never_triggers(self):
        """空头盈利态（价跌）loss 为负不触发。"""
        pos = make_position(side="short", entry=100.0, qty=10.0)
        params = make_params(max_position_loss=100.0)
        intents = interpret(
            make_bar(close=90.0), BarSignals(),
            make_account(position=pos, mark=90.0), params)
        assert intents == []

    def test_no_position_never_triggers(self):
        """空仓不判（pos None 守卫——段 3 前置）。"""
        intents = interpret(
            make_bar(close=90.0), BarSignals(), make_account(),
            make_params(max_position_loss=0.01))
        assert intents == []

    def test_reentry_resumes_from_zero_funding(self):
        """全平→再入场：新持仓快照 funding_paid=0（旧值不残留），
        判定 loss 只含价格分量——再入场从零累计的判定侧锚（生命周期
        归集断言见 test_ledger）。"""
        old_pos = make_position(entry=100.0, qty=10.0, funding_paid=40.0)
        fresh_pos = make_position(entry=100.0, qty=10.0)
        params = make_params(max_position_loss=100.0)
        # 旧持仓：价格 60+资金费 40=100 达线；新持仓同价不达线
        hit = interpret(
            make_bar(close=94.0), BarSignals(),
            make_account(position=old_pos, mark=94.0), params)
        assert [i.kind for i in hit] == ["exit_max_loss"]
        miss = interpret(
            make_bar(close=94.0), BarSignals(),
            make_account(position=fresh_pos, mark=94.0), params)
        assert miss == []


class TestMaxLossAttributionOrder:
    """族 6 归因序：金额防线段位次=SL/TP 后、时间出场前。

    同根双触发的归因：归止损（SL 在前）/归金额防线（先于时间出场
    与信号出场）。
    """

    def test_sl_wins_over_max_loss_same_bar(self):
        """与止损同根双触发归止损：bar low 触碰 SL 线（95）且 mark=94.5
        浮亏 55>50 达线 → exit_sl（段 2 在段 3 前）。"""
        pos = make_position(entry=100.0, qty=10.0)
        params = make_params(stop_loss=0.05, max_position_loss=50.0)
        intents = interpret(
            make_bar(low=94.5, close=94.5), BarSignals(),
            make_account(position=pos, mark=94.5), params)
        assert [(i.kind, i.reason) for i in intents] == [("exit_sl", "sl")]

    def test_tp_wins_over_max_loss_same_bar(self):
        """止盈同根优先（盈利态本不达线——反向对照：TP 触发时 loss
        为负，仅证明段序不倒挂）。"""
        pos = make_position(entry=100.0, qty=10.0)
        params = make_params(stop_loss=0.05, take_profit=0.10,
                             max_position_loss=50.0)
        intents = interpret(
            make_bar(high=110.5, low=99.0, close=110.0), BarSignals(),
            make_account(position=pos, mark=110.0), params)
        assert [i.kind for i in intents] == ["exit_tp"]

    def test_max_loss_wins_over_time_exit_same_bar(self):
        """与时间出场同根归金额防线：time_exit 到期+达线 → exit_max_loss。"""
        import pandas as pd
        ts = pd.Timestamp("2026-08-30 08:00:00+00:00")
        pos = make_position(entry=100.0, qty=10.0)
        params = make_params(time_exit_utc=480, max_position_loss=100.0)
        intents = interpret(
            make_bar(timestamp=ts, close=90.0), BarSignals(),
            make_account(position=pos, mark=90.0), params)
        assert [i.kind for i in intents] == ["exit_max_loss"]

    def test_max_loss_wins_over_max_hold_same_bar(self):
        """与 max_hold 同根归金额防线：到期+达线 → exit_max_loss。"""
        pos = make_position(entry_bar_seq=0)
        params = make_params(max_hold=5, max_position_loss=100.0)
        intents = interpret(
            make_bar(seq=4, close=90.0), BarSignals(),
            make_account(position=pos, mark=90.0, bar_seq=4), params)
        assert [i.kind for i in intents] == ["exit_max_loss"]

    def test_max_loss_wins_over_signal_exit_same_bar(self):
        """与信号出场同根归金额防线：exit 信号+达线 → exit_max_loss。"""
        pos = make_position(entry=100.0, qty=10.0)
        params = make_params(max_position_loss=100.0)
        intents = interpret(
            make_bar(close=90.0), BarSignals(exit=True),
            make_account(position=pos, mark=90.0), params)
        assert [i.kind for i in intents] == ["exit_max_loss"]
