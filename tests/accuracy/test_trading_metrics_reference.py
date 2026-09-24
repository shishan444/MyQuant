"""模拟盘链裁判层锚定（fix-indicator-output-mapping 阶段 1，D10 扩链）。

参照实现独立性：本文件的独立内联公式与 tests/reference/reference_metrics
（逐笔汇总法）不 import core.scoring；同数据下与被测 compute_trading_metrics
比对。口径差异预声明：
- trading_metrics.max_drawdown 为绝对额（peak-equity），reference 为小数
  负值——绝对额由本文件独立峰谷公式直接锚定，不经 reference 换算；
- total_return 同式（equity[-1]/init - 1），前提为快照末值==清算权益。
"""

from __future__ import annotations

import numpy as np
import pytest

pytestmark = [pytest.mark.integration]

from core.scoring.trading_metrics import (
    TradingMetricsInput,
    compute_trading_metrics,
)
from tests.reference.reference_metrics import reference_metrics


def _make_paper_input(
    close_pnls: list[float], init_cash: float = 10_000.0
) -> TradingMetricsInput:
    """由逐笔 pnl 构造模拟盘链输入：权益快照 = init + 累积 pnl。"""
    equity = init_cash + np.concatenate([[0.0], np.cumsum(close_pnls)])
    wins = sum(1 for p in close_pnls if p > 0)
    return TradingMetricsInput(
        initial_cash=init_cash,
        balance=float(equity[-1]),
        total_trades=len(close_pnls),
        win_count=wins,
        loss_count=len(close_pnls) - wins,
        realized_pnl=float(sum(close_pnls)),
        unrealized_pnl=0.0,
        position_side=None,
        position_margin=0.0,
        close_pnls=close_pnls,
        equity_snapshots=[float(x) for x in equity],
    )


class TestTradingMetricsReferenceAnchor:
    """模拟盘链与参照实现（逐笔汇总法）同数据同值。"""

    def test_win_rate_and_trades_match_reference(self):
        rng = np.random.default_rng(11)
        close_pnls = list(rng.normal(20.0, 80.0, size=30))
        data = _make_paper_input(close_pnls)

        result = compute_trading_metrics(data)
        ref = reference_metrics(
            np.array(data.equity_snapshots), close_pnls, data.initial_cash
        )

        # 计数类精确相等（口径分歧的直接证据，不容容差）
        assert result["total_trades"] == ref["total_trades"]
        assert result["win_rate"] == pytest.approx(ref["win_rate"], abs=1e-4)

    def test_total_return_matches_reference(self):
        rng = np.random.default_rng(12)
        close_pnls = list(rng.normal(-5.0, 60.0, size=20))
        data = _make_paper_input(close_pnls)

        result = compute_trading_metrics(data)
        ref = reference_metrics(
            np.array(data.equity_snapshots), close_pnls, data.initial_cash
        )
        assert result["total_return"] == pytest.approx(ref["total_return"], abs=1e-6)

    def test_max_drawdown_absolute_matches_inline_formula(self):
        """绝对额回撤独立锚定：peak-eq 的最大差（round(2) 容差）。"""
        rng = np.random.default_rng(13)
        close_pnls = list(rng.normal(0.0, 100.0, size=40))
        data = _make_paper_input(close_pnls)
        equity = np.array(data.equity_snapshots)

        cummax = np.maximum.accumulate(equity)
        dd_abs = float(np.max(cummax - equity))
        result = compute_trading_metrics(data)
        assert result["max_drawdown"] == pytest.approx(round(dd_abs, 2), abs=0.01)

    def test_profit_factor_matches_inline_formula(self):
        rng = np.random.default_rng(14)
        close_pnls = list(rng.normal(10.0, 90.0, size=25))
        data = _make_paper_input(close_pnls)

        gross_profit = sum(p for p in close_pnls if p > 0)
        gross_loss = sum(p for p in close_pnls if p < 0)
        expected = gross_profit / abs(gross_loss) if gross_loss != 0 else None

        result = compute_trading_metrics(data)
        if expected is None:
            assert result["profit_factor"] is None
        else:
            assert result["profit_factor"] == pytest.approx(round(expected, 4), abs=1e-3)

    def test_zero_trades_missing_semantics_anchored(self):
        """零交易缺失语义受裁判层锚定（与回测链同义）。"""
        data = _make_paper_input([], init_cash=5_000.0)
        result = compute_trading_metrics(data)
        ref = reference_metrics(
            np.array(data.equity_snapshots), [], data.initial_cash
        )
        assert result["win_rate"] is None
        assert ref["win_rate"] is None
        assert result["total_trades"] == ref["total_trades"] == 0
