"""Replay runner: drives DecisionPipeline with historical data for rapid verification.

Usage:
    result = ReplayRunner(config).run(dna, df, dfs_by_timeframe)
    print(result.total_return, result.total_trades, result.fill_rate)
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field

import pandas as pd

from core.strategy.dna import StrategyDNA
from core.strategy.executor import dna_to_signal_set
from core.trading.account import VirtualAccount
from core.trading.pipeline import DecisionPipeline
from core.trading.types import JudgmentConfig, OrderEvent

logger = logging.getLogger(__name__)


@dataclass
class ReplayResult:
    """Result of a replay run."""

    total_return: float = 0.0
    total_trades: int = 0
    equity_curve: list[float] = field(default_factory=list)
    events_log: list[dict] = field(default_factory=list)
    order_events_log: list[OrderEvent] = field(default_factory=list)
    bars_processed: int = 0
    fill_rate: float = 0.0  # filled / total orders
    avg_wait_bars: float = 0.0  # average bars from creation to fill

    @property
    def final_equity(self) -> float:
        return self.equity_curve[-1] if self.equity_curve else 0.0


class ReplayRunner:
    """Run the decision pipeline over historical data for verification."""

    def __init__(
        self,
        config: JudgmentConfig | None = None,
        init_cash: float = 10000.0,
        fee: float = 0.001,
        slippage: float = 0.0005,
        warmup_bars: int = 50,
    ) -> None:
        self.config = config or JudgmentConfig()
        self.init_cash = init_cash
        self.fee = fee
        self.slippage = slippage
        self.warmup_bars = warmup_bars

    def run(
        self,
        dna: StrategyDNA,
        df: pd.DataFrame,
        dfs_by_timeframe: dict[str, pd.DataFrame] | None = None,
        start_bar: int | None = None,
        predictor_factory=None,
    ) -> ReplayResult:
        """Run replay over historical data.

        Args:
            dna: Strategy DNA to evaluate.
            df: Historical OHLCV DataFrame with indicators computed.
            dfs_by_timeframe: Optional MTF DataFrames.
            start_bar: Bar index to start from (default: warmup_bars).
            predictor_factory: Callable(dna, df) -> PriceRangePredictor.

        Returns:
            ReplayResult with metrics and event logs.
        """
        # Ensure indicators are computed
        if start_bar is None:
            start_bar = self.warmup_bars

        # 交易链兼容性插桩（0.4.2 R2/D3：DNA 载入点——trail/分批/atr×live
        # 在此显式拒绝，不静默退化按 pct 执行）。trading 域不引入
        # ResearchError，ValueError 沿既有异常通道上层转结构化
        from core.strategy.validator import validate_trading_compatibility
        _reasons = validate_trading_compatibility(dna, "replay")
        if _reasons:
            raise ValueError(
                "回放链不支持该策略（DNA 载入即拒）: " + "; ".join(_reasons))

        # Initialize signal set
        sig_set = dna_to_signal_set(dna, df, dfs_by_timeframe)

        # Initialize account
        account = VirtualAccount(
            dna, init_cash=self.init_cash, fee=self.fee, slippage=self.slippage,
        )

        # Initialize predictor
        predictor = None
        if predictor_factory is not None:
            predictor = predictor_factory(dna, df)
            if predictor is not None and hasattr(predictor, "warmup"):
                predictor.warmup(df, min(start_bar, len(df)))

        # Initialize pipeline
        pipeline = DecisionPipeline(
            config=self.config,
            dna_risk_genes=dna.risk_genes,
        )

        result = ReplayResult()
        total_orders = 0
        filled_orders = 0
        wait_bars_sum = 0

        for i in range(start_bar, len(df)):
            row = df.iloc[i]
            ts = df.index[i]

            bar_high = float(row["high"])
            bar_low = float(row["low"])
            bar_open = float(row["open"])
            bar_close = float(row["close"])

            pipe_result = pipeline.process_bar(
                bar_high=bar_high,
                bar_low=bar_low,
                bar_open=bar_open,
                bar_close=bar_close,
                bar_time=ts.isoformat() if hasattr(ts, "isoformat") else str(ts),
                bar_idx=i,
                account=account,
                predictor=predictor,
                df=df,
                sig_set=sig_set,
                position_size=dna.risk_genes.position_size,
                stop_loss_pct=dna.risk_genes.stop_loss or 0.05,
            )

            # Collect events
            for ev in pipe_result.events:
                result.events_log.append(ev)

            for oe in pipe_result.order_events:
                result.order_events_log.append(oe)
                if oe.action == "created":
                    total_orders += 1
                elif oe.action == "filled":
                    filled_orders += 1

            result.bars_processed += 1

        # Collect final metrics
        if account.equity_snapshots:
            result.equity_curve = [s.equity for s in account.equity_snapshots]

        initial = self.init_cash
        final = result.final_equity if result.final_equity > 0 else (
            account.balance + (account.position.margin + account._unrealized_pnl(bar_close))
            if account.position else account.balance
        )
        result.total_return = (final - initial) / initial if initial > 0 else 0.0
        result.total_trades = sum(
            1 for e in result.events_log
            if e.get("type") == "position_closed"
        )
        result.fill_rate = filled_orders / total_orders if total_orders > 0 else 0.0

        return result
