"""二号模型手算锚测试（build-position-registry 任务 4.3——R3）。

R3 契约（design §6+spec「二号模型 risk_budget 收编」Requirement）：
- 恒等式锚 500：equity=100000、rf=0.01、基准价 100、pct、sl=0.02
  （距离=2）→qty=500，每笔风险额恒等式 qty×dist=equity×rf
  （500×2=100000×0.01=1000）在未触 cap 时成立；
- 裸值锚 2000（护栏分离）：距离 0.5（sl=0.005）→裸 qty=2000——
  名义 200000 超 cap 也不截断（模型无截断输入，输出=主式直出）；
- 拒绝四源参数化（stop_loss≤0/base_price≤0 或 equity≤0/atr 模式下
  ATR None/NaN/≤0）→返回 0.0 不抛异常（第二逻辑源禁令）；
- 三档距离分派数值锚：pct/trail 同为基准价×sl（比例域——D-6 终态）
  同参数同值、atr=sl×ATR（倍数域）另式。
"""
from __future__ import annotations

import pytest

pytestmark = [pytest.mark.unit]

from core.positioning import MODEL_RISK_BUDGET, get_model
from core.positioning.interfaces import SizingInput
from core.positioning.models import (
    RISK_BUDGET_ANCHORS,
    AnchorCase,
    RiskBudgetParams,
)

_NAN = float("nan")


def _model():
    return get_model(MODEL_RISK_BUDGET).implementation


def _params(**overrides) -> RiskBudgetParams:
    base = dict(risk_fraction=0.01, sl_mode="pct", stop_loss=0.02)
    base.update(overrides)
    return RiskBudgetParams(**base)


def _invoke(anchor: AnchorCase) -> float:
    """二号 size_open 锚输入调用（inputs 键沿条目锚形制）。"""
    inp = anchor.inputs
    return _model().size_open(
        SizingInput(equity=inp["equity"], base_price=inp["base_price"],
                    atr_value=inp.get("atr_value")),
        RiskBudgetParams(
            risk_fraction=inp["risk_fraction"], sl_mode=inp["sl_mode"],
            stop_loss=inp["stop_loss"]))


class TestIdentityAndBareAnchors:
    """R3 恒等式锚+裸值锚（条目锚↔手算↔实现三层锁定）。"""

    def test_identity_anchor_500_with_risk_budget_identity(self):
        """恒等式锚：500×2=100000×0.01=1000（手算标准答案+实现复现
        +每笔风险额恒等式独立核算）。"""
        anchor = RISK_BUDGET_ANCHORS[0]
        assert anchor.name == "identity_untruncated"
        assert anchor.expected == 500.0  # 手算 100000×0.01÷2
        qty = _invoke(anchor)
        assert qty == pytest.approx(500.0, rel=1e-12)
        dist = anchor.inputs["base_price"] * anchor.inputs["stop_loss"]  # 2
        assert qty * dist == pytest.approx(
            anchor.inputs["equity"] * anchor.inputs["risk_fraction"],
            rel=1e-12)  # 500×2 == 100000×0.01

    def test_bare_value_anchor_2000_untruncated(self):
        """裸值锚（护栏分离）：距离 0.5→裸 qty=2000==主式直出手算
        （100000×0.01÷0.5）——名义 2000×100=200000 超 cap 也不截断
        （模型不持截断输入，截断归执行层——spec「模型输出不含截断」
        Scenario 的数值面）。"""
        anchor = RISK_BUDGET_ANCHORS[1]
        assert anchor.name == "bare_value_over_cap"
        assert anchor.expected == 2_000.0
        inp = anchor.inputs
        hand = (inp["equity"] * inp["risk_fraction"]
                / (inp["base_price"] * inp["stop_loss"]))  # 主式手算
        qty = _invoke(anchor)
        assert qty == pytest.approx(2_000.0, rel=1e-12)
        assert qty == pytest.approx(hand, rel=1e-12)  # 输出=主式直出


class TestRejectionFourSources:
    """R3 拒绝四源参数化（返回 0.0 不抛异常——逐源对齐现状防御链）。"""

    @pytest.mark.parametrize(
        ("params", "base_price", "equity", "atr"),
        [
            pytest.param(_params(stop_loss=0.0), 100.0, 100_000.0, None,
                         id="源1 stop_loss=0"),
            pytest.param(_params(stop_loss=-0.01), 100.0, 100_000.0, None,
                         id="源1 stop_loss<0"),
            pytest.param(_params(), 0.0, 100_000.0, None,
                         id="源2 base_price=0"),
            pytest.param(_params(), -5.0, 100_000.0, None,
                         id="源2 base_price<0"),
            pytest.param(_params(), 100.0, 0.0, None,
                         id="源2 equity=0"),
            pytest.param(_params(), 100.0, -1.0, None,
                         id="源2 equity<0"),
            pytest.param(_params(sl_mode="atr", stop_loss=2.0), 100.0,
                         100_000.0, None, id="源3 atr=None"),
            pytest.param(_params(sl_mode="atr", stop_loss=2.0), 100.0,
                         100_000.0, _NAN, id="源3 atr=NaN"),
            pytest.param(_params(sl_mode="atr", stop_loss=2.0), 100.0,
                         100_000.0, 0.0, id="源3 atr=0"),
            pytest.param(_params(sl_mode="atr", stop_loss=2.0), 100.0,
                         100_000.0, -2.5, id="源3 atr<0"),
        ])
    def test_rejected_input_returns_zero_without_raise(
            self, params, base_price, equity, atr):
        """四源各输入→size_open 返回 0.0 且全程无异常（拒绝以返回值
        表达——执行层按现状回退 fixed，第二逻辑源禁令）。"""
        qty = _model().size_open(
            SizingInput(equity=equity, base_price=base_price,
                        atr_value=atr), params)
        assert qty == 0.0, "四源输入应返回拒绝值 0.0"

    def test_valid_atr_context_not_rejected(self):
        """对照：同构有效输入（atr>0）正常出值——拒绝判定不误伤。"""
        qty = _model().size_open(
            SizingInput(equity=100_000.0, base_price=100.0, atr_value=2.5),
            _params(risk_fraction=0.02, sl_mode="atr", stop_loss=2.0))
        assert qty == pytest.approx(400.0)  # 100000×0.02÷(2.0×2.5)


class TestDistanceDispatchThreeModes:
    """R3 三档距离分派数值锚（design §6——自 _stop_distance 移驻语义）。"""

    def test_pct_and_trail_same_ratio_domain_same_value(self):
        """pct/trail 同式（距离=基准价×stop_loss 比例域——D-6 终态）：
        同参数两档输出逐位相等；数值手算锚 500。"""
        for sl_mode in ("pct", "trail"):
            qty = _model().size_open(
                SizingInput(equity=100_000.0, base_price=100.0),
                _params(sl_mode=sl_mode))
            assert qty == pytest.approx(
                100_000.0 * 0.01 / (100.0 * 0.02), rel=1e-12), sl_mode
        pct_qty = _model().size_open(
            SizingInput(100_000.0, 100.0), _params(sl_mode="pct"))
        trail_qty = _model().size_open(
            SizingInput(100_000.0, 100.0), _params(sl_mode="trail"))
        assert pct_qty == trail_qty  # 比例域同式实证

    def test_atr_mode_multiplier_domain_numeric_anchor(self):
        """atr 档另式（距离=stop_loss×该 bar ATR 倍数域）：rf=0.02、
        sl=2.0、ATR=2.5→dist=5→qty=100000×0.02÷5=400（与比例域 500
        数值可区分——两域分派锚）。"""
        qty = _model().size_open(
            SizingInput(100_000.0, 100.0, atr_value=2.5),
            _params(risk_fraction=0.02, sl_mode="atr", stop_loss=2.0))
        assert qty == pytest.approx(
            100_000.0 * 0.02 / (2.0 * 2.5), rel=1e-12)
        assert qty == pytest.approx(400.0, rel=1e-12)
