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

    def test_gate_covers_scaling_groups(self, db):
        """组覆盖补全：分批三组（exit_partial/add_trigger/add_suppress）
        统计专属动作登记即拒（拒绝面覆盖全部条件组）。"""
        for group in ("exit_partial_conditions",
                      "add_trigger_conditions",
                      "add_suppress_conditions"):
            conds = {
                "when_conditions": [
                    {"subject": _RSI, "action": "gt", "target": 60}],
                "then_conditions": [
                    {"subject": _RSI, "action": "lt", "target": 40}],
                group: [{"subject": _RSI, "action": "spike", "target": 0}],
            }
            with pytest.raises(ResearchError) as ei:
                register_hypothesis(
                    db, conditions=conds, statement="g",
                    data_ranges=_RANGES, strategy_params=dict(_SP))
            assert ei.value.code == "RESEARCH_CONDITION_INVALID", group
            assert ei.value.detail["action"] == "spike", group
            assert group in ei.value.detail["field"], group

    def test_two_sets_disjoint_union_complete(self):
        """常量域自洽：可表达 ∩ 统计专属 = 空（差集无重叠——重叠
        动作的语义归属将歧义）。"""
        assert RESEARCH_EXPRESSIBLE_ACTIONS & STATISTICS_ONLY_ACTIONS \
            == frozenset()


class TestR1ThreeActionTranslation:
    """R1 三键翻译映射端到端（indicator-and-action-repair 2.7，卡 E
    ——tester 锚 C2-D1/D2/D3+F1 单源恰等）。

    eq 进 _ACTION_MAP 常规；touch_bounce/role_reversal 专门分支参数
    透传（不写键=产物不含缺省参数键——执行层缺省兜底）。
    """

    _BB = {"indicator": "BB", "params": {"period": 20, "std": 2},
           "field": "lower"}
    _BULL = {"indicator": "BullishDivergence", "params": {}}

    def _tr(self, db, when_cond):
        hid = register_hypothesis(
            db,
            conditions={"when_conditions": [when_cond],
                        "then_conditions": [
                            {"subject": self._BULL, "action": "gt",
                             "target": 60}]},
            statement="r1", data_ranges=_RANGES,
            strategy_params=dict(_SP))["hypothesis_id"]
        return translate_hypothesis(db, hypothesis_id=hid)

    def _gene_condition(self, db, when_cond):
        from core.persistence.repository.strategy import get_strategy
        t = self._tr(db, when_cond)
        dna = get_strategy(db, t["strategy_id"])["dna_json"]
        genes = __import__("json").loads(dna)["signal_genes"]
        entries = [g for g in genes if g["role"] == "entry_trigger"]
        return entries[0]["condition"]

    def test_d1_eq_full_chain(self, db):
        """D1 端到端主锚：pattern 列 eq 1 入场假设→登记→translate→
        产物条件形制匹配 executor 求值分支（threshold float 化）。"""
        cond = self._gene_condition(
            db, {"subject": self._BULL, "action": "eq", "target": 1})
        assert cond == {"type": "eq", "threshold": 1.0}

    def test_d2_eq_default_threshold(self, db):
        """D2 eq threshold 两态：显式携带物化 / executor 缺省=1 兜底
        （登记 target 兼任 threshold——不携带键的缺省形由求值层兜底，
        产物层无缺省键形制）。"""
        cond = self._gene_condition(
            db, {"subject": self._BULL, "action": "eq", "target": 0})
        assert cond == {"type": "eq", "threshold": 0.0}

    def test_d3_touch_bounce_two_states(self, db):
        """D3 touch_bounce 两态：不携带参数→产物恰 {"type"}（执行层
        缺省不物化）；携带 direction/proximity_pct→恰这些键透传。"""
        cond_bare = self._gene_condition(
            db, {"subject": self._BB, "action": "touch_bounce",
                 "target": 0})
        assert cond_bare == {"type": "touch_bounce"}, \
            "不携带键=产物不含缺省参数键"
        cond_full = self._gene_condition(
            db, {"subject": self._BB, "action": "touch_bounce",
                 "target": 0, "direction": "resistance",
                 "proximity_pct": 0.02})
        assert cond_full == {"type": "touch_bounce",
                             "direction": "resistance",
                             "proximity_pct": 0.02}, "全量透传不丢键"

    def test_d3_role_reversal_two_states(self, db):
        """D3 role_reversal 两态：缺省态恰 {"type"}；role/lookback
        显式态恰这些键。"""
        cond_bare = self._gene_condition(
            db, {"subject": self._BB, "action": "role_reversal",
                 "target": 0})
        assert cond_bare == {"type": "role_reversal"}
        cond_full = self._gene_condition(
            db, {"subject": self._BB, "action": "role_reversal",
                 "target": 0, "role": "support", "lookback": 20})
        assert cond_full == {"type": "role_reversal", "role": "support",
                             "lookback": 20}

    # 专门分支键（design 卡 E：多参透传不走 _ACTION_MAP 单参
    # lambda——沿 within_bars_after 先例；恰等面=map ∪ 分支键集）
    _SPECIAL_BRANCH_ACTIONS = frozenset(
        {"within_bars_after", "touch_bounce", "role_reversal"})

    def test_f1_single_source_exact_equality(self):
        """⑥单源恰等断言落地（2.7 生效）：RESEARCH_EXPRESSIBLE_
        ACTIONS == frozenset(_ACTION_MAP) ∪ 专门分支键集（14=11+3
        ——design 卡 E：touch_bounce/role_reversal/wthin_bars_after
        走专门分支不进 map；扩动作漏改任一侧即红）。"""
        from core.analysis.hypothesis_to_strategy import _ACTION_MAP
        assert RESEARCH_EXPRESSIBLE_ACTIONS == (
            frozenset(_ACTION_MAP) | self._SPECIAL_BRANCH_ACTIONS)

    def test_d1_backtest_evaluable(self, tmp_path):
        """D1 四层贯通末段：产物基因经 executor 求值可触发（eq 条件
        在布尔 pattern 列上真实回测求值——合成数据冒烟）。"""
        import json as _json
        import numpy as np
        import pandas as pd
        from core.features.indicators import compute_all_indicators
        from core.strategy.executor import dna_to_signal_set
        from tests.helpers.data_factory import make_ohlcv

        hid_cond = {"subject": self._BULL, "action": "eq", "target": 1}
        db = tmp_path / "q2.db"
        init_db_ext(db)
        from core.analysis.executor import ensure_research_tables
        ensure_research_tables(db)
        t = self._tr(db, hid_cond)
        from core.persistence.repository.strategy import get_strategy
        from core.strategy.dna import StrategyDNA
        dna = StrategyDNA.from_json(
            get_strategy(db, t["strategy_id"])["dna_json"])
        df = compute_all_indicators(make_ohlcv(300, seed=1))
        sig = dna_to_signal_set(dna, df)
        # 求值面健康：entries 布尔序列产出（触发与否由数据决定，
        # 冒烟断言=类型与索引对齐非崩溃）
        assert sig.entries.dtype == bool
        assert len(sig.entries) == len(df)

    def test_d1_full_chain_triggers_align_pattern(self, tmp_path):
        """D1 端到端强化（spec Scenario「登记到回测端到端」：全链成功
        且触发计数>0）：eq 条件全链（登记→translate→回测求值）触发
        bar 与形态列 True 逐 bar 对齐——「eq 1=形态列 True 的 bar
        触发」的 spec 字面断言（原 test_d1_backtest_evaluable 冒烟
        断言不锁触发，本用例补行为级）。"""
        import pandas as pd
        from core.features.indicators import compute_all_indicators
        from core.strategy.executor import dna_to_signal_set
        from tests.helpers.data_factory import make_ohlcv

        hid_cond = {"subject": self._BULL, "action": "eq", "target": 1}
        db = tmp_path / "q3.db"
        init_db_ext(db)
        from core.analysis.executor import ensure_research_tables
        ensure_research_tables(db)
        t = self._tr(db, hid_cond)
        from core.persistence.repository.strategy import get_strategy
        from core.strategy.dna import StrategyDNA
        dna = StrategyDNA.from_json(
            get_strategy(db, t["strategy_id"])["dna_json"])
        df = compute_all_indicators(make_ohlcv(300, seed=1))
        entry_genes = [g for g in dna.signal_genes
                       if g.role.value == "entry_trigger"]
        assert len(entry_genes) == 1, "构造前提：入场侧恰一条 eq 基因"
        sig = dna_to_signal_set(dna, df)
        pat = df["pattern_bullish_divergence"] == 1
        assert int(pat.sum()) > 0, "构造前提：形态列有 True（seed=1=43）"
        assert int(sig.entries.sum()) == int(pat.sum()), \
            "触发计数>0 且等于形态列 True 数"
        assert bool((sig.entries == pat).all()), \
            "entries 与形态列 True 逐 bar 对齐（eq 1 语义）"
