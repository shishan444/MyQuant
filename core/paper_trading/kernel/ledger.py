"""记账器（Ledger 状态机）+清算两式（market_model 仓位口径）。

Ledger 是每任务实例的持仓/余额状态机：只接受执行器按判定序传入的
意图，经 FillModel 产出成交后做状态变更，不做任何决策。定仓分派
（14 参数消费的 open 分支）是本模块的参数忠实执行面；公式单源引用
core/backtest/engine.py 的 risk_budget_qty（重命名公开符号，第三
消费方——不在本域复制公式）。

清算两式（D-1 仓位口径，mmr 单源 core/market_model 按判定时刻名义
选档）：
- 多头 p_liq=(quantity×entry−margin−balance)/(quantity×(1−mmr))，
  bar_low≤p_liq 触发；
- 空头 p_liq=(balance+margin+quantity×entry)/(quantity×(1+mmr))，
  bar_high≥p_liq 触发；
- 成交价=p_liq、kind=exit_liquidation；leverage=1 不判。
"""
from __future__ import annotations

from dataclasses import dataclass

from core.backtest.engine import risk_budget_qty
from core.market_model import maintenance_margin_rate
from core.paper_trading.kernel.account import (
    AccountSnapshot,
    PositionSnapshot,
)
from core.paper_trading.kernel.fills import FillModel
from core.paper_trading.kernel.types import ClosedBar, Fill, Intent, RiskParams


def liquidation_price(side: str, entry_price: float, quantity: float,
                      margin: float, balance: float,
                      mark_price: float) -> float:
    """清算触发价（仓位口径两式——解释器防线判定的单源供方）。

    mmr 按「判定时刻名义 quantity×mark_price」选档（跌价降档语义）。
    维持率档位表唯一源在 core/market_model（本模块只引用不持有
    字面量；回测侧的等效换算形制函数在本域禁用——inspect 断言
    守护）。

    Args:
        side: 持仓方向 "long"|"short"。
        entry_price: 入场价（加仓后加权均价）。
        quantity: 持仓数量。
        margin: 占用保证金。
        balance: 账户余额。
        mark_price: 判定时刻市价（mmr 选档用）。

    Returns:
        float: 清算触发价（多头跌破/空头升破即触发）。
    """
    mmr = maintenance_margin_rate(quantity * mark_price)
    if side == "long":
        return (quantity * entry_price - margin - balance) / (
            quantity * (1.0 - mmr))
    return (balance + margin + quantity * entry_price) / (
        quantity * (1.0 + mmr))


def funding_amount(signed_qty: float, settle_price: float,
                   rate: float) -> float:
    """资金费金额单点=带符号数量×结算价×费率（正=付出/负=收到）。

    signed_qty 带方向符号（多头正/空头负——方向编码在符号，快照
    重算场景无 side 列可恢复）；符号×rate 天然实现「rate>0 多头付/
    空头收、rate<0 反向」。消费方=apply_funding（正常结算）与
    runtime 补旧账/终态回补（用快照数量重算、不读当前仓位——
    公式单源防三处漂移）。

    Args:
        signed_qty: 带符号持仓数量（多头正/空头负）。
        settle_price: 结算价（承载 bar 收盘价）。
        rate: 该结算点真实费率。

    Returns:
        float: 结算金额（正=付出/负=收到）。
    """
    return signed_qty * settle_price * rate


@dataclass
class Position:
    """持仓可变状态（Ledger 内部——对外经 AccountSnapshot 只读）。"""

    side: str
    entry_price: float
    quantity: float
    margin: float
    open_cost: float
    sl_price: float | None
    tp_price: float | None
    trail_peak: float
    entry_bar_seq: int
    # 当前持仓累计资金费净支出（带符号：付出为正/收到为负）——金额
    # 防线判定的资金费分量；与账户级 total_funding（跨持仓累计披露）
    # 两口径并存。开仓归零、结算累加、并仓延续、全平随对象丢弃。
    funding_paid: float = 0.0


class Ledger:
    """记账器状态机（open/add/reduce/close/apply_funding 五操作）。

    权益口径（D17）：margin=equity×position_size（定仓的权益口径）；
    总权益=balance+margin+未实现盈亏（逐仓口径——open 时 balance 划出
    保证金，持仓中计回），非旧纸盘 balance 口径。每项费用=成交名义
    价值计费；滑点已在成交价内（FillModel），本模块不二次计滑点。
    """

    def __init__(self, initial_cash: float, params: RiskParams):
        """初始化账户与物化参数。

        Args:
            initial_cash: 初始资金（任务参数，开仓权益基准）。
            params: 物化后的风控参数（14 字段消费面）。
        """
        self.balance = float(initial_cash)
        self.params = params
        self.position: Position | None = None
        self.closed_trades = 0
        self.total_funding = 0.0

    # ── 查询 ────────────────────────────────────────────────

    def unrealized_pnl(self, mark_price: float) -> float:
        """未实现盈亏（空仓 0；方向×数量×(市价−入场价)）。"""
        pos = self.position
        if pos is None:
            return 0.0
        sign = 1.0 if pos.side == "long" else -1.0
        return sign * pos.quantity * (mark_price - pos.entry_price)

    def equity(self, mark_price: float) -> float:
        """权益（逐仓总权益=钱包余额+仓位保证金+未实现盈亏）。

        open 时 balance−=margin+fee 已划出保证金，持仓中总权益须计
        回 margin 项（P0 修复：此前漏项使开仓即权益假腰斩 margin）；
        空仓权益=balance。
        """
        pos = self.position
        if pos is None:
            return self.balance
        return (self.balance + pos.margin
                + self.unrealized_pnl(mark_price))

    def snapshot(self, mark_price: float,
                 bar_seq: int | None = None) -> AccountSnapshot:
        """只读账户快照（解释器判定与报告披露的输入）。

        Args:
            mark_price: 估值市价（bar 收盘价）。
            bar_seq: 当前 bar 序号（bars_held 计数；None=无当前 bar
                语境（空仓或恢复期），持仓时按入场 bar 计 1 根）。

        Returns:
            AccountSnapshot: 只读快照。
        """
        pos = self.position
        upnl = self.unrealized_pnl(mark_price)
        if pos is None:
            return AccountSnapshot(
                equity=self.balance, balance=self.balance,
                unrealized_pnl=0.0, position=None, bars_held=0,
                position_notional=0.0)
        bars_held = ((bar_seq if bar_seq is not None else pos.entry_bar_seq)
                     - pos.entry_bar_seq + 1)
        return AccountSnapshot(
            equity=self.balance + pos.margin + upnl, balance=self.balance,
            unrealized_pnl=upnl,
            position=PositionSnapshot(
                side=pos.side, entry_price=pos.entry_price,
                quantity=pos.quantity, margin=pos.margin,
                open_cost=pos.open_cost, sl_price=pos.sl_price,
                tp_price=pos.tp_price, trail_peak=pos.trail_peak,
                entry_bar_seq=pos.entry_bar_seq,
                funding_paid=pos.funding_paid),
            bars_held=bars_held,
            position_notional=pos.quantity * mark_price)

    # ── 持仓期逐 bar 状态维护 ────────────────────────────────

    def update_trail_peak(self, bar: ClosedBar) -> None:
        """trail 峰值逐 bar 更新（多头取 high 最大/空头取 low 最小）。

        解释器判定自足（快照峰值与当根 bar 现算含当根峰值），本方法
        是状态权威落点（执行器每 bar 调用；与判定同式幂等）。
        """
        pos = self.position
        if pos is None:
            return
        if pos.side == "long":
            pos.trail_peak = max(pos.trail_peak, bar.high)
        else:
            pos.trail_peak = min(pos.trail_peak, bar.low)

    # ── 定仓（open 分支的 14 参数消费分派） ───────────────────

    def size_open(self, base_price: float,
                  entry_atr: float | None) -> tuple[float, bool]:
        """入场定仓（fixed/risk_budget 两档分派）。

        fixed 档（缺省/"fixed"）：margin=equity×首笔生效比例（声明
        initial_fraction（物化 >0）用之、否则 position_size——与引擎
        装配 effective_first_size 分派同式，对照断言锁），quantity
        =margin×leverage/price（逐字节不变形制）。risk_budget 档：
        qty=权益×risk_fraction÷止损距离（公式单源 risk_budget_qty），
        cap=权益×min(leverage,max_exposure) 截断并标记 capped；距离
        不可定义（残缺组合/ATR NaN）时公式单点返回 (0,0)→回退 fixed
        定仓（对齐回测引擎兜底，不另设抛异常分支——第二逻辑源禁令）；
        risk_budget 档不读 initial_fraction（互斥由装载校验承担，
        直构防御=忽略）。

        Args:
            base_price: 定仓基准价（执行 bar 开盘价，滑点前——与引擎
                入场分派的 current_price 同口径）。
            entry_atr: 入场 bar ATR 值（atr 模式定仓距离消费；其余
                模式不读）。

        Returns:
            tuple[float, bool]: (quantity, capped)。
        """
        params = self.params
        equity_now = self.equity(base_price)
        if params.position_mode == "risk_budget":
            stop_dist = self._stop_distance(base_price, entry_atr)
            qty, capped = risk_budget_qty(
                equity_now, base_price, stop_dist, params.risk_fraction,
                float(params.leverage), params.max_exposure)
            if qty > 0.0:
                return qty, bool(capped)
            # (0,0) 防御分支 → 回退 fixed（受控等价覆盖此路径）
        first_size = (params.initial_fraction
                      if params.initial_fraction > 0
                      else params.position_size)
        margin = equity_now * first_size
        return margin * params.leverage / base_price, False

    def _stop_distance(self, price: float,
                       entry_atr: float | None) -> float:
        """risk_budget 定仓的止损距离（sl_mode 分派）。

        pct/trail 同为比例域（price×sl——对齐回测引擎 D-6 修复后的
        入场分派；design §3.4「trail=sl×入场 bar ATR 基准」为修复前
        旧语义，见实施汇报偏差记录）；atr=sl×该 bar ATR。距离不可
        定义两源：无止损（≤0）返回 0.0、ATR 无效（None/NaN/≤0）
        返回 NaN——两者经公式单点 (0,0) 防御回退 fixed。
        """
        sl = self.params.stop_loss
        if sl <= 0.0:
            return 0.0
        if self.params.sl_mode == "atr":
            if entry_atr is None or entry_atr != entry_atr \
                    or entry_atr <= 0.0:
                return float("nan")
            return sl * entry_atr
        return price * sl

    # ── 执行入口（意图 → 成交 → 状态变更） ───────────────────

    def execute(self, intent: Intent, exec_bar: ClosedBar,
                fill_model: FillModel,
                entry_atr: float | None = None) -> Fill | None:
        """执行单条意图（定仓→成交→状态变更，返回成交记录）。

        判定序与意图合法性由解释器/执行器保证；本方法对语境不符的
        意图（有仓开仓/无仓出场）返回 None 不抛异常（防御面）。

        Args:
            intent: 待执行意图。
            exec_bar: 承载执行的 bar。
            fill_model: 成交方式（成交唯一通道）。
            entry_atr: 入场 bar ATR（open 意图的 risk_budget 档定仓
                消费；其余意图不读）。

        Returns:
            Fill | None: 成交记录（realized_pnl 已回填）；语境不符 None。
        """
        kind = intent.kind
        if kind == "open":
            if self.position is not None:
                return None
            qty, capped = self.size_open(exec_bar.open, entry_atr)
            fill = fill_model.fill(intent, exec_bar, qty)
            fill.capped = capped
            self._apply_open(fill, exec_bar, entry_atr)
            return fill
        if kind == "add":
            if self.position is None:
                return None
            add_margin = self.params.add_fraction * self.equity(
                exec_bar.open)
            qty_add = add_margin * self.params.leverage / exec_bar.open
            fill = fill_model.fill(intent, exec_bar, qty_add)
            self._apply_add(fill)
            return fill
        if kind == "reduce":
            if self.position is None:
                return None
            qty = self.position.quantity * self.params.exit_fraction
            fill = fill_model.fill(intent, exec_bar, qty)
            self._apply_partial_exit(fill)
            return fill
        # 出场族（close/exit_*）
        if self.position is None:
            return None
        fill = fill_model.fill(intent, exec_bar, self.position.quantity)
        self._apply_close(fill)
        return fill

    # ── 状态变更（每笔费用=名义计费；slip 已在成交价内） ──────

    def _apply_open(self, fill: Fill, exec_bar: ClosedBar,
                    entry_atr: float | None) -> None:
        """开仓落账：margin=qty×成交价/leverage，balance−=margin+fee。"""
        params = self.params
        side = "long" if fill.intent.direction == "long" else "short"
        margin = fill.quantity * fill.price / params.leverage
        self.balance -= margin + fill.fee
        self.position = Position(
            side=side,
            entry_price=fill.price,
            quantity=fill.quantity,
            margin=margin,
            open_cost=fill.quantity * fill.price,
            sl_price=self._initial_stop(
                side, fill.price, entry_atr, is_tp=False),
            tp_price=self._initial_stop(
                side, fill.price, entry_atr, is_tp=True),
            trail_peak=fill.price,
            entry_bar_seq=exec_bar.seq,
            funding_paid=0.0)

    def _apply_add(self, fill: Fill) -> None:
        """加仓落账：保证金=add_fraction×当时权益→名义放大；并仓
        （entry_price 加权重锚、margin/quantity 累加；trail 峰值保持
        连续——持仓期语义不随部分加仓重置）。"""
        pos = self.position
        add_margin = fill.quantity * fill.price / self.params.leverage
        new_qty = pos.quantity + fill.quantity
        if new_qty > 0.0:
            pos.entry_price = (
                pos.entry_price * pos.quantity
                + fill.price * fill.quantity) / new_qty
        pos.quantity = new_qty
        pos.margin += add_margin
        pos.open_cost += fill.quantity * fill.price
        self.balance -= add_margin + fill.fee
        self._reanchor_stops(pos)

    def _apply_partial_exit(self, fill: Fill) -> None:
        """减仓落账：按比例释放保证金；部分已实现盈亏回填成交。"""
        pos = self.position
        sign = 1.0 if pos.side == "long" else -1.0
        fill.realized_pnl = sign * fill.quantity * (
            fill.price - pos.entry_price)
        ratio = fill.quantity / pos.quantity
        released = pos.margin * ratio
        self.balance += released + fill.realized_pnl - fill.fee
        pos.quantity -= fill.quantity
        pos.margin -= released
        pos.open_cost -= pos.open_cost * ratio
        if pos.quantity <= 0.0:
            self.closed_trades += 1
            self.position = None

    def _apply_close(self, fill: Fill) -> None:
        """全平落账：balance+=margin+pnl−fee；出场计数+1。"""
        pos = self.position
        sign = 1.0 if pos.side == "long" else -1.0
        fill.realized_pnl = sign * pos.quantity * (
            fill.price - pos.entry_price)
        self.balance += pos.margin + fill.realized_pnl - fill.fee
        self.closed_trades += 1
        self.position = None

    def record_funding(self, amount: float) -> None:
        """资金费落账单点（三键一次更新——对质①裁决收单点）。

        balance 直减（负金额=入账）；账户级 total_funding 累计（跨
        持仓披露）；当前持仓存在则持仓级 funding_paid 累计（金额
        防线判定输入——带符号：付出为正=加深浮亏、收到为负=抵减；
        无持仓只前两键）。消费方=apply_funding（正常结算）与 runtime
        结算两分支（新账/补旧账——补旧账金额按快照数量重算后经本点
        落账）——funding_paid 累加无第二写点。

        Args:
            amount: 结算金额（正=付出/负=收到；经 funding_amount
                单点或其快照重算产出）。
        """
        self.balance -= amount
        self.total_funding += amount
        if self.position is not None:
            self.position.funding_paid += amount

    def apply_funding(self, rate: float, settle_price: float) -> float:
        """资金费结算（与杠杆无关——对质六裁决：leverage=1 同扣）。

        金额经 funding_amount 单点（signed_qty=当前仓数量×方向符号；
        rate>0 多头付/空头收、rate<0 反向），落账经 record_funding
        单点（balance/total_funding/funding_paid 三键）。

        Args:
            rate: 该结算点真实费率（funding parquet）。
            settle_price: 结算价（承载 bar 收盘价）。

        Returns:
            float: 结算金额（正=付出/负=收到；空仓 0）。
        """
        pos = self.position
        if pos is None:
            return 0.0
        sign = 1.0 if pos.side == "long" else -1.0
        amount = funding_amount(sign * pos.quantity, settle_price, rate)
        self.record_funding(amount)
        return amount

    # ── 披露用止损止盈快照（开仓/重锚时点值） ────────────────

    def _initial_stop(self, side: str, entry_price: float,
                      entry_atr: float | None,
                      is_tp: bool) -> float | None:
        """开仓时点 SL/TP 披露值（与判定权威同式：atr 模式 SL/TP 均
        ATR 倍数域、pct/trail 均比例域锚入场价——review A2 修正：此前
        is_tp 恒走 pct 域致 atr 模式 TP 披露与实际判定线不一致）。

        判定权威在解释器逐 bar 现算（trail 随峰值移动、加仓重锚后
        变化）——本值仅持久化披露面。ATR 无效时 None（该 bar 无防线
        语义的披露形态）。
        """
        params = self.params
        value = params.take_profit if is_tp else params.stop_loss
        if value is None or value <= 0.0:
            return None
        if params.sl_mode == "atr":
            if entry_atr is None or entry_atr != entry_atr \
                    or entry_atr <= 0.0:
                return None
            offset = value * entry_atr
        else:
            offset = entry_price * value
        if side == "long":
            return entry_price + offset if is_tp else entry_price - offset
        return entry_price - offset if is_tp else entry_price + offset

    def _reanchor_stops(self, pos: Position) -> None:
        """加仓重锚披露值（按新加权均价重算；trail 保持连续）。"""
        # 重锚无当根 ATR 语境——atr 模式披露值置 None（防线现算不受影响）
        pos.sl_price = self._initial_stop(
            pos.side, pos.entry_price, None, is_tp=False)
        pos.tp_price = self._initial_stop(
            pos.side, pos.entry_price, None, is_tp=True)
