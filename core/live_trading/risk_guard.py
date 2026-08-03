"""实盘下单前风控（第5步）—— 下单前拦截，不复用 check_sl_tp。

模拟交易的 ``check_sl_tp``（account.py:266）是"逐 bar 后扫描 + 有副作用"（事后触发平仓）；
实盘风控是"下单前拦截"（事前拒绝），**语义不同，独立实现**。

每次下单前检查（任一违反 → 抛 ``RiskViolation`` 拒绝下单）：
- **日亏损熔断**（最高优先级）：当日累计亏损达阈值 → 停止下单
- **下单频率**：最小间隔，防程序失控刷单
- **单笔名义额**：开仓单笔上限（平仓不受限——平仓是降风险）
- **保证金占用上限**：开仓后预计占用不超权益比例

这是实盘的生命线：哪怕策略/程序出错，风控兜底防失控。demo 也强制。
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

from .types import OrderAction


class RiskViolation(Exception):
    """风控拦截（含 rule 与 detail）。"""

    def __init__(self, rule: str, detail: str) -> None:
        self.rule = rule
        self.detail = detail
        super().__init__(f"[{rule}] {detail}")


@dataclass
class RiskConfig:
    """风控参数（demo 保守默认）。"""

    max_notional_per_order: float = 1000.0  # 单笔最大名义额(USDT)
    min_order_interval_sec: float = 5.0  # 最小下单间隔(秒)
    daily_loss_limit: float = 500.0  # 日亏损熔断(USDT)，触及停止开新仓
    max_margin_usage: float = 0.5  # 保证金占用上限(占权益)


def _today_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


@dataclass
class RiskGuard:
    """下单前风控守卫（有状态：记录上次下单时间 / 日累计盈亏）。"""

    config: RiskConfig = field(default_factory=RiskConfig)
    _last_order_ts: float = 0.0
    _daily_pnl: float = 0.0
    _daily_pnl_date: str = ""

    def _rollover_daily(self, today: Optional[str] = None) -> None:
        today = today or _today_utc()
        if self._daily_pnl_date != today:
            self._daily_pnl_date = today
            self._daily_pnl = 0.0

    def check_order(
        self,
        action: OrderAction,
        quantity: float,
        price: float,
        equity: float = 0.0,
        used_margin: float = 0.0,
        now_ts: Optional[float] = None,
    ) -> None:
        """下单前检查；违反抛 ``RiskViolation``。``now_ts`` 注入用于测试。"""
        now = now_ts if now_ts is not None else time.time()
        self._rollover_daily()

        # 1. 日亏损熔断（最高优先级）
        if self._daily_pnl <= -self.config.daily_loss_limit:
            raise RiskViolation(
                "daily_loss_limit",
                f"日亏损 {self._daily_pnl:.2f} 触及 -{self.config.daily_loss_limit}",
            )

        # 2. 下单频率
        if self._last_order_ts > 0:
            elapsed = now - self._last_order_ts
            if elapsed < self.config.min_order_interval_sec:
                raise RiskViolation(
                    "min_order_interval",
                    f"距上次下单 {elapsed:.1f}s < {self.config.min_order_interval_sec}s",
                )

        # 3 & 4 仅开仓检查（平仓是降风险，不受名义额/保证金限制）
        if action in (OrderAction.OPEN_LONG, OrderAction.OPEN_SHORT):
            notional = quantity * price
            if notional > self.config.max_notional_per_order:
                raise RiskViolation(
                    "max_notional",
                    f"名义额 {notional:.2f} > {self.config.max_notional_per_order}",
                )
            if equity > 0:
                projected_usage = (used_margin + notional) / equity
                if projected_usage > self.config.max_margin_usage:
                    raise RiskViolation(
                        "max_margin_usage",
                        f"预计保证金占用 {projected_usage:.1%} > {self.config.max_margin_usage:.0%}",
                    )

    def record_order(self, now_ts: Optional[float] = None) -> None:
        """下单成功后记录时间戳（check 通过后调用）。"""
        self._last_order_ts = now_ts if now_ts is not None else time.time()

    def record_pnl(self, pnl: float) -> None:
        """记录一笔平仓盈亏（累计日盈亏，用于熔断判定）。"""
        self._rollover_daily()
        self._daily_pnl += pnl

    @property
    def daily_pnl(self) -> float:
        self._rollover_daily()
        return self._daily_pnl

    def is_halted(self) -> bool:
        """是否因日亏损熔断（停止开新仓）。"""
        self._rollover_daily()
        return self._daily_pnl <= -self.config.daily_loss_limit
