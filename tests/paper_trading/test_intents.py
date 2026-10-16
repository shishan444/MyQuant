"""信号解释器测试（rebuild-paper-trading 任务 1.3）。

验证面：映射规则 a-f 参数化（direction 过滤/反手两步/max_exposure
超限跳过不炸/mixed 符号 0/NaN 不开仓）+防线判定 g-i（pct/atr/trail
三模式触发价手算锚、trail 峰值含当根、max_hold 计数、time_exit
区间覆盖）+ATR 单 bar NaN/≤0 守卫（该 bar 无 SL/TP 意图且不回退
pct 距离）+判定序（清算优先 SL、SL 优先 TP、止损优先时间出场）。
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
                  trail_peak=100.0, entry_bar_seq=0) -> PositionSnapshot:
    return PositionSnapshot(
        side=side, entry_price=entry, quantity=qty, margin=margin,
        open_cost=qty * entry, sl_price=None, tp_price=None,
        trail_peak=trail_peak, entry_bar_seq=entry_bar_seq)


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
