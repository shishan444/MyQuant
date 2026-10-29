"""实盘交易 Runner（第7步）—— 帧驱动循环 + 状态同步 + 心跳 + 崩溃恢复孤儿单。

把 Executor 串成帧驱动循环（unify-fapi-sdk 契约 4：K 线唯一来源=
registry.acquire——与纸盘同码同实例，自建 REST 轮询通道退役）。
**持仓状态以交易所为准**（sync_state 拉 account/position）。
可测核心：``run_iteration``（单帧：sync→decide→execute→persist，
合成帧注入）。daemon 线程循环（``run``）为生产壳，实连时验证。
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

import pandas as pd

from core.paper_trading.runtime.feed import FeedRegistry, FeedSubscription
from core.strategy.dna import StrategyDNA
from core.strategy.executor import dna_to_signal_set

from core.persistence.repository import live_trading as persistence  # B3c.1：原 .persistence 退役收编
from .executor import LiveTradingExecutor
from .types import LiveAccountState, LivePosition


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


@dataclass
class LiveTradingRunner:
    """实盘任务 Runner（帧驱动循环——registry 注入）。组件注入可测。"""

    executor: LiveTradingExecutor
    client: Any  # BinanceDemoClient（持有 raw——交易面 python_binance）
    registry: FeedRegistry  # K 线唯一来源（同源原则——与纸盘同码同实例）
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

    def run_iteration(self, frame: pd.DataFrame) -> Optional[dict]:
        """单帧迭代（可测核心）。

        frame=feed 广播帧（OHLCV+指标列只读引用——与纸盘执行器同数据
        形制）。自身游标定位帧内新根逐根推进（last_bar_time 判重——
        from_idx 是提示非承诺，feed.py:127 契约一致；重复/旧根跳过；
        首帧只取尾根建立基线不回放全历史）。返回尾根结果
        {"action","bar_time"} 或 None（无新根/无动作）。
        时序保证：信号只用闭合 bar 及之前（dna_to_signal_set 对到当前
        bar 的 df）= shift(1) 等价。
        """
        if frame is None or frame.empty:
            return None
        if self.last_bar_time is None:
            new_rows = frame.iloc[[-1]]
        else:
            new_rows = frame[frame.index > self.last_bar_time]
        if new_rows.empty:
            return None
        result: Optional[dict] = None
        for bar_time in new_rows.index:
            self.last_bar_time = bar_time
            self.sync_state()  # 交易所为准
            sig_set = dna_to_signal_set(self.dna, frame.loc[:bar_time])
            bar_idx = frame.index.get_loc(bar_time)
            close_price = float(frame["close"].loc[bar_time])
            action, _order = self.executor.execute_closed_bar(
                sig_set, bar_idx, close_price)
            self._persist()
            persistence.update_heartbeat(self.db_path, self.task_id)
            result = {
                "action": action.value if action else None,
                "bar_time": str(bar_time)}
        return result

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

    def run(self) -> None:
        """daemon 线程循环（帧驱动生产壳，实连时验证）。

        registry.acquire 订阅（帧回调仅入队——feed.py:142-144 契约）
        → 队列阻塞逐帧推进 → 退出 release（订阅归零回收语义参与）。
        K 线获取路径唯一（同源原则：与纸盘同码同实例，零自建通道）。
        """
        import queue as _queue

        frames: "_queue.SimpleQueue" = _queue.SimpleQueue()
        subscription = FeedSubscription(
            on_frames=lambda df, idx: frames.put((df, idx)))
        feed = self.registry.acquire(self.symbol, self.timeframe, subscription)
        try:
            while True:
                try:
                    frame, _from_idx = frames.get()
                    self.run_iteration(frame)
                except Exception as exc:  # noqa: BLE001 - 守护循环不应因单次异常退出
                    persistence.update_live_trading_task(
                        self.db_path, self.task_id, status="error",
                        stop_reason=str(exc)[:200])
        finally:
            self.registry.release(self.symbol, self.timeframe, subscription)
