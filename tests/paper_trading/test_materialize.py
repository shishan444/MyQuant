"""物化单点测试（rebuild-paper-trading 任务 1.1）。

验证面：七缺省「有声明用声明/无声明用缺省」参数化、time_exit_utc=0
合法与 −1 哨兵边界、物化值与回测引擎装配点 _per_dna_risk_params
独立取值对照同值（两处缺省漂移必红——单源同值守护，含
position_mode/risk_fraction 两字段）。
"""
from __future__ import annotations

import pytest

pytestmark = [pytest.mark.unit]

import pandas as pd

from core.paper_trading.kernel.types import RiskParams, materialize
from core.strategy.dna import RiskGenes

# 七缺省字段：(字段, 声明值, 缺省值)
_DEFAULTABLE_FIELDS = [
    ("exit_fraction", 0.3, 0.5),
    ("add_fraction", 0.2, 0.1),
    ("max_exposure", 1.5, 1.0),
    ("max_hold", 7, 0),
    ("time_exit_utc", 600, -1),
    ("risk_fraction", 0.05, 0.0),
]


class TestMaterializeDefaults:
    """a. 七参数「有声明用声明/无声明用缺省」。"""

    @pytest.mark.parametrize(
        "field,declared,default", _DEFAULTABLE_FIELDS)
    def test_declared_wins_default_fills(self, field, declared, default):
        params = materialize(RiskGenes(**{field: declared}))
        assert getattr(params, field) == declared, \
            f"{field} 声明 {declared} 应被采用"
        params_default = materialize(RiskGenes())
        assert getattr(params_default, field) == default, \
            f"{field} 缺省应为 {default}"

    def test_position_mode_declared_and_default(self):
        """position_mode：声明透传；None→"fixed"；"fixed" 显式同值。"""
        assert materialize(
            RiskGenes(position_mode="risk_budget")).position_mode \
            == "risk_budget"
        assert materialize(RiskGenes()).position_mode == "fixed"
        assert materialize(RiskGenes(position_mode="fixed")).position_mode \
            == "fixed"

    def test_time_exit_zero_is_legal_not_disabled(self):
        """time_exit_utc=0 是 0:00 合法值；None 物化 −1 哨兵。"""
        assert materialize(RiskGenes(time_exit_utc=0)).time_exit_utc == 0
        assert materialize(RiskGenes()).time_exit_utc == -1

    def test_transparent_fields_passthrough(self):
        """七透传字段无缺省物化面（None 语义保留的 take_profit 除外）。"""
        rg = RiskGenes(
            stop_loss=0.02, take_profit=0.1, position_size=0.4,
            leverage=5, direction="short", sl_mode="atr", atr_period=21)
        params = materialize(rg)
        assert params.stop_loss == 0.02
        assert params.take_profit == 0.1
        assert params.position_size == 0.4
        assert params.leverage == 5
        assert params.direction == "short"
        assert params.sl_mode == "atr"
        assert params.atr_period == 21
        assert materialize(RiskGenes()).take_profit is None


class TestEngineAssemblyParity:
    """物化值与回测引擎装配点独立取值对照（单源同值守护）。

    引擎装配返回 9 元组 (sl_mode_val, atr_col, exit_fraction,
    add_fraction, max_exposure, max_hold, time_exit, position_mode_code,
    risk_fraction)——七缺省字段按位对照；position_mode 按码对照
    （1.0="risk_budget"/0.0=其余）。
    """

    @staticmethod
    def _engine_assembly(rg: RiskGenes):
        from core.backtest.engine import _per_dna_risk_params
        # 非 atr 模式不触碰 df；atr 模式给有效 atr_14 列（装载校验过）
        df = pd.DataFrame({"atr_14": [1.0, 1.1, 1.2]})
        return _per_dna_risk_params(rg, df)

    @pytest.mark.parametrize("rg_builder", [
        lambda: RiskGenes(),  # 全缺省（存量 DNA 形态）
        lambda: RiskGenes(  # 五缺省字段全显式声明
            exit_fraction=0.3, add_fraction=0.2, max_exposure=1.5,
            max_hold=7, time_exit_utc=600),
        lambda: RiskGenes(  # 动态定仓两键
            position_mode="risk_budget", risk_fraction=0.05),
        lambda: RiskGenes(position_mode="fixed"),
        lambda: RiskGenes(  # atr 模式+动态定仓
            sl_mode="atr", atr_period=14, position_mode="risk_budget",
            risk_fraction=0.1),
        lambda: RiskGenes(time_exit_utc=0),  # 0:00 合法时刻
    ], ids=["all-default", "five-declared", "risk-budget",
            "fixed-explicit", "atr-risk-budget", "time-exit-zero"])
    def test_parity_with_engine_assembly(self, rg_builder):
        rg = rg_builder()
        p = self._engine_assembly(rg)
        params = materialize(rg)
        assert params.exit_fraction == p[2]
        assert params.add_fraction == p[3]
        assert params.max_exposure == p[4]
        assert params.max_hold == int(p[5])
        assert params.time_exit_utc == int(p[6])
        assert ("risk_budget" if p[7] == 1.0 else "fixed") \
            == params.position_mode
        assert params.risk_fraction == p[8]
