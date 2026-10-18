"""二号双实现对拍+装配基准测试（build-position-registry 任务 4.5/4.6
——R5+R6 载体一）。

R5 契约（design §6 对拍网格+§11 承载分工）：条目 parity_grid 数据
驱动，njit 侧执行器=引擎 risk_budget_qty 直调（测试注入——positioning
包零引擎引用，依赖单向性由此保持）。三断言域：
- equal 域（名义<cap）：纯 Python size_open==引擎 risk_budget_qty
  qty（未触 cap 多组，含三档距离）；
- reject 域：纯 Python 0.0==risk_budget_qty (0.0, 0.0)；
- cap 域（名义>cap）：纯 Python 裸值不截断断言+risk_budget_qty
  (cap/price, capped=1.0) 在位断言——两侧行为各自断言，不做等值
  断言（截断等值由 R7 执行层直对拍承载）；
- 锚点 500/2000 两实现同值（无截断参数下公式对拍——公式权威驻
  注册条目，引擎函数为对拍消费方）。
等值断言器单点（_run_pure/_run_njit 两辅助承载，不重复实现）。

R6 契约（design §8/§11 基准表）：expand_for_backtest 三元组 vs
改造前 _per_dna_risk_params p[7]/p[8]/p[9] 实拍硬编码基准（四组
代表 DNA——②声明 initial 0.2 ③fixed+rf 全档物化关键组④risk_
budget）逐位一致；引擎侧同源扩面在 tests/backtest/test_position_
params_engine.py（本文件承载适配器 vs 基准层）。
"""
from __future__ import annotations

import pytest

pytestmark = [pytest.mark.unit]

from core.positioning import (
    MODEL_RISK_BUDGET,
    expand_for_backtest,
    get_model,
)
from core.positioning.interfaces import SizingInput
from core.positioning.models import (
    RISK_BUDGET_ANCHORS,
    RISK_BUDGET_PARITY_GRID,
    ParityCase,
    RiskBudgetParams,
)


def _run_pure(case: ParityCase) -> float:
    """纯 Python 侧执行器（纸盘消费形制——注册表条目实现直调）。"""
    inp = case.inputs
    return get_model(MODEL_RISK_BUDGET).implementation.size_open(
        SizingInput(equity=inp["equity"], base_price=inp["base_price"],
                    atr_value=inp.get("atr_value")),
        RiskBudgetParams(risk_fraction=inp["risk_fraction"],
                         sl_mode=inp["sl_mode"],
                         stop_loss=inp["stop_loss"]))


def _run_njit(case: ParityCase) -> tuple[float, float]:
    """njit 侧执行器=引擎 risk_budget_qty 直调（测试注入）。"""
    from core.backtest.engine import risk_budget_qty
    inp = case.inputs
    return risk_budget_qty(
        inp["equity"], inp["base_price"], inp["stop_dist"],
        inp["risk_fraction"], inp["leverage"], inp["max_exposure"])


def _cases(zone: str) -> tuple[ParityCase, ...]:
    return tuple(c for c in RISK_BUDGET_PARITY_GRID if c.zone == zone)


class TestEqualZoneParity:
    """R5 equal 域：两实现等值（未触 cap——名义<cap 判据由网格标注）。"""

    @pytest.mark.parametrize(
        "case", _cases("equal"), ids=[c.name for c in _cases("equal")])
    def test_pure_equals_njit_qty(self, case):
        """纯 Python size_open==pure_expected==risk_budget_qty qty，
        capped=0.0（未截断）；pure_expected 与主式手算对照（数据
        完整性——期望非实现生成）。"""
        inp = case.inputs
        hand = inp["equity"] * inp["risk_fraction"] / inp["stop_dist"]
        assert case.pure_expected == pytest.approx(hand, rel=1e-12)
        pure = _run_pure(case)
        assert pure == pytest.approx(case.pure_expected, rel=1e-12)
        qty, capped = _run_njit(case)
        assert qty == pytest.approx(pure, rel=1e-12), case.name
        assert capped == 0.0


class TestRejectZoneParity:
    """R5 reject 域：两实现拒绝等值（0.0==(0.0, 0.0)）。"""

    @pytest.mark.parametrize(
        "case", _cases("reject"),
        ids=[c.name for c in _cases("reject")])
    def test_both_sides_zero(self, case):
        """纯 Python 0.0（返回值拒绝）==risk_budget_qty (0.0, 0.0)
        （防御直构残缺组合的 (0,0) 回退语义——两实现拒绝语义对拍）。"""
        assert _run_pure(case) == 0.0
        assert _run_njit(case) == (0.0, 0.0)


class TestCapZoneRespectiveAnchors:
    """R5 cap 域：两侧行为各自断言（不做等值断言——截断等值由 R7
    执行层直对拍承载）。"""

    @pytest.mark.parametrize(
        "case", _cases("cap"), ids=[c.name for c in _cases("cap")])
    def test_pure_bare_value_njit_truncates(self, case):
        """纯 Python 输出裸值（主式直出，不截断）+risk_budget_qty
        截断在位（cap=权益×min(leverage, max_exposure)、qty=cap/price、
        capped=1.0——执行层护栏式的引擎侧在位锚）。"""
        inp = case.inputs
        hand = inp["equity"] * inp["risk_fraction"] / inp["stop_dist"]
        # 纯 Python：裸值不截断（护栏分离——模型无截断输入）
        assert _run_pure(case) == pytest.approx(hand, rel=1e-12)
        assert _run_pure(case) == pytest.approx(
            case.pure_expected, rel=1e-12)
        # njit：截断行为在位（手算 cap）
        cap = inp["equity"] * min(inp["leverage"], inp["max_exposure"])
        assert inp["base_price"] * hand > cap, "cap 域前提：名义须超 cap"
        qty, capped = _run_njit(case)
        assert qty == pytest.approx(cap / inp["base_price"], rel=1e-12)
        assert capped == 1.0


class TestAnchorPointsTwoImplementationsAgree:
    """R5 锚点对拍：500/2000 两实现同值（无截断参数下公式等值——
    引擎函数与注册条目手算锚对拍，公式权威驻注册条目）。"""

    @pytest.mark.parametrize("anchor", RISK_BUDGET_ANCHORS,
                             ids=[a.name for a in RISK_BUDGET_ANCHORS])
    def test_anchor_values_agree_across_implementations(self, anchor):
        """两锚输入在无截断 lev/max_exp（cap 充裕不触发）下：纯
        Python size_open==锚期望==risk_budget_qty qty、capped=0.0。
        stop_dist 按距离契约手算（pct 域=base_price×stop_loss）。"""
        from core.backtest.engine import risk_budget_qty
        inp = dict(anchor.inputs)
        dist = inp["base_price"] * inp["stop_loss"]
        qty, capped = risk_budget_qty(
            inp["equity"], inp["base_price"], dist,
            inp["risk_fraction"], leverage=100.0, max_exposure=100.0)
        assert qty == pytest.approx(anchor.expected, rel=1e-12)
        assert capped == 0.0
        case = ParityCase(  # 复用等值断言器（单点——不另写等值逻辑）
            name=anchor.name, inputs={**inp, "stop_dist": dist,
                                      "leverage": 100.0,
                                      "max_exposure": 100.0},
            zone="equal", pure_expected=anchor.expected)
        assert _run_pure(case) == pytest.approx(anchor.expected,
                                                rel=1e-12)


class TestExpandForBacktestBaseline:
    """R6 载体一：适配器三元组 vs 改造前装配实拍基准（design §11
    基准表——2.1 改造前 _per_dna_risk_params p[7]/p[8]/p[9] 实拍
    核对后冻结；tester 2026-10-18 独立复证通过）。"""

    @pytest.mark.parametrize(
        ("kwargs", "expected"),
        [
            pytest.param(
                dict(position_mode=None, position_size=0.3),
                (0.0, 0.0, 0.3), id="①fixed 缺省"),
            pytest.param(
                dict(position_mode=None, position_size=0.3,
                     initial_fraction=0.2),
                (0.0, 0.0, 0.2), id="②声明 initial_fraction"),
            pytest.param(
                dict(position_mode=None, position_size=0.3,
                     risk_fraction=0.01),
                (0.0, 0.01, 0.3), id="③fixed+rf 声明（全档物化关键组）"),
            pytest.param(
                dict(position_mode="risk_budget", position_size=0.3,
                     risk_fraction=0.01),
                (1.0, 0.01, 0.3), id="④risk_budget"),
        ])
    def test_triple_matches_hardcoded_baseline(self, kwargs, expected):
        """expand_for_backtest 三元组逐位==基准（fixed 档也物化 rf、
        risk_budget 档也物化 efs——全档物化语义的等值关键）。"""
        exp = expand_for_backtest(**kwargs)
        assert (exp.position_mode_code, exp.risk_fraction,
                exp.effective_first_size) == expected
