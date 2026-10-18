"""默认模型映射单点（旧键组合→模型 ID+参数包）。

职能：定仓分派逻辑的唯一可写实现——position_mode 旧键组合解析为
（模型 ID，参数包）。两消费方（回测装配层经 adapter.expand_for_
backtest、纸盘 ledger.size_open 直调）共用本函数，「同一分派规则
不再两处各写」的落实面。

映射规则（默认模型映射——唯一权威）：
- position_mode=="risk_budget" → 二号（参数包不含 initial_fraction
  ——risk_budget 档不读它，互斥由装载校验承担，直构防御=忽略）；
- 否则（None/"fixed"/任意非法值）→ 一号（first_size=initial_
  fraction 物化>0 优先、否则 position_size——非法 position_mode
  沿现状两环境 else 兜 fixed 语义防御忽略）。

物化判据单点：first_size 取「物化后 >0」（纸盘现状同式；直构
绕过 validator 的 initial_fraction=0.0 统一收敛为「未声明哨兵」
语义取 position_size——与 materialize None→0.0 的哨兵形制一致，
正常路径（validator 值域 (0,1]）零变化，design §7 域内裁决）。

边界：add_fraction/exit_fraction 缺省物化不进本模块——缺省表
权威保持现状两处（引擎装配+materialize）同值对照锁定，本模块
不造第三份缺省表。
"""
from __future__ import annotations

from dataclasses import dataclass

from core.positioning.models import (
    FixedProportionalParams,
    MODEL_FIXED_PROPORTIONAL,
    MODEL_RISK_BUDGET,
    RiskBudgetParams,
)


@dataclass(frozen=True)
class ResolvedSizing:
    """解析结果（模型 ID+物化参数包）。"""

    model_id: str
    params: FixedProportionalParams | RiskBudgetParams


def _materialize_legacy(position_mode: str | None,
                        position_size: float,
                        initial_fraction: float | None,
                        risk_fraction: float | None
                        ) -> tuple[bool, float, float]:
    """旧键物化单源（resolve 与 expand_for_backtest 共用——防第三
    事实源）。

    Args:
        position_mode: 旧键 position_mode（None/"fixed"/"risk_budget"）。
        position_size: 旧键仓位比例。
        initial_fraction: 首笔声明（None=未声明；物化>0 判据）。
        risk_fraction: 风险预算声明（None→0.0 物化防御值）。

    Returns:
        tuple[bool, float, float]: (is_risk_budget, first_size,
        risk_fraction_mat)。
    """
    is_risk_budget = position_mode == "risk_budget"
    first_size = (float(initial_fraction)
                  if initial_fraction is not None and initial_fraction > 0
                  else float(position_size))
    risk_fraction_mat = (float(risk_fraction)
                         if risk_fraction is not None else 0.0)
    return is_risk_budget, first_size, risk_fraction_mat


def resolve(position_mode: str | None,
            position_size: float,
            initial_fraction: float | None = None,
            risk_fraction: float | None = None,
            sl_mode: str = "pct",
            stop_loss: float = 0.0) -> ResolvedSizing:
    """旧键组合 → (模型 ID, 参数包)（定仓分派唯一权威单点）。

    平铺键签名兼容 RiskGenes 与 RiskParams（物化后）两种语境——
    两消费方各自展开属性传入。

    Args:
        position_mode: None/"fixed"→一号；"risk_budget"→二号；
            任意其他值防御忽略落一号（validator 拦正常路径）。
        position_size: 仓位比例（一号首笔缺省来源）。
        initial_fraction: 首笔声明（物化>0 优先；二号不读）。
        risk_fraction: 风险预算声明（物化 None→0.0）。
        sl_mode: 距离分派档位（二号参数包承载；一号语境不消费）。
        stop_loss: 止损参数（同上）。

    Returns:
        ResolvedSizing: model_id + 对应参数包。
    """
    is_risk_budget, first_size, risk_fraction_mat = _materialize_legacy(
        position_mode, position_size, initial_fraction, risk_fraction)
    if is_risk_budget:
        return ResolvedSizing(
            model_id=MODEL_RISK_BUDGET,
            params=RiskBudgetParams(
                risk_fraction=risk_fraction_mat,
                sl_mode=sl_mode,
                stop_loss=float(stop_loss)))
    return ResolvedSizing(
        model_id=MODEL_FIXED_PROPORTIONAL,
        params=FixedProportionalParams(first_size=first_size))
