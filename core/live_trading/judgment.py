"""判定规则：对照账户状态评估交易信号，产出单 bar 决策。

纯函数层：(BarSignals, AccountState, JudgmentConfig) -> Decision，
无副作用、无状态。服务对象是实盘决策 pipeline（pipeline.py 的单 bar
决策翻译）——本模块只做「信号+账户快照 → 动作」的映射，不含数据拉取、
下单与风控（分别归 Runner/Gateway/RiskGuard）。
历史层：自旧 core/trading/judgment.py 迁入（rebuild-paper-trading 6.1，
代码逐字保留；注释翻译 CARD-07）。
"""
from __future__ import annotations

from .types import AccountState, BarSignals, Decision, JudgmentConfig

HOLD = Decision(action="hold", reason="no action needed")


def evaluate(
    signals: BarSignals,
    state: AccountState,
    config: JudgmentConfig | None = None,
) -> Decision:
    """对交易信号应用判定规则，决定本 bar 是否行动。

    职责契约：输入当前 bar 信号（signals）、账户快照（state）与可选
    配置（config，None 用缺省值），输出一个 Decision。纯映射，不抛
    业务异常；条件不满足的分支自然落空返回 hold，不产生动作。

    规则优先序（先命中先返回，后序规则不再评估；代码行内注释的
    Rule N 为规则身份标签，沿用旧域历史编号，非执行顺序）：
    1. max_hold_bars 安全阀：亏损仓位持有过久强制平仓；
    2. exit/reduce 信号 + 有仓：先过持有时长与最小盈利两道校验；
    3. entry 信号 + 无仓：按 initial_entry_pct 比例开仓；
    4. entry 信号 + 同向持仓：盈利且未达目标仓位时加仓；
    5. entry 信号 + 反向持仓：先平仓（两步反手的本根平半步）；
    6. 无信号：hold（仓位填充逻辑归 PositionPlan，此处不动作）。
    """
    if config is None:
        config = JudgmentConfig()

    target_pct = _compute_target(state, config)

    # Rule 0：max_hold_bars 安全阀（亏损仓持有到期强制平）
    if (
        config.max_hold_bars is not None
        and state.has_position
        and state.position_bars_held >= config.max_hold_bars
        and state.unrealized_pnl < 0
    ):
        return Decision(
            action="close",
            reason=f"max_hold_bars={config.max_hold_bars} reached with loss",
        )

    # Rule 1：exit/reduce 信号且有持仓（两道校验任一不过则 hold）
    if (signals.exit or signals.reduce) and state.has_position:
        if state.position_bars_held < config.min_hold_bars:
            return Decision(
                action="hold",
                reason=f"hold_bars={state.position_bars_held} < min={config.min_hold_bars}",
            )
        # 估算往返手续费，供最小盈利校验（开+平两笔，故两倍单边）
        notional = state.position_quantity * state.position_entry
        est_fee = notional * config.fee_rate * 2  # 往返（round-trip）估算
        if 0 < state.unrealized_pnl < est_fee * config.min_profit_ratio and signals.exit:
            return Decision(
                action="hold",
                reason=f"pnl={state.unrealized_pnl:.2f} < fee_threshold={est_fee * config.min_profit_ratio:.2f}",
            )
        action = "reduce" if signals.reduce and not signals.exit else "close"
        return Decision(action=action, target_position_pct=0.0, reason="signal")

    # Rule 4：entry 信号与持仓反向 → 先平（两步反手的本根平半步）
    if signals.entry and state.has_position:
        direction = "long" if signals.direction > 0 else "short" if signals.direction < 0 else None
        if direction is None:
            return Decision(action="hold", reason="neutral_direction")
        if direction != state.position_side:
            return Decision(
                action="close",
                reason=f"reverse: {state.position_side} -> {direction}",
            )

    # Rule 2：entry 信号且无仓 → 开仓
    if signals.entry and not state.has_position:
        direction = "long" if signals.direction > 0 else "short" if signals.direction < 0 else None
        if direction is None:
            return Decision(action="hold", reason="neutral_direction")
        # DNA direction 过滤器：非 mixed 时信号方向与声明一致才开
        if state.allowed_direction != "mixed" and direction != state.allowed_direction:
            return Decision(
                action="hold",
                reason=f"direction_filter: signal={direction}, allowed={state.allowed_direction}",
            )
        entry_pct = target_pct * config.initial_entry_pct
        if config.confidence_sizing_enabled:
            entry_pct *= max(signals.confidence, 0.1)
        return Decision(
            action="open",
            direction=direction,
            target_position_pct=target_pct,
            entry_size_pct=entry_pct,
            reason=f"initial_entry={entry_pct:.1%} of target={target_pct:.1%}",
        )

    # Rule 3：entry 信号与持仓同向 → 加仓
    if signals.entry and state.has_position:
        if config.profit_add_only and state.unrealized_pnl < 0:
            return Decision(
                action="hold",
                reason="profit_add_only: losing position",
            )
        if state.actual_position_pct < target_pct:
            return Decision(
                action="add",
                direction=state.position_side,
                target_position_pct=target_pct,
                reason=f"fill: {state.actual_position_pct:.1%} < target={target_pct:.1%}",
            )
        return Decision(action="hold", reason="already at target position")

    # 无信号：hold（仓位填充逻辑现归 PositionPlan 承担）
    return HOLD


def _compute_target(state: AccountState, config: JudgmentConfig) -> float:
    """计算目标仓位比例：state 显式给正值用 state，否则缺省 0.30。"""
    if state.target_position_pct > 0:
        return state.target_position_pct
    return 0.30
