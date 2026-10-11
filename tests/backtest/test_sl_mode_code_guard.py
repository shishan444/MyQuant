"""sl_mode_code 未知模式显式拒绝（review-042 CARD-13）。

修复前：未知 sl_mode 静默按 pct 兜底（.get 缺省 0.0）——库内脏数据
（dna_json 被直改后 sl_mode 破损）会让 atr/trail 止损语义无声丢失，
回测失真不可察，违反「不静默退化」纪律。
"""

import pytest

pytestmark = [pytest.mark.integration]

from core.backtest.engine import sl_mode_code


class TestSlModeCodeGuard:
    def test_known_modes_mapped(self):
        """三档合法映射锚（单源 _SL_MODE_CODE）。"""
        assert sl_mode_code("pct") == 0.0
        assert sl_mode_code("atr") == 1.0
        assert sl_mode_code("trail") == 2.0

    def test_unknown_mode_raises(self):
        """未知模式 raise ValueError 而非静默 pct 兜底。"""
        with pytest.raises(ValueError, match="sl_mode"):
            sl_mode_code("trailing")

    def test_none_mode_raises(self):
        """None（键缺失形脏数据）同拒。"""
        with pytest.raises(ValueError):
            sl_mode_code(None)
