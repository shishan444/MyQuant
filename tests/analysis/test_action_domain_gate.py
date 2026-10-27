"""登记 action 域校验前移（indicator-and-action-repair 2.6，B6 卡 D
4.2——tester 锚 C2-D4+F3）。

统计可算但翻译不可表达的动作（STATISTICS_ONLY_ACTIONS 族）登记即拒
——研究者动作不再浪费性消耗到 translate 才 RESEARCH_NOT_EXPRESSIBLE
断链；在集内的正常登记路径零变化；错误 detail 含合法集提示。
"""
from __future__ import annotations

from pathlib import Path

import pytest

pytestmark = [pytest.mark.integration]

from core.analysis.errors import ResearchError
from core.analysis.hypothesis_service import (
    RESEARCH_EXPRESSIBLE_ACTIONS, STATISTICS_ONLY_ACTIONS,
    register_hypothesis)
from core.analysis.hypothesis_to_strategy import translate_hypothesis
from core.persistence.db_ext import init_db_ext

_RSI = {"indicator": "RSI", "params": {"period": 14}}
_RANGES = [{"symbol": "BTCUSDT", "timeframe": "4h",
            "start": "2024-01-01T00:00:00Z", "end": "2024-02-01T00:00:00Z"}]
_SP = {"stop_loss": 0.05}


@pytest.fixture()
def db(tmp_path: Path) -> Path:
    d = tmp_path / "q.db"
    init_db_ext(d)
    from core.analysis.executor import ensure_research_tables
    ensure_research_tables(d)
    return d


def _register(db, action, subject=None, when=True):
    cond = {"subject": subject or _RSI, "action": action, "target": 50}
    key = "when_conditions" if when else "then_conditions"
    other = "then_conditions" if when else "when_conditions"
    return register_hypothesis(
        db, conditions={key: [cond],
                        other: [{"subject": _RSI, "action": "gt",
                                 "target": 60}]},
        statement="s", data_ranges=_RANGES, strategy_params=dict(_SP))


class TestRegistrationGate:
    def test_statistics_only_rejected(self, db):
        """D4 主锚：STATISTICS_ONLY 族逐键登记即拒（422 结构化——
        消耗面从 translate 断链前移到登记面）。"""
        for action in ("spike", "touch", "shrink", "lookback_all",
                       "divergence_top", "consecutive_up"):
            with pytest.raises(ResearchError) as ei:
                _register(db, action)
            assert ei.value.code == "RESEARCH_CONDITION_INVALID", action
            d = ei.value.detail
            assert d["action"] == action
            assert "action" in d["field"], d["field"]
            assert d["legal_range"] == sorted(RESEARCH_EXPRESSIBLE_ACTIONS)

    def test_error_detail_slow_path(self, db):
        """③/F3 错误 detail 含合法集提示+统计专属指引（错误码族一致
        性——RESEARCH_CONDITION_INVALID 族）。"""
        with pytest.raises(ResearchError) as ei:
            _register(db, "wick_touch")
        assert ei.value.code == "RESEARCH_CONDITION_INVALID"
        assert "STATISTICS_ONLY_ACTIONS" in ei.value.detail["slow_path"]
        assert sorted(RESEARCH_EXPRESSIBLE_ACTIONS) == \
            ei.value.detail["legal_range"]

    def test_expressible_set_registration_unchanged(self, db):
        """②/F7 在集内正常登记零变化：可表达动作（当前翻译已支持
        的代表键）登记+translate 全链照旧成功。"""
        hid = _register(db, "cross_above")["hypothesis_id"]
        out = translate_hypothesis(db, hypothesis_id=hid)
        assert out["status"] == "draft"

    def test_gate_covers_all_groups(self, db):
        """组覆盖：then/exit_partial 组同拒（组无关的一致拒绝面）。"""
        with pytest.raises(ResearchError) as ei:
            _register(db, "spike", when=False)
        assert ei.value.code == "RESEARCH_CONDITION_INVALID"
        assert ei.value.detail["action"] == "spike"

    def test_two_sets_disjoint_union_complete(self):
        """常量域自洽：可表达 ∩ 统计专属 = 空（差集无重叠——重叠
        动作的语义归属将歧义）。"""
        assert RESEARCH_EXPRESSIBLE_ACTIONS & STATISTICS_ONLY_ACTIONS \
            == frozenset()
