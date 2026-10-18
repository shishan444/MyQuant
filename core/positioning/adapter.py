"""回测 njit 展开适配器（旧键组合→装配三元组）。

职能：把模型展开为回测引擎 order_func_nb 可消费的装配参数形制
（BacktestExpansion 三元组——与现状引擎装配元组 p[7]/p[8]/p[9]
逐位同形制）。内部链：_materialize_legacy 物化 → resolve 取
model_id → 查条目可展开性 → 产出三元组。

全档物化语义（与现状装配等值的关键）：三元组对两档均全量物化
——fixed 档也物化 risk_fraction（fixed+rf 声明是合法组合，现状
装配无档位条件物化）、risk_budget 档也物化 effective_first_size
（现状同样无档位条件）。若按档位省略，装配逐位等值断言在
「fixed+rf 声明」DNA 上必红。

不可展开模型取装配参数时明确拒绝（PositioningError 结构化错误，
非静默回退——D-4；文案含 model_id 与该条目支持环境域）。二号
的可展开形制=装配参数，定仓执行仍是引擎侧 njit risk_budget_qty+
order_func_nb 内联距离分派（引擎函数零改动，降为对拍消费方——
公式与注册条目手算锚对拍）。
"""
from __future__ import annotations

from dataclasses import dataclass

from core.positioning.interfaces import PositioningError
from core.positioning.models import MODEL_FIXED_PROPORTIONAL, MODEL_RISK_BUDGET
from core.positioning.registry import get_model
from core.positioning.resolve import _materialize_legacy, resolve

# 模型 ID → position_mode_code（回测装配方言，住本模块——引擎
# order_func_nb 消费的数值分派码：0.0=百分比固定路径、1.0=风险
# 预算档）
_MODEL_CODE = {
    MODEL_FIXED_PROPORTIONAL: 0.0,
    MODEL_RISK_BUDGET: 1.0,
}


@dataclass(frozen=True)
class BacktestExpansion:
    """回测装配三元组（与现状引擎装配元组 p[7]/p[8]/p[9] 逐位
    同形制——全档物化，见模块 docstring）。

    Attributes:
        position_mode_code: 定仓档位码（0.0=fixed/1.0=risk_budget）。
        risk_fraction: 物化风险预算比例（None→0.0）。
        effective_first_size: 首笔生效比例（initial_fraction 物化
            >0 优先、否则 position_size）。
    """

    position_mode_code: float
    risk_fraction: float
    effective_first_size: float


def expand_for_backtest(position_mode: str | None,
                        position_size: float,
                        initial_fraction: float | None = None,
                        risk_fraction: float | None = None
                        ) -> BacktestExpansion:
    """旧键组合 → 回测装配三元组（njit 展开适配器）。

    Args:
        position_mode: 旧键 position_mode（resolve 同语义）。
        position_size: 旧键仓位比例。
        initial_fraction: 首笔声明（物化>0 优先）。
        risk_fraction: 风险预算声明（物化 None→0.0，全档物化）。

    Returns:
        BacktestExpansion: (position_mode_code, risk_fraction,
        effective_first_size) 三元组。

    Raises:
        PositioningError: 模型不可展开（条目 expandable=False——
            仅逐根环境，回测装配层结构性不支持）。
    """
    resolved = resolve(position_mode, position_size,
                       initial_fraction, risk_fraction)
    entry = get_model(resolved.model_id)
    if not entry.expandable:
        raise PositioningError(
            f"仓位模型 {resolved.model_id!r} 不可展开为回测装配参数"
            f"（支持环境域: {list(entry.environments)}——仅逐根环境，"
            "回测装配层结构性不支持）")
    _, first_size, risk_fraction_mat = _materialize_legacy(
        position_mode, position_size, initial_fraction, risk_fraction)
    return BacktestExpansion(
        position_mode_code=_MODEL_CODE[resolved.model_id],
        risk_fraction=risk_fraction_mat,
        effective_first_size=first_size)
