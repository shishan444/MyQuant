"""resolve 默认模型映射分派测试（build-position-registry 任务 4.4——R4）。

R4 契约（design §7+spec「默认模型映射」Requirement 两 Scenario）：
- 默认映射全组合参数化 {position_mode: None/"fixed"/"risk_budget"/
  非法值}×{initial_fraction: None/0.0/0.2}×{risk_fraction: None/0.01}
  ——期望 (model_id, 参数包字段) 逐组合从映射规则推导（24 组合全
  覆盖，无一跳过）：
  * position_mode=="risk_budget"→二号（不读 initial_fraction；
    rf None→0.0 物化）；
  * 否则（None/"fixed"/任意非法值防御忽略）→一号（first_size=
    initial_fraction 物化>0 优先、否则 position_size——0.0=未声明
    哨兵收敛，spec Scenario {None,0.3,0.0}→("fixed-proportional",
    0.3)）；
- first_size 单点双语境：RiskGenes 语境（物化前 None）与 RiskParams
  语境（物化后 0.0）同输入同输出；
- risk_budget 档不读 initial_fraction（互斥由装载校验承担，直构
  防御=忽略——无第二判断源）。
"""
from __future__ import annotations

import pytest

pytestmark = [pytest.mark.unit]

from core.positioning import resolve
from core.positioning.models import (
    FixedProportionalParams,
    RiskBudgetParams,
)

_PS = 0.3  # position_size 固定（与 spec Scenario 同值）


class TestFullCombinationMatrix:
    """R4 全组合参数化（4×3×2=24 组合——期望从映射规则推导）。"""

    @pytest.mark.parametrize(
        "risk_fraction", [None, 0.01])
    @pytest.mark.parametrize(
        "initial_fraction", [None, 0.0, 0.2])
    @pytest.mark.parametrize(
        "position_mode", [None, "fixed", "risk_budget", "garbage-mode"])
    def test_resolve_matrix(self, position_mode, initial_fraction,
                            risk_fraction):
        r = resolve(position_mode, _PS, initial_fraction, risk_fraction,
                    sl_mode="atr", stop_loss=2.0)
        if position_mode == "risk_budget":
            assert r.model_id == "risk_budget"
            assert type(r.params) is RiskBudgetParams
            # rf 物化（None→0.0 直构防御值）；sl_mode/stop_loss 由
            # 参数包承载（声明参数——spec 修正措辞后的承载归属）
            assert r.params.risk_fraction == (0.01 if risk_fraction
                                              == 0.01 else 0.0)
            assert r.params.sl_mode == "atr"
            assert r.params.stop_loss == 2.0
        else:
            # None/"fixed"/非法值→一号（防御忽略——现状两环境 else
            # 兜 fixed 同语义）；first_size 物化>0 优先、0.0=哨兵收敛
            assert r.model_id == "fixed-proportional"
            assert type(r.params) is FixedProportionalParams
            assert r.params.first_size == (0.2 if initial_fraction == 0.2
                                           else _PS)


class TestFirstSizeSinglePointDualContext:
    """R4 first_size 单点：RiskGenes 语境（物化前）与 RiskParams 语境
    （物化后）同输入同输出（spec「两档旧键解析」Scenario 原文锚）。"""

    def test_spec_scenario_none_and_materialized_zero_agree(self):
        """{position_mode=None, position_size=0.3, initial_fraction=0.0}
        →("fixed-proportional", first_size=0.3)——物化前 None 与物化
        后 0.0 两种语境同解析（0=未声明哨兵语义）。"""
        genes_ctx = resolve(None, _PS, initial_fraction=None)
        params_ctx = resolve(None, _PS, initial_fraction=0.0)
        assert genes_ctx.model_id == params_ctx.model_id \
            == "fixed-proportional"
        assert genes_ctx.params == params_ctx.params
        assert genes_ctx.params.first_size == 0.3

    @pytest.mark.parametrize(
        ("initial_fraction", "expected_first"),
        [
            pytest.param(None, 0.3, id="未声明→position_size"),
            pytest.param(0.0, 0.3, id="哨兵 0.0→position_size"),
            pytest.param(0.2, 0.2, id="声明 0.2→initial_fraction"),
        ])
    def test_first_size_dispatch_three_states(self, initial_fraction,
                                              expected_first):
        """三态分派：物化>0 判据（声明替换/哨兵/未声明同落 position_
        size——合法值域下与现状两环境分派恒等价）。"""
        r = resolve(None, _PS, initial_fraction=initial_fraction)
        assert r.params.first_size == expected_first


class TestRiskBudgetIgnoresInitialFraction:
    """R4 risk_budget 档不读 initial_fraction（直构防御=忽略）。"""

    @pytest.mark.parametrize(
        "initial_fraction", [None, 0.0, 0.2])
    def test_params_independent_of_initial_fraction(self,
                                                    initial_fraction):
        """三种 initial_fraction 取值下参数包逐字段相等（无第二判断
        源——参数包不含 first_size 字段）。"""
        base = resolve("risk_budget", _PS, None, 0.01, "pct", 0.02).params
        other = resolve("risk_budget", _PS, initial_fraction, 0.01,
                        "pct", 0.02).params
        assert other == base
        assert not hasattr(other, "first_size")
