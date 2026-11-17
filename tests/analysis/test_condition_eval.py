"""条件求值内核冻结锚（retire-legacy-subsystems 任务 2 E2）。

STATISTICS_ACTION_KEYS 26 键集逐键冻结——防迁移后分派表与键集
漂移（键集增删须连带更新本锚与 action_map 断言）。
"""
from __future__ import annotations

from core.analysis.condition_eval import STATISTICS_ACTION_KEYS

_FROZEN_26 = frozenset({
    "touch", "cross_above", "cross_below", "breakout", "breakdown",
    "gt", "lt", "ge", "le",
    "price_above", "price_below",
    "eq", "lookback_all", "wick_touch",
    "spike", "shrink",
    "divergence_top", "divergence_bottom",
    "consecutive_up", "consecutive_down",
    "cross_above_series", "cross_below_series",
    "lookback_any", "within_bars_after",
    "touch_bounce", "role_reversal",
})


def test_statistics_action_keys_frozen_26() -> None:
    """统计面动作键集恰等 26 键冻结集（E2——多键/漏键/改名必红）。"""
    assert STATISTICS_ACTION_KEYS == _FROZEN_26
    assert len(STATISTICS_ACTION_KEYS) == 26
