"""Judgment rule: evaluates trading signals against account state.

Pure function: (BarSignals, AccountState, JudgmentConfig) -> Decision.
No side effects, no state.
"""
from __future__ import annotations

from .types import AccountState, BarSignals, Decision, JudgmentConfig

HOLD = Decision(action="hold", reason="no action needed")


def evaluate(
    signals: BarSignals,
    state: AccountState,
    config: JudgmentConfig | None = None,
) -> Decision:
    """Apply judgment rules to decide whether to act on trading signals.

    Rule priority order:
    1. max_hold_bars safety valve (force close losing positions held too long)
    2. exit/reduce signal + has position -> check hold time and profit
    3. entry signal + no position -> open with initial_entry_pct
    4. entry signal + same-direction position -> add if profitable
    5. entry signal + opposite-direction position -> close first
    6. no signal -> continue adding if profitable and under target

    Args:
        signals: BarSignals for the current bar.
        state: AccountState snapshot.
        config: JudgmentConfig (uses defaults if None).

    Returns:
        Decision indicating what action to take.
    """
    if config is None:
        config = JudgmentConfig()

    target_pct = _compute_target(state, config)

    # Rule 0: max_hold_bars safety valve
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

    # Rule 1: exit/reduce signal + has position
    if (signals.exit or signals.reduce) and state.has_position:
        if state.position_bars_held < config.min_hold_bars:
            return Decision(
                action="hold",
                reason=f"hold_bars={state.position_bars_held} < min={config.min_hold_bars}",
            )
        # Estimate fee cost for min profit check
        notional = state.position_quantity * state.position_entry
        est_fee = notional * config.fee_rate * 2  # round-trip estimate
        if 0 < state.unrealized_pnl < est_fee * config.min_profit_ratio and signals.exit:
            return Decision(
                action="hold",
                reason=f"pnl={state.unrealized_pnl:.2f} < fee_threshold={est_fee * config.min_profit_ratio:.2f}",
            )
        action = "reduce" if signals.reduce and not signals.exit else "close"
        return Decision(action=action, target_position_pct=0.0, reason="signal")

    # Rule 4: entry signal + opposite direction -> close first
    if signals.entry and state.has_position:
        direction = "long" if signals.direction > 0 else "short" if signals.direction < 0 else None
        if direction is None:
            return Decision(action="hold", reason="neutral_direction")
        if direction != state.position_side:
            return Decision(
                action="close",
                reason=f"reverse: {state.position_side} -> {direction}",
            )

    # Rule 2: entry signal + no position -> open
    if signals.entry and not state.has_position:
        direction = "long" if signals.direction > 0 else "short" if signals.direction < 0 else None
        if direction is None:
            return Decision(action="hold", reason="neutral_direction")
        # Enforce DNA direction constraint
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

    # Rule 3: entry signal + same direction -> add
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

    # No signal: return hold (PositionPlan handles fill logic now)
    return HOLD


def _compute_target(state: AccountState, config: JudgmentConfig) -> float:
    """Compute target position percentage from state or default."""
    if state.target_position_pct > 0:
        return state.target_position_pct
    return 0.30
