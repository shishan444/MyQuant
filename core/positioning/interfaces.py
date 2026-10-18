"""仓位模型公共接口（协议+输入载体+异常）。

职能：定义统一仓位模型接口 PositionModel（三方法契约）与开仓定仓的
输入载体 SizingInput。全部仓位模型的「怎么算」住本包注册条目
（models.py 实现+registry.py 登记），两消费方（回测装配层
core/backtest/engine.py、纸盘分派层 core/paper_trading/kernel/
ledger.py）单向消费——定仓规则不再各写一份。

职责边界（两类失败的区分）：
- 运行时资格不满足（止损距离不可定义等）以返回值 0.0 表达，不抛
  异常（第二逻辑源禁令——执行层按现状回退 fixed 定仓）；
- 装配期结构性不支持（模型不可展开却被回测取装配参数）用
  PositioningError 表达（库内防线，无回退语义——对齐 engine
  sl_mode_code 未知值 raise 的「不静默退化」哲学）。
- 接口不含敞口截断语义：cap=权益×min(leverage, max_exposure) 的
  护栏住执行层（模型只算目标仓位裸值）；leverage/max_exposure
  不进 SizingInput 也不进参数包——结构性保证「模型不读杠杆」。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class SizingInput:
    """开仓定仓输入（账户状态快照+市场数据引用）。

    Attributes:
        equity: 当时权益（定仓分母——一号滚动基数/二号预算分子）。
        base_price: 定仓基准价（二号距离 pct/trail 域与执行层
            保证金→数量换算的分母；一号 size_open 不读）。
        atr_value: 该 bar ATR 值（仅二号 atr 模式距离消费）；
            None=非 atr 语境。
    """

    equity: float
    base_price: float
    atr_value: float | None = None


class PositionModel(Protocol):
    """仓位模型三方法契约（统一接口——注册条目的实现形制）。

    三方法：size_open（开仓定仓）/size_add（加仓定仓）/size_reduce
    （减仓比例）。实现须为确定性纯计算（同输入同输出，无 IO、无
    随机、无时钟依赖）；资格不满足以返回值 0.0 表拒绝、不抛异常。

    Attributes:
        output_form: size_open 输出形制声明——"margin"（目标保证金
            金额）|"quantity"（目标数量）。
    """

    output_form: str

    def size_open(self, inp: SizingInput, params) -> float:
        """开仓定仓（形制由条目 output_form 声明）。"""
        ...

    def size_add(self, equity_now: float, add_fraction: float) -> float:
        """加仓定仓（目标保证金金额——滚动基数：分母为加仓时点权益）。"""
        ...

    def size_reduce(self, exit_fraction: float) -> float:
        """减仓比例（无量纲直传——声明减仓 30% 即实际减仓 30%）。"""
        ...


class PositioningError(ValueError):
    """仓位域装配期结构性错误（文案须含 model_id 与该条目支持环境域）。"""
