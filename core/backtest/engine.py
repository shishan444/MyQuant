"""vectorbt backtest engine wrapper using from_order_func for real-time risk management."""

from __future__ import annotations

import math
import numpy as np
import logging
from dataclasses import dataclass
from typing import Dict, Optional, Tuple

import pandas as pd
import vectorbt as vbt
from numba import njit
from vectorbt.portfolio.enums import NoOrder
from vectorbt.portfolio import nb as vbt_nb

from core.strategy.dna import StrategyDNA
from core.strategy.executor import dna_to_signals, dna_to_signal_set, batch_signal_sets, clear_indicator_cache

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 清算模型（batch1 D1：单一权威源）
#
# 合约清算第一性公式: 清算价差 d = (1 - 维持保证金率) / 杠杆 L。
# MAINTENANCE_MARGIN_FRACTION 近似 (1 - mmr)，即价格反向走
# MAINTENANCE_MARGIN_FRACTION / L 时触及清算线。注意：真实币安 USDT-M
# 分层维持率约 0.4-1%，此处 0.9 为偏保守的简化口径（遗留标注，精确化
# 归市场模型层批次）。禁止在本模块其他位置出现第二个维持率字面量。
# ---------------------------------------------------------------------------
MAINTENANCE_MARGIN_FRACTION = 0.9


@njit(cache=True)
def maintenance_threshold(init_cash: float, leverage: float) -> float:
    """权益清算线：权益低于 init_cash * (1 - MAINTENANCE_MARGIN_FRACTION / leverage) 触发清算。

    纯算术函数——@njit 订单回调（order_func_nb）与事后检测（run 内）
    共同引用此唯一实现，保证逐 bar 判定与事后检测结论一致。
    """
    return init_cash * (1.0 - MAINTENANCE_MARGIN_FRACTION / leverage)


@dataclass
class BacktestResult:
    """Structured result from a backtest run."""
    total_return: float
    sharpe_ratio: float
    max_drawdown: float
    win_rate: float
    total_trades: int
    equity_curve: pd.Series
    trades_df: pd.DataFrame | None = None
    total_funding_cost: float = 0.0
    liquidated: bool = False
    data_bars: int = 0
    trade_win_rate: float | None = None
    # 胜率三态（batch1 D2）：None+win_rate_failed=False=无交易（合法缺失）；
    # 数值=计算成功；None+win_rate_failed=True=计算失败（显式失败，禁止按缺失降级）
    win_rate_failed: bool = False
    trade_returns: np.ndarray | None = None
    bars_per_year: int = 2190
    add_count: int = 0
    reduce_count: int = 0
    metrics_dict: dict | None = None
    degraded_layers: int = 0
    market_annual_return: float = 0.0
    alpha: float = 0.0


@njit
def _apply_funding_loop_nb(adjusted: np.ndarray, position_mask: np.ndarray,
                           cost_rate: float,
                           position_values: np.ndarray | None = None) -> float:
    """Numba-compiled inner loop for funding cost deduction.

    Args:
        adjusted: Float64 equity values (modified in-place).
        position_mask: Bool array, True when position is open.
        cost_rate: Per-bar funding cost rate.
        position_values: Float64 array of position values. If provided,
            funding is based on position_value instead of total equity.

    Returns:
        Total funding cost deducted.
    """
    total_cost = 0.0
    for i in range(1, len(adjusted)):
        if position_mask[i]:
            base = position_values[i - 1] if position_values is not None else adjusted[i - 1]
            cost = base * cost_rate
            total_cost += cost
            adjusted[i] -= cost
    return total_cost


def _apply_funding_costs(
    equity_curve: pd.Series, leverage: int, timeframe: str,
    trades_df: Optional[pd.DataFrame] = None,
    position_values: Optional[np.ndarray] = None,
) -> tuple[pd.Series, float]:
    """Deduct leveraged funding costs only while position is open.

    Args:
        equity_curve: Portfolio equity over time.
        leverage: Position leverage.
        timeframe: Bar timeframe string.
        trades_df: Trade records with 'Entry Timestamp' and 'Exit Timestamp'.
                   If None, costs are deducted for all bars (legacy behavior).
        position_values: Array of per-bar position values. If provided,
            funding cost is based on position_value (actual borrowed amount)
            instead of total equity.
    """
    if leverage <= 1:
        return equity_curve, 0.0

    RATE_PER_8H = 0.001
    hours_per_bar = {
        "1m": 1/60, "5m": 5/60, "15m": 0.25, "30m": 0.5,
        "1h": 1, "4h": 4, "1d": 24, "3d": 72,
    }.get(timeframe, 4)
    # Proportional periods instead of ceil to avoid overcharging
    periods_per_bar = hours_per_bar / 8.0
    borrowed_ratio = (leverage - 1) / leverage
    cost_rate = RATE_PER_8H * periods_per_bar * borrowed_ratio

    # Build position mask: True when position is open
    position_mask = np.zeros(len(equity_curve), dtype=np.bool_)
    if trades_df is not None and len(trades_df) > 0:
        for _, trade in trades_df.iterrows():
            entry_ts = trade.get("Entry Timestamp")
            exit_ts = trade.get("Exit Timestamp")
            if entry_ts is None:
                continue
            if pd.isna(exit_ts) or exit_ts is None:
                # Open trade: position open from entry to end
                mask_slice = equity_curve.index >= entry_ts
            else:
                mask_slice = (equity_curve.index >= entry_ts) & (equity_curve.index <= exit_ts)
            position_mask |= np.asarray(mask_slice).astype(np.bool_)
    elif trades_df is None:
        # Legacy: no trades_df passed, deduct for all bars
        position_mask[:] = True
    # else: trades_df provided but empty -> no funding costs (correct)

    adjusted = equity_curve.values.astype(np.float64).copy()

    # Prepare position_values for funding calculation
    pv_for_funding = None
    if position_values is not None:
        pv_for_funding = position_values.astype(np.float64)

    total_cost = _apply_funding_loop_nb(adjusted, position_mask, cost_rate, pv_for_funding)
    return pd.Series(adjusted, index=equity_curve.index), total_cost


def _apply_leverage_to_equity(
    equity_curve: pd.Series,
    trades_df: pd.DataFrame | None,
    leverage: float,
    init_cash: float,
) -> pd.Series:
    """Amplify equity curve to reflect leverage effect.

    vbt runs with position_size only (no leverage). This function
    post-processes the curve so gains/losses are amplified by leverage.
    """
    if leverage <= 1 or trades_df is None or len(trades_df) == 0:
        return equity_curve

    eq_vals = equity_curve.values.astype(np.float64)
    result = np.empty(len(eq_vals), dtype=np.float64)

    # Build sorted trade intervals (entry_idx, exit_idx)
    intervals = []
    for _, trade in trades_df.iterrows():
        entry_ts = trade.get("Entry Timestamp")
        exit_ts = trade.get("Exit Timestamp")
        if entry_ts is None:
            continue
        if pd.isna(exit_ts) or exit_ts is None:
            exit_ts = equity_curve.index[-1]
        mask = (equity_curve.index >= entry_ts) & (equity_curve.index <= exit_ts)
        indices = np.where(mask)[0]
        if len(indices) == 0:
            continue
        intervals.append((int(indices[0]), int(indices[-1])))

    intervals.sort()

    # Apply amplification per trade block
    leveraged_base = float(init_cash)
    prev_end = 0

    for entry_idx, exit_idx in intervals:
        # Flat region before this trade
        if prev_end < entry_idx:
            result[prev_end:entry_idx] = leveraged_base

        # Amplify during position
        vbt_entry_eq = eq_vals[entry_idx]
        result[entry_idx:exit_idx + 1] = (
            leveraged_base + leverage * (eq_vals[entry_idx:exit_idx + 1] - vbt_entry_eq)
        )

        leveraged_base = float(result[exit_idx])
        prev_end = exit_idx + 1

    # Fill remaining flat region
    if prev_end < len(result):
        result[prev_end:] = leveraged_base

    return pd.Series(result, index=equity_curve.index)


@njit
def pre_sim_func_nb(c):
    """Initialize mutable state arrays for the simulation.

    Returns (entry_price, is_liquidated) arrays, one element per column.
    These are passed as the first args to order_func_nb via the lifecycle chain.
    """
    n_cols = c.target_shape[1]
    entry_price = np.full(n_cols, 0.0, dtype=np.float64)
    is_liquidated = np.full(n_cols, 0.0, dtype=np.float64)  # 0.0=False, 1.0=True
    return (entry_price, is_liquidated)


@njit
def order_func_nb(c, entry_price, is_liquidated,
                  entries, exits, adds, reduces,
                  direction_vals, size_pcts, leverages,
                  sl_stops, tp_stops, fee, slippage,
                  high_arr, low_arr, direction_signal):
    """Per-bar order callback with real-time SL/TP and liquidation.

    Args via lifecycle chain:
        entry_price, is_liquidated: mutable state from pre_sim_func_nb

    Args via order_args:
        entries, exits, adds, reduces: 2D float64 arrays
        direction_vals, size_pcts, leverages, sl_stops, tp_stops:
            1D float64 arrays (one value per column)
        fee, slippage: scalar float64
        high_arr, low_arr: 2D float64 arrays for intrabar SL/TP
        direction_signal: 2D float64 array, +1=long, -1=short (for mixed mode)
    """
    i = c.i
    col = c.col

    # Per-column parameter lookup
    direction_val = direction_vals[col]
    size_pct = size_pcts[col]
    leverage = leverages[col]
    sl_stop = sl_stops[col]
    tp_stop = tp_stops[col]
    # Fee/slippage applied at base rate; leverage amplification is handled
    # by _apply_leverage_to_equity in post-processing.
    effective_fee = fee
    effective_slippage = slippage

    # Already liquidated -> force close any remaining position, then allow re-entry if funds remain
    if is_liquidated[col] > 0.5:
        if c.position_now != 0.0:
            return vbt_nb.order_nb(
                size=np.float64(-c.position_now),
                size_type=np.int64(0),
                fees=effective_fee,
                slippage=effective_slippage,
            )
        # Position closed after liquidation, check if we can re-enter
        # If no entry signal, stay out; if entry signal, check funds below
        if entries[i, col] < 0.5:
            return NoOrder
        # Entry signal after liquidation: check if sufficient funds to open
        current_price = c.close[i, col]
        min_required = current_price * (1.0 + fee + slippage) * 0.01  # at least 1% of a share's cost
        if c.value_now < min_required:
            return NoOrder  # insufficient funds
        # Reset liquidation flag and proceed to entry logic below
        is_liquidated[col] = 0.0
        entry_price[col] = 0.0
        # Fall through to entry signal handling below

    current_price = c.close[i, col]

    # Liquidation check BEFORE SL/TP (for leveraged positions)
    if leverage > 1.0 and c.position_now != 0.0:
        maintenance = maintenance_threshold(c.init_cash[0], leverage)
        if c.value_now < maintenance:
            is_liquidated[col] = 1.0
            entry_price[col] = 0.0
            return vbt_nb.order_nb(
                size=np.float64(-c.position_now),
                size_type=np.int64(0),
                fees=effective_fee,
                slippage=effective_slippage,
            )

    # Stop-Loss / Take-Profit check using HIGH/LOW
    if c.position_now != 0.0 and entry_price[col] > 0.0:
        bar_high = high_arr[i, col]
        bar_low = low_arr[i, col]
        ep = entry_price[col]

        if c.position_now > 0.0:
            # Long position
            sl_level = ep * (1.0 - sl_stop) if sl_stop > 0.0 else 0.0
            tp_level = ep * (1.0 + tp_stop) if tp_stop > 0.0 else 0.0
            # SL: bar's LOW touches SL level
            if sl_stop > 0.0 and bar_low <= sl_level:
                entry_price[col] = 0.0
                return vbt_nb.order_nb(
                    size=np.float64(-c.position_now),
                    size_type=np.int64(0),
                    fees=effective_fee,
                    slippage=effective_slippage,
                )
            # TP: bar's HIGH touches TP level
            if tp_stop > 0.0 and bar_high >= tp_level:
                entry_price[col] = 0.0
                return vbt_nb.order_nb(
                    size=np.float64(-c.position_now),
                    size_type=np.int64(0),
                    fees=effective_fee,
                    slippage=effective_slippage,
                )
        else:
            # Short position
            sl_level = ep * (1.0 + sl_stop) if sl_stop > 0.0 else 0.0
            tp_level = ep * (1.0 - tp_stop) if tp_stop > 0.0 else 0.0
            # SL: bar's HIGH touches SL level
            if sl_stop > 0.0 and bar_high >= sl_level:
                entry_price[col] = 0.0
                return vbt_nb.order_nb(
                    size=np.float64(-c.position_now),
                    size_type=np.int64(0),
                    fees=effective_fee,
                    slippage=effective_slippage,
                )
            # TP: bar's LOW touches TP level
            if tp_stop > 0.0 and bar_low <= tp_level:
                entry_price[col] = 0.0
                return vbt_nb.order_nb(
                    size=np.float64(-c.position_now),
                    size_type=np.int64(0),
                    fees=effective_fee,
                    slippage=effective_slippage,
                )

    # Exit signal
    if exits[i, col] > 0.5 and c.position_now != 0.0:
        entry_price[col] = 0.0
        return vbt_nb.order_nb(
            size=np.float64(-c.position_now),
            size_type=np.int64(0),
            fees=effective_fee,
            slippage=effective_slippage,
        )

    # Entry signal
    if entries[i, col] > 0.5 and c.position_now == 0.0:
        entry_price[col] = current_price
        # For mixed mode: use direction_signal to determine trade side
        dir_sign = direction_signal[i, col]
        if direction_val < 1.5:  # long(0) or short(1) - fixed direction
            dir_sign = 1.0  # size is always positive; direction_val controls side
            actual_direction = direction_val
        else:  # mixed(2) - direction from signal
            if dir_sign > 0.0:
                actual_direction = 0.0  # long
            else:
                actual_direction = 1.0  # short
            dir_sign = 1.0  # size positive, direction controls side
        return vbt_nb.order_nb(
            size=np.float64(size_pct * dir_sign),
            size_type=np.int64(2),  # Percent
            direction=np.int64(int(actual_direction)),
            fees=effective_fee,
            slippage=effective_slippage,
        )

    # Reduce signal (partial exit) - reduce position by percentage
    if reduces[i, col] > 0.5 and c.position_now != 0.0:
        reduce_amount = c.position_now * size_pct
        return vbt_nb.order_nb(
            size=np.float64(-reduce_amount),
            size_type=np.int64(0),
            fees=effective_fee,
            slippage=effective_slippage,
        )

    # Add signal (add to position)
    if adds[i, col] > 0.5 and c.position_now != 0.0:
        old_pos = abs(c.position_now)
        old_ep = entry_price[col]
        # Estimate add shares based on current portfolio value and price
        add_value = c.value_now * size_pct
        add_shares = add_value / current_price
        # Update entry_price to weighted average
        if old_pos + add_shares > 0.0:
            new_ep = (old_ep * old_pos + current_price * add_shares) / (old_pos + add_shares)
            entry_price[col] = new_ep
        # Add direction must match current position direction
        if c.position_now > 0.0:
            add_dir = np.int64(0)   # Long (Buy more)
        else:
            add_dir = np.int64(1)   # Short (Sell more)
        return vbt_nb.order_nb(
            size=np.float64(size_pct),
            size_type=np.int64(2),  # Percent
            direction=add_dir,
            fees=effective_fee,
            slippage=effective_slippage,
        )

    return NoOrder


class BacktestEngine:
    """Backtest engine using vectorbt Portfolio.from_order_func."""

    def __init__(self, init_cash: float = 100000, fee: float = 0.001,
                 slippage: float = 0.0005):
        self.init_cash = init_cash
        self.fee = fee
        self.slippage = slippage

    def _build_portfolio(
        self,
        dna: StrategyDNA,
        enhanced_df: pd.DataFrame,
        dfs_by_timeframe: Optional[Dict[str, pd.DataFrame]] = None,
        signal_set=None,
    ):
        """Construct a vectorbt Portfolio from DNA signals using from_order_func."""
        if signal_set is not None:
            sig_set = signal_set
        else:
            sig_set = dna_to_signal_set(dna, enhanced_df, dfs_by_timeframe=dfs_by_timeframe)

        # Delay all signals by 1 bar to prevent look-ahead bias
        entries = sig_set.entries.shift(1).fillna(False).astype(bool)
        exits = sig_set.exits.shift(1).fillna(False).astype(bool)
        adds = sig_set.adds.shift(1).fillna(False).astype(bool)
        reduces = sig_set.reduces.shift(1).fillna(False).astype(bool)

        close = enhanced_df["close"]
        high = enhanced_df["high"]
        low = enhanced_df["low"]

        direction_map = {"long": 0, "short": 1, "mixed": 2}
        direction_val = direction_map.get(dna.risk_genes.direction, 0)
        size_pct = dna.risk_genes.position_size
        leverage = float(dna.risk_genes.leverage)
        sl_stop = float(dna.risk_genes.stop_loss) if dna.risk_genes.stop_loss else 0.0
        tp_stop = float(dna.risk_genes.take_profit) if dna.risk_genes.take_profit else 0.0

        # Convert signals to 2D numpy arrays for Numba compatibility
        entries_2d = entries.values.astype(np.float64).reshape(-1, 1)
        exits_2d = exits.values.astype(np.float64).reshape(-1, 1)
        adds_2d = adds.values.astype(np.float64).reshape(-1, 1)
        reduces_2d = reduces.values.astype(np.float64).reshape(-1, 1)
        high_2d = high.values.astype(np.float64).reshape(-1, 1)
        low_2d = low.values.astype(np.float64).reshape(-1, 1)

        # Build direction signal for mixed mode (shifted by 1 bar to prevent look-ahead)
        if sig_set.entry_direction is not None:
            direction_shifted = sig_set.entry_direction.shift(1).fillna(1.0)
            direction_signal_2d = direction_shifted.values.astype(np.float64).reshape(-1, 1)
        else:
            if direction_val >= 1.5:  # mixed but no direction signal
                logger.warning(
                    "direction='mixed' but no entry_direction signal; "
                    "defaulting to long. strategy_id=%s",
                    getattr(dna, "strategy_id", None),
                )
            direction_signal_2d = np.ones_like(entries_2d)

        pf = vbt.Portfolio.from_order_func(
            close,
            order_func_nb,
            entries_2d,
            exits_2d,
            adds_2d,
            reduces_2d,
            np.array([direction_val], dtype=np.float64),
            np.array([size_pct], dtype=np.float64),
            np.array([leverage], dtype=np.float64),
            np.array([sl_stop], dtype=np.float64),
            np.array([tp_stop], dtype=np.float64),
            np.float64(self.fee),
            np.float64(self.slippage),
            high_2d,
            low_2d,
            direction_signal_2d,
            pre_sim_func_nb=pre_sim_func_nb,
            init_cash=self.init_cash,
            freq=None,
        )

        return pf, int(adds.sum()), int(reduces.sum()), sig_set.degraded_layers

    def _build_result_from_portfolio(
        self,
        portfolio,
        dna: StrategyDNA,
        enhanced_df: pd.DataFrame,
        add_count: int = 0,
        reduce_count: int = 0,
        degraded: int = 0,
    ) -> BacktestResult:
        """Build BacktestResult from an already-constructed portfolio."""
        equity_curve = portfolio.value()
        if isinstance(equity_curve, pd.DataFrame):
            equity_curve = equity_curve.iloc[:, 0]

        # Extract vbt raw cash to compute position_value = equity - cash
        position_values_arr = None
        try:
            cash_curve = portfolio.cash()
            if isinstance(cash_curve, pd.DataFrame):
                cash_curve = cash_curve.iloc[:, 0]
            raw_equity = portfolio.value()
            if isinstance(raw_equity, pd.DataFrame):
                raw_equity = raw_equity.iloc[:, 0]
            position_values_arr = (raw_equity.values - cash_curve.values).astype(np.float64)
        except Exception:
            pass

        # Apply leverage amplification to equity curve
        leverage = float(dna.risk_genes.leverage)
        trades_for_leverage = portfolio.trades.records_readable if hasattr(portfolio.trades, "records_readable") else None
        equity_curve = _apply_leverage_to_equity(
            equity_curve, trades_for_leverage, leverage, self.init_cash,
        )

        # Amplify position_values to match leveraged equity
        if position_values_arr is not None and leverage > 1:
            position_values_arr = position_values_arr * leverage

        # Check if liquidation occurred (on amplified curve)
        liquidated = False
        if leverage > 1 and len(equity_curve) > 0:
            maintenance = maintenance_threshold(self.init_cash, leverage)
            if (equity_curve < maintenance).any():
                liquidated = True

        # Funding costs for leveraged positions (on amplified curve)
        timeframe = dna.execution_genes.timeframe
        trades_for_funding = portfolio.trades.records_readable if hasattr(portfolio.trades, "records_readable") else None
        equity_curve, total_funding_cost = _apply_funding_costs(
            equity_curve, dna.risk_genes.leverage, timeframe,
            trades_df=trades_for_funding,
            position_values=position_values_arr,
        )

        total_trades_val = portfolio.trades.count()
        if isinstance(total_trades_val, pd.Series):
            total_trades_val = int(total_trades_val.sum())
        total_trades_val = int(total_trades_val) if not pd.isna(total_trades_val) else 0

        from core.scoring.metrics import compute_metrics
        bars_per_year_map = {"15m": 365 * 96, "30m": 365 * 48, "1h": 365 * 24,
                             "4h": 365 * 6, "1d": 365, "3d": 365 // 3}
        bars_per_year = bars_per_year_map.get(timeframe, 365 * 6)

        trade_win_rate = None
        trade_returns = None
        win_rate_failed = False
        if total_trades_val > 0:
            try:
                trade_pnl = portfolio.trades.pnl
                if hasattr(trade_pnl, 'values'):
                    pnl_arr = np.array(trade_pnl.values).flatten()
                else:
                    pnl_arr = np.array(trade_pnl).flatten()

                if len(pnl_arr) > 0:
                    winning = int((pnl_arr > 0).sum())
                    trade_win_rate = winning / len(pnl_arr)
                    trade_returns = pnl_arr / self.init_cash
            except Exception:
                # 三态（D2）：计算失败不得静默降级为「无胜率」——记录原因链并显式标记
                win_rate_failed = True
                logger.error(
                    "win_rate computation failed: total_trades=%s", total_trades_val,
                    exc_info=True,
                )

        metrics = compute_metrics(
            equity_curve, total_trades=total_trades_val,
            bars_per_year=bars_per_year,
            trade_win_rate=trade_win_rate,
            trade_returns=trade_returns,
            benchmark_close=enhanced_df["close"],
        )

        sharpe = metrics["sharpe_ratio"]
        max_dd = metrics["max_drawdown"]
        win_rate = metrics["win_rate"]

        if len(equity_curve) >= 2 and equity_curve.iloc[0] > 0:
            raw_return = float(equity_curve.iloc[-1] / equity_curve.iloc[0] - 1)
        else:
            raw_return = 0.0

        trades = portfolio.trades.records_readable if hasattr(portfolio.trades, "records_readable") else None

        return BacktestResult(
            total_return=raw_return,
            sharpe_ratio=float(sharpe),
            max_drawdown=float(max_dd),
            win_rate=float(win_rate),
            total_trades=total_trades_val,
            equity_curve=equity_curve,
            trades_df=trades,
            total_funding_cost=total_funding_cost,
            liquidated=liquidated,
            data_bars=len(enhanced_df),
            trade_win_rate=trade_win_rate,
            win_rate_failed=win_rate_failed,
            trade_returns=trade_returns,
            bars_per_year=bars_per_year,
            add_count=add_count,
            reduce_count=reduce_count,
            metrics_dict=metrics,
            degraded_layers=degraded,
            market_annual_return=metrics.get("market_annual_return", 0.0),
            alpha=metrics.get("alpha", 0.0),
        )

    def run(
        self,
        dna: StrategyDNA,
        enhanced_df: pd.DataFrame,
        dfs_by_timeframe: Optional[Dict[str, pd.DataFrame]] = None,
        signal_set=None,
    ) -> BacktestResult:
        """Run backtest for a single strategy DNA."""
        build_result = self._build_portfolio(
            dna, enhanced_df,
            dfs_by_timeframe=dfs_by_timeframe,
            signal_set=signal_set,
        )
        if isinstance(build_result, tuple):
            portfolio, add_count, reduce_count, degraded = build_result
        else:
            portfolio = build_result
            add_count, reduce_count, degraded = 0, 0, 0

        return self._build_result_from_portfolio(
            portfolio, dna, enhanced_df, add_count, reduce_count, degraded,
        )

    def run_with_portfolio(
        self,
        dna: StrategyDNA,
        enhanced_df: pd.DataFrame,
        dfs_by_timeframe: Optional[Dict[str, pd.DataFrame]] = None,
    ) -> Tuple["BacktestResult", object]:
        """Run backtest and return (BacktestResult, vectorbt Portfolio) tuple."""
        build_result = self._build_portfolio(dna, enhanced_df, dfs_by_timeframe=dfs_by_timeframe)
        if isinstance(build_result, tuple):
            portfolio, add_count, reduce_count, degraded = build_result
        else:
            portfolio = build_result
            add_count, reduce_count, degraded = 0, 0, 0
        result = self._build_result_from_portfolio(
            portfolio, dna, enhanced_df, add_count, reduce_count, degraded,
        )
        return result, portfolio

    def batch_run(
        self,
        individuals: list[StrategyDNA],
        enhanced_df: pd.DataFrame,
        dfs_by_timeframe: dict | None = None,
        signal_sets: list | None = None,
    ) -> list[BacktestResult]:
        """Batch-evaluate multiple individuals in a single vbt call.

        Instead of calling run() N times, stacks all signals into (N, M) matrices
        and runs one Portfolio.from_order_func. Each column corresponds to one
        individual, enabling vectorized evaluation across the whole population.

        Args:
            individuals: List of StrategyDNA to evaluate.
            enhanced_df: Market data with indicators.
            dfs_by_timeframe: Multi-timeframe data dict (optional).
            signal_sets: Pre-computed signal sets (optional, saves recomputation).

        Returns:
            List[BacktestResult] in the same order as individuals.
        """
        M = len(individuals)
        if M == 0:
            return []
        N = len(enhanced_df)

        # 1. Compute signal sets
        if signal_sets is not None:
            sig_sets = signal_sets
        else:
            sig_sets = batch_signal_sets(individuals, enhanced_df, dfs_by_timeframe)

        # 2. Stack signals into (N, M) matrices
        entries_parts = []
        exits_parts = []
        adds_parts = []
        reduces_parts = []
        direction_signal_parts = []
        add_counts = []
        reduce_counts = []
        degraded_layers_list = []

        for ss in sig_sets:
            entries_parts.append(
                ss.entries.shift(1).fillna(False).astype(float).values
            )
            exits_parts.append(
                ss.exits.shift(1).fillna(False).astype(float).values
            )
            adds_parts.append(
                ss.adds.shift(1).fillna(False).astype(float).values
            )
            reduces_parts.append(
                ss.reduces.shift(1).fillna(False).astype(float).values
            )
            add_counts.append(int(ss.adds.sum()))
            reduce_counts.append(int(ss.reduces.sum()))
            degraded_layers_list.append(ss.degraded_layers)

            if ss.entry_direction is not None:
                direction_signal_parts.append(
                    ss.entry_direction.shift(1).fillna(1.0).astype(float).values
                )
            else:
                direction_signal_parts.append(np.ones(N))

        entries_matrix = np.column_stack(entries_parts).astype(np.float64)
        exits_matrix = np.column_stack(exits_parts).astype(np.float64)
        adds_matrix = np.column_stack(adds_parts).astype(np.float64)
        reduces_matrix = np.column_stack(reduces_parts).astype(np.float64)
        direction_signal_matrix = np.column_stack(direction_signal_parts).astype(np.float64)

        close = enhanced_df["close"]
        high_2d = np.tile(
            enhanced_df["high"].values.astype(np.float64).reshape(-1, 1), (1, M)
        )
        low_2d = np.tile(
            enhanced_df["low"].values.astype(np.float64).reshape(-1, 1), (1, M)
        )

        # 3. Build per-column parameter arrays
        direction_map = {"long": 0, "short": 1, "mixed": 2}
        direction_vals = np.array(
            [direction_map.get(dna.risk_genes.direction, 0) for dna in individuals],
            dtype=np.float64,
        )
        size_pcts = np.array(
            [float(dna.risk_genes.position_size)
             for dna in individuals],
            dtype=np.float64,
        )
        leverages = np.array(
            [float(dna.risk_genes.leverage) for dna in individuals],
            dtype=np.float64,
        )
        sl_stops = np.array(
            [float(dna.risk_genes.stop_loss) if dna.risk_genes.stop_loss else 0.0
             for dna in individuals],
            dtype=np.float64,
        )
        tp_stops = np.array(
            [float(dna.risk_genes.take_profit) if dna.risk_genes.take_profit else 0.0
             for dna in individuals],
            dtype=np.float64,
        )

        # 4. Single vbt call -- tile close to M columns to match signal matrices
        close_2d = np.tile(
            close.values.astype(np.float64).reshape(-1, 1), (1, M)
        )
        pf = vbt.Portfolio.from_order_func(
            close_2d,
            order_func_nb,
            entries_matrix,
            exits_matrix,
            adds_matrix,
            reduces_matrix,
            direction_vals,
            size_pcts,
            leverages,
            sl_stops,
            tp_stops,
            np.float64(self.fee),
            np.float64(self.slippage),
            high_2d,
            low_2d,
            direction_signal_matrix,
            pre_sim_func_nb=pre_sim_func_nb,
            init_cash=self.init_cash,
            freq=None,
        )

        # 5. Extract per-column results
        results = []
        for col_idx in range(M):
            col_pf = pf.iloc[col_idx]
            result = self._build_result_from_portfolio(
                col_pf, individuals[col_idx], enhanced_df,
                add_count=add_counts[col_idx],
                reduce_count=reduce_counts[col_idx],
                degraded=degraded_layers_list[col_idx],
            )
            results.append(result)

        return results
