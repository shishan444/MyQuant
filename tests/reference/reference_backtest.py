"""参照回测实现：纯 numpy 逐笔状态机（路径异构裁判）。

零 vbt/numba/core 依赖（独立性由 tests/accuracy/test_reference_independence 守护）。
复刻 tests/reference/contract.py 的黄金契约；未校准路径（adds/reduces 非零信号）
显式拒绝，不静默近似。

已知口径疑点（DiffReport 登记，不在此静默对齐）：
被测 funding 的持仓值 = raw_equity − cash，空头持仓时为负 → funding 反向
增加权益。本参照按「借入成本」正确语义取 |持仓值|；空头+杠杆场景比对出现
该方向分歧时如实报 DiffReport，由两级处置裁决。
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

# 币安 USDT-M 永续公开档位（公开事实内联，非共享被测常量；来源同 B7 注记）
_MMR_TIERS: tuple[tuple[float, float], ...] = (
    (50_000, 0.004),
    (250_000, 0.005),
    (1_000_000, 0.010),
    (5_000_000, 0.025),
    (20_000_000, 0.050),
    (float("inf"), 0.100),
)

_HOURS_PER_BAR = {
    "1m": 1 / 60, "5m": 5 / 60, "15m": 0.25, "30m": 0.5,
    "1h": 1, "4h": 4, "1d": 24, "3d": 72,
}


def _maintenance_threshold(init_cash: float, leverage: float) -> float:
    """契约 6：threshold = init_cash*(1-(1-mmr)/L)，mmr 按等效名义选档。"""
    notional = init_cash * leverage
    mmr = next(rate for cap, rate in _MMR_TIERS if notional <= cap)
    return init_cash * (1.0 - (1.0 - mmr) / leverage)


@dataclass
class RefTrade:
    entry_idx: int
    exit_idx: int
    direction: int          # +1 long / -1 short
    shares: float
    entry_price: float      # 含滑点成交价
    exit_price: float       # 含滑点成交价（期末收尾=无滑点）
    entry_fee: float
    exit_fee: float
    pnl: float              # 净费后（契约：shares*(out-in) − 双端 fee，方向感知）


@dataclass
class RefResult:
    trades: list[RefTrade] = field(default_factory=list)
    raw_equity: np.ndarray | None = None    # vbt 基线（无杠杆放大）
    unfunded_equity: np.ndarray | None = None  # 放大后、funding 前
    equity: np.ndarray | None = None        # 最终曲线（放大 + funding 后）
    total_funding: float = 0.0
    liquidated: bool = False
    init_cash: float = 0.0


def reference_backtest(
    ohlcv: pd.DataFrame,
    entries: pd.Series,
    exits: pd.Series,
    adds: pd.Series | None = None,
    reduces: pd.Series | None = None,
    entry_direction: pd.Series | None = None,
    *,
    direction: str = "long",
    position_size: float = 0.5,
    leverage: float = 1,
    stop_loss: float = 0.0,
    take_profit: float = 0.0,
    init_cash: float = 100000.0,
    fee: float = 0.001,
    slippage: float = 0.0005,
    timeframe: str = "4h",
) -> RefResult:
    """Run reference backtest: per-trade state machine over shifted signals.

    输入信号为原始（未 shift）序列，契约 1 的 shift(1) 在此内部执行，
    与被测引擎的输入面完全一致。
    """
    adds = adds if adds is not None else pd.Series(False, index=ohlcv.index)
    reduces = reduces if reduces is not None else pd.Series(False, index=ohlcv.index)
    if adds.any() or reduces.any():
        raise NotImplementedError(
            "adds/reduces 路径未经探针校准，参照实现显式拒绝（契约注记）；"
            "需要时先补探针实验再实现"
        )

    # 契约 1：信号延迟一根防前视
    entries_s = entries.shift(1).fillna(False).astype(bool).to_numpy()
    exits_s = exits.shift(1).fillna(False).astype(bool).to_numpy()
    dir_signal = None
    if entry_direction is not None:
        dir_signal = entry_direction.shift(1).fillna(1.0).to_numpy()
    elif direction == "mixed":
        raise ValueError("mixed direction requires entry_direction series")

    close = ohlcv["close"].to_numpy(dtype=np.float64)
    high = ohlcv["high"].to_numpy(dtype=np.float64)
    low = ohlcv["low"].to_numpy(dtype=np.float64)
    n = len(ohlcv)

    cash = init_cash
    shares = 0.0
    pos_dir = 0            # 0 无仓 / +1 / -1
    entry_ref_price = 0.0  # SL/TP 判定基准（无滑点 close）
    ep_slip = 0.0          # 含滑点成交价
    entry_fee_open = 0.0
    entry_idx_open = -1
    is_liquidated = False

    trades: list[RefTrade] = []
    raw_eq = np.empty(n, dtype=np.float64)

    def _close_position(i: int, *, slip: bool = True, fee_charged: bool = True) -> RefTrade:
        """契约 1/7 平仓：exit=close*(1-slip)*(-pos_dir 方向感知)；期末无滑无费。"""
        nonlocal cash, shares, pos_dir, entry_ref_price
        c = close[i]
        if slip:
            xp = c * (1 - slippage) if pos_dir > 0 else c * (1 + slippage)
        else:
            xp = c
        xfee = shares * xp * fee if fee_charged else 0.0
        if pos_dir > 0:
            cash += shares * xp - xfee
        else:
            cash -= shares * xp + xfee
        pnl = pos_dir * shares * (xp - ep_slip) - entry_fee_open - xfee
        t = RefTrade(
            entry_idx=entry_idx_open, exit_idx=i, direction=pos_dir,
            shares=shares, entry_price=ep_slip, exit_price=xp,
            entry_fee=entry_fee_open, exit_fee=xfee, pnl=pnl,
        )
        shares = 0.0
        pos_dir = 0
        entry_ref_price = 0.0
        return t

    for i in range(n):
        c = close[i]
        value_now = cash + shares * c * pos_dir if shares else cash
        acted = False

        # ── 契约 5 优先级 1：已清算状态（强平残余 + 资金检查后重入）
        if is_liquidated:
            if shares != 0.0:
                trades.append(_close_position(i))
                acted = True
            elif entries_s[i]:
                min_required = c * (1.0 + fee + slippage) * 0.01
                if value_now >= min_required:
                    is_liquidated = False
                    entry_ref_price = 0.0
                # 资金不足 → NoOrder（acted 保持 False，但也不进入下方 entry：
                # flag 未重置时 entry 分支被跳过）
        if acted:
            raw_eq[i] = cash + (shares * c * pos_dir if shares else 0.0)
            continue

        # ── 优先级 2：清算检查（lev>1 持仓）
        # 行为锁定注记（缺陷 D-1）：被测 vbt c.value_now 在 order 执行时刻为
        # 前一根收盘估值 → 清算盯市晚一根触发（边界场景多担一根风险）。
        # 本参照复刻该当前行为以锁定比对；哨兵测试守候修复日。
        if leverage > 1.0 and shares != 0.0:
            prev = close[i - 1] if i > 0 else c
            value_chk = cash + shares * prev * pos_dir
            maintenance = _maintenance_threshold(init_cash, leverage)
            if value_chk < maintenance:
                is_liquidated = True
                trades.append(_close_position(i))
                raw_eq[i] = cash
                continue

        # ── 优先级 3：SL / TP（high/low 触发，close±slip 成交）
        if shares != 0.0 and entry_ref_price > 0.0:
            ep = entry_ref_price
            if pos_dir > 0:
                sl_hit = stop_loss > 0 and low[i] <= ep * (1 - stop_loss)
                tp_hit = take_profit > 0 and high[i] >= ep * (1 + take_profit)
            else:
                sl_hit = stop_loss > 0 and high[i] >= ep * (1 + stop_loss)
                tp_hit = take_profit > 0 and low[i] <= ep * (1 - take_profit)
            if sl_hit or tp_hit:
                trades.append(_close_position(i))
                raw_eq[i] = cash
                continue

        # ── 优先级 4：exit 信号
        if exits_s[i] and shares != 0.0:
            trades.append(_close_position(i))
            raw_eq[i] = cash
            continue

        # ── 优先级 5：entry 信号（无仓）
        if entries_s[i] and shares == 0.0 and not is_liquidated:
            if direction in ("long", "short"):
                side = 1 if direction == "long" else -1
            else:  # mixed
                side = 1 if dir_signal[i] > 0 else -1
            price_in = c * (1 + slippage) if side > 0 else c * (1 - slippage)
            # 契约 3：shares = value_now*size / (price_slip*(1+fee))
            qty = value_now * position_size / (price_in * (1 + fee))
            fee_in = qty * price_in * fee
            entry_ref_price = c
            ep_slip = price_in
            entry_fee_open = fee_in
            entry_idx_open = i
            shares = qty
            pos_dir = side
            if side > 0:
                cash -= qty * price_in + fee_in
            else:
                cash += qty * price_in - fee_in
            raw_eq[i] = cash + shares * c * pos_dir
            continue

        # ── 无动作：逐 bar 重估
        raw_eq[i] = cash + (shares * c * pos_dir if shares else 0.0)

    # ── 契约 7：期末 open trade 以末根 close 无滑点无费收尾
    if shares != 0.0:
        trades.append(_close_position(n - 1, slip=False, fee_charged=False))
        raw_eq[n - 1] = cash

    # ── 契约 8：杠杆后处理（区间放大 → funding → 清算标志检测）
    unfunded = _apply_leverage(raw_eq, trades, leverage, init_cash)
    liquidated = False
    if leverage > 1 and len(unfunded):
        maintenance = _maintenance_threshold(init_cash, leverage)
        liquidated = bool((unfunded < maintenance).any())
    pv_levered = _levered_position_values(ohlcv, trades, raw_eq, leverage)
    result, total_funding = _apply_funding(
        unfunded, pv_levered, ohlcv.index, trades, leverage, timeframe,
    )

    return RefResult(
        trades=trades,
        raw_equity=raw_eq,
        unfunded_equity=unfunded,
        equity=result,
        total_funding=total_funding,
        liquidated=liquidated,
        init_cash=init_cash,
    )


def _apply_leverage(raw_eq: np.ndarray, trades: list[RefTrade], leverage: float,
                    init_cash: float) -> np.ndarray:
    """契约 8a：按 trade 区间 [entry,exit] 放大 base+L*(eq-entry_eq)。"""
    if leverage <= 1 or not trades:
        return raw_eq.copy()
    result = np.empty(len(raw_eq), dtype=np.float64)
    base = float(init_cash)
    prev_end = 0
    for t in sorted(trades, key=lambda x: x.entry_idx):
        entry_idx, exit_idx = t.entry_idx, t.exit_idx
        if prev_end < entry_idx:
            result[prev_end:entry_idx] = base
        result[entry_idx:exit_idx + 1] = (
            base + leverage * (raw_eq[entry_idx:exit_idx + 1] - raw_eq[entry_idx])
        )
        base = float(result[exit_idx])
        prev_end = exit_idx + 1
    if prev_end < len(result):
        result[prev_end:] = base
    return result


def _levered_position_values(ohlcv: pd.DataFrame, trades: list[RefTrade],
                             raw_eq: np.ndarray, leverage: float) -> np.ndarray | None:
    """放大后持仓值序列（funding 基数）：raw_equity − cash 逐 bar，×L。

    逐笔状态机不保留逐 bar cash，这里由 raw_eq 与 trade 区间重构：
    持仓 bar 的 cash = 区间起点 entry 后现金（状态机语义：持仓期 cash 不变），
    故 pv[i] = raw_eq[i] − cash_at_entry。取绝对值=借入成本语义（见模块注记）。
    """
    if leverage <= 1 or not trades:
        return None
    n = len(ohlcv)
    pv = np.zeros(n, dtype=np.float64)
    close = ohlcv["close"].to_numpy(dtype=np.float64)
    for t in trades:
        if t.entry_idx >= n:
            continue
        # 持仓市值：[entry, exit) 内 shares*close；exit bar 平仓后归零
        # （与被测 raw_equity−cash 逐 bar 语义一致：平仓当根 pv=0）
        for i in range(t.entry_idx, min(t.exit_idx, n - 1) + 1):
            holding = t.shares * close[i] if i < t.exit_idx else 0.0
            pv[i] = abs(holding)
    return pv * leverage


def _apply_funding(equity: np.ndarray, pv: np.ndarray | None, index: pd.DatetimeIndex,
                   trades: list[RefTrade], leverage: float, timeframe: str,
                   ) -> tuple[np.ndarray, float]:
    """契约 8b：持仓区间每 bar 扣 pv[i-1]*rate；rate=0.001*(h/8)*((L-1)/L)。"""
    if leverage <= 1 or pv is None or not trades:
        return equity, 0.0
    hours = _HOURS_PER_BAR.get(timeframe, 4)
    cost_rate = 0.001 * (hours / 8.0) * ((leverage - 1) / leverage)
    adjusted = equity.copy()
    total = 0.0
    for t in trades:
        for i in range(max(t.entry_idx + 1, 1), t.exit_idx + 1):
            if i < len(adjusted) and pv[i - 1] > 0:
                cost = pv[i - 1] * cost_rate
                total += cost
                adjusted[i] -= cost
    return adjusted, total
