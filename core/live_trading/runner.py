"""实盘交易 Runner（第7步）—— bar 轮询循环 + 状态同步 + 心跳 + 崩溃恢复孤儿单。

把 Executor 串成 bar 轮询循环。**持仓状态以交易所为准**（sync_state 拉 account/position）。
可测核心：``run_iteration``（单 bar：sync→decide→execute→persist，mock 数据拉取）。
daemon 线程循环（``run``）为生产壳，实连时验证。
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

import pandas as pd

from core.strategy.dna import StrategyDNA
from core.strategy.executor import dna_to_signal_set

from . import persistence
from .executor import LiveTradingExecutor
from .types import LiveAccountState, LivePosition

# 币安 futures_klines 返回的标准 12 列
_KLINE_COLS = [
    "open_time", "open", "high", "low", "close", "volume",
    "close_time", "quote_asset_volume", "trades",
    "taker_buy_base", "taker_buy_quote", "ignore",
]


def parse_position_from_exchange(
    symbol: str, raw_pos: Optional[dict]
) -> Optional[LivePosition]:
    """币安 position_information → LivePosition（One-Way：positionAmt 符号定方向）。flat 返回 None。"""
    if not raw_pos:
        return None
    amt = float(raw_pos.get("positionAmt", 0))
    if amt == 0:
        return None
    side = "long" if amt > 0 else "short"
    return LivePosition(
        symbol=symbol,
        side=side,
        quantity=abs(amt),
        entry_price=float(raw_pos.get("entryPrice", 0)),
        unrealized_pnl=float(raw_pos.get("unRealizedProfit", 0)),
        margin=abs(float(raw_pos.get("initialMargin", 0))),
    )


def parse_account_state(
    symbol: str,
    account: dict,
    position_raw: Optional[dict],
    allowed_direction: str = "mixed",
) -> LiveAccountState:
    """币安 account + position → LiveAccountState（交易所为准）。"""
    balance = 0.0
    for a in account.get("assets", []):
        if a.get("asset") == "USDT":
            balance = float(a.get("availableBalance", a.get("balance", 0)))
            break
    equity = float(account.get("totalWalletBalance", balance))
    pos = parse_position_from_exchange(symbol, position_raw)
    return LiveAccountState(
        balance=balance, equity=equity, position=pos, allowed_direction=allowed_direction
    )


def klines_to_df(klines: list) -> pd.DataFrame:
    """币安 futures_klines 返回 → OHLCV DataFrame（open_time 索引）。"""
    if not klines:
        return pd.DataFrame(columns=["open", "high", "low", "close", "volume"])
    n_cols = min(len(_KLINE_COLS), len(klines[0]))
    cols = _KLINE_COLS[:n_cols]
    df = pd.DataFrame(klines, columns=cols)
    for c in ["open", "high", "low", "close", "volume"]:
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce")
    df["open_time"] = pd.to_datetime(df["open_time"], unit="ms", utc=True)
    return df.set_index("open_time")[["open", "high", "low", "close", "volume"]]


@dataclass
class LiveTradingRunner:
    """实盘任务 Runner（bar 轮询循环）。组件注入可测。"""

    executor: LiveTradingExecutor
    client: Any  # BinanceDemoClient（持有 raw）
    symbol: str
    timeframe: str
    dna: StrategyDNA
    db_path: Path
    task_id: str
    allowed_direction: str = "mixed"
    last_bar_time: Any = None

    def sync_state(self) -> None:
        """从交易所同步账户状态到 executor.state（交易所为准）。"""
        account = self.client.raw.futures_account()
        positions = self.client.raw.futures_position_information(symbol=self.symbol)
        pos_raw = next((p for p in positions if p.get("symbol") == self.symbol), None)
        self.executor.state = parse_account_state(
            self.symbol, account, pos_raw, self.allowed_direction
        )

    def run_iteration(self, klines: list) -> Optional[dict]:
        """单 bar 迭代（可测核心）。

        klines=交易所返回的 K 线列表。返回 {"action","bar_time"} 或 None（无新bar/无动作）。
        时序保证：信号只用闭合 bar 及之前（dna_to_signal_set 对到当前 bar 的 df）= shift(1) 等价。
        """
        df = klines_to_df(klines)
        if df.empty:
            return None
        bar_time = df.index[-1]
        if bar_time == self.last_bar_time:
            return None
        self.last_bar_time = bar_time

        self.sync_state()  # 交易所为准
        sig_set = dna_to_signal_set(self.dna, df)
        bar_idx = len(df) - 1
        close_price = float(df["close"].iloc[bar_idx])
        action, _order = self.executor.execute_closed_bar(sig_set, bar_idx, close_price)
        self._persist()
        persistence.update_heartbeat(self.db_path, self.task_id)
        return {"action": action.value if action else None, "bar_time": str(bar_time)}

    def _persist(self) -> None:
        state = self.executor.state
        pos = state.position
        persistence.update_live_trading_task(
            self.db_path,
            self.task_id,
            last_bar_time=str(self.last_bar_time),
            balance=state.balance,
            equity=state.equity,
            position_side=pos.side if pos else "",
            position_qty=pos.quantity if pos else 0,
            position_entry=pos.entry_price if pos else 0,
            position_sl=pos.sl_price if pos else None,
            position_tp=pos.tp_price if pos else None,
            unrealized_pnl=pos.unrealized_pnl if pos else 0,
        )

    def recover_orphan_orders(self) -> list[dict]:
        """启动时查 demo open orders（孤儿单），返回列表（调用方决定撤/认领，防重复挂单）。"""
        return self.client.raw.futures_get_open_orders(symbol=self.symbol)

    def run(self, poll_interval_sec: float = 2.0, kline_limit: int = 200) -> None:
        """daemon 线程循环（生产壳，实连时验证）。"""
        import time

        while True:
            try:
                klines = self.client.raw.futures_klines(
                    symbol=self.symbol, interval=self.timeframe, limit=kline_limit
                )
                self.run_iteration(klines)
            except Exception as exc:  # noqa: BLE001 - 守护循环不应因单次异常退出
                persistence.update_live_trading_task(
                    self.db_path, self.task_id, status="error", stop_reason=str(exc)[:200]
                )
            time.sleep(poll_interval_sec)
