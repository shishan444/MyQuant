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

from core.analysis.hypothesis_to_strategy import build_signal_genes
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
        """翻译入口经 build_signal_genes（translate_hypothesis 退役后
        产物形制断言经存活入口承载——_translate_conditions 单点）。"""
        return build_signal_genes(
            when_conditions=[when_cond],
            then_conditions=[{"subject": self._BULL, "action": "gt",
                              "target": 60}],
            db_path=db, strategy_timeframe="4h")

    def _gene_condition(self, db, when_cond):
        from core.strategy.dna import SignalRole
        genes, _sources = self._tr(db, when_cond)
        entries = [g for g in genes if g.role == SignalRole.ENTRY_TRIGGER]
        return entries[0].condition

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

    # test_f1（单源恰等断言）随 RESEARCH_EXPRESSIBLE_ACTIONS 守护对象
    # 退役删除（retire-legacy-subsystems 任务 3.2）——保留面重建锚在
    # tests/strategy/test_executor.py：_ACTION_MAP ⊆ STATISTICS_ACTION_KEYS

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
        from core.strategy.dna import (
            ExecutionGenes, LogicGenes, RiskGenes, StrategyDNA)
        genes, _sources = self._tr(db, hid_cond)
        dna = StrategyDNA(
            signal_genes=genes, logic_genes=LogicGenes(),
            execution_genes=ExecutionGenes(timeframe="4h"),
            risk_genes=RiskGenes(), signal_sources=[{"type": "rule"}])
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
        from core.strategy.dna import (
            ExecutionGenes, LogicGenes, RiskGenes, StrategyDNA)
        genes, _sources = self._tr(db, hid_cond)
        dna = StrategyDNA(
            signal_genes=genes, logic_genes=LogicGenes(),
            execution_genes=ExecutionGenes(timeframe="4h"),
            risk_genes=RiskGenes(), signal_sources=[{"type": "rule"}])
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


class TestNotExpressibleBranches:
    """不可表达拒绝三分支（阶段四②盲区消解——h2s 拆分模块的错误面
    补强，覆盖分支 review-042 CARD-14 bool 排除/close 基础列 ge/le/
    空 when 无插件）。"""

    _BB = {"indicator": "BB", "params": {"period": 20, "std": 2},
           "field": "lower"}

    def test_bool_target_rejected(self, db):
        """bool target 排除（CARD-14）：True/False 是布尔断言非阈值，
        列名 target 通道拒绝（与 list/None 非法形同拒绝面）。"""
        from core.analysis.errors import ResearchError
        with pytest.raises(ResearchError) as ei:
            build_signal_genes(
                when_conditions=[{"subject": self._BB, "action": "gt",
                                  "target": True}],
                then_conditions=[{"subject": self._BB, "action": "gt",
                                  "target": 2}],
                db_path=db, strategy_timeframe="4h")
        assert ei.value.code == "RESEARCH_NOT_EXPRESSIBLE"
        assert "须为数值" in ei.value.message

    def test_close_subject_ge_le_rejected(self, db):
        """close 基础列×编码列名 target 组合仅支持 lt/gt——ge/le 含
        等号形态显式拒绝（价格与指标线的严格比较约束）。"""
        from core.analysis.errors import ResearchError
        with pytest.raises(ResearchError) as ei:
            build_signal_genes(
                when_conditions=[{"subject": "close", "action": "ge",
                                  "target": "ema_20"}],
                then_conditions=[{"subject": self._BB, "action": "gt",
                                  "target": 2}],
                db_path=db, strategy_timeframe="4h")
        assert ei.value.code == "RESEARCH_NOT_EXPRESSIBLE"
        assert "lt/gt" in ei.value.message

    def test_empty_when_no_plugin_rejected(self, db):
        """空 when 且无插件源：无可表达条件整组拒绝（纯插件列最小形态
        when=[插件]/then=[] 放行语义的对面——when 侧不能空手）。"""
        from core.analysis.errors import ResearchError
        with pytest.raises(ResearchError) as ei:
            build_signal_genes(
                when_conditions=[],
                then_conditions=[{"subject": self._BB, "action": "gt",
                                  "target": 2}],
                db_path=db, strategy_timeframe="4h")
        assert ei.value.code == "RESEARCH_NOT_EXPRESSIBLE"
        assert "无可表达条件" in ei.value.message
