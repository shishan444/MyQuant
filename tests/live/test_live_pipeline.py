"""第6步 LivePipeline 单元测试（决策翻译，复用 judgment 纯计算）。

构造 fake SignalSet + LiveAccountState，验证 open/close/hold/反手/方向过滤路径。
"""

import pytest

pytestmark = [pytest.mark.integration]  # B10.1 标记激活
import math

import pandas as pd
import pytest

from core.live_trading.pipeline import (
    LivePipeline,
    decision_to_action,
    to_judgment_state,
)
from core.live_trading.types import LiveAccountState, LivePosition, OrderAction
from core.trading.types import Decision


def _sig(entry=False, exit_=False, add=False, reduce_=False, direction=1.0):
    """构造单 bar fake SignalSet（iloc[0] 取值）。"""

    class _S:
        entries = pd.Series([entry])
        exits = pd.Series([exit_])
        adds = pd.Series([add])
        reduces = pd.Series([reduce_])
        entry_direction = pd.Series([direction])
        confidence = pd.Series([1.0])

    return _S()


def _state(side="", qty=0.0, entry=0.0, allowed="mixed", equity=10000.0, bars=5, pnl=0.0, margin=0.0):
    pos = None
    if side:
        pos = LivePosition(
            symbol="BTCUSDT", side=side, quantity=qty, entry_price=entry,
            unrealized_pnl=pnl, bars_held=bars, margin=margin,
        )
    return LiveAccountState(balance=equity, equity=equity, position=pos, allowed_direction=allowed)


class TestToJudgmentState:
    def test_flat(self):
        s = to_judgment_state(_state())
        assert s.has_position is False
        assert s.position_side == "flat"
        assert s.allowed_direction == "mixed"

    def test_with_long_position(self):
        s = to_judgment_state(_state(side="long", qty=0.1, entry=50000, margin=5000, equity=10000))
        assert s.has_position is True
        assert s.position_side == "long"
        assert s.position_entry == 50000
        assert s.actual_position_pct == pytest.approx(0.5)  # 5000/10000


class TestDecisionToAction:
    def test_open_long(self):
        assert decision_to_action(Decision(action="open", direction="long"), "") == OrderAction.OPEN_LONG

    def test_open_short(self):
        assert decision_to_action(Decision(action="open", direction="short"), "") == OrderAction.OPEN_SHORT

    def test_close_long(self):
        assert decision_to_action(Decision(action="close"), "long") == OrderAction.CLOSE_LONG

    def test_close_short(self):
        assert decision_to_action(Decision(action="close"), "short") == OrderAction.CLOSE_SHORT

    def test_add_is_none_v1(self):
        # v1 不处理加仓
        assert decision_to_action(Decision(action="add"), "long") is None

    def test_hold_is_none(self):
        assert decision_to_action(Decision(action="hold"), "") is None

    def test_close_no_position_none(self):
        assert decision_to_action(Decision(action="close"), "") is None


class TestPipelineDecide:
    def test_entry_long_when_flat(self):
        p = LivePipeline()
        action, dec, sig = p.decide(_sig(entry=True, direction=1.0), 0, _state(allowed="mixed"))
        assert action == OrderAction.OPEN_LONG

    def test_entry_short_when_flat_mixed(self):
        p = LivePipeline()
        action, _, _ = p.decide(_sig(entry=True, direction=-1.0), 0, _state(allowed="mixed"))
        assert action == OrderAction.OPEN_SHORT

    def test_direction_filter_blocks(self):
        # allowed=long 但信号 short → direction filter → hold
        p = LivePipeline()
        action, _, _ = p.decide(_sig(entry=True, direction=-1.0), 0, _state(allowed="long"))
        assert action is None

    def test_exit_closes_long(self):
        p = LivePipeline()
        action, _, _ = p.decide(
            _sig(exit_=True), 0, _state(side="long", qty=0.1, entry=50000, bars=5)
        )
        assert action == OrderAction.CLOSE_LONG

    def test_reverse_signal_closes_long_first(self):
        # 持多 + 反向 entry(short) → judgment Rule4 产出 close（先平，反手第一步）
        p = LivePipeline()
        action, dec, _ = p.decide(
            _sig(entry=True, direction=-1.0), 0,
            _state(side="long", qty=0.1, entry=50000, bars=5, allowed="mixed"),
        )
        assert action == OrderAction.CLOSE_LONG  # 先平多（反手第一步）
        assert dec.action == "close"

    def test_same_direction_add_is_none_v1(self):
        # 持多 + 同向 entry(long) → judgment Rule3 add → v1 None
        p = LivePipeline()
        action, dec, _ = p.decide(
            _sig(entry=True, direction=1.0), 0,
            _state(side="long", qty=0.1, entry=50000, bars=5, margin=3000, equity=10000),
        )
        assert action is None

    def test_no_signal_hold(self):
        p = LivePipeline()
        action, _, _ = p.decide(_sig(), 0, _state())
        assert action is None
