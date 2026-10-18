"""仓位模型注册表包（core/positioning）。

包结构（五模块+白名单公开面）：
- interfaces：PositionModel 协议+SizingInput+PositioningError；
- models：一号/二号实现+参数包+锚与对拍网格数据+模型 ID 常量；
- registry：注册表本体（静态登记+准入校验+只读查询）；
- resolve：默认模型映射单点（旧键组合→模型 ID+参数包）；
- adapter：回测 njit 展开适配器（旧键组合→装配三元组）。

依赖方向：本包为公共层，只依赖标准库——不 import core/backtest、
core/paper_trading、core/strategy、api 中任何模块（对拍的 njit 侧
执行器由 tests/positioning/ 拉起引擎侧注入，单向性由此保持）。
注册表面无 register/注销/改写类符号（REJ-09/REJ-06——_register/
_validate_entry/_materialize_legacy 为下划线私有，测试直接调用做
单元级验证）。
"""
from core.positioning.interfaces import (
    PositionModel,
    PositioningError,
    SizingInput,
)
from core.positioning.models import (
    MODEL_FIXED_PROPORTIONAL,
    MODEL_RISK_BUDGET,
)
from core.positioning.registry import (
    RegistryEntry,
    get_model,
    list_models,
)
from core.positioning.resolve import resolve
from core.positioning.adapter import expand_for_backtest

__all__ = [
    "PositionModel",
    "SizingInput",
    "PositioningError",
    "RegistryEntry",
    "get_model",
    "list_models",
    "resolve",
    "expand_for_backtest",
    "MODEL_FIXED_PROPORTIONAL",
    "MODEL_RISK_BUDGET",
]
