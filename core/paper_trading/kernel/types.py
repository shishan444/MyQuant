"""内核词汇：Intent/Fill/ExecutionEvent/RiskParams 数据类+物化单点。

Intent 的 kind 十值=出场七种（exit_sl/exit_tp/exit_max_loss/
exit_max_hold/exit_time/exit_liquidation+信号出场 close）+入场三种
（open/add/reduce）。
RiskParams 十六字段=风控基因组全字段物化（含动态定仓两键
position_mode/risk_fraction 与仓位参数两键 initial_fraction/
max_position_loss），缺省物化与回测引擎装配单源同值
（core/backtest/engine.py _per_dna_risk_params）——「有声明用声明、
无声明用缺省」单点收敛在 materialize。ClosedBar/BarSignals 是解释器
的 bar 级输入词汇（纯值对象）。
"""
from __future__ import annotations

from dataclasses import dataclass

# Intent.kind 合法值（十值，spec 执行忠实性条款词汇）
INTENT_KINDS: tuple[str, ...] = (
    "open", "close", "add", "reduce",
    "exit_sl", "exit_tp", "exit_max_loss",
    "exit_max_hold", "exit_time", "exit_liquidation",
)

# 即时类意图（当根 bar 回顾判定后立即按触发价/清算价成交——八步序
# 第 1/2 步消费；其余意图挂 pending 次根开盘执行）
IMMEDIATE_KINDS: frozenset[str] = frozenset(
    {"exit_sl", "exit_tp", "exit_liquidation"})


@dataclass(frozen=True)
class ClosedBar:
    """单根已闭合 K 线（解释器/成交的 bar 输入——纯值对象）。

    Attributes:
        seq: bar 序号（0 起；max_hold 计数与 ATR 列读取的锚）。
        open: 开盘价（信号/时间类意图的执行价基准）。
        high: 最高价（空头 SL/多头 TP/trail 峰值的触碰判定）。
        low: 最低价（多头 SL/空头 TP 触碰判定）。
        close: 收盘价（权益估值与清算 mmr 选档的判定时刻价）。
        timestamp: bar 开盘时刻（UTC；time_exit 覆盖判定用。合成
            测试无时间场景可 None——time_exit 无声明路径零影响）。
        tf_minutes: bar 周期分钟数（time_exit 区间覆盖判定用）。
        atr: 当根 ATR 值（执行器从 feed 指标列 atr_{period} 注入；
            atr 模式防线与定仓消费。None/NaN/≤0=该 bar 无 ATR）。
    """

    seq: int
    open: float
    high: float
    low: float
    close: float
    timestamp: object | None = None
    tf_minutes: float = 240.0
    atr: float | None = None


@dataclass(frozen=True)
class BarSignals:
    """四类信号在单根 bar 的布尔值+方向符号（SignalSet 的 bar 切片）。

    Attributes:
        entry: 入场信号。
        exit: 出场信号。
        add: 加仓信号。
        reduce: 减仓信号。
        entry_direction: 入场方向符号 +1 多/−1 空/0 无（mixed 模式
            消费；0/NaN 不开仓）。
    """

    entry: bool = False
    exit: bool = False
    add: bool = False
    reduce: bool = False
    entry_direction: float = 0.0


@dataclass(frozen=True)
class Intent:
    """动作意图（解释器产出，执行器消费的最小动作单元）。

    size_fraction 是比例语义的自描述（open=首笔生效比例（声明
    initial_fraction 用之、否则 position_size——实际定仓在
    ledger.size_open 分派，本字段仅自描述）、reduce=exit_fraction、
    add=add_fraction、全平类=1.0）；权威值在 RiskParams，
    记账器消费时读参数单源。trigger_price 仅即时类携带（SL/TP 触发价、
    清算 p_liq——按触发价成交的锚）。

    Attributes:
        kind: 九值动作类型（INTENT_KINDS）。
        direction: "long"|"short"|""（入场/方向翻转类携带，出场全平类
            为空串）。
        size_fraction: 比例因子（见模块 docstring 语义表）。
        reason: 审计字符串（如 "reverse"/"signal"/"window"），落成交表。
        trigger_price: 即时类触发价；其余 None。
    """

    kind: str
    direction: str = ""
    size_fraction: float = 1.0
    reason: str = ""
    trigger_price: float | None = None


@dataclass
class Fill:
    """成交（FillModel 产出——把意图变成成交的唯一通道产物）。

    realized_pnl 仅出场类填写（记账器在 apply 时按持仓入场价结算回填；
    入场/加仓/减仓类为 None）。

    Attributes:
        intent: 来源意图（审计链）。
        price: 成交价（已含滑点偏移——slippage 已在价内）。
        quantity: 成交数量（记账器定仓后传入）。
        fee: 手续费（=名义 quantity×price×费率）。
        slippage_cost: 滑点成本（不利偏移的货币量，披露用）。
        realized_pnl: 已实现盈亏（出场类；其余 None）。
        capped: risk_budget 定仓名义触及 cap=权益×min(leverage,
            max_exposure) 截断标记（spec「成交记录标记 capped=true」）。
    """

    intent: Intent
    price: float
    quantity: float
    fee: float
    slippage_cost: float
    realized_pnl: float | None = None
    capped: bool = False


@dataclass
class ExecutionEvent:
    """审计事件（成交+bar 定位+八步序 seq 编号，落 pt_fill 的载体）。

    执行器按 §3.5 八步序为每笔成交编号（「为什么这根 K 线做了这个
    动作」的审计锚）；kernel 提供词汇，编号分派归执行器。

    Attributes:
        fill: 成交明细。
        bar_time: 承载 bar 的时刻字符串（原样透传，落表形制）。
        seq: bar 内序号（八步序编号）。
    """

    fill: Fill
    bar_time: str
    seq: int


@dataclass(frozen=True)
class RiskParams:
    """物化后的风控参数（十六字段——RiskGenes 全字段）。

    九字段带缺省物化（None→缺省），与回测引擎装配点同值：
    exit_fraction→0.5、add_fraction→0.1、max_exposure→1.0、
    max_hold→0（0=禁用）、time_exit_utc→-1 哨兵（0 是 0:00 合法值
    非禁用）、position_mode→"fixed"、risk_fraction→0.0、
    initial_fraction→0.0（0=未声明）、max_position_loss→0.0
    （0=禁用哨兵——值域 >0 使 0 天然无歧义）。

    Attributes:
        stop_loss: 止损参数（pct/trail=比例、atr=ATR 倍数）。
        take_profit: 止盈参数；None=未声明（不启用）。
        position_size: 仓位比例（fixed 档定仓）。
        leverage: 杠杆倍数（任务覆写面在执行器层替换本字段）。
        direction: "long"|"short"|"mixed"（mixed 时方向来自信号）。
        sl_mode: "pct"|"atr"|"trail"。
        atr_period: atr 模式回看周期。
        exit_fraction: 部分离场比例（reduce 分支）。
        add_fraction: 加仓比例（分母=当前权益）。
        max_exposure: 总敞口上限（名义/权益）。
        max_hold: 最长持有根数；0=禁用。
        time_exit_utc: 每日 UTC 分钟时刻全平；-1=禁用哨兵。
        position_mode: "fixed"|"risk_budget"（动态定仓分派）。
        risk_fraction: 风险预算比例（risk_budget 档消费）。
        initial_fraction: 首笔动用资金比例；0=未声明（fixed 档
            首笔定仓消费：>0 时替换 position_size 分派）。
        max_position_loss: 持仓金额防线；0=禁用（浮亏达线次根
            开盘全平，判定在解释器段 3）。
    """

    stop_loss: float
    take_profit: float | None
    position_size: float
    leverage: int
    direction: str
    sl_mode: str
    atr_period: int
    exit_fraction: float = 0.5
    add_fraction: float = 0.1
    max_exposure: float = 1.0
    max_hold: int = 0
    time_exit_utc: int = -1
    position_mode: str = "fixed"
    risk_fraction: float = 0.0
    initial_fraction: float = 0.0
    max_position_loss: float = 0.0


def materialize(risk_genes) -> RiskParams:
    """风控基因组 → 物化 RiskParams（「有声明用声明、无声明用缺省」单点）。

    九缺省与回测引擎装配单源同值（_per_dna_risk_params——两处缺省
    漂移由 tests/paper_trading/test_materialize.py 对照断言守护）。
    其余七字段原样透传（stop_loss/take_profit/position_size/leverage/
    direction/sl_mode/atr_period 无缺省物化面）。

    Args:
        risk_genes: core.strategy.dna.RiskGenes 实例。

    Returns:
        RiskParams: 十六字段全具体的物化参数。
    """
    return RiskParams(
        stop_loss=float(risk_genes.stop_loss),
        take_profit=(None if risk_genes.take_profit is None
                     else float(risk_genes.take_profit)),
        position_size=float(risk_genes.position_size),
        leverage=int(risk_genes.leverage),
        direction=risk_genes.direction,
        sl_mode=risk_genes.sl_mode,
        atr_period=int(risk_genes.atr_period),
        exit_fraction=(0.5 if risk_genes.exit_fraction is None
                       else float(risk_genes.exit_fraction)),
        add_fraction=(0.1 if risk_genes.add_fraction is None
                      else float(risk_genes.add_fraction)),
        max_exposure=(1.0 if risk_genes.max_exposure is None
                      else float(risk_genes.max_exposure)),
        max_hold=(0 if risk_genes.max_hold is None
                  else int(risk_genes.max_hold)),
        time_exit_utc=(-1 if risk_genes.time_exit_utc is None
                       else int(risk_genes.time_exit_utc)),
        position_mode=(risk_genes.position_mode or "fixed"),
        risk_fraction=(0.0 if risk_genes.risk_fraction is None
                       else float(risk_genes.risk_fraction)),
        initial_fraction=(0.0 if risk_genes.initial_fraction is None
                          else float(risk_genes.initial_fraction)),
        max_position_loss=(0.0 if risk_genes.max_position_loss is None
                           else float(risk_genes.max_position_loss)),
    )
