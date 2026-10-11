"""策略历史回放评估器（ReplayRunner）——历史数据驱动决策管线快速验证。

职能：把 DecisionPipeline + VirtualAccount 在给定历史 OHLCV 上逐 bar
推演，产出收益/成交率/事件日志等评估指标；与 paper 模拟盘链共享
同一决策与记账组件——回放验证的就是模拟盘将来执行的确切行为。

服务对象：api/routes/trading.py 回放端点与测试（纯内存评估，不落
任务表、不推送 WS）。

用法：
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
    """回放产出：收益/交易数/权益曲线/事件与订单日志/成交率等指标。"""

    total_return: float = 0.0
    total_trades: int = 0
    equity_curve: list[float] = field(default_factory=list)
    events_log: list[dict] = field(default_factory=list)
    order_events_log: list[OrderEvent] = field(default_factory=list)
    bars_processed: int = 0
    fill_rate: float = 0.0  # 成交订单数 / 总订单数
    avg_wait_bars: float = 0.0  # 订单从创建到成交的平均等待 bar 数

    @property
    def final_equity(self) -> float:
        return self.equity_curve[-1] if self.equity_curve else 0.0


class ReplayRunner:
    """决策管线的历史数据回放验证器（纯内存评估，与 paper 链共享
    DecisionPipeline/VirtualAccount——评估结论对模拟盘执行有效）。"""

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
        """历史数据回放：从 start_bar 起逐 bar 驱动决策管线。

        Args:
            dna: 待评估的策略 DNA。
            df: 已计算指标的历史 OHLCV DataFrame。
            dfs_by_timeframe: 可选的 MTF 各周期 DataFrame。
            start_bar: 起始 bar 下标（缺省 warmup_bars——跳过指标
                预热期）。
            predictor_factory: 可调用对象 (dna, df) -> PriceRangePredictor。

        Returns:
            ReplayResult（收益/成交率等指标与事件日志）。
        """
        # 指标须由调用方预先算好（起始 bar 缺省跳过 warmup 预热期）
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

        # 组装信号集
        sig_set = dna_to_signal_set(dna, df, dfs_by_timeframe)

        # 构造账户（VirtualAccount——与 paper 链同款记账）
        account = VirtualAccount(
            dna, init_cash=self.init_cash, fee=self.fee, slippage=self.slippage,
        )

        # 初始化预测器（工厂可注入；具备 warmup 能力则预热）
        predictor = None
        if predictor_factory is not None:
            predictor = predictor_factory(dna, df)
            if predictor is not None and hasattr(predictor, "warmup"):
                predictor.warmup(df, min(start_bar, len(df)))

        # 构造决策管线（判定配置+DNA 风控基因注入）
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

            # 收集事件（持仓事件入 events_log；订单事件计数驱动
            # fill_rate 统计）
            for ev in pipe_result.events:
                result.events_log.append(ev)

            for oe in pipe_result.order_events:
                result.order_events_log.append(oe)
                if oe.action == "created":
                    total_orders += 1
                elif oe.action == "filled":
                    filled_orders += 1

            result.bars_processed += 1

        # 汇总终态指标（末根 bar 持仓未平则按市值计权益——回放不强制
        # 期末平仓，敞口价值并入 final）
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
