"""纸盘恢复器现状容忍用例（D 类隔离——阶段四步骤 3 V3 化）。

D 类=现状记录锚：行为是现行实现的实际语义，但其「应然」口径未经
裁决（非 spec 定形）——隔离至本文件待口径裁决后升级或移除。与
test_recovery.py 的 A/B/C 类锚分开维护，避免容忍面混入规格面。

当前在册容忍面：
- 零 bar 豁免分支对意图判据 4 的短路（bars=0+balance NULL+持仓全
  NULL 时坏 pending_intent_json 也放行）——docstring「其余判据也
  无从触发」的实现语义；裁决点：零 bar 残态是否应校验意图可解析。
"""
from __future__ import annotations

from pathlib import Path

import pytest

pytestmark = [pytest.mark.unit]

from core.paper_trading.runtime.recovery import validate_recovery
from core.persistence.repository import paper
from tests.paper_trading.test_recovery import _RecoveryHarness


class TestZeroBarTolerances:
    """零 bar 豁免的 D 类现状容忍（待口径裁决）。"""

    def test_zero_bar_shortcircuits_intent_check(self, tmp_path: Path):
        """现状记录：bars=0+balance NULL+持仓核心字段组全 NULL（豁免
        域三条件齐备，收紧后持仓判据在此分支内先查）时判据 4 仍被
        短路（坏意图也放行）——豁免分支直接 return None。"""
        h = _RecoveryHarness(tmp_path)
        paper.update_task(
            h.db, h.task_id, status="running",
            started_at="2026-10-16T00:00:00+00:00")
        paper.append_bar(
            h.db, h.task_id, bar_time=h.df.index[0].isoformat(),
            fills=[], equity={"equity": 1.0, "balance": 1.0,
                              "unrealized_pnl": 0.0},
            task_state={"bars_processed": 0, "last_bar_time": None,
                        "pending_intent_json": "{broken"})
        assert validate_recovery(h.task()) is None
